from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from .backend_manager import BackendConnection
from .middleware import process_charger_to_backend
from .charge_point import BrokerChargePoint, StarletteWebSocketAdapter

logger = logging.getLogger("ocpp_broker.session")

# Upper bound for a remote command's response wait (also the BrokerChargePoint
# library timeout, so the per-request timeout is the only one that ever fires).
COMMAND_MAX_TIMEOUT = 300


class CommandRejected(ValueError):
    """The requested command is not a valid OCPP 1.6 CALL; nothing was sent."""


@dataclass
class CommandResult:
    message_id: str
    status: str  # "success" | "error" | "timeout"
    response: Optional[Dict[str, Any]] = None  # camelCase CallResult payload
    error: Optional[str] = None  # "<code>: <description>" for a CallError

class SessionMode(str, Enum):
    BROKER = "broker"
    RELAY = "relay"


class ChargerSession:
    """
    Encapsulates the lifecycle of a charger connection.
    Keeps all per-connection state (backend link, charge point instance, mode)
    in one place to keep the broker orchestrator focused on orchestration.
    """

    def __init__(self, broker, charger_id: str, org_name: str, org_entry: Dict[str, Any], websocket):
        self.broker = broker
        self.charger_id = charger_id
        self.org_name = org_name
        self.org_entry = org_entry
        self.websocket = websocket
        self.mode: SessionMode = (
            SessionMode.RELAY if org_entry.get("connect_to_backend") else SessionMode.BROKER
        )
        self.backend_conn: Optional[BackendConnection] = None
        self.follower_conns: list[BackendConnection] = []
        self.charge_point: Optional[BrokerChargePoint] = None
        # Set by OcppBroker.handle_charger; lets a newer connection evict this one.
        self.handler_task: Optional[asyncio.Task] = None
        self.finished = asyncio.Event()
        self._closed = asyncio.Event()
        # message id -> future resolved with the charger's CallResult/CallError
        # frame (relay mode only; broker mode uses BrokerChargePoint.call)
        self._pending_calls: Dict[str, asyncio.Future] = {}
    async def evict(self, grace: float = 5.0):
        """
        Disconnect this session because the same charger connected again.
        Closes the socket and waits for the handler to unwind, cancelling it if
        the peer never completes the close handshake.
        """
        try:
            await self.websocket.close(code=4003, reason="Replaced by a newer connection")
        except Exception as exc:
            logger.debug("Error closing replaced socket for %s: %s", self.charger_id, exc)

        task = self.handler_task
        if task is None or task.done() or task is asyncio.current_task():
            return
        try:
            await asyncio.wait_for(self.finished.wait(), timeout=grace)
        except asyncio.TimeoutError:
            logger.warning("Replaced session for %s did not exit; cancelling it", self.charger_id)
            task.cancel()

    async def start(self):
        # Verify charger is connected before proceeding
        if not self._is_charger_connected():
            logger.warning("⚠️ Charger %s websocket not connected, cannot start session", self.charger_id)
            return
        
        logger.info("✅ Charger %s connected to broker, starting session (mode: %s)", self.charger_id, self.mode.value)
        
        if self.mode is SessionMode.RELAY:
            # Only connect to backend if charger is connected AND connect_to_backend is True
            if not self.org_entry.get("connect_to_backend", False):
                logger.warning("⚠️ Backend connection disabled for charger %s, switching to broker mode", self.charger_id)
                await self._run_local_charge_point()
            else:
                await self._ensure_backend_connection()
                await self._relay_loop()
        else:
            await self._run_local_charge_point()

    async def close(self):
        """Close session and all associated backend connections."""
        logger.info(f"Closing session for charger {self.charger_id}")
        self._closed.set()
        for future in self._pending_calls.values():
            if not future.done():
                future.set_exception(ConnectionError(f"Charger {self.charger_id} disconnected"))
        self._pending_calls.clear()        
        # Forget our backend links, unless a newer session already replaced them
        links = self.broker.org_backends.get(self.org_name, {}).get(self.charger_id)
        if links is not None and links.get("leader") is self.backend_conn:
            del self.broker.org_backends[self.org_name][self.charger_id]

        # Close all backend connections (leader and followers)
        if self.backend_conn:
            await self.backend_conn.close()
            self.backend_conn = None
        
        for follower in self.follower_conns:
            await follower.close()
        self.follower_conns = []
        
        # Clean up charge point if in broker mode
        self.charge_point = None
        
        logger.debug(f"Session closed for charger {self.charger_id}")

    async def send_to_charger(self, message: str):
        try:
            await self.websocket.send_text(message)
        except Exception as exc:
            logger.warning("Failed to deliver backend message to %s: %s", self.charger_id, exc)

    # ------------------------------------------------------------------ #
    # Remote commands (REST -> charger)                                  #
    # ------------------------------------------------------------------ #
    async def send_command(
        self,
        action: str,
        payload: Dict[str, Any],
        timeout: float = 30,
        message_id: Optional[str] = None,
    ) -> CommandResult:
        """
        Send an OCPP CALL to the charger and wait for its CallResult/CallError.

        ``payload`` uses OCPP's camelCase keys. Raises CommandRejected if the
        request is not a valid OCPP 1.6 CALL and ConnectionError if the charger
        is (or becomes) unreachable. A charger that does not answer within
        ``timeout`` yields a result with status "timeout".
        """
        message_id = message_id or str(uuid.uuid4())
        if self._closed.is_set():
            raise ConnectionError(f"Charger {self.charger_id} is disconnected")
        if self.mode is SessionMode.BROKER:
            return await self._command_via_charge_point(action, payload, timeout, message_id)
        return await self._command_via_relay(action, payload, timeout, message_id)

    async def _command_via_charge_point(self, action, payload, timeout, message_id) -> CommandResult:
        from ocpp.charge_point import camel_to_snake_case, remove_nones, serialize_as_dict, snake_to_camel_case
        from ocpp.exceptions import OCPPError
        from ocpp.messages import Call, validate_payload
        from ocpp.v16 import call

        if self.charge_point is None:
            raise ConnectionError(f"Charger {self.charger_id} is not ready for commands yet")

        if not re.fullmatch(r"[A-Za-z]+", action or ""):
            raise CommandRejected(f"Invalid OCPP action name: {action!r}")
        request_cls = getattr(call, action, None)
        if not (isinstance(request_cls, type) and dataclasses.is_dataclass(request_cls)):
            raise CommandRejected(f"Unknown OCPP 1.6 action: {action}")
        try:
            request = request_cls(**camel_to_snake_case(payload))
        except TypeError as exc:
            raise CommandRejected(f"Invalid payload for {action}: {exc}") from exc
        try:
            await validate_payload(
                Call(unique_id=message_id, action=action, payload=remove_nones(snake_to_camel_case(serialize_as_dict(request)))),
                "1.6",
            )
        except OCPPError as exc:
            raise CommandRejected(f"Invalid payload for {action}: {exc}") from exc

        call_task = asyncio.ensure_future(
            self.charge_point.call(request, suppress=False, unique_id=message_id)
        )
        closed_task = asyncio.ensure_future(self._closed.wait())
        try:
            done, _ = await asyncio.wait(
                {call_task, closed_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            closed_task.cancel()
            if not call_task.done():
                call_task.cancel()  # releases the library's call lock; a late reply is ignored

        if call_task not in done:
            if self._closed.is_set():
                raise ConnectionError(f"Charger {self.charger_id} disconnected before replying")
            return CommandResult(message_id, "timeout", error=f"No response within {timeout}s")
        try:
            result = call_task.result()
        except OCPPError as exc:  # CallError sent by the charger
            return CommandResult(message_id, "error", error=f"{exc.code}: {exc.description}")
        except asyncio.TimeoutError as exc:
            return CommandResult(message_id, "timeout", error=str(exc))
        response = snake_to_camel_case(remove_nones(serialize_as_dict(result)))
        return CommandResult(message_id, "success", response=response)

    async def _command_via_relay(self, action, payload, timeout, message_id) -> CommandResult:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_calls[message_id] = future
        try:
            await self.websocket.send_text(json.dumps([2, message_id, action, payload]))
            frame = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return CommandResult(message_id, "timeout", error=f"No response within {timeout}s")
        finally:
            self._pending_calls.pop(message_id, None)

        if frame[0] == 4:  # CALLERROR: [4, id, code, description, details]
            code = frame[2] if len(frame) > 2 else "GenericError"
            description = frame[3] if len(frame) > 3 else ""
            return CommandResult(message_id, "error", error=f"{code}: {description}")
        return CommandResult(message_id, "success", response=frame[2] if len(frame) > 2 else {})

    def _resolve_pending_call(self, parsed: Any) -> bool:
        """Hand a charger CallResult/CallError to the REST caller waiting for it."""
        if not (isinstance(parsed, list) and len(parsed) >= 2 and parsed[0] in (3, 4)):
            return False
        future = self._pending_calls.get(parsed[1])
        if future is None:
            return False
        if not future.done():
            future.set_result(parsed)
        return True

    # ------------------------------------------------------------------ # 
    # Internal helpers                                                   # 
    # ------------------------------------------------------------------ # 
    def _is_charger_connected(self) -> bool:
        """Check if charger websocket is still connected."""
        if not self.websocket:
            return False
        try:
            # Check websocket state (Starlette/FastAPI WebSocket)
            client_state = getattr(self.websocket, "client_state", None)
            if client_state:
                state_name = getattr(client_state, "name", None)
                return state_name != "DISCONNECTED"
            # If we can't determine state, assume connected
            return True
        except Exception:
            # If check fails, assume disconnected for safety
            return False

    async def _ensure_backend_connection(self):
        """Establish backend connection ONLY after charger is confirmed connected."""
        # Double-check charger is still connected before connecting to backend
        if not self._is_charger_connected():
            logger.error("❌ Cannot connect to backend: charger %s is not connected", self.charger_id)
            raise RuntimeError(f"Charger {self.charger_id} websocket not connected")
        
        # Verify charger session exists in broker
        if self.broker.sessions.get((self.org_name, self.charger_id)) is not self:
            logger.error("❌ Cannot connect to backend: charger %s session not found in broker", self.charger_id)
            raise RuntimeError(f"Charger {self.charger_id} session not found")
        
        logger.info("✅ Charger %s confirmed connected, establishing backend connection...", self.charger_id)
        backends = self.org_entry.get("backends") or []
        if not backends:
            raise RuntimeError(f"Organization {self.org_name} has no backend definition.")

        # Get OCPP subprotocol from organization config, backend config, or default to "ocpp1.6"
        org_subprotocol = self.org_entry.get("ocpp_subprotocol", "ocpp1.6")
        
        leader_config = next((b for b in backends if b.get("leader")), backends[0])
        follower_configs = [b for b in backends if b is not leader_config]

        # Get subprotocol for leader (backend-specific or org-level or default)
        leader_subprotocol = leader_config.get("ocpp_subprotocol", org_subprotocol)

        # Leader connection
        self.backend_conn = BackendConnection(
            broker=self.broker,
            charger_id=self.charger_id,
            url=leader_config["url"],
            org=self.org_name,
            is_leader=True,
            subprotocol=leader_subprotocol,
        )
        logger.info("🔗 Establishing leader backend for charger %s -> %s (subprotocol: %s)", 
                   self.charger_id, leader_config["url"], leader_subprotocol)
        await self.backend_conn.connect()
        self.broker.org_backends.setdefault(self.org_name, {}).setdefault(self.charger_id, {})["leader"] = self.backend_conn

        # Follower connections
        for follower_cfg in follower_configs:
            # Get subprotocol for follower (backend-specific or org-level or default)
            follower_subprotocol = follower_cfg.get("ocpp_subprotocol", org_subprotocol)
            follower_conn = BackendConnection(
                broker=self.broker,
                charger_id=self.charger_id,
                url=follower_cfg["url"],
                org=self.org_name,
                is_leader=False,
                subprotocol=follower_subprotocol,
            )
            self.follower_conns.append(follower_conn)
            logger.info("🔗 Establishing follower backend for charger %s -> %s (subprotocol: %s)", 
                       self.charger_id, follower_cfg["url"], follower_subprotocol)
            await follower_conn.connect()
        self.broker.org_backends[self.org_name][self.charger_id]["followers"] = self.follower_conns

    async def _run_local_charge_point(self):
        # Check if validation is enabled for this organization when broker acts as backend
        validate_messages = self.org_entry.get("validate_messages_when_broker_backend", False)
        logger.info(
            "🎯 Broker acting as backend for charger %s (org: %s, validation: %s)", 
            self.charger_id, 
            self.org_name,
            "enabled" if validate_messages else "disabled"
        )
        adapter = StarletteWebSocketAdapter(
            self.websocket, 
            validate_messages=validate_messages,
            org_entry=self.org_entry
        )
        self.charge_point = BrokerChargePoint(
            charge_point_id=self.charger_id,
            websocket=adapter,
            broker=self.broker,
            org_name=self.org_name,
            response_timeout=COMMAND_MAX_TIMEOUT,
        )
        # Store reference to charge_point in adapter for MongoDB saving
        adapter._charge_point = self.charge_point
        try:
            await self.charge_point.start()
        except Exception as exc:
            logger.exception("ChargePoint %s terminated with error: %s", self.charger_id, exc)

    async def _relay_loop(self):
        if not self.backend_conn:
            logger.warning("⚠️ No backend connection for charger %s", self.charger_id)
            return

        # Ensure backend connection is ready before starting relay
        if not self.backend_conn.connected_event.is_set():
            logger.info("⏳ Waiting for backend connection to be ready for charger %s...", self.charger_id)
            try:
                await asyncio.wait_for(self.backend_conn.connected_event.wait(), timeout=30.0)
                logger.info("✅ Backend connection ready for charger %s", self.charger_id)
            except asyncio.TimeoutError:
                logger.error("❌ Timeout waiting for backend connection for charger %s", self.charger_id)
                return

        # Check if validation is enabled for this organization when backend is leader
        validate_messages = self.org_entry.get("validate_messages_when_backend_leader", False)
        logger.info("🚀 Relay active for charger %s (validation: %s)", 
                   self.charger_id, "enabled" if validate_messages else "disabled")
        
        while True:
            try:
                msg = await self.websocket.receive_text()
                msg_out, parsed = await process_charger_to_backend(
                    self.charger_id, 
                    msg, 
                    validate=validate_messages,
                    org_entry=self.org_entry
                )
                if self._resolve_pending_call(parsed):
                    continue  # reply to a broker-issued command; the backend never asked for it
                if parsed and isinstance(parsed, list) and len(parsed) >= 3:
                    action = parsed[2]
                    logger.info("[%s] → %s → backend", self.charger_id, action)
                await self.backend_conn.send(msg_out)
            except Exception as exc:
                logger.info("[%s] relay stopped: %s", self.charger_id, exc)
                break

