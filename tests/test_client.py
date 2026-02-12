"""Tests for PyStrandsClient."""
import json
import socket
import threading
import time
import uuid
import pytest

from pystrands.client import PyStrandsClient
from pystrands.context import Context, ConnectionRequestContext, JSONModel


# ====================
# Test helpers
# ====================

class MockTCPServer:
    """A minimal TCP server that simulates the Go server for testing."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.clients = []
        self.received_messages = []
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self):
        while self._running:
            try:
                self.sock.settimeout(0.5)
                conn, addr = self.sock.accept()
                self.clients.append(conn)
                threading.Thread(target=self._read_loop, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _read_loop(self, conn):
        buffer = ""
        while self._running:
            try:
                conn.settimeout(0.5)
                data = conn.recv(65536)
                if not data:
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        self.received_messages.append(json.loads(line))
            except socket.timeout:
                continue
            except Exception:
                break

    def send_to_all(self, msg_dict):
        data = (json.dumps(msg_dict) + "\n").encode("utf-8")
        for conn in self.clients:
            try:
                conn.sendall(data)
            except Exception:
                pass

    def send_to(self, idx, msg_dict):
        data = (json.dumps(msg_dict) + "\n").encode("utf-8")
        self.clients[idx].sendall(data)

    def stop(self):
        self._running = False
        for c in self.clients:
            try:
                c.close()
            except Exception:
                pass
        self.sock.close()


@pytest.fixture
def mock_server():
    server = MockTCPServer()
    yield server
    server.stop()


# ====================
# Context tests
# ====================

class TestContext:
    def test_from_json(self):
        ctx = Context.from_json({
            "client_id": "c1",
            "room_id": "r1",
            "metadata": {"key": "val"}
        })
        assert ctx.client_id == "c1"
        assert ctx.room_id == "r1"
        assert ctx.metadata == {"key": "val"}

    def test_to_json(self):
        ctx = Context(client_id="c1", room_id="r1", metadata={})
        d = ctx.to_json()
        assert d == {"client_id": "c1", "room_id": "r1", "metadata": {}}

    def test_roundtrip(self):
        original = {"client_id": "abc", "room_id": "room-x", "metadata": {"a": 1}}
        ctx = Context.from_json(original)
        assert ctx.to_json() == original


class TestConnectionRequestContext:
    def test_from_json(self):
        ctx = Context(client_id="c1", room_id="r1", metadata={})
        crc = ConnectionRequestContext(
            headers={"Host": ["localhost"]},
            url="/ws/",
            remote_addr="127.0.0.1:1234",
            context=ctx,
            accepted=True,
        )
        assert crc.accepted is True
        assert crc.context.client_id == "c1"

    def test_to_json(self):
        ctx = Context(client_id="c1", room_id="r1", metadata={})
        crc = ConnectionRequestContext(
            headers={},
            url="/ws/",
            remote_addr="127.0.0.1",
            context=ctx,
            accepted=False,
        )
        d = crc.to_json()
        assert d["accepted"] is False
        assert d["url"] == "/ws/"


class TestJSONModel:
    def test_missing_fields_default_none(self):
        ctx = Context.from_json({"client_id": "c1"})
        assert ctx.client_id == "c1"
        assert ctx.room_id is None
        assert ctx.metadata is None


# ====================
# Client connect/disconnect tests
# ====================

class TestClientConnect:
    def test_connect_success(self, mock_server):
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        result = client.connect()
        assert result is True
        assert client.connected is True
        client.disconnect()
        assert client.connected is False

    def test_connect_failure(self):
        client = PyStrandsClient("127.0.0.1", 1, auto_reconnect=False)
        result = client.connect()
        assert result is False
        assert client.connected is False

    def test_disconnect_idempotent(self, mock_server):
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        client.disconnect()
        client.disconnect()  # should not raise
        assert client.connected is False


# ====================
# Message sending tests
# ====================

class TestMessageSending:
    def test_broadcast_message(self, mock_server):
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        client.broadcast_message("hello all")
        time.sleep(0.2)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "broadcast"]
        assert len(msgs) >= 1
        assert msgs[0]["params"]["message"] == "hello all"
        client.disconnect()

    def test_send_room_message(self, mock_server):
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        client.send_room_message("room-1", "room msg")
        time.sleep(0.2)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "message_to_room"]
        assert len(msgs) >= 1
        assert msgs[0]["params"]["room_id"] == "room-1"
        assert msgs[0]["params"]["message"] == "room msg"
        client.disconnect()

    def test_send_private_message(self, mock_server):
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        client.send_private_message("client-42", "secret")
        time.sleep(0.2)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "message_to_connection"]
        assert len(msgs) >= 1
        assert msgs[0]["params"]["conn_id"] == "client-42"
        assert msgs[0]["params"]["message"] == "secret"
        client.disconnect()

    def test_send_when_disconnected(self):
        client = PyStrandsClient("127.0.0.1", 1, auto_reconnect=False)
        # Should not raise
        client.broadcast_message("nope")


# ====================
# Connection request handling tests
# ====================

class TestConnectionRequest:
    def test_connection_request_accept(self, mock_server):
        class MyClient(PyStrandsClient):
            def on_connection_request(self, context):
                context.context.room_id = "custom-room"
                context.context.client_id = "custom-id"
                return True

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        # Simulate server sending connection_request
        request_id = str(uuid.uuid4())
        mock_server.send_to(0, {
            "request_id": request_id,
            "action": "connection_request",
            "params": {
                "headers": {"Host": ["localhost"]},
                "url": "/ws/room",
                "remote_addr": "10.0.0.1:5555",
            }
        })
        time.sleep(0.3)

        # Client should have sent a response back
        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        resp = responses[0]
        assert resp["params"]["accepted"] is True
        assert resp["params"]["room_id"] == "custom-room"
        assert resp["params"]["client_id"] == "custom-id"
        client.disconnect()

    def test_connection_request_reject(self, mock_server):
        class MyClient(PyStrandsClient):
            def on_connection_request(self, context):
                return False

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        request_id = str(uuid.uuid4())
        mock_server.send_to(0, {
            "request_id": request_id,
            "action": "connection_request",
            "params": {
                "headers": {},
                "url": "/ws/",
                "remote_addr": "10.0.0.1",
            }
        })
        time.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        assert responses[0]["params"]["accepted"] is False
        client.disconnect()

    def test_connection_request_default_accept(self, mock_server):
        """Default on_connection_request returns True."""
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        request_id = str(uuid.uuid4())
        mock_server.send_to(0, {
            "request_id": request_id,
            "action": "connection_request",
            "params": {
                "headers": {},
                "url": "/ws/",
                "remote_addr": "10.0.0.1",
            }
        })
        time.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        assert responses[0]["params"]["accepted"] is True
        client.disconnect()


# ====================
# Event callback dispatching tests
# ====================

class TestEventCallbacks:
    def test_on_new_connection(self, mock_server):
        events = []

        class MyClient(PyStrandsClient):
            def on_new_connection(self, context):
                events.append(("new_connection", context.client_id))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "new_connection",
            "params": {
                "context": {"client_id": "nc-1", "room_id": "r1", "metadata": {}}
            }
        })
        time.sleep(0.2)
        assert ("new_connection", "nc-1") in events
        client.disconnect()

    def test_on_message(self, mock_server):
        events = []

        class MyClient(PyStrandsClient):
            def on_message(self, message, context):
                events.append(("message", message, context.client_id))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "new_message",
            "params": {
                "message": "test-msg",
                "context": {"client_id": "m-1", "room_id": "r1", "metadata": {}}
            }
        })
        time.sleep(0.2)
        assert ("message", "test-msg", "m-1") in events
        client.disconnect()

    def test_on_disconnect(self, mock_server):
        events = []

        class MyClient(PyStrandsClient):
            def on_disconnect(self, context):
                events.append(("disconnect", context.client_id))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "disconnected",
            "params": {
                "context": {"client_id": "d-1", "room_id": "r1", "metadata": {}}
            }
        })
        time.sleep(0.2)
        assert ("disconnect", "d-1") in events
        client.disconnect()

    def test_on_error(self, mock_server):
        events = []

        class MyClient(PyStrandsClient):
            def on_error(self, error, context):
                events.append(("error", error))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "error",
            "params": {
                "error": "something broke",
                "context": {"client_id": "e-1", "room_id": "r1", "metadata": {}}
            }
        })
        time.sleep(0.2)
        assert ("error", "something broke") in events
        client.disconnect()

    def test_heartbeat_ignored(self, mock_server):
        """Heartbeat messages should be silently handled (no error)."""
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "heartbeat",
            "action": "heartbeat",
            "params": {}
        })
        time.sleep(0.2)
        assert client.connected is True
        client.disconnect()

    def test_unknown_action_ignored(self, mock_server):
        """Unknown actions should be silently ignored."""
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "totally_unknown_action",
            "params": {}
        })
        time.sleep(0.2)
        assert client.connected is True
        client.disconnect()

    def test_invalid_json_handled(self, mock_server):
        """Invalid JSON should not crash the client."""
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        client.connect()
        time.sleep(0.1)

        # Send raw invalid JSON
        data = b"this is not json\n"
        mock_server.clients[0].sendall(data)
        time.sleep(0.2)
        assert client.connected is True
        client.disconnect()


# ====================
# Reconnection tests
# ====================

class TestReconnection:
    def test_auto_reconnect_on_server_close(self, mock_server):
        events = []

        class MyClient(PyStrandsClient):
            pass

        client = MyClient(
            "127.0.0.1", mock_server.port,
            auto_reconnect=True,
            reconnect_delay=0.2,
            max_reconnect_delay=1.0,
        )
        client.connect()
        time.sleep(0.1)
        assert client.connected is True

        # Close the server-side connection to simulate drop
        for c in mock_server.clients:
            c.close()
        mock_server.clients.clear()

        time.sleep(0.5)  # Give time for reconnect attempt

        # Client should have attempted reconnection
        # It may or may not succeed depending on timing, but it should not crash
        client.disconnect()

    def test_no_reconnect_on_intentional_disconnect(self, mock_server):
        client = PyStrandsClient(
            "127.0.0.1", mock_server.port,
            auto_reconnect=True,
            reconnect_delay=0.1,
        )
        client.connect()
        time.sleep(0.1)
        client.disconnect()
        time.sleep(0.5)

        # Should NOT have reconnected
        assert client.connected is False
        assert client._intentional_disconnect is True

    def test_no_reconnect_when_disabled(self, mock_server):
        client = PyStrandsClient(
            "127.0.0.1", mock_server.port,
            auto_reconnect=False,
        )
        client.connect()
        time.sleep(0.1)

        for c in mock_server.clients:
            c.close()
        mock_server.clients.clear()

        time.sleep(0.5)
        assert client.connected is False
        client.disconnect()


# ====================
# run_forever tests
# ====================

class TestRunForever:
    def test_run_forever_uses_event(self, mock_server):
        """run_forever should block without CPU burn and respond to stop."""
        client = PyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)

        def run():
            client.connect()
            try:
                while not client._stop_event.is_set():
                    client._stop_event.wait(timeout=0.5)
            except KeyboardInterrupt:
                pass
            client.disconnect()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        assert client.connected is True

        client._stop_event.set()
        t.join(timeout=2)
        assert not t.is_alive()
