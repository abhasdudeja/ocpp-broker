from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, Optional

from starlette.websockets import WebSocketDisconnect

from .backend_manager import DEFAULT_MAX_BUFFERED, DEFAULT_OUTAGE_TIMEOUT, BackendConnection
from .middleware import process_charger_to_backend
from .sockets import locked_send
from .charge_point import BrokerChargePoint, StarletteWebSocketAdapter
from .charger_state import ChargerState, remote_address
from .commands import LOG_SIZE, CommandEntry
from .history import MessageLabels, message_document
from .local_backend import LocalBackend
from .transaction_ids import TransactionIdTable, backend_keys, leader_index, local_index

logger = logging.getLogger("ocpp_broker.session")

# Upper bound for a remote command's response wait (also the BrokerChargePoint
# library timeout, so the per-request timeout is the only one that ever fires).
COMMAND_MAX_TIMEOUT = 300


def explain_validation_error(exc: Exception) -> str:
    """What was wrong with a command's payload, in a line (the library's own text carries the whole message and a schema dump)."""
    cause = (getattr(exc, "details", None) or {}).get("cause")
    if isinstance(cause, str) and cause:
        first = cause.splitlines()[0]
        return first.split(" is not valid: ", 1)[1] if " is not valid: " in first else first
    return getattr(exc, "description", None) or str(exc)


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
        # What the console shows: when it connected, boot details, connector statuses, traffic counts.
        # Filled by watching the charger's frames; see charger_state.py.
        self.state = ChargerState(remote_address=remote_address(websocket), ocpp_version=str(org_entry.get("ocpp_subprotocol", "ocpp1.6"))[4:])
        # The commands sent to this charger through the API or the console (see commands.py)
        self.command_log: Deque[CommandEntry] = deque(maxlen=LOG_SIZE)
        # The broker answers the charger itself (BROKER) or a backend does (RELAY). A backend marked
        # ``local: true`` is the broker itself: as the leader it answers (BROKER mode, the other backends
        # are observe-only followers); as a follower it is a silent standby, and the mode becomes BROKER
        # if it is promoted when the external leader fails.
        backends = org_entry.get("backends") or []
        local = local_index(backends) if org_entry.get("connect_to_backend") else None
        self.local_key: Optional[str] = backend_keys(backends)[local] if local is not None else None
        local_leads = local is not None and leader_index(backends) == local
        self.mode: SessionMode = (
            SessionMode.RELAY if org_entry.get("connect_to_backend") and not local_leads else SessionMode.BROKER
        )
        self.backend_conn: Optional[BackendConnection | LocalBackend] = None
        self.follower_conns: list[BackendConnection | LocalBackend] = []
        self.charge_point: Optional[BrokerChargePoint] = None
        # Set by OcppBroker.handle_charger; lets a newer connection evict this one.
        self.handler_task: Optional[asyncio.Task] = None
        self.finished = asyncio.Event()
        self._closed = asyncio.Event()
        # Serialises every write to the charger socket (see sockets.locked_send).
        self._send_lock = asyncio.Lock()
        # message id -> future resolved with the charger's CallResult/CallError
        # frame (relay mode only; broker mode uses BrokerChargePoint.call)
        self._pending_calls: Dict[str, asyncio.Future] = {}
        # Observe-only fan-out sends and the leader-failover watcher
        self._background: set[asyncio.Task] = set()
        self._failover_task: Optional[asyncio.Task] = None
        # The backend that leads according to the configuration. After a failover it is the one to give the
        # charger back to (``leader_failback``), and the console says when the charger is not with it.
        self.preferred: Optional[Any] = None
        self._failback_task: Optional[asyncio.Task] = None
        # Relay mode with followers: translates transaction ids per backend (None = off).
        # Owned by the broker so it outlives this socket; see transaction_ids.py.
        self._ids: Optional[TransactionIdTable] = None
        self._hold_timer: Optional[asyncio.TimerHandle] = None
        # Names the action a reply answers, for the message log (history.py)
        self._labels = MessageLabels()

    def publish(self, type: str, **data: Any) -> None:
        """Tell the live event stream something happened to this charger (a no-op without an event bus)."""
        events = getattr(self.broker, "events", None)
        if events is not None:
            events.publish(type, self.org_name, self.charger_id, **data)

    def _record_frame(self, direction: str, raw: str) -> None:
        """Keep a frame in the message log, when that is switched on (``mongodb.history.messages``)."""
        config = getattr(self.broker, "history", None)
        if config is None or not config.messages:
            return
        document = message_document(self.org_name, self.charger_id, direction, raw, self._labels, config)
        if document is not None:
            self.broker.write_history("save_message", **document)

    def _note_outbound(self, message: str) -> None:
        """A frame went to the charger."""
        self.state.note_sent()
        self._record_frame("out", message)

    def _observe(self, raw: str) -> None:
        """Note a frame from the charger in its state, and publish what it changed."""
        self._record_frame("in", raw)
        for kind, data in self.state.observe(raw):
            self.publish(kind, **data)
            if kind == "charger.status" and self.mode is SessionMode.RELAY and self.local_key is None:
                # A backend answers in relay mode, so no handler here stores the status
                connector = self.state.connectors.get(data["connector_id"])
                writer = getattr(self.broker, "write_history", None)
                if connector is not None and writer is not None:
                    writer(
                        "save_status_notification", self.org_name, self.charger_id,
                        connector_id=connector.connector_id, status=connector.status,
                        error_code=connector.error_code, info=connector.info, timestamp=connector.updated_at,
                    )
            if kind == "charger.boot" and self.state.boot is not None:
                note = getattr(self.broker, "note_presence", None)
                if note is not None:
                    boot = self.state.boot
                    note(
                        self.org_name,
                        self.charger_id,
                        vendor=boot.vendor,
                        model=boot.model,
                        serial_number=boot.serial_number,
                        firmware_version=boot.firmware_version,
                        last_boot_at=boot.received_at,
                    )

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
        
        if self.org_entry.get("connect_to_backend") and (self.mode is SessionMode.RELAY or self.local_key is not None):
            # Backends: external ones, and this broker itself when one is marked local
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
        for task in [*self._background, self._failover_task, self._failback_task]:
            if task is not None:
                task.cancel()
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None

        # Forget our backend links, unless a newer session already replaced them
        links = self.broker.org_backends.get(self.org_name, {}).get(self.charger_id)
        if (
            links is not None
            and links.get("leader") is self.backend_conn
            and links.get("followers", self.follower_conns) is self.follower_conns
        ):
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

    async def _send_text(self, message: str):
        """Write one frame to the charger; any failure surfaces as ConnectionError."""
        try:
            await locked_send(self._send_lock, self.websocket.send_text, message)
            self._note_outbound(message)
        except asyncio.TimeoutError as exc:
            logger.error("Charger %s did not accept a frame in time; dropping the connection", self.charger_id)
            try:
                await self.websocket.close(code=1011, reason="Send timed out")
            except Exception:
                pass
            raise ConnectionError(f"Send to charger {self.charger_id} timed out") from exc
        except Exception as exc:
            raise ConnectionError(f"Send to charger {self.charger_id} failed: {exc}") from exc

    async def send_to_charger(self, message: str):
        try:
            await self._send_text(message)
        except ConnectionError as exc:
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
            raise CommandRejected(f"Invalid payload for {action}: {explain_validation_error(exc)}") from exc

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
            await self._send_text(json.dumps([2, message_id, action, payload]))
            if self._ids is not None:
                self._ids.note_command(action, payload)  # ids the broker itself gave the charger are taken
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
        
        keys = backend_keys(backends)
        lead = leader_index(backends)
        self._ids = await self.broker.transaction_table(self.org_name, self.charger_id, self.org_entry)
        links = self.broker.org_backends.setdefault(self.org_name, {}).setdefault(self.charger_id, {})

        # The leader first. Do not block the charger on a backend: frames buffer until it is reachable.
        self.backend_conn = self._member(backends[lead], keys[lead], True, org_subprotocol)
        self.preferred = self.backend_conn
        await self.backend_conn.connect(wait=False)
        links["leader"] = self.backend_conn
        if isinstance(self.backend_conn, LocalBackend):
            self.charge_point = self.backend_conn.charge_point

        for index, config in enumerate(backends):
            if index == lead:
                continue
            follower = self._member(config, keys[index], False, org_subprotocol)
            self.follower_conns.append(follower)
            await follower.connect(wait=False)
        links["followers"] = self.follower_conns

    def _member(self, config: Dict[str, Any], key: str, is_leader: bool, org_subprotocol: str):
        """One backend of this charger: an external server, or this broker itself (``local: true``)."""
        role = "leader" if is_leader else "follower"
        if config.get("local"):
            logger.info("🏠 Charger %s: this broker is a %s backend (%s)", self.charger_id, role, key)
            return LocalBackend(
                broker=self.broker,
                charger_id=self.charger_id,
                org=self.org_name,
                key=key,
                is_leader=is_leader,
                response_timeout=COMMAND_MAX_TIMEOUT,
                on_failed=self._on_local_backend_failed,
            )
        subprotocol = config.get("ocpp_subprotocol", org_subprotocol)
        logger.info("🔗 Establishing %s backend for charger %s -> %s (subprotocol: %s)", role, self.charger_id, config["url"], subprotocol)
        return BackendConnection(
            broker=self.broker,
            charger_id=self.charger_id,
            url=config["url"],
            org=self.org_name,
            is_leader=is_leader,
            subprotocol=subprotocol,
            # the leader holds charger frames while unreachable; followers are best-effort and never block
            max_buffered=self.org_entry.get("backend_buffer_size", DEFAULT_MAX_BUFFERED) if is_leader else 0,
            outage_timeout=self.org_entry.get("backend_outage_timeout", DEFAULT_OUTAGE_TIMEOUT),
            on_undeliverable=self._reject_charger_call if is_leader else None,
            on_disconnected=self._on_backend_link_lost,
            on_connected=self._on_backend_link_up,
            key=key,
        )

    def _on_local_backend_failed(self, member: LocalBackend) -> None:
        """The local backend (the ``ocpp`` library) stopped on an error."""
        if member is self.backend_conn:
            # It was answering the charger: end the connection so the charger reconnects to a fresh session
            logger.error("The local backend of %s failed while it was the leader; closing the charger's connection", self.charger_id)
            self._spawn(self._close_charger_socket())
        else:
            logger.error("The local standby of %s failed; it no longer follows this charger", self.charger_id)

    async def _close_charger_socket(self) -> None:
        try:
            await self.websocket.close(code=1011, reason="The local backend failed")
        except Exception as exc:
            logger.debug("Error closing socket of %s: %s", self.charger_id, exc)

    # ------------------------------------------------------------------ #
    # Followers: observe-only fan-out and leader failover                #
    # ------------------------------------------------------------------ #
    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _fan_out_to_followers(self, message: str, parsed: Any) -> None:
        """
        Give every follower a copy of the charger's CALLs. This is observe-only:
        followers get no buffering, never block the leader's path, and whatever
        they answer is discarded (only the leader may talk to the charger).
        CALLRESULTs are not copied; they answer the leader's own calls.
        """
        if not (isinstance(parsed, list) and parsed and parsed[0] == 2):
            return
        for follower in self.follower_conns:
            self._spawn(self._send_to_follower(follower, message))

    async def _send_to_follower(self, follower: Any, message: str) -> None:
        try:
            await follower.send(message)
        except Exception as exc:
            logger.debug("[%s] could not copy frame to follower %s: %s", self.charger_id, follower.url, exc)

    # ------------------------------------------------------------------ #
    # Transaction id table (relay mode with followers)                   #
    # ------------------------------------------------------------------ #
    def _follower_states(self) -> Dict[str, bool]:
        """Each follower's key and whether a frame can be written to it right now."""
        return {f.key: f.is_ready() for f in self.follower_conns}

    def _send_to_followers(self, pairs: list[tuple[str, str]]) -> None:
        """Send (follower key, frame) pairs; frames for one follower go out in order in one task."""
        grouped: Dict[str, list[str]] = {}
        for key, frame in pairs:
            grouped.setdefault(key, []).append(frame)
        for key, frames in grouped.items():
            follower = next((f for f in self.follower_conns if f.key == key), None)
            if follower is not None:
                self._spawn(self._send_batch_to_follower(follower, frames))

    async def _send_batch_to_follower(self, follower: Any, frames: list[str]) -> None:
        for frame in frames:
            await self._send_to_follower(follower, frame)

    def _arm_hold_timer(self) -> None:
        """Wake up when the oldest copy waiting for a follower's id must be given up on."""
        if self._ids is None:
            return
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None
        delay = self._ids.next_expiry_in()
        if delay is not None and not self._closed.is_set():
            self._hold_timer = asyncio.get_running_loop().call_later(delay + 0.01, self._on_hold_timer)

    def _on_hold_timer(self) -> None:
        self._hold_timer = None
        if self._ids is None or self._closed.is_set():
            return
        self._send_to_followers(self._ids.expire_holds())
        self._arm_hold_timer()

    def frames_for_charger(self, conn: Any, message: str) -> list[str]:
        """
        What to send the charger for a frame from the leader: the frame itself, or,
        with the id table on, the frame in the charger's transaction ids (plus any
        replies owed to retried starts that were waiting on the same answer).
        """
        if self._ids is None:
            return [message]
        try:
            parsed = json.loads(message)
        except ValueError:
            return [message]
        plan = self._ids.from_leader(conn.key, parsed, message)
        frames = [plan.frame, *plan.extra]
        return [f for f in frames if f is not None]  # None: an answer to an observer copy, not for the charger

    def note_follower_frame(self, conn: BackendConnection, message: str) -> None:
        """A follower spoke. Its answer to a start copy tells the id table which id it issued."""
        if self._ids is None:
            return
        try:
            parsed = json.loads(message)
        except ValueError:
            return
        released = self._ids.from_follower(conn.key, parsed)
        if released:
            self._send_to_followers(released)
        self._arm_hold_timer()

    def _on_backend_link_up(self, conn: BackendConnection) -> None:
        self.publish("backend.link", backend=conn.key, role="leader" if conn is self.backend_conn else "follower", connected=True)

    def _on_backend_link_lost(self, conn: BackendConnection) -> None:
        """A backend link dropped. If it was the leader, start watching for failover."""
        self.publish("backend.link", backend=conn.key, role="leader" if conn is self.backend_conn else "follower", connected=False)
        timeout = self.org_entry.get("leader_failover_timeout", 15)
        if conn is not self.backend_conn or not self.follower_conns or not timeout:
            return
        if self._failover_task is None or self._failover_task.done():
            self._failover_task = asyncio.ensure_future(self._watch_leader(conn, float(timeout)))

    async def _watch_leader(self, leader: BackendConnection, timeout: float) -> None:
        """Promote the first healthy follower if the leader stays down for ``timeout`` seconds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        warned = False
        while not self._closed.is_set() and leader is self.backend_conn and not leader.is_ready():
            await asyncio.sleep(max(0.0, min(1.0, deadline - loop.time())) if loop.time() < deadline else 1.0)
            if self._closed.is_set() or leader is not self.backend_conn or leader.is_ready():
                return
            if loop.time() < deadline:
                continue  # the leader may still come back
            # Past the timeout: promote the first healthy follower, and look again every second until there is one
            candidate = next((f for f in self.follower_conns if f.is_ready()), None)
            if candidate is not None:
                self._promote(candidate)
                return
            if not warned:
                warned = True
                logger.warning(
                    "[%s] leader backend %s is down and no follower is healthy; still waiting",
                    self.charger_id,
                    leader.url,
                )

    def promote_to(self, key: str) -> None:
        """
        Make the follower ``key`` the leader now (an operator's choice). Raises ValueError, with a reason that
        can be shown, if that cannot be done: no such follower, or it is not connected.
        """
        if self._closed.is_set():
            raise ValueError("The charger is disconnected")
        if self.backend_conn is not None and self.backend_conn.key == key:
            raise ValueError(f"{key} already leads")
        target = next((f for f in self.follower_conns if f.key == key), None)
        if target is None:
            raise ValueError(f"{key} is not a backend of this charger")
        if not target.is_ready():
            raise ValueError(f"{key} is not connected, so it cannot take over")
        self._promote(target, "manual")

    def _promote(self, new_leader: Any, reason: str = "failover") -> None:
        """Swap roles: ``new_leader`` becomes the leader, the old leader a follower."""
        old_leader = self.backend_conn
        assert old_leader is not None and new_leader in self.follower_conns
        if reason == "failover":
            logger.warning(
                "🔁 [%s] FAILOVER: leader %s unreachable, promoting follower %s",
                self.charger_id,
                old_leader.url,
                new_leader.url,
            )
        else:
            logger.warning(
                "🔁 [%s] %s: %s takes over from %s",
                self.charger_id,
                "FAIL-BACK" if reason == "failback" else "LEADER CHANGED BY AN OPERATOR",
                new_leader.url,
                old_leader.url,
            )
        # Whatever was waiting for the old leader is answered with CALLERROR (the
        # charger retries it, and the retry goes to the new leader). It is not
        # replayed: the new leader already saw those CALLs as observed copies.
        old_leader.reject_buffered()
        self.publish("backend.failover", old_leader=old_leader.key, new_leader=new_leader.key, reason=reason)

        old_leader.is_leader = False
        old_leader.max_buffered = 0
        old_leader.on_undeliverable = None

        new_leader.is_leader = True
        new_leader.max_buffered = self.org_entry.get("backend_buffer_size", DEFAULT_MAX_BUFFERED)
        new_leader.outage_timeout = self.org_entry.get("backend_outage_timeout", DEFAULT_OUTAGE_TIMEOUT)
        new_leader.on_undeliverable = self._reject_charger_call

        self.follower_conns = [f for f in self.follower_conns if f is not new_leader] + [old_leader]
        self.backend_conn = new_leader
        if isinstance(new_leader, LocalBackend):
            # The standby takes over: from now on the broker answers the charger (and commands use its library)
            self.mode = SessionMode.BROKER
            self.charge_point = new_leader.charge_point
        elif isinstance(old_leader, LocalBackend):
            # The broker hands the charger back to an external backend and goes on as a silent standby
            self.mode = SessionMode.RELAY
            self.charge_point = None
        if self._ids is not None:
            self._ids.leader_changed(new_leader.key)
            self._arm_hold_timer()
        links = self.broker.org_backends.get(self.org_name, {}).get(self.charger_id)
        if links is not None:
            links["leader"] = new_leader
            links["followers"] = self.follower_conns
        if reason == "failover" and new_leader is not self.preferred and self.org_entry.get("leader_failback"):
            if self._failback_task is None or self._failback_task.done():
                self._failback_task = asyncio.ensure_future(self._watch_preferred(float(self.org_entry.get("leader_failback_delay", 60))))

    async def _watch_preferred(self, delay: float) -> None:
        """
        Give the charger back to the configured leader once it has been connected for ``delay`` seconds without a
        break (a leader that flaps would otherwise be handed the charger just to lose it again). Only after a
        failover: a leader an operator chose stays until an operator says otherwise.
        """
        loop = asyncio.get_running_loop()
        healthy_since: Optional[float] = None
        while not self._closed.is_set():
            await asyncio.sleep(min(1.0, max(delay / 4, 0.05)))
            preferred = self.preferred
            if preferred is None or preferred is self.backend_conn or preferred not in self.follower_conns:
                return  # it leads again (by an operator's hand, say), or it is gone
            if not preferred.is_ready():
                healthy_since = None
                continue
            if healthy_since is None:
                healthy_since = loop.time()
            if loop.time() - healthy_since >= delay:
                self._promote(preferred, "failback")
                return

    async def _reject_charger_call(self, message: str):
        """
        The backend could not take a frame from the charger (outage or full
        outbox). Answer a CALL with a CALLERROR so the charger is not left
        waiting; anything else (e.g. a CALLRESULT) is simply dropped.
        """
        try:
            parsed = json.loads(message)
        except ValueError:
            return
        if isinstance(parsed, list) and len(parsed) >= 2 and parsed[0] == 2:
            error = [4, parsed[1], "InternalError", "Backend unavailable, please retry", {}]
            logger.warning("[%s] answering %s with CallError: backend unavailable", self.charger_id, parsed[2] if len(parsed) > 2 else "CALL")
            await self.send_to_charger(json.dumps(error))
            if self._ids is not None and len(parsed) > 2 and parsed[2] == "StartTransaction":
                # The attempt is over; retries that were waiting on it are answered too
                for waiting in self._ids.start_failed(parsed[1]):
                    await self.send_to_charger(waiting)
        else:
            logger.warning("[%s] dropped an undeliverable frame: %s", self.charger_id, message[:200])

    async def _run_local_charge_point(self):
        logger.info(
            "🎯 Broker acting as backend for charger %s (org: %s)", self.charger_id, self.org_name
        )
        adapter = StarletteWebSocketAdapter(
            self.websocket,
            send_lock=self._send_lock,
            on_receive=self._observe,
            on_send=self._note_outbound,
        )
        self.charge_point = BrokerChargePoint(
            charge_point_id=self.charger_id,
            websocket=adapter,
            broker=self.broker,
            org_name=self.org_name,
            response_timeout=COMMAND_MAX_TIMEOUT,
        )
        try:
            await self.charge_point.start()
        except WebSocketDisconnect:
            logger.info("Charger %s disconnected", self.charger_id)
        except Exception as exc:
            logger.exception("ChargePoint %s terminated with error: %s", self.charger_id, exc)

    async def _relay_with_ids(self, message: str, parsed: Any) -> None:
        """Send one charger frame on, each backend getting its own transaction ids."""
        assert self._ids is not None and self.backend_conn is not None
        plan = self._ids.from_charger(parsed, message, self.backend_conn.key, self._follower_states())
        if plan.reply is not None:
            await self.send_to_charger(plan.reply)  # a retried start: answered from the stored result
        if plan.to_leader is not None:
            await self.backend_conn.send(plan.to_leader)
        self._send_to_followers(plan.to_followers)
        self._arm_hold_timer()

    async def _relay_loop(self):
        if not self.backend_conn:
            logger.warning("⚠️ No backend connection for charger %s", self.charger_id)
            return

        logger.info("🚀 Relay active for charger %s", self.charger_id)
        
        while True:
            try:
                msg = await self.websocket.receive_text()
                self._observe(msg)
                msg_out, parsed = await process_charger_to_backend(self.charger_id, msg)
                if self._resolve_pending_call(parsed):
                    continue  # reply to a broker-issued command; the backend never asked for it
                if parsed and isinstance(parsed, list) and len(parsed) >= 3:
                    action = parsed[2]
                    logger.info("[%s] → %s → backend", self.charger_id, action)
                if self._ids is None:
                    await self.backend_conn.send(msg_out)
                    self._fan_out_to_followers(msg_out, parsed)
                else:
                    await self._relay_with_ids(msg_out, parsed)
            except Exception as exc:
                logger.info("[%s] relay stopped: %s", self.charger_id, exc)
                break

