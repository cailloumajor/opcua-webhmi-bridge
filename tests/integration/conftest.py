from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, List, Optional, Protocol

import pytest
import requests
import toml
from _pytest.fixtures import FixtureRequest
from yarl import URL

OPC_SERVER_HOST = "opc-server"


class MainProcessFixture(Protocol):
    def __call__(
        self,
        args: List[str],  # noqa: U100
        env: Optional[Dict[str, str]] = None,  # noqa: U100
    ) -> subprocess.Popen[str]:
        ...


@pytest.fixture(scope="session")
def console_script(request: FixtureRequest) -> str:
    pyproject = toml.load(request.config.rootpath / "pyproject.toml")
    scripts: Dict[str, str] = pyproject["tool"]["poetry"]["scripts"]
    for script, function in scripts.items():
        if "main:app" in function:
            return script
    raise ValueError("Console script not found in pyproject.toml")


@pytest.fixture
def main_process(console_script: str) -> MainProcessFixture:
    def _inner(
        args: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.Popen[str]:
        args = [console_script] + args
        if env is not None:
            env = dict(os.environ, **env)
        return subprocess.Popen(args, env=env, text=True)

    return _inner


class OPCServer:
    def __init__(self) -> None:
        self.root_url = URL(f"http://{OPC_SERVER_HOST}")

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


@pytest.fixture(scope="session")
def opcserver() -> OPCServer:
    opc_server = OPCServer()
    while not opc_server.ping():
        time.sleep(0.1)
    return opc_server
