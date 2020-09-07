import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, List, Union, cast

from pydantic import AnyUrl, BaseSettings, Field, PositiveInt, stricturl
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


class WebSocketSettings(BaseSettings):
    host: str = Field("0.0.0.0", help="WebSocket server bind address")
    port: PositiveInt = Field(3000, help="WebSocket server port")

    class Config:
        env_prefix = "websocket_"


@dataclasses.dataclass
class Settings:
    influx: InfluxSettings
    opc: OPCSettings
    websocket: WebSocketSettings

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
    def help(cls) -> str:
        @dataclasses.dataclass
        class HelpLine:
            env_var: str
            help_text: str
            default_value: Union[str, None]

            def as_str(self, max_name_length: int) -> str:
                padding = " " * (max_name_length - len(self.env_var) + 2)
                default = (
                    f" (default: {self.default_value})" if self.default_value else ""
                )
                return f"{self.env_var}{padding}{self.help_text}{default}"

        help_lines: List[HelpLine] = []
        for field in dataclasses.fields(cls):
            for props in field.type.schema()["properties"].values():
                env_var = list(props["env_names"])[0].upper()
                help_text = props["help"]
                default_value = props.get("default")
                if default_value:
                    default_value = str(default_value)
                help_lines.append(HelpLine(env_var, help_text, default_value))

        max_name_length = max((len(l.env_var) for l in help_lines))
        return "\n".join((l.as_str(max_name_length) for l in help_lines))
