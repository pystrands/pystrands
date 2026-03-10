"""Decorator-based API for PyStrands."""
import asyncio
import inspect
from typing import Callable, Optional
from pystrands.async_client import AsyncPyStrandsClient


class PyStrands:
    """
    Decorator-based PyStrands client.

    Example:
        app = PyStrands()

        @app.on_message
        async def handle(message, context):
            await app.send_room_message(context.room_id, message)

        app.run()
    """

    def __init__(self, host: str = "localhost", port: int = 8081,
                 auto_reconnect: bool = True, reconnect_delay: float = 1.0,
                 max_reconnect_delay: float = 30.0, reconnect_backoff: float = 2.0):
        self.host = host
        self.port = port
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.reconnect_backoff = reconnect_backoff

        self._handlers = {}
        self._client: Optional[AsyncPyStrandsClient] = None

    def _register(self, event: str) -> Callable:
        """Register a handler for an event."""
        def decorator(func: Callable) -> Callable:
            self._handlers[event] = func
            return func
        return decorator

    def on_connection_request(self, func: Callable) -> Callable:
        """Register handler for connection requests."""
        return self._register("connection_request")(func)

    def on_new_connection(self, func: Callable) -> Callable:
        """Register handler for new connections."""
        return self._register("new_connection")(func)

    def on_message(self, func: Callable) -> Callable:
        """Register handler for incoming messages."""
        return self._register("message")(func)

    def on_disconnect(self, func: Callable) -> Callable:
        """Register handler for disconnections."""
        return self._register("disconnect")(func)

    def on_error(self, func: Callable) -> Callable:
        """Register handler for errors."""
        return self._register("error")(func)

    async def _call(self, event: str, *args):
        """Call a registered handler (sync or async)."""
        handler = self._handlers.get(event)
        if not handler:
            return None
        if inspect.iscoroutinefunction(handler):
            return await handler(*args)
        else:
            return await asyncio.to_thread(handler, *args)

    def _create_client(self) -> AsyncPyStrandsClient:
        """Create the internal client with handlers wired up."""
        app = self

        class _Client(AsyncPyStrandsClient):
            async def on_connection_request(self, request):
                result = await app._call("connection_request", request)
                return result if result is not None else True

            async def on_new_connection(self, context):
                await app._call("new_connection", context)

            async def on_message(self, message, context):
                await app._call("message", message, context)

            async def on_disconnect(self, context):
                await app._call("disconnect", context)

            async def on_error(self, error, context):
                await app._call("error", error, context)

        return _Client(
            host=self.host,
            port=self.port,
            auto_reconnect=self.auto_reconnect,
            reconnect_delay=self.reconnect_delay,
            max_reconnect_delay=self.max_reconnect_delay,
            reconnect_backoff=self.reconnect_backoff,
        )

    # Message sending methods (delegate to internal client)
    async def send_room_message(self, room_id: str, message: str):
        """Send a message to a room."""
        if self._client:
            await self._client.send_room_message(room_id, message)

    async def send_private_message(self, client_id: str, message: str):
        """Send a private message to a specific client."""
        if self._client:
            await self._client.send_private_message(client_id, message)

    async def broadcast(self, message: str):
        """Broadcast a message to all clients."""
        if self._client:
            await self._client.broadcast_message(message)

    async def run_async(self):
        """Run the client (async version)."""
        self._client = self._create_client()
        await self._client.run_forever()

    def run(self):
        """Run the client (blocking)."""
        asyncio.run(self.run_async())
