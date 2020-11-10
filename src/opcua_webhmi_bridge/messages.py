"""Defines messages types to be exchanged between components of the application."""

import enum
import json
from dataclasses import InitVar, asdict, dataclass, field
from typing import Any, Dict, List, Union

from asyncua.ua import ExtensionObject

DataChangePayload = Union[List[Dict[str, Any]], Dict[str, Any]]


class OPCUAEncoder(json.JSONEncoder):
    """JSON encoder that recognizes OPC-UA data structures."""

    def default(self, o: Any) -> Any:
        """Extends standard library JSON encoder."""
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


@dataclass
class BaseMessage:
    """Base class for application messages.

    Attributes:
        message_type: A string describing the message type.
    """

    message_type: str = field(init=False)

    @property
    def frontend_data(self) -> Dict[str, Any]:
        """Returns a dictionary representation excluding the message type field."""
        return {k: v for k, v in asdict(self).items() if k != "message_type"}


@dataclass
class OPCDataChangeMessage(BaseMessage):
    """OPC-UA data change message.

    Attributes:
        message_type: Same as base class.
        node_id: OPC-UA node ID.
        payload: The flattened representation of OPC-UA data.
    """

    message_type = "opc_data_change"
    node_id: str
    payload: DataChangePayload = field(init=False)
    ua_object: InitVar[ExtensionObject]

    def __post_init__(self, ua_object: ExtensionObject) -> None:
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

    message_type = "opc_status"
    payload: LinkStatus


@dataclass
class HeartBeatMessage(BaseMessage):
    """Heartbeat empty message."""

    message_type = "heartbeat"
    payload: None = None
