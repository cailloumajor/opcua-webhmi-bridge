from dataclasses import dataclass
from unittest.mock import Mock

import pytest

from opcua_webhmi_bridge.messages import BaseMessage, MessageType, OPCDataMessage


@dataclass
class MessageForTesting(BaseMessage):
    message_type = Mock()
    numeric_field: int
    text_field: str


@dataclass
class SubType1:
    field1: str
    field2: int
    ua_types = [("field1", "String"), ("field2", "Int")]


@dataclass
class SubType2:
    field1: bool
    field2: SubType1
    ua_types = [("field1", "Bool"), ("field2", "SubType1")]


@dataclass
class RootType:
    field1: SubType1
    field2: SubType2
    field3: bool
    ua_types = [("field1", "SubType1"), ("field2", "SubType2"), ("field3", "Bool")]


@pytest.mark.parametrize(
    ["message_type", "expected"],
    [
        (MessageType.OPC_DATA, "proxied:opc_data"),
        (MessageType.OPC_STATUS, "proxied:opc_status"),
        (MessageType.HEARTBEAT, "heartbeat"),
    ],
)
def test_messagetype_centrifugo_channel(
    message_type: MessageType, expected: str
) -> None:
    assert message_type.centrifugo_channel == expected


def test_message_frontend_data() -> None:
    message = MessageForTesting(42, "test")
    assert message.frontend_data == {
        "numeric_field": 42,
        "text_field": "test",
    }


def test_opc_data_conversion() -> None:
    opc_data = [
        RootType(
            SubType1("abcd", 1),
            SubType2(False, SubType1("efgh", 2)),
            True,
        ),
        RootType(
            SubType1("ijkl", 3),
            SubType2(True, SubType1("mnop", 4)),
            False,
        ),
    ]
    message = OPCDataMessage("test_node", opc_data)
    assert message.payload == [
        {
            "field1": {"field1": "abcd", "field2": 1},
            "field2": {"field1": False, "field2": {"field1": "efgh", "field2": 2}},
            "field3": True,
        },
        {
            "field1": {"field1": "ijkl", "field2": 3},
            "field2": {"field1": True, "field2": {"field1": "mnop", "field2": 4}},
            "field3": False,
        },
    ]
