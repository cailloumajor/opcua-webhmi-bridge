import json
import time
from datetime import datetime
from enum import IntEnum
from typing import Any, Generator

import pytest
import requests
import websocket
from yarl import URL

from .conftest import MainProcessFixture, OPCServer

CENTRIFUGO_HOST = "centrifugo"


class Method(IntEnum):
    CONNECT = 0
    SUBSCRIBE = 1


class CentrifugoServer:
    def __init__(self) -> None:
        self.root_url = URL(f"http://{CENTRIFUGO_HOST}:8000")

    @property
    def api_url(self) -> URL:
        return self.root_url / "api"

    @property
    def ping_url(self) -> str:
        return str(self.root_url / "health")

    def ping(self) -> bool:
        try:
            resp = requests.get(self.ping_url, timeout=1)
            resp.raise_for_status()
        except requests.RequestException:
            return False
        else:
            return True

    def _api_send(self, method: str, data: dict[str, Any]) -> Any:
        url = str(self.api_url / method)
        headers = {"X-API-Key": "apikey"}
        resp = requests.post(url, headers=headers, json=data, timeout=1)
        resp.raise_for_status()
        return resp.json()

    def history_remove(self, channel: str) -> None:
        data = {"channel": channel}
        self._api_send("history_remove", data)

    def history(self, channel: str) -> Any:
        data = {"channel": channel, "limit": 10}
        return self._api_send("history", data)


@pytest.fixture
def centrifugo_server() -> CentrifugoServer:
    server = CentrifugoServer()
    start_time = datetime.now()
    while not server.ping():
        elapsed = datetime.now() - start_time
        assert (
            elapsed.total_seconds() < 30
        ), "Timeout waiting for Centrifugo server to be ready"
        time.sleep(1.0)
    server.history_remove("heartbeat")
    server.history_remove("proxied:opc_data")
    server.history_remove("proxied:opc_status")
    return server


class CentrifugoClient:
    def __init__(self, url: URL) -> None:
        self._command_id = 0
        self.client = websocket.WebSocket()
        self.client.connect(str(url))
        self._send_command(Method.CONNECT, {})

    def _send_command(self, method: Method, params: dict[str, Any]) -> None:
        self._command_id += 1
        command_data = {
            "id": self._command_id,
            "method": method.value,
            "params": params,
        }
        self.client.send(json.dumps(command_data))
        reply_resp = json.loads(self.client.recv())
        assert reply_resp["id"] == self._command_id, "Bad Centrifugo reply id"
        assert reply_resp.get("error") is None, "Centrifugo command failed"

    def subscribe(self, channel: str) -> None:
        self._send_command(Method.SUBSCRIBE, {"channel": channel})


@pytest.fixture
def centrifugo_client(
    centrifugo_server: CentrifugoServer,
) -> Generator[CentrifugoClient, None, None]:
    client = CentrifugoClient(
        centrifugo_server.root_url.with_scheme("ws") / "connection/websocket"
    )
    yield client
    client.client.close()


def test_smoketest(
    centrifugo_client: CentrifugoClient,
    centrifugo_server: CentrifugoServer,
    main_process: MainProcessFixture,
    mandatory_env_args: dict[str, str],
    opcserver: OPCServer,
) -> None:
    def ping_main_process() -> bool:
        url = "http://localhost:8008/centrifugo/subscribe"
        data = {"channel": "heartbeat"}
        try:
            resp = requests.post(url, json=data, timeout=1)
            resp.raise_for_status()
        except requests.RequestException:
            return False
        else:
            return True

    envargs = dict(
        mandatory_env_args,
        CENTRIFUGO_API_KEY="apikey",
        CENTRIFUGO_API_URL=str(centrifugo_server.api_url),
    )
    process = main_process([], envargs)
    start_time = datetime.now()
    while not (ping_main_process() and opcserver.has_subscriptions()):
        elapsed = datetime.now() - start_time
        assert (
            elapsed.total_seconds() < 20
        ), "Timeout waiting for Centrifugo subscribe proxy"
        time.sleep(1.0)
        assert process.poll() is None
    centrifugo_client.subscribe("proxied:opc_data")
    centrifugo_client.subscribe("proxied:opc_status")
    centrifugo_client.subscribe("heartbeat")
    start_time = datetime.now()
    while not centrifugo_server.history("heartbeat")["result"]["publications"]:
        elapsed = datetime.now() - start_time
        assert (
            elapsed.total_seconds() < 10
        ), "Timeout waiting for heartbeat channel to have publication"
        time.sleep(1.0)
    opcserver.change_node("monitored")
    time.sleep(1.0)

    def publication_length(channel: str) -> int:
        history = centrifugo_server.history(channel)
        return len(history["result"]["publications"])

    assert publication_length("proxied:opc_data") == 2
    assert publication_length("proxied:opc_status") == 2
