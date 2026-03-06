"""Real-world scenario tests for AsyncPyStrandsClient.

These tests simulate actual production conditions:
- Handler failures
- Partial TCP reads
- Concurrent load
- Reconnection edge cases
- Large/malformed messages
- Backpressure
"""
import asyncio
import json
import socket
import threading
import time
import uuid
import pytest

from pystrands.async_client import AsyncPyStrandsClient
from pystrands.context import Context


# ====================
# Test helpers
# ====================

class MockTCPServer:
    """Minimal TCP server simulating the Go broker."""

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

    def send_to(self, idx, msg_dict):
        data = (json.dumps(msg_dict) + "\n").encode("utf-8")
        self.clients[idx].sendall(data)

    def send_raw(self, idx, raw_bytes):
        """Send raw bytes (for testing partial reads, malformed data)."""
        self.clients[idx].sendall(raw_bytes)

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
# Scenario 1: Handler throws an exception
# The client should NOT crash. Other messages should still be processed.
# ====================

class TestHandlerCrash:
    @pytest.mark.asyncio
    async def test_on_message_exception_doesnt_kill_client(self, mock_server):
        """If on_message raises, the client should keep running and process the next message."""
        received = []

        class CrashyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                if message == "crash":
                    raise ValueError("handler exploded!")
                received.append(message)

        client = CrashyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Send a message that crashes the handler
        mock_server.send_to(0, {
            "request_id": "1", "action": "new_message",
            "params": {"message": "crash", "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.1)

        # Send a normal message after the crash
        mock_server.send_to(0, {
            "request_id": "2", "action": "new_message",
            "params": {"message": "still alive", "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.2)

        assert client.connected is True, "Client should still be connected after handler exception"
        assert "still alive" in received, "Client should process messages after a handler crash"
        assert "crash" not in received, "Crashed message should not appear in received"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_on_connection_request_exception_rejects(self, mock_server):
        """If on_connection_request raises, the connection should be rejected (not crash)."""

        class CrashyClient(AsyncPyStrandsClient):
            async def on_connection_request(self, context):
                raise RuntimeError("auth service down!")

        client = CrashyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "cr-1", "action": "connection_request",
            "params": {"headers": {}, "url": "/ws/", "remote_addr": "10.0.0.1"}
        })
        await asyncio.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        assert responses[0]["params"]["accepted"] is False, "Should reject when handler crashes"
        assert client.connected is True, "Client should survive connection_request handler crash"
        await client.disconnect()


# ====================
# Scenario 2: Partial TCP reads / split messages
# TCP doesn't guarantee message boundaries. A JSON line might arrive in chunks.
# ====================

class TestPartialReads:
    @pytest.mark.asyncio
    async def test_message_split_across_tcp_chunks(self, mock_server):
        """A single JSON message delivered in two separate TCP reads."""
        received = []

        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                received.append(message)

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Send first half of JSON
        full_msg = json.dumps({
            "request_id": "split-1", "action": "new_message",
            "params": {"message": "split-test", "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
        }) + "\n"

        mid = len(full_msg) // 2
        mock_server.send_raw(0, full_msg[:mid].encode())
        await asyncio.sleep(0.1)
        mock_server.send_raw(0, full_msg[mid:].encode())
        await asyncio.sleep(0.2)

        assert "split-test" in received, "Should reassemble split TCP data correctly"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_multiple_messages_in_single_tcp_read(self, mock_server):
        """Multiple JSON messages arriving in a single TCP read (batched)."""
        received = []

        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                received.append(message)

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Send 5 messages in a single TCP write
        batch = ""
        for i in range(5):
            batch += json.dumps({
                "request_id": f"batch-{i}", "action": "new_message",
                "params": {"message": f"msg-{i}", "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
            }) + "\n"

        mock_server.send_raw(0, batch.encode())
        await asyncio.sleep(0.3)

        assert len(received) == 5, f"Should handle batched messages, got {len(received)}"
        for i in range(5):
            assert f"msg-{i}" in received
        await client.disconnect()


# ====================
# Scenario 3: Malformed data recovery
# Real networks can have garbage. Client should skip bad data and keep going.
# ====================

class TestMalformedData:
    @pytest.mark.asyncio
    async def test_garbage_between_valid_messages(self, mock_server):
        """Garbage data between valid JSON messages. Client should skip and continue."""
        received = []

        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                received.append(message)

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        ctx = {"client_id": "c1", "room_id": "r1", "metadata": {}}

        # Valid message
        mock_server.send_to(0, {
            "request_id": "1", "action": "new_message",
            "params": {"message": "before-garbage", "context": ctx}
        })
        await asyncio.sleep(0.1)

        # Garbage
        mock_server.send_raw(0, b"this is total garbage\n")
        mock_server.send_raw(0, b"{{{{not json}}}}\n")
        await asyncio.sleep(0.1)

        # Valid message after garbage
        mock_server.send_to(0, {
            "request_id": "2", "action": "new_message",
            "params": {"message": "after-garbage", "context": ctx}
        })
        await asyncio.sleep(0.2)

        assert "before-garbage" in received
        assert "after-garbage" in received, "Client should recover from garbage data"
        assert client.connected is True
        await client.disconnect()


# ====================
# Scenario 4: Slow handler doesn't block other messages
# If on_message takes 2 seconds, incoming messages should still be read from TCP.
# ====================

class TestSlowHandler:
    @pytest.mark.asyncio
    async def test_slow_handler_doesnt_block_receive(self, mock_server):
        """A slow on_message shouldn't prevent receiving subsequent messages.
        
        Note: Since _handle_incoming is awaited sequentially in the receive loop,
        messages are processed in order. But the TCP buffer keeps accumulating data
        so nothing is lost — just delayed until the slow handler finishes.
        """
        received = []
        handler_times = []

        class SlowClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                start = asyncio.get_event_loop().time()
                if message == "slow":
                    await asyncio.sleep(0.5)  # simulate slow DB call
                received.append(message)
                handler_times.append(asyncio.get_event_loop().time() - start)

        client = SlowClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        ctx = {"client_id": "c1", "room_id": "r1", "metadata": {}}

        # Send slow message followed by fast messages
        for msg in ["slow", "fast-1", "fast-2", "fast-3"]:
            mock_server.send_to(0, {
                "request_id": str(uuid.uuid4()), "action": "new_message",
                "params": {"message": msg, "context": ctx}
            })
            await asyncio.sleep(0.02)

        # Wait for all to be processed
        await asyncio.sleep(1.5)

        assert len(received) == 4, f"All messages should be received, got {len(received)}: {received}"
        assert received[0] == "slow", "Slow message should be first (FIFO)"
        assert "fast-1" in received
        assert "fast-2" in received
        assert "fast-3" in received
        assert client.connected is True
        await client.disconnect()


# ====================
# Scenario 5: Send during disconnected state
# User code sends messages while the client is not connected.
# ====================

class TestSendWhileDisconnected:
    @pytest.mark.asyncio
    async def test_send_before_connect(self):
        """Sending before connect should not crash."""
        client = AsyncPyStrandsClient("127.0.0.1", 1, auto_reconnect=False)
        # These should silently do nothing, not crash
        await client.broadcast_message("hello")
        await client.send_room_message("room", "hello")
        await client.send_private_message("client", "hello")

    @pytest.mark.asyncio
    async def test_send_after_disconnect(self, mock_server):
        """Sending after disconnect should not crash."""
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await client.disconnect()

        # These should silently do nothing
        await client.broadcast_message("hello")
        await client.send_room_message("room", "hello")
        await client.send_private_message("client", "hello")


# ====================
# Scenario 6: Large messages
# ====================

class TestLargeMessages:
    @pytest.mark.asyncio
    async def test_large_message_send(self, mock_server):
        """Send a large message (~100KB). Should not corrupt or truncate."""
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        large_payload = "x" * 100_000  # 100KB
        await client.broadcast_message(large_payload)
        await asyncio.sleep(0.5)

        msgs = [m for m in mock_server.received_messages if m.get("action") == "broadcast"]
        assert len(msgs) >= 1, "Large message should be received"
        assert len(msgs[0]["params"]["message"]) == 100_000, "Large message should not be truncated"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_large_message_receive(self, mock_server):
        """Receive a large message (~100KB). Buffer should handle it."""
        received = []

        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                received.append(message)

        client = MyClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        large_payload = "y" * 100_000
        mock_server.send_to(0, {
            "request_id": "big", "action": "new_message",
            "params": {"message": large_payload, "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.5)

        assert len(received) == 1, "Large message should be received"
        assert len(received[0]) == 100_000, "Large message should not be truncated"
        await client.disconnect()


# ====================
# Scenario 7: Rapid connect/disconnect cycles
# Simulate flapping connection. No resource leaks, no crashes.
# ====================

class TestRapidReconnect:
    @pytest.mark.asyncio
    async def test_rapid_connect_disconnect_cycles(self, mock_server):
        """Connect and disconnect 10 times rapidly. Should not leak or crash."""
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)

        for i in range(10):
            result = await client.connect()
            assert result is True, f"Connect {i} should succeed"
            assert client.connected is True
            await client.disconnect()
            assert client.connected is False

        # Final connect should still work
        result = await client.connect()
        assert result is True, "Should still work after rapid cycles"
        await client.disconnect()


# ====================
# Scenario 8: Server abruptly kills the TCP connection
# No graceful close — just gone. Client should detect and handle.
# ====================

class TestAbruptDisconnect:
    @pytest.mark.asyncio
    async def test_server_kills_connection(self, mock_server):
        """Server abruptly closes socket. Client should detect and mark disconnected."""
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)
        assert client.connected is True

        # Server kills the connection
        for c in mock_server.clients:
            c.close()
        mock_server.clients.clear()

        await asyncio.sleep(0.5)
        assert client.connected is False, "Client should detect abrupt disconnect"
        await client.disconnect()


# ====================
# Scenario 9: Connection request with context enrichment
# Backend accepts connection, assigns custom room/client IDs, adds metadata.
# This is the core use case — routing and auth happen here.
# ====================

class TestConnectionRequestEnrichment:
    @pytest.mark.asyncio
    async def test_auth_and_routing(self, mock_server):
        """Simulate real auth: check headers, assign room based on URL, add user metadata."""

        class AuthBackend(AsyncPyStrandsClient):
            async def on_connection_request(self, context):
                # Real scenario: check auth token in headers
                auth = context.headers.get("Authorization", [None])[0]
                if auth != "Bearer valid-token":
                    return False

                # Assign room based on URL path
                context.context.room_id = context.url.strip("/").replace("ws/", "")
                context.context.client_id = "user-123"
                context.context.metadata = {"role": "admin", "name": "Diwakar"}
                return True

        client = AuthBackend("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Valid auth
        mock_server.send_to(0, {
            "request_id": "auth-1", "action": "connection_request",
            "params": {
                "headers": {"Authorization": ["Bearer valid-token"]},
                "url": "/ws/chat-room-42",
                "remote_addr": "10.0.0.1:5555"
            }
        })
        await asyncio.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        resp = responses[0]["params"]
        assert resp["accepted"] is True
        assert resp["room_id"] == "chat-room-42"
        assert resp["client_id"] == "user-123"
        assert resp["metadata"]["role"] == "admin"

        # Invalid auth
        mock_server.received_messages.clear()
        mock_server.send_to(0, {
            "request_id": "auth-2", "action": "connection_request",
            "params": {
                "headers": {"Authorization": ["Bearer bad-token"]},
                "url": "/ws/secret",
                "remote_addr": "10.0.0.1:6666"
            }
        })
        await asyncio.sleep(0.3)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) >= 1
        assert responses[0]["params"]["accepted"] is False

        await client.disconnect()


# ====================
# Scenario 10: Echo server (request-response pattern)
# The most common use case: receive message, process, send response back.
# ====================

class TestEchoPattern:
    @pytest.mark.asyncio
    async def test_receive_process_respond(self, mock_server):
        """Classic echo: receive a message, process it, send a response to the room."""

        class EchoBackend(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                # Process and respond
                response = f"echo: {message.upper()}"
                await self.send_room_message(context.room_id, response)

        client = EchoBackend("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Simulate a user message
        mock_server.send_to(0, {
            "request_id": "echo-1", "action": "new_message",
            "params": {
                "message": "hello world",
                "context": {"client_id": "user-1", "room_id": "general", "metadata": {}}
            }
        })
        await asyncio.sleep(0.3)

        # Check that the echo response was sent back
        room_msgs = [m for m in mock_server.received_messages if m.get("action") == "message_to_room"]
        assert len(room_msgs) >= 1, "Echo response should be sent"
        assert room_msgs[0]["params"]["room_id"] == "general"
        assert room_msgs[0]["params"]["message"] == "echo: HELLO WORLD"
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_burst_echo(self, mock_server):
        """50 messages rapid-fire. All should get echo responses."""
        echo_count = 0

        class EchoBackend(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                nonlocal echo_count
                await self.send_room_message(context.room_id, f"echo: {message}")
                echo_count += 1

        client = EchoBackend("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        ctx = {"client_id": "user-1", "room_id": "burst-room", "metadata": {}}

        # Send 50 messages rapidly
        for i in range(50):
            mock_server.send_to(0, {
                "request_id": f"burst-{i}", "action": "new_message",
                "params": {"message": f"msg-{i}", "context": ctx}
            })

        # Wait for processing
        await asyncio.sleep(2.0)

        assert echo_count == 50, f"All 50 messages should be echoed, got {echo_count}"
        room_msgs = [m for m in mock_server.received_messages if m.get("action") == "message_to_room"]
        assert len(room_msgs) == 50, f"All 50 echo responses should be sent, got {len(room_msgs)}"
        await client.disconnect()


# ====================
# Scenario 11: Concurrent connection requests
# Multiple WS clients connecting at the same time.
# ====================

class TestFullServerRestart:
    @pytest.mark.asyncio
    async def test_server_crash_and_restart(self):
        """Server completely dies and restarts on the same port.
        Client should reconnect and resume receiving messages."""
        received = []
        PORT = 19877

        class MyClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                received.append(message)

        # Start server v1
        sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock1.bind(("127.0.0.1", PORT))
        sock1.listen(5)
        clients1 = []
        running = [True]

        def accept_loop(s, cl):
            while running[0]:
                try:
                    s.settimeout(0.5)
                    conn, _ = s.accept()
                    cl.append(conn)
                except socket.timeout:
                    continue
                except:
                    break

        t1 = threading.Thread(target=accept_loop, args=(sock1, clients1), daemon=True)
        t1.start()

        # Connect client
        client = MyClient("127.0.0.1", PORT, auto_reconnect=True,
                          reconnect_delay=0.3, max_reconnect_delay=1.0)
        await client.connect()
        await asyncio.sleep(0.2)
        assert client.connected is True

        # Send a message on server v1
        ctx = {"client_id": "c1", "room_id": "r1", "metadata": {}}
        msg1 = json.dumps({"request_id": "v1", "action": "new_message",
                           "params": {"message": "from-server-v1", "context": ctx}}) + "\n"
        clients1[0].sendall(msg1.encode())
        await asyncio.sleep(0.2)
        assert "from-server-v1" in received

        # KILL THE ENTIRE SERVER
        running[0] = False
        for c in clients1:
            c.close()
        sock1.close()
        await asyncio.sleep(0.5)
        assert client.connected is False, "Client should detect server is gone"

        # START SERVER v2 on same port
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock2.bind(("127.0.0.1", PORT))
        sock2.listen(5)
        clients2 = []
        running[0] = True
        t2 = threading.Thread(target=accept_loop, args=(sock2, clients2), daemon=True)
        t2.start()

        # Wait for reconnect
        await asyncio.sleep(2.0)
        assert client.connected is True, "Client should reconnect to restarted server"
        assert len(clients2) >= 1, "Server v2 should have the reconnected client"

        # Send a message on server v2
        msg2 = json.dumps({"request_id": "v2", "action": "new_message",
                           "params": {"message": "from-server-v2", "context": ctx}}) + "\n"
        clients2[0].sendall(msg2.encode())
        await asyncio.sleep(0.3)
        assert "from-server-v2" in received, "Client should receive messages after server restart"

        # Cleanup
        await client.disconnect()
        running[0] = False
        sock2.close()


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_disconnect_waits_for_inflight_handler(self, mock_server):
        """disconnect() should wait for a running on_message handler to finish,
        not kill it mid-execution."""
        handler_completed = False

        class SlowClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                nonlocal handler_completed
                await asyncio.sleep(0.5)  # simulate slow work (DB write, API call)
                handler_completed = True

        client = SlowClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        # Trigger slow handler
        mock_server.send_to(0, {
            "request_id": "slow", "action": "new_message",
            "params": {"message": "slow-work", "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.1)  # let handler start

        # Disconnect while handler is running
        await client.disconnect(timeout=3.0)

        assert handler_completed is True, "Handler should finish before disconnect completes"

    @pytest.mark.asyncio
    async def test_disconnect_force_closes_on_timeout(self, mock_server):
        """If handler takes longer than timeout, force close."""
        handler_interrupted = False

        class VerySlowClient(AsyncPyStrandsClient):
            async def on_message(self, message, context):
                nonlocal handler_interrupted
                try:
                    await asyncio.sleep(10.0)  # way longer than timeout
                except asyncio.CancelledError:
                    handler_interrupted = True
                    raise

        client = VerySlowClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        mock_server.send_to(0, {
            "request_id": "stuck", "action": "new_message",
            "params": {"message": "stuck", "context": {"client_id": "c1", "room_id": "r1", "metadata": {}}}
        })
        await asyncio.sleep(0.1)

        # Disconnect with short timeout — should force close
        await client.disconnect(timeout=0.5)

        assert handler_interrupted is True, "Handler should be cancelled after timeout"
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_disconnect_with_no_inflight_is_instant(self, mock_server):
        """Disconnect when nothing is running should be fast."""
        client = AsyncPyStrandsClient("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        start = asyncio.get_event_loop().time()
        await client.disconnect()
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0, f"Disconnect with no in-flight should be fast, took {elapsed:.2f}s"


class TestConcurrentConnectionRequests:
    @pytest.mark.asyncio
    async def test_multiple_connection_requests(self, mock_server):
        """10 connection requests sent rapidly. All should get responses."""
        decisions = []

        class GatekeeperBackend(AsyncPyStrandsClient):
            async def on_connection_request(self, context):
                # Accept even-numbered, reject odd
                num = int(context.url.split("-")[-1])
                accepted = (num % 2 == 0)
                decisions.append((num, accepted))
                return accepted

        client = GatekeeperBackend("127.0.0.1", mock_server.port, auto_reconnect=False)
        await client.connect()
        await asyncio.sleep(0.1)

        for i in range(10):
            mock_server.send_to(0, {
                "request_id": f"cr-{i}", "action": "connection_request",
                "params": {"headers": {}, "url": f"/ws/room-{i}", "remote_addr": f"10.0.0.{i}"}
            })
            await asyncio.sleep(0.02)

        await asyncio.sleep(1.0)

        responses = [m for m in mock_server.received_messages if m.get("action") == "response"]
        assert len(responses) == 10, f"All 10 should get responses, got {len(responses)}"
        assert len(decisions) == 10
        await client.disconnect()
