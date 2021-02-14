import json
import time
from datetime import datetime
from typing import Any, Dict, Generator

import pytest
import requests
import websocket
from yarl import URL

from .conftest import MainProcessFixture

CENTRIFUGO_HOST = "centrifugo"


class CentrifugoServer:
    def __init__(self) -> None:
        self.root_url = URL(f"http://{CENTRIFUGO_HOST}:8000")

    def url(self, endpoint: str) -> str:
        return str(self.root_url / endpoint)

    def ping(self) -> bool:
        try:
            resp = requests.get(self.url("health"))
            resp.raise_for_status()
        except requests.RequestException:
            return False
        else:
            return True

    def history(self, channel: str) -> Any:
        headers = {"Authorization": "apikey apikey"}
        data = {"method": "history", "params": {"channel": channel}}
        resp = requests.post(self.url("api"), headers=headers, json=data)
        resp.raise_for_status()
        return resp.json()


@pytest.fixture
def centrifugo_server() -> CentrifugoServer:
    server = CentrifugoServer()
    while not server.ping():
        time.sleep(0.1)
    return server


class CentrifugoClient:
    def __init__(self, url: URL) -> None:
        self._command_id = 0
        self.client = websocket.WebSocket()
        self.client.connect(str(url))
        self._send_command("connect", {})

    def _send_command(self, method: str, params: Dict[str, Any]) -> None:
        self._command_id += 1
        command_data = {"id": self._command_id, "method": method, "params": params}
        self.client.send(json.dumps(command_data))
        reply_resp = json.loads(self.client.recv())
        assert reply_resp["id"] == self._command_id, "Bad Centrifugo reply id"
        assert reply_resp.get("error") is None, "Centrifugo command failed"

    def subscribe(self, channel: str) -> None:
        self._send_command("subscribe", {"channel": channel})


@pytest.fixture
def centrifugo_client(
    centrifugo_server: CentrifugoServer,
) -> Generator[CentrifugoClient, None, None]:
    client = CentrifugoClient(
        centrifugo_server.root_url.with_scheme("ws") / "connection/websocket"
    )
    yield client
    client.client.close()


@pytest.mark.usefixtures("opcserver")
def test_smoketest(
    centrifugo_client: CentrifugoClient,
    centrifugo_server: CentrifugoServer,
    main_process: MainProcessFixture,
    mandatory_env_args: Dict[str, str],
) -> None:
    def ping_main_process() -> bool:
        url = "http://localhost:8008/centrifugo/subscribe"
        data = {"channel": "heartbeat"}
        try:
            resp = requests.post(url, json=data)
            resp.raise_for_status()
        except requests.RequestException:
            return False
        else:
            return True

    envargs = dict(
        mandatory_env_args,
        CENTRIFUGO_API_KEY="apikey",
        CENTRIFUGO_API_URL=centrifugo_server.url("api"),
    )
    process = main_process([], envargs)
    start_time = datetime.now()
    while not ping_main_process():
        elapsed = datetime.now() - start_time
        assert elapsed.total_seconds() < 10
        time.sleep(1.0)
        assert process.poll() is None
    centrifugo_client.subscribe("opc_data_change")
    centrifugo_client.subscribe("opc_status")
    centrifugo_client.subscribe("heartbeat")
    start_time = datetime.now()
    while not centrifugo_server.history("heartbeat")["result"]["publications"]:
        elapsed = datetime.now() - start_time
        assert elapsed.total_seconds() < 10
        time.sleep(1.0)
    assert (
        len(centrifugo_server.history("opc_data_change")["result"]["publications"]) == 2
    )
