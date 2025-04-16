# PyStrands

A lightweight, framework-agnostic real-time library for Python applications.

## Overview

PyStrands is a simple and efficient real-time communication library that enables developers to build real-time features into their Python applications. It provides a client-server architecture with support for room-based messaging, private messaging, and broadcast capabilities.

## Features

- Real-time bidirectional communication
- Room-based messaging
- Private messaging between clients
- Broadcast messaging
- Connection request handling
- Metadata support for clients
- Framework-agnostic design
- Simple and intuitive API
- Easy-to-use CLI for server management

## Installation

```bash
pip install pystrands
```

## Quick Start

### Running the Server

To start the PyStrands server, use the CLI command:

```bash
python -m pystrands server [options]
```

The server binary will be automatically downloaded for your platform and architecture. All arguments after `server` will be passed to the server binary.

Example:
```bash
python -m pystrands server --ws-port 8080 --tcp-port 8081
```

### Basic Client Implementation

```python
from pystrands import PyStrandsClient

class MyClient(PyStrandsClient):
    def on_connection_request(self, request):
        print(f"Connection request from {request.context.client_id} in room {request.context.room_id}")
        # change room id or client id
        request.context.room_id = "new_room"            # by default it is websocket url endpoint
        request.context.client_id = "new_client_id"     # by default it is uuid
        request.context.metadata = {"name": "John Doe"} # additional metadata
        # accept the connection
        request.accepted = True                         # accepted by default
    
    def on_connect(self, context):
        print(f"Connected to {context.client_id} in room {context.room_id}")
        # send welcome message to the client
        self.send_private_message(context.client_id, f"Welcome to the room! {context.metadata.get('name')}")
    
    def on_message(self, message, context):
        print(f"Received message: {message} from {context.context.client_id} in room {context.context.room_id}")

    def on_disconnect(self, context):
        print(f"Disconnected from {context.context.client_id} in room {context.context.room_id}")

    def on_error(self, error, context):
        print(f"Error: {error} from {context.context.client_id} in room {context.context.room_id}")

# Create and connect client
client = MyClient(host="localhost", port=8081)
client.connect()

# Send messages
client.broadcast_message("Hello everyone!")
client.send_room_message("room1", "Hello room!")
client.send_private_message("client123", "Private message!")

# Run the client
client.run_forever()
```

## API Reference

### PyStrandsClient

The main client class that handles communication with the PyStrands server.

#### Methods

- `__init__(host="localhost", port=8081)`: Initialize the client
- `connect()`: Establish connection to the server
- `disconnect()`: Close the connection
- `run_forever()`: Start the client's event loop
- `broadcast_message(message: str)`: Send a message to all connected clients
- `send_room_message(room_id: str, message: str)`: Send a message to a specific room
- `send_private_message(client_id: str, message: str)`: Send a private message to a specific client

#### Callbacks

- `on_connect(context: Context)`: Called when the client successfully connects
- `on_connection_request(context: ConnectionRequestContext)`: Called when a new connection request is received
- `on_message(message, context: Context)`: Called when a message is received
- `on_disconnect(context: Context)`: Called when the client disconnects
- `on_error(error)`: Called when an error occurs

### Context Classes

#### Context
- `client_id`: Unique identifier for the client
- `room_id`: ID of the room the client is in
- `metadata`: Additional information about the context

#### ConnectionRequestContext
- `headers`: Headers of the connection request
- `url`: URL endpoint of the connection request
- `remote_addr`: Remote address of the connection request
- `context`: Context of the connection request
- `accepted`: Whether the connection request should be accepted

## Requirements

- Python >= 3.8

## License

This project is licensed under the MIT License - see the [LICENSE](licence) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
