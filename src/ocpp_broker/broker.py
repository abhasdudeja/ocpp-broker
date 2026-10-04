import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .backend_manager import BackendConnection
from .registry import ChargerRegistry
from .config import load_config
from .tag_manager import TagManager
from .session import ChargerSession
from .transaction_ids import TransactionIdTable, backend_keys, table_for_org
from .transaction_store import TransactionStore

logger = logging.getLogger("ocpp_broker.broker")


class OcppBroker:
    """
    Bi-directional OCPP Broker backed by the upstream `ocpp` library.
    Each charger connection can either be relayed to an upstream backend or be
    handled locally via a BrokerChargePoint instance.
    """

    def __init__(self):
        # Shown by GET /api/system/info; lets an operator tell instances apart
        self.instance_id = uuid.uuid4().hex[:8]
        self.started_at = datetime.now(timezone.utc)
        self.org_backends: Dict[str, Dict[str, BackendConnection]] = {}
        self.org_registries: Dict[str, ChargerRegistry] = {}
        # Keyed by (org_name, charger_id): the same charger id may exist in several orgs.
        self.sessions: Dict[Tuple[str, str], ChargerSession] = {}
        # Transaction id tables outlive sessions: a charger's socket drops and
        # reconnects in the middle of a charge, and the mapping must survive that.
        self.transaction_tables: Dict[Tuple[str, str], TransactionIdTable] = {}
        self._tx_store: Optional[TransactionStore] = None
        self._tx_store_for: Any = None  # the MongoDBService the store was made for
        self._tx_memory_warned = False
        self.tag_manager: Optional[TagManager] = None
        self.data_transfer_handler = None  # Will be created on first use
        self.mongodb_service = None  # Will be initialized if MongoDB is configured
        self.config_data: Dict[str, Any] = {}
        self._cfg_path = "config.yaml"
        # Only used when MongoDB cannot supply transaction ids; see next_transaction_id.
        self._fallback_tx_counters: Dict[str, int] = {}
        self._fallback_warned: set[str] = set()

    # ------------------------------------------------------------------
    # Configuration and initialization
    # ------------------------------------------------------------------
    async def load_config(self):
        self.config_data = load_config(self._cfg_path)
        logger.info(
            "Loaded configuration for %s organizations.",
            len(self.config_data.get("organizations", [])),
        )
        
        # Initialize MongoDB service if configured
        await self._initialize_mongodb()
        # Created here (not on first charger) so the REST API sees it from startup
        self._ensure_tag_manager()

    async def ensure_org_initialized(self, org_name: str):
        if org_name not in self.org_registries:
            self.org_registries[org_name] = ChargerRegistry()
        self._ensure_tag_manager()

    def _ensure_tag_manager(self):
        if self.tag_manager is None:
            # Pass MongoDB service to TagManager if available
            mongodb = getattr(self, "mongodb_service", None)
            self.tag_manager = TagManager(self.config_data, mongodb_service=mongodb)
            if self.tag_manager.is_enabled():
                logger.info("Tag management enabled and initialized")
            else:
                logger.info(
                    "Tag management disabled - no organizations with tag management enabled"
                )

    # ------------------------------------------------------------------
    # Handle new charger connection
    # ------------------------------------------------------------------
    async def handle_charger(self, websocket, path):
        """Handle new charger connection and create backend link."""
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) != 2:
            await websocket.close(code=4000, reason="Invalid path format")
            return

        org_name, charger_id = parts
        if not self.config_data:
            await self.load_config()

        org_entry = next(
            (o for o in self.config_data.get("organizations", []) if o.get("name") == org_name),
            None,
        )
        if not org_entry:
            logger.warning(
                "❌ Rejected charger %s: unknown organization '%s'.", charger_id, org_name
            )
            await websocket.close(code=4002, reason="Unknown organization")
            return

        connect_to_backend = org_entry.get("connect_to_backend", False)
        await self.ensure_org_initialized(org_name)

        logger.info(
            "✅ Accepted charger %s for org '%s' (backend connection: %s)",
            charger_id,
            org_name,
            "enabled" if connect_to_backend else "disabled",
        )

        session = ChargerSession(
            broker=self,
            charger_id=charger_id,
            org_name=org_name,
            org_entry=org_entry,
            websocket=websocket,
        )
        key = (org_name, charger_id)
        # Swap synchronously (no await between lookup and store) so two racing
        # connects can't both believe they are the first.
        previous = self.sessions.get(key)
        self.sessions[key] = session
        session.handler_task = asyncio.current_task()

        try:
            if previous is not None:
                logger.warning(
                    "Charger %s/%s reconnected while a session was still open; replacing it",
                    org_name,
                    charger_id,
                )
                await previous.evict()
            await session.start()
        except Exception as exc:
            logger.exception("Error in message handling for %s: %s", charger_id, exc)
        finally:
            await session.close()
            # Only drop our own entry: a newer connection may have replaced us.
            if self.sessions.get(key) is session:
                del self.sessions[key]
            self.release_transaction_table(org_name, charger_id)
            session.finished.set()
            logger.info("🧹 Cleaned up charger %s session.", charger_id)

    # ------------------------------------------------------------------
    # Helpers + backend callbacks
    # ------------------------------------------------------------------
    def get_registry(self, org_name: str) -> ChargerRegistry:
        return self.org_registries[org_name]

    def _transaction_store(self) -> Optional[TransactionStore]:
        """The store that keeps id tables in MongoDB, or None when MongoDB is not in use."""
        mongodb = getattr(self, "mongodb_service", None)
        if mongodb is None:
            return None
        if self._tx_store is None or self._tx_store_for is not mongodb:
            self._tx_store, self._tx_store_for = TransactionStore(mongodb), mongodb
        return self._tx_store

    async def transaction_table(
        self, org_name: str, charger_id: str, org_entry: Dict[str, Any]
    ) -> Optional[TransactionIdTable]:
        """
        The charger's transaction id table (created, and restored from MongoDB, on first use),
        or None when mapping is off.
        """
        key = (org_name, charger_id)
        table = self.transaction_tables.get(key)
        if table is None:
            table = table_for_org(org_entry)
            if table is None:
                return None
            self.transaction_tables[key] = table
            store = self._transaction_store()
            if store is None:
                if not self._tx_memory_warned:
                    self._tx_memory_warned = True
                    logger.warning(
                        "Transaction id mapping is memory-only: without MongoDB it is lost when the broker "
                        "restarts, and followers lose track of transactions already running."
                    )
            else:
                keys = set(backend_keys(org_entry.get("backends") or []))

                def on_change(uid: str, doc: Optional[Dict[str, Any]], expires: Optional[float]) -> None:
                    if doc is None:
                        store.delete(uid)
                    elif expires is not None:
                        store.save(org_name, charger_id, uid, doc, expires)

                table.on_change = on_change
                stored = await store.load(org_name, charger_id)
                restored = table.restore(stored, keys)
                if restored:
                    logger.info("Restored %d transaction id record(s) for %s/%s", restored, org_name, charger_id)
        return table

    def release_transaction_table(self, org_name: str, charger_id: str) -> None:
        """Drop a charger's table once nothing in it is worth keeping and no session uses it."""
        key = (org_name, charger_id)
        table = self.transaction_tables.get(key)
        if table is not None and key not in self.sessions and table.is_idle():
            del self.transaction_tables[key]

    async def next_transaction_id(self, org_name: str) -> int:
        """
        Allocate a transaction id from a per-organization counter.

        The counter lives in MongoDB (``$inc``, so it is atomic across broker
        instances and survives restarts). If MongoDB is unavailable we fall back
        to an in-memory counter and warn loudly: those ids are only unique
        within this process and may collide with ids issued before a restart.
        """
        mongodb = getattr(self, "mongodb_service", None)
        if mongodb is not None and mongodb.is_connected():
            try:
                value = await mongodb.next_sequence(f"transaction_id:{org_name}")
                self._fallback_warned.discard(org_name)
                return value
            except Exception as exc:
                logger.error(
                    "Could not allocate transaction id from MongoDB for org '%s': %s",
                    org_name,
                    exc,
                )
        return self._next_fallback_transaction_id(org_name)

    def _next_fallback_transaction_id(self, org_name: str) -> int:
        if org_name not in self._fallback_warned:
            self._fallback_warned.add(org_name)
            logger.warning(
                "!!! TRANSACTION IDS FOR ORG '%s' ARE NOT DURABLE !!! MongoDB is not "
                "available, so ids come from an in-memory counter. They restart with "
                "the broker and can collide with ids already stored by the charger or "
                "backend. Configure MongoDB (mongodb.enabled) before production use.",
                org_name,
            )
        counter = self._fallback_tx_counters.get(org_name)
        if counter is None:
            # Seed from the clock so a restarted broker does not start over at 1.
            counter = int(time.time())
        counter += 1
        self._fallback_tx_counters[org_name] = counter
        return counter

    async def forward_backend_message(self, backend_conn: BackendConnection, message: str):
        """Deliver backend messages to the connected charger websocket."""
        session = self.sessions.get((backend_conn.org, backend_conn.id))
        if not session:
            logger.warning(
                "Cannot deliver backend message to %s: charger not connected", backend_conn.id
            )
            return

        if not backend_conn.is_leader:
            # Followers are observe-only: they receive a copy of the charger's CALLs
            # and reply to them, but only the leader may talk to the charger. Their
            # answers are never forwarded; the id table only reads which transaction
            # id a follower issued for a start.
            session.note_follower_frame(backend_conn, message)
            logger.debug(
                "Ignored message from follower backend %s (org=%s)", backend_conn.id, backend_conn.org
            )
            return

        # The charger must see its own transaction ids, not the leader's, when they differ
        frames = session.frames_for_charger(backend_conn, message)
        if not frames:
            return
        message = frames[0]

        # Save command to MongoDB when broker is leader
        try:
            import json
            parsed = json.loads(message)
            if isinstance(parsed, list) and len(parsed) >= 3:
                message_type = parsed[0]
                action = parsed[2] if len(parsed) > 2 else None
                payload = parsed[3] if len(parsed) > 3 else {}
                
                # Only save CALL messages (type 2) - commands from Central System to Charge Point
                if message_type == 2 and action:
                    mongodb = getattr(self, "mongodb_service", None)
                    if mongodb and mongodb.is_connected():
                        try:
                            await mongodb.save_ocpp_message(
                                org_name=session.org_name,
                                charger_id=backend_conn.id,
                                message_type="call",
                                action=action,
                                payload=payload,
                                direction="broker_to_charger",
                                message_id=parsed[1] if len(parsed) > 1 else None
                            )
                        except Exception as e:
                            logger.warning(f"Failed to save command {action} to MongoDB: {e}")
        except Exception as e:
            logger.debug(f"Could not parse message for MongoDB saving: {e}")
        
        try:
            for frame in frames:
                await session.send_to_charger(frame)
            logger.info("[%s] ← from backend", backend_conn.id)
        except Exception as exc:
            logger.warning(
                "Error sending backend message to charger %s: %s", backend_conn.id, exc
            )

    def _on_backend_connected(self, backend):
        logger.info("✅ Backend connected for charger %s (org=%s)", backend.id, backend.org)

    def _on_backend_disconnected(self, backend):
        logger.info("⚠️ Backend disconnected for charger %s (org=%s)", backend.id, backend.org)
    
    # ------------------------------------------------------------------
    # MongoDB initialization
    # ------------------------------------------------------------------
    async def _initialize_mongodb(self):
        """Initialize MongoDB service if configured."""
        import os
        
        # Check config first, then environment variables
        mongodb_config = self.config_data.get("mongodb", {})
        
        # Check if MongoDB is enabled (config or env var)
        enabled = mongodb_config.get("enabled", False)
        if not enabled:
            # Check environment variable
            enabled = os.environ.get("MONGODB_ENABLED", "").lower() in ("true", "1", "yes", "on")
        
        if not enabled:
            logger.warning(
                "MongoDB not configured or disabled: transaction ids will come from a "
                "non-durable in-memory counter and nothing will be persisted."
            )
            return
        
        try:
            from .mongodb_service import MongoDBService
            
            # Get connection string (env var takes precedence)
            connection_string = (
                os.environ.get("MONGODB_CONNECTION_STRING") or
                mongodb_config.get("connection_string") or
                "mongodb://localhost:27017"
            )
            
            # Get database name (env var takes precedence)
            database_name = (
                os.environ.get("MONGODB_DATABASE_NAME") or
                mongodb_config.get("database_name") or
                "ocpp_broker"
            )
            
            self.mongodb_service = MongoDBService(connection_string, database_name)
            await self.mongodb_service.connect()
            logger.info("✅ MongoDB service initialized and connected")
        except Exception as e:
            logger.error(f"❌ Failed to initialize MongoDB service: {e}", exc_info=True)
            self.mongodb_service = None
