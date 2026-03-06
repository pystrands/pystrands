"""Tests for AsyncPyStrandsClient."""
import asyncio
import json
import socket
import threading
import uuid
import pytest
import pytest_asyncio

from pystrands.async_client import AsyncPyStrandsClient
from pystrands.context import Context, ConnectionRequestContext


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
# Connect/disconnect tests
# ====================

class TestAsyncClientConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        result = await client.connect()
        assert result is True
        assert client.connected is True
        await client.disconnect()
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        client = AsyncPyStrandsClient("127.0.0.1", 1, auto_reconnect=False)
        result = await client.connect()
        assert result is False
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await client.disconnect()
        await client.disconnect()  # should not raise
        assert client.connected is False


# ====================
# Message sending tests
# ====================

class TestAsyncMessageSending:
    @pytest.mark.asyncio
    async def test_broadcast_message(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        await client.broadcast_message("hello async")
        await asyncio.sleep(0.2)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "broadcast"]
        assert len(msgs) >= 1
        assert msgs[0]["params"]["message"] == "hello async"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_send_room_message(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        await client.send_room_message("room-async", "async room msg")
        await asyncio.sleep(0.2)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "message_to_room"]
        assert len(msgs) >= 1
        assert msgs[0]["params"]["room_id"] == "room-async"
        assert msgs[0]["params"]["message"] == "async room msg"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_send_private_message(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        await client.send_private_message("client-async", "secret async")
        await asyncio.sleep(0.2)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "message_to_connection"]
        assert len(msgs) >= 1
        assert msgs[0]["params"]["conn_id"] == "client-async"
        assert msgs[0]["params"]["message"] == "secret async"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_send_when_disconnected(self):
        client = AsyncPyStrandsClient("127.0.0.1", 1, auto_reconnect=False)
        # Should not raise
        await client.broadcast_message("nope")


# ====================
# Connection request handling tests
# ====================

class TestAsyncConnectionRequest:
    @pytest.mark.asyncio
    async def test_connection_request_accept(self, mock_server):
        class MyClient(AsyncPyStrandsClient):
            async def on_connection_request(self, context):
                context.context.room_id = "custom-async-room"
                context.context.client_id = "custom-async-id"
                return True

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

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
        await asyncio.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        resp = responses[0]
        assert resp["params"]["accepted"] is True
        assert resp["params"]["room_id"] == "custom-async-room"
        assert resp["params"]["client_id"] == "custom-async-id"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connection_request_reject(self, mock_server):
        class MyClient(AsyncPyStrandsClient):
            async def on_connection_request(self, context):
                return False

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": str(uuid.uuid4()),
            "action": "connection_request",
            "params": {"headers": {}, "url": "/ws/", "remote_addr": "10.0.0.1"}
        })
        await asyncio.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        assert responses[0]["params"]["accepted"] is False
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connection_request_default_accept(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": str(uuid.uuid4()),
            "action": "connection_request",
            "params": {"headers": {}, "url": "/ws/", "remote_addr": "10.0.0.1"}
        })
        await asyncio.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        assert responses[0]["params"]["accepted"] is True
        await client.disconnect()


# ====================
# Event callback tests
# ====================

class TestAsyncEventCallbacks:
    @pytest.mark.asyncio
    async def test_on_new_connection(self, mock_server):
        events = []

        class MyClient(AsyncPyStrandsClient):
            async def on_new_connection(self, context):
                events.append(("new_connection", context.client_id))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "new_connection",
            "params": {"context": {"client_id": "nc-async", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.2)
        assert ("new_connection", "nc-async") in events
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_on_message(self, mock_server):
        events = []

        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                events.append(("message", message, context.client_id))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "new_message",
            "params": {
                "message": "async-test-msg",
                "context": {"client_id": "m-async", "room_id": "r1", "metadata": {}}
            }
        })
        await asyncio.sleep(0.2)
        assert ("message", "async-test-msg", "m-async") in events
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_on_disconnect(self, mock_server):
        events = []

        class MyClient(AsyncPyStrandsClient):
            async def on_disconnect(self, context):
                events.append(("disconnect", context.client_id))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "disconnected",
            "params": {"context": {"client_id": "d-async", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.2)
        assert ("disconnect", "d-async") in events
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_on_error(self, mock_server):
        events = []

        class MyClient(AsyncPyStrandsClient):
            async def on_error(self, error, context):
                events.append(("error", error))

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "error",
            "params": {
                "error": "async broke",
                "context": {"client_id": "e-async", "room_id": "r1", "metadata": {}}
            }
        })
        await asyncio.sleep(0.2)
        assert ("error", "async broke") in events
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_heartbeat_ignored(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "heartbeat",
            "action": "heartbeat",
            "params": {}
        })
        await asyncio.sleep(0.2)
        assert client.connected is True
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_invalid_json_handled(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.clients[0].sendall(b"not valid json\n")
        await asyncio.sleep(0.2)
        assert client.connected is True
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_unknown_action_ignored(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "x",
            "action": "totally_unknown",
            "params": {}
        })
        await asyncio.sleep(0.2)
        assert client.connected is True
        await client.disconnect()


# ====================
# Reconnection tests
# ====================

class TestAsyncReconnection:
    @pytest.mark.asyncio
    async def test_auto_reconnect_on_server_close(self, mock_server):
        client = AsyncPyStrandsClient(
            "127.0.0.1", mock_server.port,
            auto_reconnect=True,
            reconnect_delay=0.2,
            max_reconnect_delay=1.0,
        )
        await client.connect()
        await asyncio.sleep(0.1)
        assert client.connected is True

        # Close server-side connection
        for c in mock_server.clients:
            c.close()
        mock_server.clients.clear()

        await asyncio.sleep(0.5)
        # Should have attempted reconnection without crashing
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_no_reconnect_on_intentional_disconnect(self, mock_server):
        client = AsyncPyStrandsClient(
            "127.0.0.1", mock_server.port,
            auto_reconnect=True,
            reconnect_delay=0.1,
        )
        await client.connect()
        await asyncio.sleep(0.1)
        await client.disconnect()
        await asyncio.sleep(0.5)

        assert client.connected is False
        assert client._intentional_disconnect is True

    @pytest.mark.asyncio
    async def test_no_reconnect_when_disabled(self, mock_server):
        client = AsyncPyStrandsClient(
            "127.0.0.1", mock_server.port,
            auto_reconnect=False,
        )
        await client.connect()
        await asyncio.sleep(0.1)

        for c in mock_server.clients:
            c.close()
        mock_server.clients.clear()

        await asyncio.sleep(0.5)
        assert client.connected is False
        await client.disconnect()


# ====================
# Concurrent send tests
# ====================

class TestAsyncConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_sends(self, mock_server):
        """Multiple concurrent sends should not corrupt data (write lock)."""
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Fire 20 concurrent broadcasts
        tasks = [
            client.broadcast_message(f"msg-{i}")
            for i in range(20)
        ]
        await asyncio.gather(*tasks)
        await asyncio.sleep(0.3)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "broadcast"]
        assert len(msgs) == 20
        # All messages should be valid (no corruption)
        received_texts = sorted([m["params"]["message"] for m in msgs])
        expected_texts = sorted([f"msg-{i}" for i in range(20)])
        assert received_texts == expected_texts
        await client.disconnect()


# ====================
# run_forever tests
# ====================

class TestAsyncRunForever:
    @pytest.mark.asyncio
    async def test_run_forever_stops_on_disconnect(self, mock_server):
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()

        async def stop_later():
            await asyncio.sleep(0.3)
            await client.disconnect()

        stop_task = asyncio.create_task(stop_later())
        await asyncio.wait_for(client.run_forever(), timeout=2.0)
        assert client.connected is False
