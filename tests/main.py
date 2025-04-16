# user_app2.py
from pystrands import PyStrandsClient
import base64

class MyStrand(PyStrandsClient):
    def __init__(self, host="localhost", port=8081, _id=None):
        super().__init__(host, port)
        self._id = _id

    def on_connection_request(self, request):
        return True
    
    def on_connect(self, context):
        return self.send_private_message(context.client_id, "Hello from server")

    def on_message(self, message, context):
        print(f"on_message override {self._id} {base64.b64decode(message).decode('utf-8')} {context}")
        self.send_room_message("room1", f"Hello from {self._id}")

    def on_disconnect(self, context):
        print(f"on_disconnect override {self._id} {context}")

client = MyStrand("localhost", 8081, "AAA")
client.connect()
client2 = MyStrand("localhost", 8081, "BBB")
client2.connect()
client3 = MyStrand("localhost", 8081, "CCC")
client3.connect()
client.run_forever()
