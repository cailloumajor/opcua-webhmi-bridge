"""Defines messages types to be exchanged between components of the application."""

import enum
import json
from dataclasses import InitVar, asdict, dataclass, field
from typing import Any

PROXIED_CHANNEL_PREFIX = "proxied:"

JsonScalar = str | int | float | bool | None
DataChangePayload = dict[str, Any] | list[dict[str, Any] | JsonScalar] | JsonScalar


class MessageType(str, enum.Enum):
    """Enumeration of message types."""

    OPC_DATA = "opc_data"
    OPC_STATUS = "opc_status"
    HEARTBEAT = "heartbeat"

    @property
    def centrifugo_channel(self) -> str:
        """Returns the Centrifugo channel name for this member."""
        channel: str = self.value
        if self.name.startswith("OPC_"):
            channel = PROXIED_CHANNEL_PREFIX + channel
        return channel


class OPCUAEncoder(json.JSONEncoder):
    """JSON encoder that recognizes OPC-UA data structures."""

    def default(self, o: Any) -> Any:
        """Extends standard library JSON encoder."""
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)  # pragma: no cover


@dataclass
class BaseMessage:
    """Base class for application messages.

    Attributes:
        message_type: A member of MessageType enum describing the message type.
    """

    message_type: MessageType = field(init=False)

    @property
    def frontend_data(self) -> dict[str, Any]:
        """Returns a dictionary representation excluding the message type field."""
        return {k: v for k, v in asdict(self).items() if k != "message_type"}


@dataclass
class OPCDataMessage(BaseMessage):
    """OPC-UA data message.

    Attributes:
        message_type: Same as base class.
        node_id: OPC-UA node ID.
        payload: The flattened representation of OPC-UA data.
    """

    message_type = MessageType.OPC_DATA
    node_id: str
    payload: DataChangePayload = field(init=False)
    ua_object: InitVar[Any]

    def __post_init__(self, ua_object: Any) -> None:
        """Initializes payload field from raw OPC-UA data.

        Args:
            ua_object: The raw OPC-UA data.
        """
        self.payload = json.loads(json.dumps(ua_object, cls=OPCUAEncoder))


@enum.unique
class LinkStatus(str, enum.Enum):
    """Enumeration for link status."""

    Up = "UP"
    Down = "DOWN"


@dataclass
class OPCStatusMessage(BaseMessage):
    """OPC-UA server link status message.

    Attributes:
        message_type: Same as base class.
        payload: The status of OPC-UA server link.
    """

    message_type = MessageType.OPC_STATUS
    payload: LinkStatus


@dataclass
class HeartBeatMessage(BaseMessage):
    """Heartbeat empty message."""

    message_type = MessageType.HEARTBEAT
    payload: None = None
