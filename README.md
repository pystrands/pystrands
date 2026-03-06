# PyStrands

A lightweight, framework-agnostic real-time communication library for Python applications.

## Overview

PyStrands provides a client-server architecture for building real-time features. A Go-based WebSocket broker handles client connections, while Python backends process messages via TCP — enabling horizontal scaling and clean separation of concerns.

```
WebSocket Clients ←→ Go Broker ←TCP→ Python Backends
```

## Features

- **Sync & Async clients** — `PyStrandsClient` (threading) and `AsyncPyStrandsClient` (asyncio)
- Room-based, private, and broadcast messaging
- Connection request handling with auth & routing
- Auto-reconnect with exponential backoff
- Message queuing in the Go broker when backends are down
- Framework-agnostic — works with Flask, FastAPI, Django, or standalone
- 66 tests including real-world production scenarios

## Installation

```bash
pip install pystrands
```

## Quick Start

### Running the Server

```bash
python -m pystrands server --ws-port 8080 --tcp-port 8081 --queue-size 1000
```

The server binary is automatically downloaded for your platform.

Options:
- `--ws-port` — WebSocket port for clients (default: 8080)
- `--tcp-port` — TCP port for Python backends (default: 8081)
- `--queue-size` — Message buffer when no backends connected (default: 1000, 0 = disabled)

### Async Client (Recommended)

```python
import asyncio
from pystrands import AsyncPyStrandsClient

class ChatBackend(AsyncPyStrandsClient):
    async def on_connection_request(self, request):
        # Auth: check headers, assign room, set metadata
        auth = request.headers.get("Authorization", [None])[0]
        if auth != "Bearer valid-token":
            return False  # reject

        request.context.room_id = request.url.strip("/")
        request.context.metadata = {"role": "user"}
        return True  # accept

    async def on_message(self, message, context):
        print(f"[{context.room_id}] {context.client_id}: {message}")
        # Echo back to the room
        await self.send_room_message(context.room_id, f"echo: {message}")

    async def on_disconnect(self, context):
        print(f"{context.client_id} left {context.room_id}")

client = ChatBackend(host="localhost", port=8081)
asyncio.run(client.run_forever())
```

### Sync Client

```python
from pystrands import PyStrandsClient

class ChatBackend(PyStrandsClient):
    def on_connection_request(self, request):
        request.context.room_id = request.url.strip("/")
        return True

    def on_message(self, message, context):
        print(f"[{context.room_id}] {context.client_id}: {message}")
        self.send_room_message(context.room_id, f"echo: {message}")

client = ChatBackend(host="localhost", port=8081)
client.run_forever()
```

## API Reference

### PyStrandsClient / AsyncPyStrandsClient

```python
# Constructor
client = PyStrandsClient(
    host="localhost",
    port=8081,
    auto_reconnect=True,       # reconnect on connection loss
    reconnect_delay=1.0,       # initial delay (seconds)
    max_reconnect_delay=30.0,  # max delay cap
    reconnect_backoff=2.0,     # exponential multiplier
)
```

#### Methods

| Method | Description |
|--------|-------------|
| `connect()` | Connect to the Go broker |
| `disconnect()` | Cleanly disconnect |
| `run_forever()` | Block until disconnect |
| `broadcast_message(message)` | Send to all WebSocket clients |
| `send_room_message(room_id, message)` | Send to a specific room |
| `send_private_message(client_id, message)` | Send to a specific client |

#### Callbacks (override these)

| Callback | When |
|----------|------|
| `on_connection_request(context)` | WebSocket client wants to connect. Return `True`/`False`. |
| `on_new_connection(context)` | Client successfully connected |
| `on_message(message, context)` | Client sent a message |
| `on_disconnect(context)` | Client disconnected |
| `on_error(error, context)` | Error occurred |

### Context Classes

#### Context
- `client_id` — Unique client identifier
- `room_id` — Room the client belongs to
- `metadata` — Dict for custom data (auth info, user details, etc.)

#### ConnectionRequestContext
- `headers` — HTTP headers from the WebSocket upgrade request
- `url` — URL endpoint the client connected to
- `remote_addr` — Client's IP address
- `context` — The `Context` object (modify this for routing)
- `accepted` — Whether to accept (default: `True`)

## Architecture

```
┌──────────┐     WebSocket     ┌──────────┐      TCP       ┌──────────────┐
│  Browser  │ ←──────────────→ │ Go Broker │ ←────────────→ │ Python Backend│
│  Client   │                  │           │                │ (your code)  │
└──────────┘                   │  Queue:   │                └──────────────┘
                               │  [msg1]   │                ┌──────────────┐
                               │  [msg2]   │ ←────────────→ │ Python Backend│
                               │  [msg3]   │                │ (replica 2)  │
                               └──────────┘                └──────────────┘
```

- **Go Broker** handles WebSocket connections, routing, and message queuing
- **Python Backends** connect via TCP, process messages, send responses
- **Horizontal scaling** — add more Python backends, broker load-balances
- **Message queue** — if all backends go down, messages buffer in the broker and flush on reconnect

## Requirements

- Python >= 3.10

## License

MIT — see [LICENSE](licence) for details.
