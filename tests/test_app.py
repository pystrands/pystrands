"""Tests for the decorator-based PyStrands API."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pystrands import PyStrands
from pystrands.context import Context, ConnectionRequestContext


class TestPyStrandsDecorators:
    """Test decorator registration."""

    def test_on_message_registers_handler(self):
        app = PyStrands()

        @app.on_message
        async def handler(message, context):
            pass

        assert "message" in app._handlers
        assert app._handlers["message"] == handler

    def test_on_connection_request_registers_handler(self):
        app = PyStrands()

        @app.on_connection_request
        async def handler(request):
            return True

        assert "connection_request" in app._handlers

    def test_on_new_connection_registers_handler(self):
        app = PyStrands()

        @app.on_new_connection
        async def handler(context):
            pass

        assert "new_connection" in app._handlers

    def test_on_disconnect_registers_handler(self):
        app = PyStrands()

        @app.on_disconnect
        async def handler(context):
            pass

        assert "disconnect" in app._handlers

    def test_on_error_registers_handler(self):
        app = PyStrands()

        @app.on_error
        async def handler(error, context):
            pass

        assert "error" in app._handlers

    def test_decorator_returns_original_function(self):
        app = PyStrands()

        @app.on_message
        async def my_handler(message, context):
            return "test"

        assert my_handler.__name__ == "my_handler"


class TestPyStrandsCall:
    """Test handler invocation."""

    @pytest.mark.asyncio
    async def test_call_async_handler(self):
        app = PyStrands()
        called_with = []

        @app.on_message
        async def handler(message, context):
            called_with.append((message, context))
            return "async result"

        result = await app._call("message", "hello", "ctx")
        assert called_with == [("hello", "ctx")]
        assert result == "async result"

    @pytest.mark.asyncio
    async def test_call_sync_handler(self):
        app = PyStrands()
        called_with = []

        @app.on_message
        def handler(message, context):  # sync function
            called_with.append((message, context))
            return "sync result"

        result = await app._call("message", "hello", "ctx")
        assert called_with == [("hello", "ctx")]
        assert result == "sync result"

    @pytest.mark.asyncio
    async def test_call_unregistered_event_returns_none(self):
        app = PyStrands()
        result = await app._call("message", "hello", "ctx")
        assert result is None


class TestPyStrandsClientCreation:
    """Test internal client creation."""

    def test_create_client_inherits_settings(self):
        app = PyStrands(
            host="example.com",
            port=9000,
            auto_reconnect=False,
            reconnect_delay=5.0,
        )
        client = app._create_client()
        assert client.host == "example.com"
        assert client.port == 9000
        assert client.auto_reconnect is False
        assert client.reconnect_delay == 5.0

    @pytest.mark.asyncio
    async def test_client_calls_registered_handlers(self):
        app = PyStrands()
        messages = []

        @app.on_message
        async def handler(message, context):
            messages.append(message)

        client = app._create_client()
        ctx = Context(client_id="c1", room_id="r1", metadata={})
        await client.on_message("test message", ctx)

        assert messages == ["test message"]

    @pytest.mark.asyncio
    async def test_connection_request_defaults_to_true(self):
        app = PyStrands()
        # No handler registered
        client = app._create_client()

        request = MagicMock()
        result = await client.on_connection_request(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_connection_request_uses_handler_result(self):
        app = PyStrands()

        @app.on_connection_request
        async def handler(request):
            return False

        client = app._create_client()
        request = MagicMock()
        result = await client.on_connection_request(request)
        assert result is False


class TestPyStrandsMessaging:
    """Test message sending methods."""

    @pytest.mark.asyncio
    async def test_send_room_message_delegates(self):
        app = PyStrands()
        app._client = AsyncMock()

        await app.send_room_message("room1", "hello")
        app._client.send_room_message.assert_called_once_with("room1", "hello")

    @pytest.mark.asyncio
    async def test_send_private_message_delegates(self):
        app = PyStrands()
        app._client = AsyncMock()

        await app.send_private_message("client1", "hello")
        app._client.send_private_message.assert_called_once_with("client1", "hello")

    @pytest.mark.asyncio
    async def test_broadcast_delegates(self):
        app = PyStrands()
        app._client = AsyncMock()

        await app.broadcast("hello everyone")
        app._client.broadcast_message.assert_called_once_with("hello everyone")

    @pytest.mark.asyncio
    async def test_send_without_client_is_safe(self):
        app = PyStrands()
        # _client is None
        await app.send_room_message("room1", "hello")  # should not raise
        await app.send_private_message("client1", "hello")
        await app.broadcast("hello")
