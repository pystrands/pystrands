# user_app2.py
from pystrands import PyStrandsClient
from pystrands.context import ConnectionRequestContext, Context

class MyStrand(PyStrandsClient):
    def on_connection_request(self, context: ConnectionRequestContext):
        context.context.room_id = 'room-one'
        context.context.client_id = 'user-one'
        context.context.metadata = {
            'name': 'User',
        }

    def on_new_connection(self, context):
        print(f"on_new_connection override {context.client_id} {context.room_id}")
        self.send_room_message(context.room_id, f"Hello {context.metadata.get('name')} from server")
    
    def on_message(self, message: str, context: Context):
        print(f"on_message override {context.client_id} {message}")
        self.send_room_message(context.room_id, f"Message received {context.metadata.get('name')}")
    
    def on_disconnect(self, context: Context):
        print(f"on_disconnect override {context.client_id} {context.room_id}")

client = MyStrand("205.209.121.166", 8081)
client.connect()
client.run_forever()
