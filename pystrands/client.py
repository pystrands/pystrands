import socket
import json
import threading
import uuid
import logging
import time
from pystrands.context import ConnectionRequestContext, Context

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Create a formatter that includes timestamp and module name
formatter = logging.Formatter('%(asctime)s - [PyStrandsClient] - %(levelname)s - %(message)s')

# Create a console handler and set the formatter
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class PyStrandsClient:
    """
    PyStrandsClient is a client for the PyStrands server.
    """

    def __init__(self, host="localhost", port=8081, auto_reconnect=True,
                 reconnect_delay=1.0, max_reconnect_delay=30.0,
                 reconnect_backoff=2.0):
        super().__init__()
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self.receive_thread = None
        self._stop_event = threading.Event()

        # Reconnection settings
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.reconnect_backoff = reconnect_backoff
        self._intentional_disconnect = False

    def connect(self) -> bool:
        """Connect to the TCP server."""
        try:
            self._intentional_disconnect = False
            self._stop_event.clear()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.connected = True
            logger.info("Connected to %s:%s", self.host, self.port)
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            return True
        except Exception as e:
            logger.error("Connection error: %s", e)
            self.connected = False
            return False

    def disconnect(self, timeout=5.0):
        """Cleanly disconnect. Waits for in-flight handlers to finish.

        Args:
            timeout: Max seconds to wait for in-flight handlers before force-closing.
        """
        self._intentional_disconnect = True
        self.connected = False
        self._stop_event.set()
        # Close socket first to unblock recv() in receive thread
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        # Wait for receive thread to finish (lets in-flight handler complete)
        if self.receive_thread and self.receive_thread.is_alive():
            self.receive_thread.join(timeout=timeout)
            if self.receive_thread.is_alive():
                logger.warning("Graceful shutdown timed out after %.1fs.", timeout)
        logger.info("Disconnected.")

    def _reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        if not self.auto_reconnect or self._intentional_disconnect:
            return

        delay = self.reconnect_delay
        while not self._stop_event.is_set() and not self._intentional_disconnect:
            logger.info("Attempting reconnection in %.1f seconds...", delay)
            self._stop_event.wait(delay)
            if self._stop_event.is_set() or self._intentional_disconnect:
                break
            if self.connect():
                logger.info("Reconnected successfully.")
                return
            delay = min(delay * self.reconnect_backoff, self.max_reconnect_delay)

    def run_forever(self):
        """Block the main thread until disconnect. Uses threading.Event for CPU-efficiency."""
        self.connect()
        try:
            # Block efficiently instead of busy-waiting
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass
        self.disconnect()

    def broadcast_message(self, message):
        """Broadcast a message to all connections."""
        self._send_json("broadcast", {"message": message})

    def send_room_message(self, room_id, message):
        """Send a message to a specific room."""
        self._send_json("message_to_room", {"room_id": room_id, "message": message})

    def send_private_message(self, client_id, message):
        """Send a message to a specific connection using the `client_id`."""
        self._send_json("message_to_connection", {"conn_id": client_id, "message": message})

    def _send_json(self, action, params, request_id=None):
        """Send a JSON message following your protocol format."""
        if not self.connected:
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
            self.sock.sendall(serialized.encode("utf-8"))
        except Exception as e:
            logger.error("Send error: %s", e)
            self._handle_connection_lost()

    def _handle_connection_lost(self):
        """Handle a lost connection — close socket and optionally reconnect."""
        was_connected = self.connected
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        if was_connected and self.auto_reconnect and not self._intentional_disconnect:
            threading.Thread(target=self._reconnect, daemon=True).start()

    def _receive_loop(self):
        """Internal thread: read data, parse lines, dispatch to handlers."""
        buffer = ""
        while self.connected:
            try:
                data = self.sock.recv(65536)
                if not data:
                    logger.info("Server closed connection.")
                    self._handle_connection_lost()
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._handle_incoming(line)
            except Exception as e:
                if self.connected:
                    logger.error("Receive error: %s", e)
                    self._handle_connection_lost()
                break

    def _handle_incoming(self, raw_line):
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
                    result = self.on_connection_request(connection_request_context)
                    # Normalize return value: if user returns True/False use it,
                    # otherwise use connection_request_context.accepted
                    if isinstance(result, bool):
                        accepted = result
                    else:
                        accepted = connection_request_context.accepted

                    self._send_json("response", {
                        "accepted": accepted,
                        **connection_request_context.context.to_json(),
                    }, request_id)
                except Exception as e:
                    logger.error("Error processing connection request: %s", e)
                    self._send_json("response",
                                    {"accepted": False},
                                    request_id)
            elif action == "new_connection":
                self.on_new_connection(Context.from_json(params.get("context")))
            elif action == "new_message":
                self.on_message(params.get("message"), Context.from_json(params.get("context")))
            elif action == "disconnected":
                self.on_disconnect(Context.from_json(params.get("context")))
            elif action == "error":
                self.on_error(params.get("error"), Context.from_json(params.get("context")))
            elif action == "heartbeat":
                # Respond to server heartbeat — just ignore / no-op
                pass
            else:
                # Unknown action — ignore
                pass

        except json.JSONDecodeError:
            logger.error("Invalid JSON: %s", raw_line)
        except Exception as e:
            logger.error("Error processing message: %s", e)

    def on_new_connection(self, context):
        """Override this method to handle the new connection event.
        Called when a WebSocket client successfully connects to the Go server."""
        logger.info("New connection: %s", context.to_json())

    def on_connection_request(self, context):
        """Override this method to handle the connection request.

        Args:
            context: ConnectionRequestContext with headers, url, remote_addr, context, accepted.

        Returns:
            bool: True to accept, False to reject. Or modify context.accepted directly.
                  If no explicit bool is returned, context.accepted is used (defaults to True).
        """
        logger.info("Connection request from %s in room %s",
                     context.context.client_id, context.context.room_id)
        return True

    def on_error(self, error, context):
        """Override this method to handle the error event."""
        logger.error("Error: %s", error)

    def on_disconnect(self, context):
        """Override this method to handle the disconnect event."""
        logger.info("Disconnected: %s in room %s", context.client_id, context.room_id)

    def on_message(self, message, context):
        """Override this method to handle the message event."""
        logger.info("Message: %s from %s in room %s", message, context.client_id, context.room_id)
