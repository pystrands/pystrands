import socket
import json
import threading
import uuid
from collections import defaultdict, abc
from .base import PyStrandBase

class PyStrandClient(PyStrandBase):
    """
    Advanced usage: let users subclass this,
    override on_connect/on_message/on_disconnect, and call .run_forever().
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
            print(f"[PyStrandClient] Connected to {self.host}:{self.port}")
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
        except Exception as e:
            print("[PyStrandClient] Connection error:", e)

    def disconnect(self):
        """Cleanly disconnect."""
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
            print("[PyStrandClient] Disconnected.")

    def run_forever(self):
        """Simple loop to keep main thread alive if needed."""
        self.connect()
        try:
            while self.connected:
                pass
        except KeyboardInterrupt:
            pass
        self.disconnect()

    def send_json(self, action: str, params: dict, request_id: str=None):
        """Send a JSON message following your protocol format."""
        if not self.connected:
            print("[PyStrandClient] Not connected, cannot send.")
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
            print("[PyStrandClient] Send error:", e)
            self.disconnect()

    def _receive_loop(self):
        """Internal thread: read data, parse lines, dispatch to handlers."""
        buffer = ""
        while self.connected:
            try:
                data = self.sock.recv(1024)
                if not data:
                    print("[PyStrandClient] Server closed connection.")
                    self.connected = False
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._handle_incoming(line)
            except Exception as e:
                print("[PyStrandClient] Receive error:", e)
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
                resp = self.on_connect(params, params.get("conn_id"))
                if isinstance(resp, bool):
                    self.send_json("response",
                                   {
                                       "accepted": resp,
                                       "roomID": params.get("url", 'room'),
                                       "clientID": str(uuid.uuid4()),
                                    },
                                   request_id
                                   )
                elif isinstance(resp, dict):
                    if 'roomID' not in resp:
                        resp['roomID'] = params.get("url", 'room')
                    if 'clientID' not in resp:
                        resp['clientID'] = str(uuid.uuid4())
                    self.send_json("response",
                                   {
                                       "request_id": request_id,
                                       "accepted": True,
                                       **resp,
                                   },
                                   request_id)
                elif isinstance(resp, str):
                    self.send_json("response",
                                   {
                                       "request_id": request_id,
                                       "accepted": True,
                                       "roomID": resp,
                                       "clientID": str(uuid.uuid4()),
                                   },
                                   request_id)
                else:
                    self.send_json("response",
                                   {
                                       "accepted": False,
                                   },
                                   request_id)
            elif action == "new_message":
                self.on_message(params.get("message"), params.get("metadata"), params.get("conn_id"))
            elif action == "disconnected":
                self.on_disconnect(params, params.get("conn_id"))
            else:
                # you can handle other actions or fallback
                pass

        except json.JSONDecodeError:
            print("[PyStrandClient] Invalid JSON:", raw_line)
        except Exception as e:
            print("[PyStrandClient] Error processing message:", e)


class PyStrand(PyStrandClient):
    """
    Simpler usage: also supports decorators like @client.on("connect"),
    internally calls base methods so you can do both if you want.
    """

    def __init__(self, host="localhost", port=8081):
        super().__init__(host, port)
        # event_handlers dict, each key -> list of callback functions
        self.event_handlers = defaultdict(list)

    def on(self, event_name):
        """Decorator usage: @client.on('connect') or 'disconnect' or 'message' etc."""
        def wrapper(func):
            self.event_handlers[event_name].append(func)
            return func
        return wrapper

    # override base methods to dispatch to event handlers
    def on_connect(self, metadata, conn):
        super().on_connect(metadata, conn)  # if user subclassed
        for func in self.event_handlers["connect"]:
            func(metadata, conn)

    def on_disconnect(self, metadata, conn):
        super().on_disconnect(metadata, conn)
        for func in self.event_handlers["disconnect"]:
            func(metadata, conn)

    def on_message(self, message, metadata, conn):
        super().on_message(message, metadata, conn)
        for func in self.event_handlers["message"]:
            func(message, metadata, conn)
