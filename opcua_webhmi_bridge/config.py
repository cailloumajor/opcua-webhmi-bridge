import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple, cast

from pydantic import (
    AnyUrl,
    BaseSettings,
    Field,
    HttpUrl,
    PositiveInt,
    SecretStr,
    stricturl,
)
from pydantic.error_wrappers import ValidationError

if TYPE_CHECKING:
    OpcUrl = AnyUrl
else:
    OpcUrl = stricturl(allowed_schemes={"opc.tcp"})


class ConfigError(ValueError):
    pass


class InfluxSettings(BaseSettings):
    db_name: str = Field(..., help="Name of the InfluxDB database to use")
    host: str = Field("localhost", help="Hostname to connect to InfluxDB")
    port: PositiveInt = Field(8086, help="Port to connect to InfluxDB")

    class Config:
        env_prefix = "influx_"


class MessagingSettings(BaseSettings):
    api_key: SecretStr = Field(..., help="Centrifugo API key")
    centrifugo_url: HttpUrl = Field(
        "http://localhost:8000", help="URL of Centrifugo HTTP api"
    )

    class Config:
        env_prefix = "messaging_"


class OPCSettings(BaseSettings):
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

    class Config:
        env_prefix = "opc_"


@dataclasses.dataclass
class Settings:
    influx: InfluxSettings
    messaging: MessagingSettings
    opc: OPCSettings

    def __init__(self) -> None:
        env_file = Path(__file__).parent / "../.env"
        try:
            for field in dataclasses.fields(self):
                setattr(self, field.name, field.type(env_file))
        except ValidationError as err:
            first_error = err.errors()[0]
            settings_model = cast(BaseSettings, err.model)
            env_var = settings_model.Config.env_prefix
            env_var += first_error["loc"][0]
            env_var = env_var.upper()
            raise ConfigError(f"{env_var} environment variable: {first_error['msg']}")

    @classmethod
    def help(cls) -> List[Tuple[str, str]]:
        env_vars_tuples: List[Tuple[str, str]] = []
        for field in dataclasses.fields(cls):
            for props in field.type.schema()["properties"].values():
                env_var = list(props["env_names"])[0].upper()
                help_text = props["help"]
                default_value = props.get("default")
                default = f" (default: {default_value})" if default_value else ""
                env_vars_tuples.append((env_var, f"{help_text}{default}"))
        return env_vars_tuples
