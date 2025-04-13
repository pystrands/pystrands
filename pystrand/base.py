class PyStrandBase:
    """
    Extend this class to override your on_connect, on_disconnect, etc.
    """
    def on_connect(self, metadata, conn):
        """
        Called when a new connection is established.
        metadata: dict containing info from handshake
        conn: an object or ID representing the connection
        """
        pass

    def on_disconnect(self, metadata, conn):
        """
        Called when a connection is closed.
        """
        pass

    def on_message(self, message, metadata, conn):
        """
        Called when a new message is received.
        """
        pass
