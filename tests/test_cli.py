import re
import subprocess  # nosec
from typing import NamedTuple

import pytest

from opcua_webhmi_bridge.config import ConfigError, Settings


class EnvArg(NamedTuple):
    name: str
    value: str


COMMAND = "opcua-agent"
MANDATORY_ENV_ARGS = {
    EnvArg("CENTRIFUGO_API_KEY", "key"),
    EnvArg("INFLUX_DB_NAME", "db"),
    EnvArg("OPC_SERVER_URL", "opc.tcp://127.0.0.1:4840"),
    EnvArg("OPC_MONITOR_NODES", '["node1", "node2"]'),
    EnvArg("OPC_RECORD_NODES", '["node3", "node4"]'),
}


@pytest.fixture
def set_mandatory_vars(monkeypatch):
    for v in MANDATORY_ENV_ARGS:
        monkeypatch.setenv(v.name, v.value)


def test_entrypoint():
    completed = subprocess.run(f"{COMMAND} --help", shell=True)  # nosec
    assert completed.returncode == 0  # nosec


def test_all_mandatory_args(set_mandatory_vars):
    assert Settings() is not None  # nosec


@pytest.mark.parametrize(
    "arg_to_remove", list(MANDATORY_ENV_ARGS), ids=lambda arg: arg.name
)
def test_missing_mandatory_arg(monkeypatch, set_mandatory_vars, arg_to_remove):
    monkeypatch.delenv(arg_to_remove.name)
    with pytest.raises(ConfigError, match=re.escape(arg_to_remove.name)):
        Settings()
