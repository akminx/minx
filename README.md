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

## Test

```bash
.venv/bin/python -m pytest -q
```

## Type Check

```bash
.venv/bin/python -m mypy minx_mcp/finance/server.py minx_mcp/finance/analytics.py minx_mcp/vault_writer.py
```

## Notes

- Finance imports are restricted to the configured staging/import root.
- Finance stores money internally as integer cents and renders dollars at the MCP/report boundary.
