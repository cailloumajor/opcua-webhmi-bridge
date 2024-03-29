import os
import subprocess
import time
from datetime import datetime
from typing import Generator, Optional, Protocol

import pytest
import requests
from _pytest.fixtures import FixtureRequest
from yarl import URL

OPC_SERVER_HOST = "opc-server"
OPC_SERVER_HTTP_PORT = 8080


@pytest.fixture
def mandatory_env_args(
    mandatory_env_args: dict[str, str], request: FixtureRequest
) -> dict[str, str]:
    dirpath = request.fspath.dirpath()
    return dict(
        mandatory_env_args,
        OPC_SERVER_URL=f"opc.tcp://authorized_user:authorized_password@{OPC_SERVER_HOST}:4840",
        OPC_CERT_FILE=dirpath.join("test-client-cert.der").strpath,
        OPC_PRIVATE_KEY_FILE=dirpath.join("test-client-key.pem").strpath,
        OPC_MONITOR_NODES='["Monitored"]',
        OPC_RECORD_NODES='["Recorded"]',
    )


class MainProcessFixture(Protocol):
    def __call__(
        self,
        args: list[str],  # noqa: U100
        env: Optional[dict[str, str]] = None,  # noqa: U100
    ) -> subprocess.Popen[str]:
        ...


@pytest.fixture
def main_process() -> Generator[MainProcessFixture, None, None]:
    sentinel: list[subprocess.Popen[str]] = []

    def _inner(
        args: list[str], env: Optional[dict[str, str]] = None
    ) -> subprocess.Popen[str]:
        args = ["opcua-agent"] + args
        if env is not None:
            env = dict(os.environ, **env)
        process = subprocess.Popen(args, env=env, text=True)
        sentinel.append(process)
        return process

    yield _inner

    for process in sentinel:
        process.terminate()
        process.wait(timeout=5.0)


class OPCServer:
    def __init__(self) -> None:
        self.root_url = URL(f"http://{OPC_SERVER_HOST}:{OPC_SERVER_HTTP_PORT}")

    def _url(self, endpoint: str) -> str:
        return str(self.root_url / endpoint)

    def ping(self) -> bool:
        try:
            resp = requests.get(self._url("ping"), timeout=1)
            resp.raise_for_status()
        except requests.RequestException:
            return False
        else:
            return True

    def reset(self) -> None:
        resp = requests.delete(self._url("api"), timeout=1)
        resp.raise_for_status()

    def change_node(self, kind: str) -> None:
        resp = requests.post(self._url("api/node"), params={"kind": kind}, timeout=1)
        resp.raise_for_status()

    def has_subscriptions(self) -> bool:
        resp = requests.get(self._url("api/subscriptions"), timeout=1)
        return bool(resp.json())


@pytest.fixture()
def opcserver() -> OPCServer:
    opc_server = OPCServer()
    start_time = datetime.now()
    while not opc_server.ping():
        elapsed = datetime.now() - start_time
        assert elapsed.total_seconds() < 30, "Timeout trying to ping OPC-UA server"
        time.sleep(1.0)
    opc_server.reset()
    return opc_server
