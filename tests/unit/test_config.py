import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List, NamedTuple

import pytest
from pytest import MonkeyPatch

from opcua_webhmi_bridge.config import ConfigError, Settings


class EnvArg(NamedTuple):
    name: str
    value: str


SetVarsFixture = Callable[[Iterable[EnvArg]], None]


def arg_name(arg: EnvArg) -> str:
    return arg.name


@pytest.fixture
def mandatory_env_args(mandatory_env_args: Dict[str, str]) -> List[EnvArg]:
    return [EnvArg(key, value) for key, value in mandatory_env_args.items()]


@pytest.fixture
def set_vars(monkeypatch: MonkeyPatch) -> SetVarsFixture:
    def inner(vars: Iterable[EnvArg]) -> None:
        for v in vars:
            monkeypatch.setenv(v.name, v.value)

    return inner


def test_all_mandatory_args(
    mandatory_env_args: List[EnvArg],
    set_vars: SetVarsFixture,
) -> None:
    set_vars(mandatory_env_args)
    assert Settings() is not None


def test_missing_mandatory_arg(
    mandatory_env_args: List[EnvArg],
    mandatory_env_args_keys: str,
    monkeypatch: MonkeyPatch,
    set_vars: SetVarsFixture,
) -> None:
    set_vars(mandatory_env_args)
    monkeypatch.delenv(mandatory_env_args_keys)
    with pytest.raises(ConfigError, match=re.escape(mandatory_env_args_keys)):
        Settings()


@pytest.mark.parametrize(
    "bad_arg",
    [
        EnvArg("CENTRIFUGO_API_URL", "example.com:1234"),
        EnvArg("CENTRIFUGO_PROXY_PORT", "0"),
        EnvArg("CENTRIFUGO_PROXY_PORT", "65537"),
        EnvArg("CENTRIFUGO_PROXY_PORT", "port"),
        EnvArg("INFLUXDB_BASE_URL", "example.com:8086"),
        EnvArg("OPC_SERVER_URL", "http://example.com:1234"),
        EnvArg("OPC_MONITOR_NODES", '"not_an_iterable"'),
        EnvArg("OPC_MONITOR_NODES", "invalid_json"),
        EnvArg("OPC_RECORD_NODES", '"not_an_iterable"'),
        EnvArg("OPC_RECORD_NODES", "invalid_json"),
        EnvArg("OPC_MONITOR_DELAY", "-1"),
        EnvArg("OPC_MONITOR_DELAY", "0"),
        EnvArg("OPC_RETRY_DELAY", "-1"),
        EnvArg("OPC_RETRY_DELAY", "0"),
        EnvArg("OPC_CERT_FILE", "/nonexistent"),
        EnvArg("OPC_PRIVATE_KEY_FILE", "/nonexistent"),
    ],
    ids=arg_name,
)
def test_bad_arg_type(
    mandatory_env_args: List[EnvArg],
    monkeypatch: MonkeyPatch,
    set_vars: SetVarsFixture,
    bad_arg: EnvArg,
) -> None:
    set_vars(mandatory_env_args)
    monkeypatch.setenv(bad_arg.name, bad_arg.value)
    with pytest.raises(ConfigError, match=re.escape(bad_arg.name)):
        Settings()


@pytest.mark.parametrize(
    ["apply_args", "expect_failure"],
    [
        (["OPC_CERT_FILE"], True),
        (["OPC_PRIVATE_KEY_FILE"], True),
        (["OPC_CERT_FILE", "OPC_PRIVATE_KEY_FILE"], False),
    ],
    ids=[
        "Certificate file only",
        "Private key file only",
        "Both certificate and private key files",
    ],
)
def test_opc_cert_and_key(
    apply_args: List[str],
    expect_failure: bool,
    mandatory_env_args: List[EnvArg],
    monkeypatch: MonkeyPatch,
    set_vars: SetVarsFixture,
    tmp_path: Path,
) -> None:
    set_vars(mandatory_env_args)
    for arg in apply_args:
        file = tmp_path / arg.lower()
        file.touch()
        monkeypatch.setenv(arg, str(file))
    if expect_failure:
        with pytest.raises(
            ConfigError, match="Missing one of OPC_CERT_FILE/OPC_PRIVATE_KEY_FILE"
        ):
            Settings()
    else:
        Settings()


@pytest.mark.parametrize(
    "overlapping_arg",
    [
        EnvArg("OPC_MONITOR_NODES", '["node1", "node2", "node3"]'),
        EnvArg("OPC_RECORD_NODES", '["node2", "node3", "node4"]'),
    ],
)
def test_overlapping_nodes(
    mandatory_env_args: List[EnvArg],
    monkeypatch: MonkeyPatch,
    overlapping_arg: EnvArg,
    set_vars: SetVarsFixture,
) -> None:
    set_vars(mandatory_env_args)
    monkeypatch.setenv(overlapping_arg.name, overlapping_arg.value)
    with pytest.raises(ConfigError, match="Same node ids found"):
        Settings()


def test_help(mandatory_env_args: List[EnvArg], set_vars: SetVarsFixture) -> None:
    set_vars(mandatory_env_args)
    mandatory_names = [n for n, _ in mandatory_env_args]
    for env_var, help_text in Settings.help():
        assert (env_var in mandatory_names) != ("default:" in help_text)
