from __future__ import annotations

from subprocess import Popen
from typing import TYPE_CHECKING, Callable, Dict, List

import pytest
import toml
from _pytest.fixtures import FixtureRequest

COMMAND = "opcua-agent"

if TYPE_CHECKING:
    MainProcessFixture = Callable[[List[str]], Popen[str]]


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
    def _inner(args: List[str]) -> Popen[str]:
        args = [console_script] + args
        return Popen(args, text=True)

    return _inner


def test_entrypoint(main_process: MainProcessFixture) -> None:
    process = main_process(["--help"])
    assert process.wait(timeout=5.0) == 0
