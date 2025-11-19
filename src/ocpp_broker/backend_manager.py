import asyncio
import json
import logging
import websockets

logger = logging.getLogger("ocpp_broker.backend_manager")


class BackendConnection:
    """
    Represents a 1-to-1 persistent WebSocket link between the broker (acting as a charger)
    and the upstream OCPP backend. Each charger has its own BackendConnection.
    """

    def __init__(self, broker, charger_id: str, url: str, org: str = "default", is_leader: bool = False, subprotocol: str = "ocpp1.6"):
        self.broker = broker
        self.id = charger_id            # Charger ID used for backend identification
        self.url = url.rstrip("/")      # Base URL (from config)
        self.org = org
        self.is_leader = is_leader     # Leader/follower status
        self.subprotocol = subprotocol  # OCPP subprotocol (e.g., "ocpp1.6", "ocpp2.0.1")
        self.websocket = None
        self._connect_task = None
        self._running = False
        self.connected_event = asyncio.Event()  # signals when backend connection is ready

    async def connect(self):
        """Start backend connection loop and wait until it's connected."""
        if self._running:
            return
        self._running = True
        self._connect_task = asyncio.create_task(self._run_connect_loop())
        # Wait for connection signal before continuing
        await self.connected_event.wait()

    def _is_websocket_closed(self):
        """Check if websocket is closed, handling different websocket types."""
        if not self.websocket:
            return True
        try:
            # Try the 'closed' attribute first (for some websocket implementations)
            if hasattr(self.websocket, 'closed'):
                return self.websocket.closed
            # For websockets library, check close_code (None means open)
            if hasattr(self.websocket, 'close_code'):
                return self.websocket.close_code is not None
            # Default: assume open if we can't determine
            return False
        except AttributeError:
            # If attribute doesn't exist, assume open
            return False

    async def close(self):
        """Close active backend connection gracefully."""
        logger.info(f"Closing backend connection for charger {self.id}")
        self._running = False  # Stop the connection loop
        self.connected_event.clear()  # Signal that connection is no longer ready
        
        # Cancel the connection task if it's running
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Error cancelling connect task for {self.id}: {e}")
        
        # Close the websocket if it's open
        try:
            if self.websocket and not self._is_websocket_closed():
                await self.websocket.close()
        except Exception as e:
            logger.debug(f"Error closing websocket for {self.id}: {e}")
        
        self.websocket = None
        logger.debug(f"Backend connection closed for charger {self.id}")

    def _is_charger_still_connected(self) -> bool:
        """Check if the charger is still connected to the broker before connecting to backend."""
        session = self.broker.sessions.get(self.id)
        if not session:
            return False
        # Check if charger websocket is still connected
        if not session.websocket:
            return False
        try:
            client_state = getattr(session.websocket, "client_state", None)
            if client_state:
                state_name = getattr(client_state, "name", None)
                return state_name != "DISCONNECTED"
            return True
        except Exception:
            return False

    async def _run_connect_loop(self):
        """Continuously try to connect to backend until stopped."""
        backoff = 1
        while self._running:
            # Verify charger is still connected before attempting backend connection
            if not self._is_charger_still_connected():
                logger.warning(f"⚠️ Charger {self.id} disconnected, stopping backend connection attempt")
                break
            
            try:
                target_url = f"{self.url}/{self.id}"  # ✅ append charger_id
                logger.info(f"Connecting to backend for charger {self.id} ({self.org}) -> {target_url}")

                async with websockets.connect(
                    target_url,
                    subprotocols=[self.subprotocol],  # Use configured subprotocol
                    ping_interval=20,  # Send ping every 20 seconds (OCPP compatible)
                    ping_timeout=20,  # Wait 20 seconds for pong response
                    close_timeout=5,  # Wait 5 seconds for close handshake
                    max_size=2**20,  # 1MB max message size (OCPP recommendation)
                    max_queue=32  # Max queued messages
                ) as ws:
                    self.websocket = ws
                    
                    # Verify configured subprotocol was negotiated
                    negotiated_subprotocol = getattr(ws, 'subprotocol', None)
                    if negotiated_subprotocol != self.subprotocol:
                        logger.error(
                            f"❌ OCPP subprotocol not negotiated for {self.id}! "
                            f"Expected '{self.subprotocol}', got '{negotiated_subprotocol}'. "
                            f"Connection may not be OCPP compliant."
                        )
                        # Continue anyway, but log the warning
                    else:
                        logger.info(f"✅ OCPP subprotocol '{self.subprotocol}' verified for charger {self.id}")
                    
                    # Verify charger is still connected before marking backend as ready
                    if not self._is_charger_still_connected():
                        logger.warning(f"⚠️ Charger {self.id} disconnected during backend connection, closing backend")
                        break
                    
                    self.connected_event.set()  # signal broker that backend connection is ready
                    logger.info(f"✅ Connected to backend for charger {self.id} ({self.org}) via {self.subprotocol}")
                    self.broker._on_backend_connected(self)
                    
                    # Run reader loop - it will exit if charger disconnects
                    await self._reader_loop()
                    
                    # If we exit the reader loop, charger likely disconnected
                    if not self._is_charger_still_connected():
                        logger.info(f"Charger {self.id} disconnected, backend connection terminated")
                        break

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Check if charger disconnected during connection attempt
                if not self._is_charger_still_connected():
                    logger.info(f"Charger {self.id} disconnected during backend connection attempt, stopping")
                    break
                logger.warning(f"⚠️ Backend connection error for {self.id} ({self.org}): {e}")
                self.connected_event.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                try:
                    self.broker._on_backend_disconnected(self)
                except Exception:
                    pass
                self.websocket = None
                await asyncio.sleep(1)

    async def _reader_loop(self):
        """Receive messages from backend and forward to broker."""
        assert self.websocket is not None
        try:
            async for msg in self.websocket:
                # Check if charger is still connected before processing messages
                if not self._is_charger_still_connected():
                    logger.info(f"⚠️ Charger {self.id} disconnected, stopping backend message reader")
                    break
                await self._handle_message(msg)
        except Exception as e:
            if not self._is_charger_still_connected():
                logger.info(f"Charger {self.id} disconnected, backend reader loop stopped")
            else:
                logger.warning(f"Backend reader loop error for {self.id}: {e}")

    async def _handle_message(self, msg: str):
        """Handle messages coming from backend."""
        try:
            data = json.loads(msg)
        except Exception:
            data = None

        # Forward every backend message to broker (to send to charger)
        logger.info(f"[{self.id}] ← Message from backend: {msg[:200]}")  # log truncated message
        await self.broker.forward_backend_message(self, msg)

    async def send(self, message: str):
        """Send OCPP message to backend if connected."""
        # Wait for connection to be ready before sending
        if not self.connected_event.is_set():
            logger.warning(f"⚠️ Backend connection not ready for {self.id}, waiting...")
            try:
                # Wait up to 5 seconds for connection
                await asyncio.wait_for(self.connected_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"❌ Timeout waiting for backend connection for {self.id}")
                return
        
        if self.websocket and not self._is_websocket_closed():
            try:
                await self.websocket.send(message)
                logger.debug(f"[{self.id}] → backend: {message[:200]}")
            except Exception as e:
                logger.warning(f"⚠️ Error sending to backend for {self.id}: {e}")
                # Clear the connected event if send fails, so we reconnect
                self.connected_event.clear()
        else:
            logger.warning(f"⚠️ Cannot send to backend for {self.id}: not connected or closed.")
