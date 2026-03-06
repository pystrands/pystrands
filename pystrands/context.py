from typing import Dict, List


class JSONModel:
    def __init__(self, **kwargs):
        for k in self._get_annotations():
            setattr(self, k, kwargs.get(k))

    @classmethod
    def _get_annotations(cls):
        """Get annotations from the class hierarchy (compatible with Python 3.14+)."""
        annotations = {}
        for klass in reversed(cls.__mro__):
            annotations.update(getattr(klass, '__annotations__', {}))
        return annotations

    @classmethod
    def from_json(cls, data):
        return cls(**data)

    def to_json(self):
        return {k: getattr(self, k) for k in self._get_annotations()}


class Context(JSONModel):
    client_id: str
    """
    The client ID is a unique identifier for the client.
    """
    room_id: str
    """
    The room ID is the ID of the room that the client is in.
    """
    metadata: dict
    """
    Metadata is a dictionary of key-value pairs that are used to store additional information about the context.
    """


class ConnectionRequestContext(JSONModel):
    headers: Dict[str, List[str]]
    """
    The headers of the connection request.
    """
    url: str
    """
    The URL endpoint of the connection request.
    """
    remote_addr: str
    """
    The remote address of the connection request.
    """
    context: "Context"
    """
    The context of the connection request.
    """
    accepted: bool
    """
    Accepted is a boolean that indicates whether the connection request should be accepted.
    """
