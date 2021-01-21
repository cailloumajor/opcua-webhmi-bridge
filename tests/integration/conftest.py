from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Generator, List, Optional, Protocol, TextIO

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
def tests_path() -> Path:
    return Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def console_script(request: FixtureRequest) -> str:
    pyproject = toml.load(request.config.rootpath / "pyproject.toml")
    scripts: Dict[str, str] = pyproject["tool"]["poetry"]["scripts"]
    for script, function in scripts.items():
        if "main:app" in function:
            return script
    raise ValueError("Console script not found in pyproject.toml")


@pytest.fixture(scope="session")
def main_process_logfile(tests_path: Path) -> Generator[TextIO, None, None]:
    with open(tests_path / "main_process.log", "w") as logfile:
        yield logfile


@pytest.fixture
def main_process(
    console_script: str,
    main_process_logfile: TextIO,
    request: FixtureRequest,
) -> MainProcessFixture:
    def _inner(
        args: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.Popen[str]:
        args = [console_script] + args
        if env is not None:
            env = dict(os.environ, **env)
        return subprocess.Popen(
            args,
            env=env,
            stdout=main_process_logfile,
            stderr=subprocess.STDOUT,
            text=True,
        )

    main_process_logfile.write(f"==== {request.node.name} ====\n")
    main_process_logfile.flush()
    return _inner


class OPCServer:
    def __init__(self, log_file: TextIO) -> None:
        mydir = Path(__file__).resolve().parent
        self.root_url = URL.build(
            scheme="http", host="127.0.0.1", port=OPC_SERVER_HTTP_PORT
        )
        self.process = subprocess.Popen(
            [sys.executable, str(mydir / "opc_server.py"), str(OPC_SERVER_HTTP_PORT)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
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
def opcua_log_file(tests_path: Path) -> Generator[TextIO, None, None]:
    with open(tests_path / "opc_server.log", "w") as logfile:
        yield logfile


@pytest.fixture(scope="session")
def opcserver(opcua_log_file: TextIO) -> Generator[OPCServer, None, None]:
    opc_server = OPCServer(opcua_log_file)
    while not opc_server.ping():
        time.sleep(0.1)
    yield opc_server
    opc_server.process.terminate()
    opc_server.process.wait()
