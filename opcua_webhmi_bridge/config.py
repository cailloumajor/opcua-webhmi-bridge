import dataclasses
import os
from pathlib import Path
from typing import Any, Callable, List, Optional, TypedDict, TypeVar, Union

from dotenv import load_dotenv

_T = TypeVar("_T")


class EnvError(ValueError):
    """Raised when an environment variable or if a required environment variable is unset."""


class FieldMetadata(TypedDict, total=False):
    help: str
    factory: Callable[[str], _T]


def config_field(
    help: str,
    default: Optional[_T] = None,
    factory: Optional[Callable[[str], _T]] = None,
) -> Union[_T, Any]:
    metadata: FieldMetadata = {"help": help}
    if factory is not None:
        metadata["factory"] = factory
    if default is None:
        return dataclasses.field(metadata=metadata)
    else:
        return dataclasses.field(default=default, metadata=metadata)


@dataclasses.dataclass(init=False)
class _Config:
    # Mandatory fields
    influx_db_name: str = config_field(help="Name of the InfluxDB database to use")
    opc_server_url: str = config_field(help="URL of the OPC-UA server")
    opc_monitor_nodes: List[str] = config_field(
        help="List of node IDs to monitor without recording, separated by commas",
        factory=lambda s: s.split(","),
    )
    opc_record_nodes: List[str] = config_field(
        help="List of node IDs to monitor and record, separated by commas",
        factory=lambda s: s.split(","),
    )
    # Optional fields
    influx_host: str = config_field(
        help="Hostname to connect to InfluxDB", default="localhost"
    )
    influx_port: int = config_field(help="Port to connect to InfluxDB", default=8086)
    opc_retry_delay: int = config_field(
        help="Delay in seconds to retry OPC-UA connection", default=5
    )
    websocket_host: str = config_field(
        help="WebSocket server bind address", default="0.0.0.0"
    )
    websocket_port: int = config_field(help="WebSocket server port", default=3000)

    def init(self, verbose: bool) -> None:
        env_path = Path(__file__).parent / "../.env"
        env_path = env_path.resolve()
        load_dotenv(dotenv_path=env_path, verbose=verbose)
        for field in dataclasses.fields(self):
            env_var = field.name.upper()
            if env_var not in os.environ:
                if field.default is dataclasses.MISSING:
                    raise EnvError(f"Missing required environment variable {env_var}")
                setattr(self, field.name, field.default)
            else:
                env_value = os.environ[env_var]
                try:
                    if field.metadata.get("factory") is not None:
                        setattr(self, field.name, field.metadata["factory"](env_value))
                    else:
                        setattr(self, field.name, field.type(env_value))
                except ValueError as err:
                    raise EnvError(f"Error in {env_var} environment variable: {err}")

    def __str__(self) -> str:
        return "\n".join(
            [f"{f.name}={getattr(self, f.name)}" for f in dataclasses.fields(self)]
        )

    @classmethod
    def generate_help(cls) -> str:
        help_lines = []
        fields = dataclasses.fields(cls)
        max_name_length = max((len(f.name) for f in fields))
        for field in fields:
            help_line = field.name.upper()
            help_line += " " * (max_name_length - len(field.name) + 2)
            help_line += field.metadata["help"]
            if field.default is not dataclasses.MISSING:
                help_line += f" (default: {field.default})"
            help_lines.append(help_line)
        return "\n".join(help_lines)


config = _Config()
