# user_app2.py
from pystrand import PyStrandClient
import base64

class MyStrand(PyStrandClient):
    def __init__(self, host="localhost", port=8081, _id=None):
        super().__init__(host, port)
        self._id = _id

    def on_connect(self, metadata):
        print(f"on_connect override {self._id} {metadata}")
        return metadata['headers'].get('Authorization', [''])[0] == 'Bearer 1234567890'

    def on_message(self, message, metadata):
        print(f"on_message override {self._id} {base64.b64decode(message).decode('utf-8')} {metadata}")
        self.send_room_message("room1", f"Hello from {self._id}")

    def on_disconnect(self, metadata):
        print(f"on_disconnect override {self._id} {metadata}")

client = MyStrand("localhost", 8081, "AAA")
client.connect()
client2 = MyStrand("localhost", 8081, "BBB")
client2.connect()
client3 = MyStrand("localhost", 8081, "CCC")
client3.connect()
client.run_forever()
