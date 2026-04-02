# minx-mcp

Shared Minx MCP platform and finance domain.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Run finance over stdio

```bash
.venv/bin/python -m minx_mcp.finance --transport stdio
```

## Run finance over HTTP

```bash
.venv/bin/python -m minx_mcp.finance --transport http --host 127.0.0.1 --port 8000
```

The HTTP transport uses FastMCP streamable HTTP and is intended as the runtime seam for later dashboard work.
