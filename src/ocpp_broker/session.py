from __future__ import annotations

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
        if self.mode is SessionMode.RELAY:
            await self._ensure_backend_connection()
            await self._relay_loop()
        else:
            await self._run_local_charge_point()

    async def close(self):
        if self.backend_conn:
            await self.backend_conn.close()
        for follower in self.follower_conns:
            await follower.close()
        self.backend_conn = None
        self.follower_conns = []
        self.charge_point = None

    async def send_to_charger(self, message: str):
        try:
            await self.websocket.send_text(message)
        except Exception as exc:
            logger.warning("Failed to deliver backend message to %s: %s", self.charger_id, exc)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    async def _ensure_backend_connection(self):
        backends = self.org_entry.get("backends") or []
        if not backends:
            raise RuntimeError(f"Organization {self.org_name} has no backend definition.")

        leader_config = next((b for b in backends if b.get("leader")), backends[0])
        follower_configs = [b for b in backends if b is not leader_config]

        # Leader connection
        self.backend_conn = BackendConnection(
            broker=self.broker,
            charger_id=self.charger_id,
            url=leader_config["url"],
            org=self.org_name,
            is_leader=True,
        )
        logger.info("🔗 Establishing leader backend for charger %s -> %s", self.charger_id, leader_config["url"])
        await self.backend_conn.connect()
        self.broker.org_backends.setdefault(self.org_name, {}).setdefault(self.charger_id, {})["leader"] = self.backend_conn

        # Follower connections
        for follower_cfg in follower_configs:
            follower_conn = BackendConnection(
                broker=self.broker,
                charger_id=self.charger_id,
                url=follower_cfg["url"],
                org=self.org_name,
                is_leader=False,
            )
            self.follower_conns.append(follower_conn)
            logger.info("🔗 Establishing follower backend for charger %s -> %s", self.charger_id, follower_cfg["url"])
            await follower_conn.connect()
        self.broker.org_backends[self.org_name][self.charger_id]["followers"] = self.follower_conns

    async def _run_local_charge_point(self):
        logger.info("🎯 Broker acting as backend for charger %s (org: %s)", self.charger_id, self.org_name)
        adapter = StarletteWebSocketAdapter(self.websocket)
        self.charge_point = BrokerChargePoint(
            charge_point_id=self.charger_id,
            websocket=adapter,
            broker=self.broker,
            org_name=self.org_name,
        )
        try:
            await self.charge_point.start()
        except Exception as exc:
            logger.exception("ChargePoint %s terminated with error: %s", self.charger_id, exc)

    async def _relay_loop(self):
        if not self.backend_conn:
            logger.warning("⚠️ No backend connection for charger %s", self.charger_id)
            return

        logger.info("🚀 Relay active for charger %s", self.charger_id)
        while True:
            try:
                msg = await self.websocket.receive_text()
                msg_out, parsed = await process_charger_to_backend(self.charger_id, msg)
                if parsed and isinstance(parsed, list) and len(parsed) >= 3:
                    action = parsed[2]
                    logger.info("[%s] → %s → backend", self.charger_id, action)
                await self.backend_conn.send(msg_out)
            except Exception as exc:
                logger.info("[%s] relay stopped: %s", self.charger_id, exc)
                break

