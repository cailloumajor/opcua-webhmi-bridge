from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Generator, List, Optional, Protocol

import pytest
import requests
import toml
from _pytest.fixtures import FixtureRequest
from yarl import URL

OPC_SERVER_HTTP_PORT = 8000


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
        mydir = Path(__file__).resolve().parent
        self.log_file = open(mydir / "opc_server.log", "w")
        self.root_url = URL.build(
            scheme="http", host="127.0.0.1", port=OPC_SERVER_HTTP_PORT
        )
        self.process = subprocess.Popen(
            [sys.executable, str(mydir / "opc_server.py"), str(OPC_SERVER_HTTP_PORT)],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        assert not self.ping(), "OPC-UA testing server already started"

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


@pytest.fixture(scope="session")
def opcserver() -> Generator[OPCServer, None, None]:
    opc_server = OPCServer()
    while not opc_server.ping():
        time.sleep(0.1)
    yield opc_server
    opc_server.process.terminate()
    opc_server.process.wait()
    opc_server.log_file.close()
