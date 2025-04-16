import socket
import json
import threading
import uuid
import logging
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
    Override functions
    - on_connection_request
        parameters:
            metadata: dict
        returns:
            bool: True if the connection is accepted, False otherwise
    ---
    Example:
    ```python
    class MyClient(PyStrandsClient):
        def on_connection_request(self, metadata):
            logger.info(f"Connection request from {metadata}")
            return True
        
        def on_message(self, message, metadata):
            logger.info(f"Received message: {message} from {metadata}")

        def on_disconnect(self, metadata):
            logger.info(f"Disconnected from {metadata}")

        def on_error(self, error):
            logger.error(f"Error: {error}")
    ```
    """

    def __init__(self, host="localhost", port=8081):
        super().__init__()
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self.receive_thread = None

    def connect(self):
        """Connect to the TCP server."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.connected = True
            logger.info(f"Connected to {self.host}:{self.port}")
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
        except Exception as e:
            logger.error("Connection error: %s", e)

    def disconnect(self):
        """Cleanly disconnect."""
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
            logger.info("Disconnected.")

    def run_forever(self):
        """Simple loop to keep main thread alive if needed."""
        self.connect()
        try:
            while self.connected:
                pass
        except KeyboardInterrupt:
            pass
        self.disconnect()
    
    def broadcast_message(self, message: str):
        """Broadcast a message to all connections."""
        self.__send_json("broadcast", {"message": message})
    
    def send_room_message(self, room_id: str, message: str):
        """Send a message to a specific room."""
        self.__send_json("message_to_room", {"room_id": room_id, "message": message})
    
    def send_private_message(self, client_id: str, message: str):
        """Send a message to a specific connection using the `client_id`."""
        self.__send_json("message_to_connection", {"conn_id": client_id, "message": message})

    def __send_json(self, action: str, params: dict, request_id: str=None):
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
            self.disconnect()

    def _receive_loop(self):
        """Internal thread: read data, parse lines, dispatch to handlers."""
        buffer = ""
        while self.connected:
            try:
                data = self.sock.recv(1024)
                if not data:
                    logger.info("Server closed connection.")
                    self.connected = False
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._handle_incoming(line)
            except Exception as e:
                logger.error("Receive error: %s", e)
                self.disconnect()

    def _handle_incoming(self, raw_line: str):
        """Parse JSON, figure out action, call appropriate method."""
        try:
            msg = json.loads(raw_line)
            action = msg.get("action")
            params = msg.get("params", {})
            request_id = msg.get("request_id")
            # example protocol assumption
            if action == "connection_request": 
                try:
                    meta_data = {
                        "client_id": str(uuid.uuid4()),
                        "room_id": params.get("url", 'room'),
                        "metadata": {},
                    }
                    context = Context.from_json(meta_data)
                    connection_request_context = ConnectionRequestContext.from_json({
                        "headers": params.get("headers", {}),
                        "url": params.get("url", 'room'),
                        "remote_addr": params.get("remote_addr"),
                        "context": context,
                        "accepted": True,
                    })
                    resp = self.on_connection_request(connection_request_context)
                    self.__send_json("response", {
                        "accepted": resp,
                        **connection_request_context.context.to_json(),
                    }, request_id)
                except Exception as e:
                    logger.error("Error processing message: %s", e)
                    self.__send_json("response",
                                    {
                                        "accepted": False,
                                    },
                                    request_id)
            elif action == "new_connection":
                self.on_new_connection(Context.from_json(params))
            elif action == "new_message":
                self.on_message(params.get("message"), Context.from_json(params.get("metaData")))
            elif action == "disconnected":
                self.on_disconnect(Context.from_json(params))
            elif action == "error":
                self.on_error(params.get("error"), Context.from_json(params.get("metaData")))
            else:
                # you can handle other actions or fallback
                pass

        except json.JSONDecodeError:
            logger.error("Invalid JSON: %s", raw_line)
        except Exception as e:
            logger.error("Error processing message: %s", e)

    def on_connect(self, context: Context):
        """Override this method to handle the connection request."""
        logger.info(f"Connected to {context.to_json()}")

    def on_connection_request(self, context: ConnectionRequestContext):
        """Override this method to handle the connection request."""
        logger.info(f"Connection request from {context.context.client_id} in room {context.context.room_id}")
        return True
    
    def on_error(self, error):
        """Override this method to handle the error event."""
        logger.error(f"Error: {error}")
    
    def on_disconnect(self, context: Context):
        """Override this method to handle the disconnect event."""
        pass
    
    def on_message(self, message, context: Context):
        """Override this method to handle the message event."""
        logger.info(f"Received message: {message} from {context.client_id} in room {context.room_id}")
