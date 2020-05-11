# pyright: strict
import dataclasses
import os
from typing import Optional, TypeVar

_T = TypeVar("_T")


class EnvError(ValueError):
    """Raised when an environment variable or if a required environment variable is unset."""


def config_field(help: str, default: Optional[_T] = None) -> _T:
    metadata = {"help": help}
    if default is None:
        return dataclasses.field(metadata=metadata)
    else:
        return dataclasses.field(default=default, metadata=metadata)


@dataclasses.dataclass
class Config:
    opc_server_url: str = config_field(help="URL of the OPC-UA server")
    opc_monitor_node: str = config_field(help="String ID of node to monitor")
    opc_retry_delay: int = config_field(
        help="Delay in seconds to retry OPC-UA connection", default=5
    )
    websocket_host: str = config_field(
        help="WebSocket server bind address", default="0.0.0.0"
    )
    websocket_port: int = config_field(help="WebSocket server port", default=3000)

    def __init__(self) -> None:
        for field in dataclasses.fields(self):
            env_var = field.name.upper()
            if env_var not in os.environ:
                if field.default is dataclasses.MISSING:
                    raise EnvError(f"Missing required environment variable {env_var}")
                setattr(self, field.name, field.default)
            else:
                try:
                    setattr(self, field.name, field.type(os.environ[env_var]))
                except ValueError:
                    raise EnvError(
                        f"Conversion of {env_var} environment variable to {field.type.__name__} failed"
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
