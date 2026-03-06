"""Async client for PyStrands server using asyncio."""
import asyncio
import json
import uuid
import logging
from pystrands.context import ConnectionRequestContext, Context

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

formatter = logging.Formatter('%(asctime)s - [PyStrandsAsyncClient] - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class AsyncPyStrandsClient:
    """
    Async client for the PyStrands server.

    Usage:
        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                print(f"Got: {message}")

        client = MyClient("localhost", 8081)
        await client.connect()
        await client.broadcast_message("hello")
        await client.run_forever()
    """

    def __init__(self, host="localhost", port=8081, auto_reconnect=True,
                 reconnect_delay=1.0, max_reconnect_delay=30.0,
                 reconnect_backoff=2.0):
        self.host = host
        self.port = port
        self.connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._receive_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        # Reconnection settings
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.reconnect_backoff = reconnect_backoff
        self._intentional_disconnect = False

        # Write lock to prevent concurrent writes
        self._write_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect to the TCP server."""
        try:
            self._intentional_disconnect = False
            self._stop_event.clear()
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            logger.info("Connected to %s:%s", self.host, self.port)
            self._receive_task = asyncio.create_task(self._receive_loop())
            return True
        except Exception as e:
            logger.error("Connection error: %s", e)
            self.connected = False
            return False

    async def disconnect(self):
        """Cleanly disconnect."""
        self._intentional_disconnect = True
        self.connected = False
        self._stop_event.set()
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        logger.info("Disconnected.")

    async def _reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        if not self.auto_reconnect or self._intentional_disconnect:
            return

        delay = self.reconnect_delay
        while not self._stop_event.is_set() and not self._intentional_disconnect:
            logger.info("Attempting reconnection in %.1f seconds...", delay)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass
            if self._intentional_disconnect:
                break
            if await self.connect():
                logger.info("Reconnected successfully.")
                return
            delay = min(delay * self.reconnect_backoff, self.max_reconnect_delay)

    async def run_forever(self):
        """Block until disconnect. Use with asyncio.run() or await."""
        if not self.connected:
            await self.connect()
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        await self.disconnect()

    async def broadcast_message(self, message):
        """Broadcast a message to all connections."""
        await self._send_json("broadcast", {"message": message})

    async def send_room_message(self, room_id, message):
        """Send a message to a specific room."""
        await self._send_json("message_to_room", {"room_id": room_id, "message": message})

    async def send_private_message(self, client_id, message):
        """Send a message to a specific connection using the `client_id`."""
        await self._send_json("message_to_connection", {"conn_id": client_id, "message": message})

    async def _send_json(self, action, params, request_id=None):
        """Send a JSON message following the protocol format."""
        if not self.connected or not self._writer:
            logger.info("Not connected, cannot send.")
            return

        if not request_id:
            request_id = str(uuid.uuid4())

        message = {
            "action": action,
            "request_id": request_id,
            "params": params
        }

        try:
            serialized = json.dumps(message) + "\n"
            async with self._write_lock:
                self._writer.write(serialized.encode("utf-8"))
                await self._writer.drain()
        except Exception as e:
            logger.error("Send error: %s", e)
            await self._handle_connection_lost()

    async def _handle_connection_lost(self):
        """Handle a lost connection — close and optionally reconnect."""
        was_connected = self.connected
        self.connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        if was_connected and self.auto_reconnect and not self._intentional_disconnect:
            asyncio.create_task(self._reconnect())

    async def _receive_loop(self):
        """Read data, parse lines, dispatch to handlers."""
        buffer = ""
        while self.connected and self._reader:
            try:
                data = await self._reader.read(65536)
                if not data:
                    logger.info("Server closed connection.")
                    await self._handle_connection_lost()
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        await self._handle_incoming(line)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.connected:
                    logger.error("Receive error: %s", e)
                    await self._handle_connection_lost()
                break

    async def _handle_incoming(self, raw_line):
        """Parse JSON, figure out action, call appropriate method."""
        try:
            msg = json.loads(raw_line)
            action = msg.get("action")
            params = msg.get("params", {})
            request_id = msg.get("request_id")
            logger.info("Received message: %s", raw_line)

            if action == "connection_request":
                try:
                    meta_data = {
                        "client_id": str(uuid.uuid4()),
                        "room_id": params.get("url", "room"),
                        "metadata": {},
                    }
                    ctx = Context.from_json(meta_data)
                    connection_request_context = ConnectionRequestContext.from_json({
                        "headers": params.get("headers", {}),
                        "url": params.get("url", "room"),
                        "remote_addr": params.get("remote_addr"),
                        "context": ctx,
                        "accepted": True,
                    })
                    result = await self.on_connection_request(connection_request_context)
                    if isinstance(result, bool):
                        accepted = result
                    else:
                        accepted = connection_request_context.accepted

                    await self._send_json("response", {
                        "accepted": accepted,
                        **connection_request_context.context.to_json(),
                    }, request_id)
                except Exception as e:
                    logger.error("Error processing connection request: %s", e)
                    await self._send_json("response",
                                          {"accepted": False},
                                          request_id)
            elif action == "new_connection":
                await self.on_new_connection(Context.from_json(params.get("context")))
            elif action == "new_message":
                await self.on_message(params.get("message"), Context.from_json(params.get("context")))
            elif action == "disconnected":
                await self.on_disconnect(Context.from_json(params.get("context")))
            elif action == "error":
                await self.on_error(params.get("error"), Context.from_json(params.get("context")))
            elif action == "heartbeat":
                pass
            else:
                pass

        except json.JSONDecodeError:
            logger.error("Invalid JSON: %s", raw_line)
        except Exception as e:
            logger.error("Error processing message: %s", e)

    async def on_new_connection(self, context):
        """Override to handle new WebSocket client connections."""
        logger.info("New connection: %s", context.to_json())

    async def on_connection_request(self, context):
        """Override to handle connection requests.

        Args:
            context: ConnectionRequestContext with headers, url, remote_addr, context, accepted.

        Returns:
            bool: True to accept, False to reject. Or modify context.accepted directly.
        """
        logger.info("Connection request from %s in room %s",
                     context.context.client_id, context.context.room_id)
        return True

    async def on_error(self, error, context):
        """Override to handle errors."""
        logger.error("Error: %s", error)

    async def on_disconnect(self, context):
        """Override to handle WebSocket client disconnections."""
        logger.info("Disconnected: %s in room %s", context.client_id, context.room_id)

    async def on_message(self, message, context):
        """Override to handle incoming messages."""
        logger.info("Message: %s from %s in room %s", message, context.client_id, context.room_id)
