import asyncio
import contextlib
import logging
from typing import Any, Callable, ContextManager, List, cast
from unittest.mock import AsyncMock as AsyncMockType
from unittest.mock import Mock as MockType

import pytest
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from pytest import LogCaptureFixture
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.messages import LinkStatus
from opcua_webhmi_bridge.opcua import SIMATIC_NAMESPACE_URI, OPCUAClient

LogRecordsType = Callable[[], List[logging.LogRecord]]


class ExceptionForTesting(Exception):
    pass


class InfiniteLoopBreaker(Exception):
    pass


class FakeUaStatusCodeError(Exception):
    pass


@pytest.fixture
def log_records(caplog: LogCaptureFixture) -> LogRecordsType:
    caplog.set_level(logging.INFO)

    def _inner() -> List[logging.LogRecord]:
        return list(
            filter(
                lambda r: r.name == OPCUAClient.logger.name,
                caplog.records,
            )
        )

    return _inner


@pytest.fixture
def opcua_client(mocker: MockerFixture) -> OPCUAClient:
    config = mocker.Mock(
        cert_file="certFile",
        private_key_file="keyFile",
        monitor_nodes=["monitornode1", "monitornode2"],
        record_nodes=["recnode1", "recnode2"],
        retry_delay=1234,
    )
    centrifugo_proxy_server = mocker.Mock(last_opc_status=None)
    return OPCUAClient(config, centrifugo_proxy_server, mocker.Mock(), mocker.Mock())


@pytest.fixture
def status_message_mock(mocker: MockerFixture) -> MockType:
    return mocker.patch("opcua_webhmi_bridge.opcua.OPCStatusMessage")


def test_status_initialized(opcua_client: OPCUAClient) -> None:
    assert opcua_client._status == LinkStatus.Down


@pytest.mark.parametrize(
    ["url", "expect_user_pass"],
    [
        ("//opc/server.url", False),
        ("//user:pass@opc/server.url", True),
    ],
    ids=["Without user & password", "With user & password"],
)
def test_create_opc_client(
    expect_user_pass: bool, mocker: MockerFixture, url: str
) -> None:
    mocked_asyncua_client = mocker.patch("asyncua.Client")
    config = mocker.Mock(server_url=url)
    opcua_client = OPCUAClient(config, mocker.Mock(), mocker.Mock(), mocker.Mock())
    created_client = opcua_client._create_opc_client()
    assert mocked_asyncua_client.call_args_list == [mocker.call(url="//opc/server.url")]
    set_user = cast(MockType, created_client.set_user)
    set_password = cast(MockType, created_client.set_password)
    expected_set_user_call = []
    expected_set_pw_call = []
    if expect_user_pass:
        expected_set_user_call.append(mocker.call("user"))
        expected_set_pw_call.append(mocker.call("pass"))
    assert set_user.call_args_list == expected_set_user_call
    assert set_password.call_args_list == expected_set_pw_call


@pytest.mark.parametrize(
    ["subscription_success"],
    [
        (True,),
        (False,),
    ],
    ids=["Subscription success", "Subscription failure"],
)
def test_task(
    event_loop: asyncio.AbstractEventLoop,
    log_records: LogRecordsType,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
    subscription_success: bool,
) -> None:
    mocked_client = mocker.patch.object(opcua_client, "_create_opc_client").return_value
    mocker.patch("opcua_webhmi_bridge.opcua.UaStatusCodeError", FakeUaStatusCodeError)
    type_node = mocker.sentinel.type_node
    mocked_client.set_security = mocker.AsyncMock()
    mocked_client.get_namespace_index = mocker.AsyncMock(
        return_value=mocker.sentinel.ns
    )
    mocked_client.nodes.opc_binary.get_child = mocker.AsyncMock(return_value=type_node)
    mocked_client.load_type_definitions = mocker.AsyncMock()
    mocked_client.create_subscription = mocker.AsyncMock()
    subscription = mocked_client.create_subscription.return_value
    sub_results = [12, 34, 56, 78, 910]
    if not subscription_success:
        sub_results[-2] = mocker.Mock(**{"check.side_effect": FakeUaStatusCodeError})
    subscription.subscribe_data_change = mocker.AsyncMock(return_value=sub_results)
    mocked_sleep: AsyncMockType = mocker.patch("asyncio.sleep")
    gotten_node = mocked_client.get_node.return_value
    read_data_value = gotten_node.read_data_value = mocker.AsyncMock(
        side_effect=InfiniteLoopBreaker
    )
    cm: ContextManager[Any]
    if subscription_success:
        cm = contextlib.suppress(InfiniteLoopBreaker)
    else:
        cm = pytest.raises(FakeUaStatusCodeError)
    with cm:
        event_loop.run_until_complete(opcua_client._task())
    assert mocked_client.__aenter__.await_count == 1
    assert mocked_client.set_security.await_args_list == [
        mocker.call(SecurityPolicyBasic256Sha256, "certFile", "keyFile")
    ]
    assert mocked_client.get_namespace_index.await_args_list == [
        mocker.call(SIMATIC_NAMESPACE_URI)
    ]
    assert mocked_client.nodes.opc_binary.get_child.await_args_list == [
        mocker.call("sentinel.ns:SimaticStructures")
    ]
    assert mocked_client.load_type_definitions.await_args_list == [
        mocker.call([type_node])
    ]
    get_node = cast(MockType, mocked_client.get_node)
    expected_get_node_calls = [
        mocker.call("ns=sentinel.ns;s=monitornode1"),
        mocker.call("ns=sentinel.ns;s=monitornode2"),
        mocker.call("ns=sentinel.ns;s=recnode1"),
        mocker.call("ns=sentinel.ns;s=recnode2"),
    ]
    if subscription_success:
        expected_get_node_calls.append(mocker.call(2259))
    assert get_node.call_args_list == expected_get_node_calls
    assert mocked_client.create_subscription.await_args_list == [
        mocker.call(1000, opcua_client)
    ]
    assert subscription.subscribe_data_change.await_args_list == [
        mocker.call([gotten_node, gotten_node, gotten_node, gotten_node])
    ]
    if subscription_success:
        assert mocked_sleep.await_args_list == [mocker.call(5)]
        assert read_data_value.await_args_list == [mocker.call()]
    else:
        last_log_record = log_records()[-1]
        assert last_log_record.levelno == logging.ERROR
        assert "Error subscribing to node" in last_log_record.message


class TestSetStatus:
    @pytest.mark.parametrize(
        ["new_status", "clear_last_opc_data_call_count"],
        [
            (LinkStatus.Down, 1),
            (LinkStatus.Up, 0),
        ],
        ids=[
            LinkStatus.Down,
            LinkStatus.Up,
        ],
    )
    def test_same_status(
        self,
        clear_last_opc_data_call_count: int,
        status_message_mock: MockType,
        mocker: MockerFixture,
        new_status: LinkStatus,
        opcua_client: OPCUAClient,
    ) -> None:
        opcua_client._status = new_status
        opcua_client.set_status(new_status)
        clear_last_opc_data = cast(
            MockType, opcua_client._centrifugo_proxy_server.clear_last_opc_data
        )
        assert clear_last_opc_data.call_count == clear_last_opc_data_call_count
        assert status_message_mock.call_args_list == [mocker.call(payload=new_status)]
        assert (
            opcua_client._centrifugo_proxy_server.last_opc_status
            == status_message_mock.return_value
        )

    def test_status_changed(
        self,
        status_message_mock: MockType,
        mocker: MockerFixture,
        opcua_client: OPCUAClient,
    ) -> None:
        opcua_client.set_status(LinkStatus.Up)
        assert opcua_client._status == LinkStatus.Up
        messaging_writer_put = cast(
            MockType, opcua_client._frontend_messaging_writer.put
        )
        assert messaging_writer_put.call_args_list == [
            mocker.call(status_message_mock.return_value)
        ]
        assert status_message_mock.call_args_list == [
            mocker.call(payload=LinkStatus.Up)
        ]


@pytest.mark.parametrize(
    ["node_id", "influx_write"],
    [
        ("monitornode1", False),
        ("recnode1", True),
    ],
    ids=[
        "Not recorded node",
        "Recorded node",
    ],
)
def test_datachange_notification(
    influx_write: bool,
    mocker: MockerFixture,
    node_id: str,
    opcua_client: OPCUAClient,
) -> None:
    data_change_message_mock = mocker.patch(
        "opcua_webhmi_bridge.opcua.OPCDataChangeMessage"
    )
    node = mocker.Mock()
    node.configure_mock(**{"nodeid.Identifier": node_id})
    value = mocker.sentinel.value
    mocker.patch.object(opcua_client, "set_status")
    opcua_client.datachange_notification(node, value, mocker.Mock())
    set_status = cast(MockType, opcua_client.set_status)
    message_instance = data_change_message_mock.return_value
    assert set_status.call_args_list == [mocker.call(LinkStatus.Up)]
    assert data_change_message_mock.call_args_list == [
        mocker.call(node_id=node_id, ua_object=value)
    ]
    record_last_opc_data = cast(
        MockType, opcua_client._centrifugo_proxy_server.record_last_opc_data
    )
    assert record_last_opc_data.call_args_list == [mocker.call(message_instance)]
    messaging_writer_put = cast(MockType, opcua_client._frontend_messaging_writer.put)
    assert messaging_writer_put.call_args_list == [mocker.call(message_instance)]
    influx_writer_put = cast(MockType, opcua_client._influx_writer.put)
    expected_influx_put_call = [mocker.call(message_instance)] if influx_write else []
    assert influx_writer_put.call_args_list == expected_influx_put_call


def test_before_sleep(
    log_records: LogRecordsType,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
) -> None:
    retry_call_state = mocker.Mock()
    retry_call_state.configure_mock(
        **{
            "outcome.exception.return_value": ExceptionForTesting("exception text"),
            "next_action.sleep": 42,
        }
    )
    mocker.patch.object(opcua_client, "set_status")
    opcua_client.before_sleep(retry_call_state)
    set_status = cast(MockType, opcua_client.set_status)
    assert set_status.call_args_list == [mocker.call(LinkStatus.Down)]
    last_log_record = log_records()[-1]
    assert last_log_record.levelno == logging.INFO
    assert "Retrying OPC client task" in last_log_record.message
    assert "42 seconds" in last_log_record.message
    assert "ExceptionForTesting: exception text" in last_log_record.message


def test_task_wrapper(
    event_loop: asyncio.AbstractEventLoop,
    opcua_client: OPCUAClient,
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(opcua_client, "_task")
    async_retrying = mocker.patch("tenacity.AsyncRetrying", autospec=True)
    wait_fixed = mocker.patch("tenacity.wait_fixed")
    retry_if_exception_type = mocker.patch("tenacity.retry_if_exception_type")
    event_loop.run_until_complete(opcua_client.task())
    assert async_retrying.call_args_list == [
        mocker.call(
            wait=wait_fixed.return_value,
            retry=retry_if_exception_type.return_value.__or__.return_value,
            before_sleep=opcua_client.before_sleep,
        )
    ]
    assert wait_fixed.call_args_list == [mocker.call(1234)]
    assert retry_if_exception_type.call_args_list == [
        mocker.call(OSError),
        mocker.call(asyncio.TimeoutError),
    ]
    call_method = cast(MockType, async_retrying.return_value.call)
    assert call_method.call_args_list == [mocker.call(opcua_client._task)]
