import re
from typing import Callable, Iterable, NamedTuple

import pytest
from pytest import MonkeyPatch

from opcua_webhmi_bridge.config import ConfigError, Settings


class EnvArg(NamedTuple):
    name: str
    value: str


SetVarsType = Callable[[Iterable[EnvArg]], None]

MANDATORY_ENV_ARGS = [
    EnvArg("CENTRIFUGO_API_KEY", "key"),
    EnvArg("INFLUX_DB_NAME", "db"),
    EnvArg("OPC_SERVER_URL", "opc.tcp://localhost:4840"),
    EnvArg("OPC_MONITOR_NODES", '["node1", "node2"]'),
    EnvArg("OPC_RECORD_NODES", '["node3", "node4"]'),
]


def arg_name(arg: EnvArg) -> str:
    return arg.name


@pytest.fixture
def set_vars(monkeypatch: MonkeyPatch) -> SetVarsType:
    def inner(vars: Iterable[EnvArg]) -> None:
        for v in vars:
            monkeypatch.setenv(v.name, v.value)

    return inner


def test_all_mandatory_args(set_vars: SetVarsType) -> None:
    set_vars(MANDATORY_ENV_ARGS)
    assert Settings() is not None


@pytest.mark.parametrize("arg_to_remove", MANDATORY_ENV_ARGS, ids=arg_name)
def test_missing_mandatory_arg(
    monkeypatch: MonkeyPatch,
    set_vars: SetVarsType,
    arg_to_remove: EnvArg,
) -> None:
    set_vars(MANDATORY_ENV_ARGS)
    monkeypatch.delenv(arg_to_remove.name)
    with pytest.raises(ConfigError, match=re.escape(arg_to_remove.name)):
        Settings()


@pytest.mark.parametrize(
    "bad_arg",
    [
        EnvArg("CENTRIFUGO_API_URL", "example.com:1234"),
        EnvArg("CENTRIFUGO_PROXY_PORT", "0"),
        EnvArg("CENTRIFUGO_PROXY_PORT", "65537"),
        EnvArg("CENTRIFUGO_PROXY_PORT", "port"),
        EnvArg("INFLUX_ROOT_URL", "example.com:8086"),
        EnvArg("OPC_SERVER_URL", "http://example.com:1234"),
        EnvArg("OPC_MONITOR_NODES", '"not_an_iterable"'),
        EnvArg("OPC_MONITOR_NODES", "invalid_json"),
        EnvArg("OPC_RECORD_NODES", '"not_an_iterable"'),
        EnvArg("OPC_RECORD_NODES", "invalid_json"),
        EnvArg("OPC_RETRY_DELAY", "-1"),
    ],
    ids=arg_name,
)
def test_bad_arg_type(
    monkeypatch: MonkeyPatch,
    set_vars: SetVarsType,
    bad_arg: EnvArg,
) -> None:
    set_vars(MANDATORY_ENV_ARGS)
    monkeypatch.setenv(bad_arg.name, bad_arg.value)
    with pytest.raises(ConfigError, match=re.escape(bad_arg.name)):
        Settings()


@pytest.mark.parametrize(
    "overlapping_arg",
    [
        EnvArg("OPC_MONITOR_NODES", '["node1", "node2", "node3"]'),
        EnvArg("OPC_RECORD_NODES", '["node2", "node3", "node4"]'),
    ],
)
def test_overlapping_nodes(
    monkeypatch: MonkeyPatch,
    overlapping_arg: EnvArg,
    set_vars: SetVarsType,
) -> None:
    set_vars(MANDATORY_ENV_ARGS)
    monkeypatch.setenv(overlapping_arg.name, overlapping_arg.value)
    with pytest.raises(ConfigError, match="Same node ids found"):
        Settings()


def test_help(set_vars: SetVarsType) -> None:
    set_vars(MANDATORY_ENV_ARGS)
    mandatory_names = [n for n, _ in MANDATORY_ENV_ARGS]
    for env_var, help_text in Settings.help():
        assert (env_var in mandatory_names) != ("default:" in help_text)
