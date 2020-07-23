import asyncio
import dataclasses
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterator, Set, Union, cast

from asyncua.ua.uatypes import ExtensionObject


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


@dataclasses.dataclass
class BaseMessage:
    message_type: str = dataclasses.field(init=False)

    def __str__(self) -> str:
        return json.dumps(dataclasses.asdict(self), cls=OPCUAEncoder)

    def to_python(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], json.loads(str(self)))


@dataclasses.dataclass
class OPCDataChangeMessage(BaseMessage):
    message_type = "opc_data_change"
    node_id: str
    data: ExtensionObject


@dataclasses.dataclass
class OPCStatusMessage(BaseMessage):
    message_type = "opc_status"
    data: bool


OPCMessage = Union[OPCDataChangeMessage, OPCStatusMessage]

if TYPE_CHECKING:
    OPCMessageQueue = asyncio.Queue[OPCMessage]
else:
    OPCMessageQueue = asyncio.Queue


class _Hub:
    def __init__(self) -> None:
        self._subscribers: Set[OPCMessageQueue] = set()
        self._last_datachange_message: Dict[str, OPCDataChangeMessage] = {}

    def publish(self, message: OPCMessage) -> None:
        if isinstance(message, OPCDataChangeMessage):
            self._last_datachange_message[message.node_id] = message
        if isinstance(message, OPCStatusMessage) and message.data is False:
            self._last_datachange_message = {}
        for queue in self._subscribers:
            queue.put_nowait(message)

    @contextmanager
    def subscribe(self) -> Iterator[OPCMessageQueue]:
        queue = OPCMessageQueue()
        for message in self._last_datachange_message.values():
            queue.put_nowait(message)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.remove(queue)


hub = _Hub()
