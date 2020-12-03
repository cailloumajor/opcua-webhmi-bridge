"""Management of configuration from environment variables."""

import dataclasses
import re
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple, cast

from pydantic import (
    AnyHttpUrl,
    AnyUrl,
    BaseSettings,
    Field,
    PositiveInt,
    SecretStr,
    conint,
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

    def __init__(self, field: str, error: str) -> None:
        """Initializes configuration error exception.

        Args:
            field: Configuration field the error is about.
            error: A string describing the error.
        """
        super().__init__(f"{field.upper()} environment variable: {error}")
        self.field = field
        self.error = error


class CentrifugoSettings(BaseSettings):
    """Centrifugo related configuration options."""

    api_key: SecretStr = Field(..., help="Centrifugo API key")
    api_url: AnyHttpUrl = Field(
        "http://localhost:8000/api", help="URL of Centrifugo HTTP api"
    )
    proxy_host: str = Field(
        "0.0.0.0", help="Host for Centrifugo proxy server to listen on"
    )
    proxy_port: PortField = Field(
        8008, help="Port for Centrifugo proxy server to listen on"
    )

    class Config:  # noqa: D106
        env_prefix = "centrifugo_"


class InfluxSettings(BaseSettings):
    """InfluxDB related configuration options."""

    db_name: str = Field(..., help="Name of the InfluxDB database to use")
    host: str = Field("localhost", help="Host on which InfluxDB server is reachable")
    port: PortField = Field(8086, help="Port on which InfluxDB server is reachable")

    class Config:  # noqa: D106
        env_prefix = "influx_"


class OPCSettings(BaseSettings):
    """OPC-UA related configuration options."""

    server_url: OpcUrl = Field(..., help="URL of the OPC-UA server")
    monitor_nodes: List[str] = Field(
        ..., help="Array of node IDs to monitor without recording (JSON format)"
    )
    record_nodes: List[str] = Field(
        ..., help="Array of node IDs to monitor and record (JSON format)"
    )
    retry_delay: PositiveInt = Field(
        5, help="Delay in seconds to retry OPC-UA connection"
    )

    class Config:  # noqa: D106
        env_prefix = "opc_"


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
            first_error = err.errors()[0]
            settings_model = cast(BaseSettings, err.model)
            config_field = settings_model.Config.env_prefix
            config_field += first_error["loc"][0]
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
