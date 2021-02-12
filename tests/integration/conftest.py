from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, Generator, List, Optional, Protocol

import pytest
import requests
from yarl import URL

OPC_SERVER_HOST = "opc-server"
OPC_SERVER_HTTP_PORT = 8080


@pytest.fixture
def mandatory_env_args(mandatory_env_args: Dict[str, str]) -> Dict[str, str]:
    return dict(
        mandatory_env_args,
        OPC_SERVER_URL=f"opc.tcp://{OPC_SERVER_HOST}:4840",
        OPC_MONITOR_NODES='["Monitored"]',
        OPC_RECORD_NODES='["Recorded"]',
    )


class MainProcessFixture(Protocol):
    def __call__(
        self,
        args: List[str],  # noqa: U100
        env: Optional[Dict[str, str]] = None,  # noqa: U100
    ) -> subprocess.Popen[str]:
        ...


@pytest.fixture
def main_process() -> Generator[MainProcessFixture, None, None]:
    sentinel: List[subprocess.Popen[str]] = []

    def _inner(
        args: List[str], env: Optional[Dict[str, str]] = None
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
            resp = requests.get(self._url("ping"))
            resp.raise_for_status()
        except requests.RequestException:
            return False
        else:
            return True

    def reset(self) -> None:
        resp = requests.delete(self._url("api"))
        resp.raise_for_status()

    def change_node(self, kind: str) -> None:
        resp = requests.post(self._url("api/node"), params={"kind": kind})
        resp.raise_for_status()

    def has_subscriptions(self) -> bool:
        resp = requests.get(self._url("api/subscriptions"))
        return bool(resp.json())


@pytest.fixture()
def opcserver() -> OPCServer:
    opc_server = OPCServer()
    while not opc_server.ping():
        time.sleep(0.1)
    opc_server.reset()
    return opc_server
