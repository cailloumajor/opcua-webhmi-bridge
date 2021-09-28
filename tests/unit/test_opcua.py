import asyncio
import contextlib
import logging
import time
from typing import Any, Callable, ContextManager, Iterator, List, Union, cast
from unittest.mock import AsyncMock, Mock

import pytest
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.ua import NodeId, ObjectIds
from pytest import LogCaptureFixture
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.messages import LinkStatus
from opcua_webhmi_bridge.opcua import (
    SIMATIC_NAMESPACE_URI,
    STATE_POLL_INTERVAL,
    OPCUAClient,
)

LogRecordsType = Callable[[], List[logging.LogRecord]]


class ExceptionForTestingError(Exception):
    pass


class InfiniteLoopBreakerError(Exception):
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
        monitor_nodes=["monitornode1", "monitornode2"],
        record_nodes=["recnode1", "recnode2"],
        record_interval=42,
        retry_delay=1234,
    )
    centrifugo_proxy_server = mocker.Mock(last_opc_status=None)
    return OPCUAClient(config, centrifugo_proxy_server, mocker.Mock(), mocker.Mock())


@pytest.fixture
def status_message_mock(mocker: MockerFixture) -> Mock:
    return mocker.patch("opcua_webhmi_bridge.opcua.OPCStatusMessage")


def test_status_initialized(opcua_client: OPCUAClient) -> None:
    assert opcua_client._status == LinkStatus.Down


@pytest.mark.parametrize(
    ["url", "expect_user_pass", "with_cert_file"],
    [
        ("//opc/server.url", False, False),
        ("//user:pass@opc/server.url", True, False),
        ("//opc/server.url", False, True),
        ("//user:pass@opc/server.url", True, True),
    ],
    ids=[
        "Without credentials and encryption",
        "With credentials",
        "With encryption",
        "With credentials and encryption",
    ],
)
def test_create_opc_client(
    event_loop: asyncio.AbstractEventLoop,
    expect_user_pass: bool,
    mocker: MockerFixture,
    url: str,
    with_cert_file: bool,
) -> None:
    mocked_asyncua_client = mocker.patch("asyncua.Client")
    mocked_asyncua_client.return_value.set_security = mocker.AsyncMock()
    config = mocker.Mock(server_url=url)
    if with_cert_file:
        config.configure_mock(cert_file="certFile", private_key_file="keyFile")
    else:
        config.configure_mock(cert_file=None, private_key_file=None)
    opcua_client = OPCUAClient(config, mocker.Mock(), mocker.Mock(), mocker.Mock())
    created_client = event_loop.run_until_complete(opcua_client._create_opc_client())
    assert mocked_asyncua_client.call_args_list == [mocker.call(url="//opc/server.url")]
    set_user = cast(Mock, created_client.set_user)
    set_password = cast(Mock, created_client.set_password)
    expected_set_user_call = []
    expected_set_pw_call = []
    expected_set_security_call = []
    if expect_user_pass:
        expected_set_user_call.append(mocker.call("user"))
        expected_set_pw_call.append(mocker.call("pass"))
    if with_cert_file:
        expected_set_security_call.append(
            mocker.call(SecurityPolicyBasic256Sha256, "certFile", "keyFile")
        )
    assert set_user.call_args_list == expected_set_user_call
    assert set_password.call_args_list == expected_set_pw_call
    assert created_client.set_security.await_args_list == expected_set_security_call


@pytest.mark.parametrize(
    "subscription_success",
    [True, False],
    ids=["Subscription success", "Subscription error"],
)
def test_subscribe(
    event_loop: asyncio.AbstractEventLoop,
    log_records: LogRecordsType,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
    subscription_success: bool,
) -> None:
    mocked_client = mocker.MagicMock()
    nsi = mocker.sentinel.nsi
    mocker.patch("opcua_webhmi_bridge.opcua.UaStatusCodeError", FakeUaStatusCodeError)
    mocked_client.create_subscription = mocker.AsyncMock()
    subscription = mocked_client.create_subscription.return_value
    sub_results: List[Union[int, FakeUaStatusCodeError]] = [12, 34]
    if not subscription_success:
        sub_results[-1] = FakeUaStatusCodeError()
    subscription.subscribe_data_change = mocker.AsyncMock(side_effect=sub_results)

    cm: ContextManager[Any]
    if subscription_success:
        cm = contextlib.suppress(InfiniteLoopBreakerError)
    else:
        cm = pytest.raises(FakeUaStatusCodeError)
    with cm:
        event_loop.run_until_complete(opcua_client._subscribe(mocked_client, nsi))

    assert mocked_client.create_subscription.await_args_list == [
        mocker.call(1000, opcua_client)
    ]
    get_node = cast(Mock, mocked_client.get_node)
    assert get_node.call_args_list == [
        mocker.call(NodeId("monitornode1", mocker.sentinel.nsi)),
        mocker.call(NodeId("monitornode2", mocker.sentinel.nsi)),
    ]
    assert subscription.subscribe_data_change.await_args_list == [
        mocker.call(get_node.return_value),
        mocker.call(get_node.return_value),
    ]
    if not subscription_success:
        last_log_record = log_records()[-1]
        assert last_log_record.levelno == logging.ERROR
        assert "Error subscribing to node" in last_log_record.message


def test_poll_status(
    event_loop: asyncio.AbstractEventLoop,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
) -> None:
    mocked_client = mocker.MagicMock()
    mocked_sleep = mocker.patch("asyncio.sleep")
    mocked_read_data_value = mocker.AsyncMock(
        side_effect=[None, None, InfiniteLoopBreakerError]
    )
    mocked_client.get_node.return_value.read_data_value = mocked_read_data_value

    with contextlib.suppress(InfiniteLoopBreakerError):
        event_loop.run_until_complete(opcua_client._poll_status(mocked_client))

    assert mocked_client.get_node.call_args_list == [
        mocker.call(ObjectIds.Server_ServerStatus_State)
    ]
    assert mocked_sleep.await_args_list == [
        mocker.call(STATE_POLL_INTERVAL),
        mocker.call(STATE_POLL_INTERVAL),
        mocker.call(STATE_POLL_INTERVAL),
    ]
    assert mocked_read_data_value.await_count == 3


def read_values_side_effect(
    *args: Any, **kwargs: Any  # noqa: U100
) -> Iterator[List[str]]:
    pass_count = 0
    while True:
        if pass_count == 0:
            yield ["val1", "val2"]
        elif pass_count == 1:
            time.sleep(0.5)
            yield ["val3", "val4"]
        elif pass_count >= 2:
            raise InfiniteLoopBreakerError()
        pass_count += 1


def test_poll_nodes(
    event_loop: asyncio.AbstractEventLoop,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
) -> None:
    mocked_client = mocker.MagicMock()
    mocked_nodes = [
        mocker.Mock(**{"nodeid.Identifier": mocker.sentinel.node1}),
        mocker.Mock(**{"nodeid.Identifier": mocker.sentinel.node2}),
    ]
    mocked_client.get_node.side_effect = mocked_nodes
    mocked_client.read_values = mocker.AsyncMock(side_effect=read_values_side_effect())
    data_message_mock = mocker.patch(
        "opcua_webhmi_bridge.opcua.OPCDataMessage",
        side_effect=[
            mocker.sentinel.message1,
            mocker.sentinel.message2,
            mocker.sentinel.message3,
            mocker.sentinel.message4,
        ],
    )
    mocked_sleep = mocker.patch("asyncio.sleep")

    with contextlib.suppress(InfiniteLoopBreakerError):
        event_loop.run_until_complete(
            opcua_client._poll_nodes(mocked_client, mocker.sentinel.nsi)
        )

    assert mocked_client.get_node.call_args_list == [
        mocker.call(NodeId("recnode1", mocker.sentinel.nsi)),
        mocker.call(NodeId("recnode2", mocker.sentinel.nsi)),
    ]
    assert mocked_client.read_values.await_args_list == [
        mocker.call(mocked_nodes),
        mocker.call(mocked_nodes),
        mocker.call(mocked_nodes),
    ]
    assert mocked_sleep.await_args_list == [
        mocker.call(pytest.approx(42, abs=0.1)),
        mocker.call(pytest.approx(41.5, abs=0.1)),
    ]
    assert data_message_mock.call_args_list == [
        mocker.call(mocker.sentinel.node1, "val1"),
        mocker.call(mocker.sentinel.node2, "val2"),
        mocker.call(mocker.sentinel.node1, "val3"),
        mocker.call(mocker.sentinel.node2, "val4"),
    ]
    mocked_influx_put = cast(Mock, opcua_client._influx_writer.put)
    assert mocked_influx_put.call_args_list == [
        mocker.call(mocker.sentinel.message1),
        mocker.call(mocker.sentinel.message2),
        mocker.call(mocker.sentinel.message3),
        mocker.call(mocker.sentinel.message4),
    ]


def test_task(
    event_loop: asyncio.AbstractEventLoop,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
) -> None:
    mocked_client = mocker.MagicMock()
    mocker.patch.object(opcua_client, "_create_opc_client", return_value=mocked_client)
    mocker.patch.object(opcua_client, "_subscribe")
    mocker.patch.object(opcua_client, "_poll_status")
    mocker.patch.object(
        opcua_client, "_poll_nodes", side_effect=ExceptionForTestingError
    )
    type_node = mocker.sentinel.type_node
    mocked_client.get_namespace_index = mocker.AsyncMock(
        return_value=mocker.sentinel.ns
    )
    mocked_client.nodes.opc_binary.get_child = mocker.AsyncMock(return_value=type_node)
    mocked_client.load_type_definitions = mocker.AsyncMock()

    with pytest.raises(ExceptionForTestingError):
        event_loop.run_until_complete(opcua_client._task())

    assert mocked_client.__aenter__.await_count == 1
    assert mocked_client.get_namespace_index.await_args_list == [
        mocker.call(SIMATIC_NAMESPACE_URI)
    ]
    assert mocked_client.nodes.opc_binary.get_child.await_args_list == [
        mocker.call("sentinel.ns:SimaticStructures")
    ]
    assert mocked_client.load_type_definitions.await_args_list == [
        mocker.call([type_node])
    ]
    mocked_subscribe = cast(AsyncMock, opcua_client._subscribe)
    assert mocked_subscribe.await_args_list == [
        mocker.call(mocked_client, mocker.sentinel.ns)
    ]
    mocked_poll_status = cast(AsyncMock, opcua_client._poll_status)
    assert mocked_poll_status.await_args_list == [mocker.call(mocked_client)]
    mocked_poll_nodes = cast(AsyncMock, opcua_client._poll_nodes)
    assert mocked_poll_nodes.await_args_list == [
        mocker.call(mocked_client, mocker.sentinel.ns)
    ]


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
        status_message_mock: Mock,
        mocker: MockerFixture,
        new_status: LinkStatus,
        opcua_client: OPCUAClient,
    ) -> None:
        opcua_client._status = new_status
        opcua_client.set_status(new_status)
        clear_last_opc_data = cast(
            Mock, opcua_client._centrifugo_proxy_server.clear_last_opc_data
        )
        assert clear_last_opc_data.call_count == clear_last_opc_data_call_count
        assert status_message_mock.call_args_list == [mocker.call(payload=new_status)]
        assert (
            opcua_client._centrifugo_proxy_server.last_opc_status
            == status_message_mock.return_value
        )

    def test_status_changed(
        self,
        status_message_mock: Mock,
        mocker: MockerFixture,
        opcua_client: OPCUAClient,
    ) -> None:
        opcua_client.set_status(LinkStatus.Up)
        assert opcua_client._status == LinkStatus.Up
        messaging_writer_put = cast(Mock, opcua_client._frontend_messaging_writer.put)
        assert messaging_writer_put.call_args_list == [
            mocker.call(status_message_mock.return_value)
        ]
        assert status_message_mock.call_args_list == [
            mocker.call(payload=LinkStatus.Up)
        ]


def test_datachange_notification(
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
) -> None:
    data_change_message_mock = mocker.patch("opcua_webhmi_bridge.opcua.OPCDataMessage")
    node = mocker.Mock()
    node.configure_mock(**{"nodeid.Identifier": "monitornode1"})
    value = mocker.sentinel.value
    mocker.patch.object(opcua_client, "set_status")
    opcua_client.datachange_notification(node, value, mocker.Mock())
    set_status = cast(Mock, opcua_client.set_status)
    message_instance = data_change_message_mock.return_value
    assert set_status.call_args_list == [mocker.call(LinkStatus.Up)]
    assert data_change_message_mock.call_args_list == [
        mocker.call(node_id="monitornode1", ua_object=value)
    ]
    record_last_opc_data = cast(
        Mock, opcua_client._centrifugo_proxy_server.record_last_opc_data
    )
    assert record_last_opc_data.call_args_list == [mocker.call(message_instance)]
    messaging_writer_put = cast(Mock, opcua_client._frontend_messaging_writer.put)
    assert messaging_writer_put.call_args_list == [mocker.call(message_instance)]
    influx_writer_put = cast(Mock, opcua_client._influx_writer.put)
    influx_writer_put.assert_not_called()


def test_before_sleep(
    log_records: LogRecordsType,
    mocker: MockerFixture,
    opcua_client: OPCUAClient,
) -> None:
    retry_call_state = mocker.Mock()
    retry_call_state.configure_mock(
        **{
            "outcome.exception.return_value": ExceptionForTestingError(
                "exception text"
            ),
            "next_action.sleep": 42,
        }
    )
    mocker.patch.object(opcua_client, "set_status")
    opcua_client.before_sleep(retry_call_state)
    set_status = cast(Mock, opcua_client.set_status)
    assert set_status.call_args_list == [mocker.call(LinkStatus.Down)]
    last_log_record = log_records()[-1]
    assert last_log_record.levelno == logging.INFO
    assert "Retrying OPC client task" in last_log_record.message
    assert "42 seconds" in last_log_record.message
    assert "ExceptionForTestingError: exception text" in last_log_record.message


def test_task_wrapper(
    event_loop: asyncio.AbstractEventLoop,
    opcua_client: OPCUAClient,
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(opcua_client, "_task")
    async_retrying = mocker.patch(
        "tenacity.AsyncRetrying", return_value=mocker.AsyncMock()
    )
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
    call_method = cast(AsyncMock, async_retrying.return_value)
    assert call_method.await_args_list == [mocker.call(opcua_client._task)]
