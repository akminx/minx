from __future__ import annotations


def build_transport_config(transport: str, host: str, port: int) -> dict[str, object]:
    if transport == "stdio":
        return {"transport": "stdio", "host": host, "port": port}
    if transport == "http":
        return {"transport": "streamable-http", "host": host, "port": port}
    raise ValueError(f"Unsupported transport: {transport}")


def run_server(mcp, transport: str, host: str, port: int) -> None:
    config = build_transport_config(transport, host, port)
    mcp.settings.host = config["host"]
    mcp.settings.port = config["port"]
    try:
        mcp.run(transport=config["transport"])
    except KeyboardInterrupt:
        return
