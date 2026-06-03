# Minx Cheap-Model Routing Review

Date: 2026-05-01

## Goal

Keep full Minx capability available while making Hermes reliable on cheap models
such as Gemini 2.5 Flash. The target shape is small, obvious tool menus for
normal Discord lanes, with broader orchestration still available when needed.

## Current Setup

- Discord generic Hermes toolsets are trimmed to `clarify`, `skills`, and `web`.
- Discord still has access to all Minx MCP servers:
  - `minx_core`
  - `minx_finance`
  - `minx_meals`
  - `minx_training`
- Active local skills should stay minimal:
  - `minx`
  - `minx-core-function-call`
  - `recipe-substitution-guidance`
- Broad non-Minx skill bundles are parked under:
  - `~/.hermes/skills.disabled/minx-cheap-model-20260501-refresh/`
- Finance email ingestion is exposed through the narrow tool:
  - `minx_finance.finance_stage_email_statement`

## Lane Strategy

- `#finance`: import, query, plan, investigate, and finance email staging.
- `#training`: training, goals, plan, investigate.
- `#meals`: meals, pantry, recipes, plan, investigate, recipe substitutions.
- `#capture`: memory, goals, and entity intake.
- `#reports`: daily/weekly review, monitoring, and cross-domain summaries.
- `#ask-minx`: broad control plane for cross-domain or unusual requests.

## What To Watch

- Gemini Flash choosing the wrong Minx core tool.
- `recipe-substitution-guidance` feeling redundant with meals behavior.
- `memory-review`, `onboard-entity`, and `wiki-update` overlapping in capture flows.
- `goal-nudge` duplicating daily or weekly review output.
- `finance_stage_email_statement` being too narrow or not narrow enough.
- `#ask-minx` becoming the only reliable lane, which would mean domain lanes are too constrained.

## Consolidation Candidates

- Fold `recipe-substitution-guidance` into meals prompts if it is only used there.
- Merge capture-related skills if memory/wiki/entity routing gets fuzzy.
- Fold `goal-nudge` into daily/weekly review if reports repeat the same goal alerts.
- Keep `minx/plan` and `minx/investigate` separate unless users consistently ask for one and get the other.

## Verification Commands

Check the Discord gateway is actually loaded. The Hermes TUI can be online while
the Discord gateway is stopped, in which case Discord messages will be ignored.

```bash
hermes gateway status
tail -n 80 ~/.hermes/logs/gateway.log
```

Expected log lines after restart:

```text
[Discord] Registered /skill command with 12 skill(s) via autocomplete
[Discord] Connected as minxbot#1741
Gateway running with 1 platform(s)
```

If the service is disabled, re-enable and start it:

```bash
launchctl enable gui/$(id -u)/ai.hermes.gateway
hermes gateway start
```

For Discord `/skill` autocomplete, Minx skill directories must be real local
directories under `~/.hermes/skills/minx/`. Symlinks to
`/Users/akmini/Documents/minx-hermes/skills/minx/` load for some Hermes paths
but are filtered out by Discord slash registration because the resolved target
is outside `~/.hermes/skills`.

Check effective Discord toolsets:

```bash
PYTHONPATH=/Users/akmini/.hermes/hermes-agent \
/Users/akmini/.hermes/hermes-agent/venv/bin/python3 - <<'PY'
from hermes_cli.config import load_config
from hermes_cli.tools_config import _get_platform_tools
cfg = load_config()
print(sorted(_get_platform_tools(cfg, "discord", include_default_mcp_servers=True)))
PY
```

Expected:

```text
['clarify', 'minx_core', 'minx_finance', 'minx_meals', 'minx_training', 'skills', 'web']
```

Check active local skills:

```bash
find ~/.hermes/skills -maxdepth 1 -type d | sed 's#.*/##' | sort
```

Check live finance MCP includes email staging:

```bash
.venv/bin/python - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://127.0.0.1:8000/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            print("finance_stage_email_statement" in names)

asyncio.run(main())
PY
```

Run targeted finance tests:

```bash
uv run pytest \
  tests/test_finance_server.py::test_finance_stage_email_statement_downloads_supported_attachments \
  tests/test_finance_server.py::test_finance_server_registers_phase2_safe_tool_names \
  -q
```

## Next Review

After a week of real use, decide based on observed routing failures rather than
theoretical overlap. Prefer small prompt/config tweaks before deleting useful
capability.
