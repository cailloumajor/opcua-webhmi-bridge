import asyncio
import contextlib
from typing import Any, Awaitable, Callable, Dict, cast
from unittest.mock import Mock as MockType

import aiohttp
import pytest
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectorError
from aiohttp.test_utils import RawTestServer, TestClient, unused_port
from aiohttp.web_urldispatcher import _WebHandler
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.frontend_messaging import CentrifugoProxyServer
from opcua_webhmi_bridge.messages import MessageType

ClientFixture = Callable[[RawTestServer], Awaitable[TestClient]]
RawServerFixture = Callable[[_WebHandler], Awaitable[RawTestServer]]


@pytest.fixture
def server_port() -> int:
    return unused_port()


@pytest.fixture
def proxy_server(mocker: MockerFixture, server_port: int) -> CentrifugoProxyServer:
    config = mocker.Mock(proxy_host="127.0.0.1", proxy_port=server_port)
    return CentrifugoProxyServer(config, mocker.Mock())


def test_opc_status_init(proxy_server: CentrifugoProxyServer) -> None:
    assert str(proxy_server.last_opc_status.payload) == "LinkStatus.Down"


def test_last_opc_data(
    mocker: MockerFixture,
    proxy_server: CentrifugoProxyServer,
) -> None:
    assert proxy_server._last_opc_data == {}
    message = mocker.Mock(node_id="test_id")
    proxy_server.record_last_opc_data(message)
    assert proxy_server._last_opc_data == {"test_id": message}
    proxy_server.clear_last_opc_data()
    assert proxy_server._last_opc_data == {}


class TestCentrifugoSubscribe:
    @pytest.mark.parametrize(
        ["json", "exp_status", "exp_reason"],
        [
            (None, 500, "JSON decode error"),
            ([], 400, "Bad request format"),
        ],
        ids=[
            "Bad JSON",
            "Bad format",
        ],
    )
    async def test_http_error(
        self,
        aiohttp_client: ClientFixture,
        aiohttp_raw_server: RawServerFixture,
        exp_status: int,
        exp_reason: str,
        json: Any,
        proxy_server: CentrifugoProxyServer,
    ) -> None:
        server = await aiohttp_raw_server(proxy_server.centrifugo_subscribe)
        client = await aiohttp_client(server)
        response = await client.post("/", json=json)
        assert response.status == exp_status
        assert response.reason == exp_reason

    @pytest.mark.parametrize(
        ["json", "expected_error"],
        [
            ({}, {"code": 1000, "message": "Missing channel field"}),
            ({"channel": "badchannel"}, {"code": 1001, "message": "Unknown channel"}),
        ],
        ids=[
            "Missing channel",
            "Unknown channel",
        ],
    )
    async def test_centrifugo_error(
        self,
        aiohttp_client: ClientFixture,
        aiohttp_raw_server: RawServerFixture,
        expected_error: Dict[str, Any],
        json: Dict[str, Any],
        proxy_server: CentrifugoProxyServer,
    ) -> None:
        server = await aiohttp_raw_server(proxy_server.centrifugo_subscribe)
        client = await aiohttp_client(server)
        response = await client.post("/", json=json)
        assert response.status == 200
        response_json = await response.json()
        assert response_json["error"] == expected_error

    async def test_opc_data_change(
        self,
        aiohttp_client: ClientFixture,
        aiohttp_raw_server: RawServerFixture,
        mocker: MockerFixture,
        proxy_server: CentrifugoProxyServer,
    ) -> None:
        messages = [mocker.Mock(f"message_{i}") for i in range(5)]
        proxy_server._last_opc_data = {
            str(index): value for index, value in enumerate(messages)
        }
        server = await aiohttp_raw_server(proxy_server.centrifugo_subscribe)
        client = await aiohttp_client(server)
        await client.post("/", json={"channel": "opc_data_change"})
        put = cast(MockType, proxy_server._messaging_writer.put)
        expected_calls = [((m,),) for m in messages]
        assert put.call_args_list == expected_calls

    async def test_opc_status(
        self,
        aiohttp_client: ClientFixture,
        aiohttp_raw_server: RawServerFixture,
        mocker: MockerFixture,
        proxy_server: CentrifugoProxyServer,
    ) -> None:
        status_message = mocker.Mock()
        proxy_server.last_opc_status = status_message
        server = await aiohttp_raw_server(proxy_server.centrifugo_subscribe)
        client = await aiohttp_client(server)
        await client.post("/", json={"channel": "opc_status"})
        put = cast(MockType, proxy_server._messaging_writer.put)
        put.assert_called_once_with(status_message)

    @pytest.mark.parametrize("message_type", MessageType)
    async def test_known_channel(
        self,
        aiohttp_client: ClientFixture,
        aiohttp_raw_server: RawServerFixture,
        message_type: MessageType,
        proxy_server: CentrifugoProxyServer,
    ) -> None:
        server = await aiohttp_raw_server(proxy_server.centrifugo_subscribe)
        client = await aiohttp_client(server)
        response = await client.post("/", json={"channel": message_type.value})
        assert response.status == 200
        assert await response.json() == {"result": {}}


@pytest.mark.asyncio
async def test_task(
    event_loop: asyncio.AbstractEventLoop,
    mocker: MockerFixture,
    proxy_server: CentrifugoProxyServer,
    server_port: int,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=await request.text())

    mocker.patch.object(proxy_server, "centrifugo_subscribe", handler)

    task = event_loop.create_task(proxy_server.task())

    reached = False

    while not reached:
        try:
            async with aiohttp.request(
                "POST",
                f"http://127.0.0.1:{server_port}/centrifugo/subscribe",
                data="Request text",
            ) as resp:
                assert resp.status == 200
                assert await resp.text() == "Request text"
        except ClientConnectorError:
            await asyncio.sleep(0.1)
        else:
            reached = True

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
