from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, Optional

from .backend_manager import BackendConnection
from .middleware import process_charger_to_backend
from .charge_point import BrokerChargePoint, StarletteWebSocketAdapter

logger = logging.getLogger("ocpp_broker.session")


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
        if self.charger_id not in self.broker.sessions:
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
                if parsed and isinstance(parsed, list) and len(parsed) >= 3:
                    action = parsed[2]
                    logger.info("[%s] → %s → backend", self.charger_id, action)
                await self.backend_conn.send(msg_out)
            except Exception as exc:
                logger.info("[%s] relay stopped: %s", self.charger_id, exc)
                break

