import enum
import json
from dataclasses import InitVar, asdict, dataclass, field
from typing import Any, Dict, List, Union

from asyncua.ua import ExtensionObject

DataChangePayload = Union[List[Dict[str, Any]], Dict[str, Any]]


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


@dataclass
class BaseMessage:
    message_type: str = field(init=False)

    @property
    def frontend_data(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k != "message_type"}


@dataclass
class OPCDataChangeMessage(BaseMessage):
    message_type = "opc_data_change"
    node_id: str
    payload: DataChangePayload = field(init=False)
    ua_object: InitVar[ExtensionObject]

    def __post_init__(self, ua_object: ExtensionObject) -> None:
        self.payload = json.loads(json.dumps(ua_object, cls=OPCUAEncoder))


@enum.unique
class LinkStatus(str, enum.Enum):
    Up = "UP"
    Down = "DOWN"


@dataclass
class OPCStatusMessage(BaseMessage):
    message_type = "opc_status"
    payload: LinkStatus


@dataclass
class HeartBeatMessage(BaseMessage):
    message_type = "heartbeat"
    payload: None = None
