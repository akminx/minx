from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict

TransportName = Literal["stdio", "http"]
RunTransport = Literal["stdio", "streamable-http"]


class TransportConfig(TypedDict):
    transport: RunTransport
    host: str
    port: int


class MCPServerLike(Protocol):
    settings: Any

    def run(self, transport: RunTransport, mount_path: str | None = None) -> None: ...


def build_transport_config(transport: TransportName, host: str, port: int) -> TransportConfig:
    if transport == "stdio":
        return {"transport": "stdio", "host": host, "port": port}
    if transport == "http":
        return {"transport": "streamable-http", "host": host, "port": port}
    raise ValueError(f"Unsupported transport: {transport}")


def run_server(
    mcp: MCPServerLike,
    transport: TransportName,
    host: str,
    port: int,
) -> None:
    config = build_transport_config(transport, host, port)
    mcp.settings.host = config["host"]
    mcp.settings.port = config["port"]
    try:
        mcp.run(transport=config["transport"])
    except KeyboardInterrupt:
        return
