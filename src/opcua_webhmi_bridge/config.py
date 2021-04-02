"""Management of configuration from environment variables."""

import dataclasses
import re
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union, cast

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    BaseSettings,
    Field,
    FilePath,
    PositiveInt,
    SecretStr,
    conint,
    root_validator,
    stricturl,
)
from pydantic.env_settings import SettingsError
from pydantic.error_wrappers import ValidationError

if TYPE_CHECKING:
    OpcUrl = AnyUrl
    PortField = int
else:
    OpcUrl = stricturl(tld_required=False, allowed_schemes={"opc.tcp"})
    PortField = conint(gt=0, le=2 ** 16)


class ConfigError(ValueError):
    """Configuration error exception."""

    def __init__(self, field: Union[str, None], error: str) -> None:
        """Initializes configuration error exception.

        Args:
            field: Configuration field the error is about.
            error: A string describing the error.
        """
        msg = f"{field.upper()} environment variable: " if field else ""
        msg += error
        super().__init__(msg)
        self.field = field
        self.error = error


class CentrifugoSettings(BaseSettings):
    """Centrifugo related configuration options."""

    api_key: SecretStr = Field(..., help="Centrifugo API key")
    api_url: AnyHttpUrl = Field(
        "http://localhost:8000/api", help="URL of Centrifugo HTTP api"
    )
    proxy_port: PortField = Field(
        8008, help="Port for Centrifugo proxy server to listen on"
    )

    class Config:  # noqa: D106
        env_prefix = "centrifugo_"


class InfluxSettings(BaseSettings):
    """InfluxDB related configuration options."""

    org: str = Field(..., help="InfluxDB organization")
    bucket: str = Field(..., help="InfluxDB bucket")
    token: str = Field(..., help="InfluxDB auth token with write permission")
    base_url: AnyHttpUrl = Field("http://localhost:8086/", help="Base InfluxDB URL")

    class Config:  # noqa: D106
        env_prefix = "influxdb_"


class OPCSettings(BaseSettings):
    """OPC-UA related configuration options."""

    server_url: OpcUrl = Field(
        ..., help="URL of the OPC-UA server, including username / password if needed"
    )
    monitor_nodes: List[str] = Field(
        ..., help="Array of node IDs to monitor without recording (JSON format)"
    )
    record_nodes: List[str] = Field(
        ..., help="Array of node IDs to monitor and record (JSON format)"
    )
    retry_delay: PositiveInt = Field(
        5, help="Delay in seconds to retry OPC-UA connection"
    )
    cert_file: FilePath = Field(None, help="Path of the OPC-UA client certificate")
    private_key_file: FilePath = Field(
        None, help="Path of the OPC-UA client private key"
    )

    @root_validator
    def check_nodes_overlapping(
        cls: "OPCSettings",  # noqa: U100, N805
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Validates that monitored node ids and recorded node ids do not ovelap."""
        monitor_nodes: List[str] = values.get("monitor_nodes", [])
        record_nodes: List[str] = values.get("record_nodes", [])
        overlapping = set(monitor_nodes) & set(record_nodes)
        if len(overlapping):
            raise ValueError(
                "Same node ids found in OPC_MONITOR_NODES "
                "and OPC_RECORD_NODES environment variables"
            )
        return values

    @root_validator
    def check_cert_and_key_set(
        cls: "OPCSettings",  # noqa: U100, N805
        values: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Validates that both or none of certificate and private key files parameters are set."""
        cert_file: Optional[str] = values.get("cert_file")
        private_key_file: Optional[str] = values.get("private_key_file")
        if (cert_file is None) != (private_key_file is None):
            raise ValueError("Missing one of OPC_CERT_FILE/OPC_PRIVATE_KEY_FILE")
        return values

    class Config:  # noqa: D106
        env_prefix = "opc_"

        @staticmethod
        def schema_extra(schema: Dict[str, Any]) -> None:
            """Processes the generated schema."""
            for prop in ("cert_file", "private_key_file"):
                schema["properties"][prop]["default"] = "unset"


@dataclasses.dataclass
class Settings:
    """Globally manage environment variables configuration options."""

    centrifugo: CentrifugoSettings
    influx: InfluxSettings
    opc: OPCSettings

    def __init__(self, env_file: Optional[Path] = None) -> None:
        """Checks the validity of each configuration option.

        Args:
            env_file: Path to a file defining environment variables.

        Raises:
            ConfigError: A configuration option is not valid.
        """
        try:
            for field in dataclasses.fields(self):
                setattr(self, field.name, field.type(env_file))
        except ValidationError as err:
            config_field = None
            first_error = err.errors()[0]
            loc: str = first_error["loc"][0]
            if loc != "__root__":
                settings_model = cast(BaseSettings, err.model)
                config_field = settings_model.Config.env_prefix + loc
            raise ConfigError(config_field, first_error["msg"])
        except SettingsError as err:
            config_field = "!-UNKNOWN-!"
            error_msg = str(err)
            cause = err.__cause__
            if isinstance(cause, JSONDecodeError):
                match = re.search(r'"(\w+)"$', error_msg)
                # In this context (SettingsError from JSONDecodeError),
                # match must not be None
                assert match is not None  # noqa: S101
                config_field = match[1]
                error_msg = f"JSON decoding error (`{cause}`)"
            raise ConfigError(config_field, error_msg)

    @classmethod
    def help(cls) -> List[Tuple[str, str]]:
        """Generate environment variables configuration options help text.

        Returns:
            A list of 2-tuples. Each tuple consists of the environment variable
            and its descriptive text.
        """
        env_vars_tuples: List[Tuple[str, str]] = []
        for field in dataclasses.fields(cls):
            for props in field.type.schema()["properties"].values():
                env_var = list(props["env_names"])[0].upper()
                help_text = props["help"]
                default_value = props.get("default")
                default = f" (default: {default_value})" if default_value else ""
                env_vars_tuples.append((env_var, f"{help_text}{default}"))
        return env_vars_tuples
