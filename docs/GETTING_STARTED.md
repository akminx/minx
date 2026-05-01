# Getting Started — Minx + Discord

End-to-end setup for using Minx as a personal assistant in Discord. Goes from a fresh machine to a working `#ask-minx` channel that can answer questions about your real finance, meals, and training data.

If you only want a CLI smoke (no Discord), skip to [Appendix A — CLI-only path](#appendix-a--cli-only-path).

---

## 0 · How the pieces fit

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────────────┐
│   Discord app    │───▶│  Hermes harness  │───▶│   Minx MCP servers       │
│   (you type      │    │  (~/.hermes)     │    │   (4 HTTP servers in     │
│   in #channels)  │◀───│  routes channels │◀───│    minx-mcp repo)        │
└──────────────────┘    │  to skills,      │    │                          │
                        │  enforces        │    │  ~/.minx/data/minx.db    │
                        │  budgets         │    │  ← canonical state       │
                        └──────────────────┘    └──────────────────────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │   OpenRouter     │
                        │   (LLM provider) │
                        └──────────────────┘
```

**Three repos, three roles:**

| Repo | Role |
|---|---|
| `minx-mcp` | The four MCP servers (Core, Finance, Meals, Training). Owns the SQLite DB. |
| `minx-hermes` | Skills, smoke scripts, the `minx-investigate` runner. Symlinked into `~/.hermes/skills/`. |
| `minx-vault` | Your Obsidian vault. Minx reads/writes notes here. |

**Two daemons that need to run:**
1. The Minx MCP stack (4 HTTP servers on ports 8000–8003)
2. The Hermes harness (which connects Discord ↔ MCP)

---

## 1 · Prerequisites

| Need | Why |
|---|---|
| macOS or Linux | Tested on macOS 14+ |
| `uv` (Python package manager) | All scripts run through `uv` |
| `sqlite3` CLI | Live DB observability |
| OpenRouter account + API key | LLM provider for the agent |
| Discord account + a server you control | Where you'll talk to Minx |
| Obsidian (optional but recommended) | For vault-based memory |
| Hermes harness installed at `~/.hermes` | The Discord bridge |

---

## 2 · One-time setup

### 2.1 Clone the repos

```bash
mkdir -p ~/Documents
cd ~/Documents
git clone git@github.com:akminx/minx.git minx-mcp
git clone git@github.com:akminx/minx-hermes.git
# Vault is yours — clone or initialize wherever you keep Obsidian
git clone git@github.com:akminx/minx-vault.git
```

### 2.2 Install Python deps

```bash
cd ~/Documents/minx-mcp && uv sync --all-extras
cd ~/Documents/minx-hermes && uv sync --all-extras
```

### 2.3 Save secrets to `~/.minx/.env`

This file lives **outside** any repo and is loaded automatically by `start_hermes_stack.sh`.

```bash
mkdir -p ~/.minx
cat > ~/.minx/.env <<'EOF'
# Minx local secrets — DO NOT COMMIT
export OPENROUTER_API_KEY="sk-or-v1-YOUR-KEY-HERE"
export MINX_OPENROUTER_API_KEY="$OPENROUTER_API_KEY"
export MINX_EMBEDDING_DIMENSIONS="512"
export MINX_INVESTIGATION_MODEL="google/gemini-2.5-flash"
EOF
chmod 600 ~/.minx/.env
```

### 2.4 Write the LLM preference into the DB (one-time)

```bash
cd ~/Documents/minx-mcp
source ~/.minx/.env
uv run scripts/configure-openrouter.py --model google/gemini-2.5-flash
```

You should see: `Wrote preference 'core/llm_config' to /Users/<you>/.minx/data/minx.db`.

### 2.5 Set up the Discord server

Create (or pick) a Discord server you own. Inside it, create these channels — names matter, Hermes routes by lane:

| Channel | Purpose |
|---|---|
| `#ask-minx` | General questions. The default entry point. |
| `#finance` | Drop bank CSVs here to import. Ask spending questions. |
| `#training` | Log workouts, ask training questions. |
| `#meals` | Log meals, ask nutrition questions. |
| `#capture` | Drop notes, ideas, preferences. |
| `#reports` | Where Minx posts scheduled summaries. |
| `#minx-ops` | Ops/health channel. Smoke results land here. |

### 2.6 Wire Hermes to your Discord

Hermes lives at `~/.hermes`. Follow the upstream Hermes setup for getting your Discord bot token in place (`~/.hermes/auth.json`) and your channel IDs in `~/.hermes/config.yaml` under `discord.channel_directory`. The required keys are:

```yaml
discord:
  channel_directory:
    ask_minx: <channel-id>
    finance: <channel-id>
    training: <channel-id>
    meals: <channel-id>
    capture: <channel-id>
    reports: <channel-id>
    minx_ops: <channel-id>
```

Validate the wiring:

```bash
cd ~/Documents/minx-hermes
python3 scripts/minx_flow_config.py --check --config ~/.hermes/config.yaml
```

Fix any errors it reports before continuing.

### 2.7 Symlink Minx skills into Hermes

Hermes loads skills from `~/.hermes/skills/`. The skills live in `minx-hermes/skills/minx/` and are exposed via symlinks.

```bash
mkdir -p ~/.hermes/skills/minx
for skill in ~/Documents/minx-hermes/skills/minx/*/; do
  name=$(basename "$skill")
  ln -sfn "$skill" ~/.hermes/skills/minx/"$name"
done
ls -la ~/.hermes/skills/minx/    # all should be symlinks
```

### 2.8 Wire the one-step finance import flow

This binds `#finance` so that uploading a supported CSV/PDF auto-triggers `finance-import`:

```bash
cd ~/Documents/minx-hermes
./scripts/configure-finance-import-flow.sh
./scripts/configure-finance-import-flow.sh --check    # verify
```

---

## 3 · Daily use — start the stack

You'll do this every time you reboot. Two terminals stay open while you use Minx.

### Terminal 1 — Minx MCP servers

```bash
cd ~/Documents/minx-mcp
./scripts/start_hermes_stack.sh
```

You should see four servers starting on ports 8000–8003. The script auto-creates `~/.minx/data` and `~/.minx/staging` and sources `~/.minx/.env`. Leave running.

### Terminal 2 — Hermes (Discord bridge)

Start Hermes per its own instructions (typically `hermes` or whatever your launcher is). It will connect to Discord and start listening on the channels you configured.

### Terminal 3 (optional) — Live observability

```bash
watch -n 2 "sqlite3 ~/.minx/data/minx.db 'SELECT id, kind, status, tool_call_count, json_extract(trajectory_json, \"\$[#-1].tool\") AS last_tool FROM investigations ORDER BY id DESC LIMIT 5;'"
```

Useful for watching what the agent is doing in real time.

---

## 4 · First smoke — does Discord ↔ MCP work?

In **`#ask-minx`**, post:

> hey — say hello and confirm you're online

Expected: Hermes replies in-channel with a short message. No tools called, no DB writes. If this works, the full chain (Discord → Hermes → LLM → response) is healthy.

If it fails: see [Troubleshooting](#troubleshooting).

---

## 5 · Loading your data

Order matters. Load **finance first** (highest signal density), then meals, then training. Run a real investigation between each domain so you catch issues per-domain instead of debugging three at once.

### 5.1 Finance — drop a CSV in `#finance`

Three accounts are pre-seeded: `DCU` (bank), `Discover` (credit), `Robinhood Gold` (credit). Each has an import profile that knows the CSV format.

**In Discord:**

1. Go to `#finance`.
2. Drag-drop your bank's CSV (or PDF) into the channel.
3. Type the account name, e.g.: `import this as DCU`

Hermes will:
- Stage the file under `~/.minx/staging/discord/YYYY-MM-DD/`
- Run `finance_import_preview` (no writes)
- Show you the parsed preview
- If it looks right, ask before running `finance_import` to actually write

If Hermes can't tell which account it is, it'll ask one short clarification question.

**Then in `#ask-minx` or `#finance`:**

> what merchants did I spend the most at last month?

You should get a grounded answer with citations to specific merchants from your statement. If it's generic prose with no specifics, the model didn't find your data — see [Troubleshooting](#troubleshooting).

**Repeat for each account.** Best practice: import one statement per account first, validate the answers look right, then bulk-import history.

### 5.2 Meals — log in `#meals`

Just type what you ate:

> logged: oatmeal + protein shake for breakfast, ~35g protein, ~450 cal

Hermes routes to `meal_log` and confirms. For pantry items:

> add 2 lbs chicken breast to pantry, expires next Friday

For a recipe sync (if you keep recipe notes in your vault):

> scan my Recipes folder

Then ask:

> weekly meal plan, 100g protein/day, using my pantry

### 5.3 Training — log in `#training`

```
logged push day: bench 80kg 5x5, overhead press 50kg 3x6
```

Hermes routes to `training_session_log`. Ask:

> what changed in my training this month?

### 5.4 Capture — long-term preferences in `#capture`

Use this for things you want Minx to remember about you:

> remember: I prefer high-protein breakfasts, and I lift Mon/Wed/Fri

Hermes will ask before saving anything sensitive.

### 5.5 Reports — what shows up in `#reports`

Once you have data and the cron jobs are loaded, scheduled reports (daily review, weekly review, goal nudges, memory review) post here automatically. You don't talk in this channel — it's read-only output from Minx.

---

## 6 · CLI fallback (when Discord isn't enough)

For bulk loads or when you want raw control. All scripts live in `minx-mcp/scripts/` and require the stack to be running.

### Bulk-import finance from a file already on disk

```bash
cd ~/Documents/minx-mcp
# Preview (no writes)
uv run scripts/finance-import.py ~/Downloads/statement.csv --account "DCU"
# Commit
uv run scripts/finance-import.py ~/Downloads/statement.csv --account "DCU" --commit
```

### Bulk-load meals or training from JSON

`~/meals.json`:
```json
[
  {"meal_kind": "breakfast", "occurred_at": "2026-04-30T08:00:00",
   "summary": "oatmeal + protein shake", "protein_grams": 35, "calories": 450},
  {"meal_kind": "lunch", "occurred_at": "2026-04-30T12:30:00",
   "summary": "chicken bowl", "protein_grams": 48, "calories": 720}
]
```

```bash
uv run scripts/seed-meals.py ~/meals.json
```

`~/sessions.json`:
```json
[
  {
    "occurred_at": "2026-04-29T17:30:00",
    "notes": "push day",
    "sets": [
      {"exercise": "Bench Press", "weight_kg": 80, "reps": 5},
      {"exercise": "Overhead Press", "weight_kg": 50, "reps": 6}
    ]
  }
]
```

```bash
uv run scripts/seed-training.py ~/sessions.json
```

### Drive an investigation directly

```bash
cd ~/Documents/minx-hermes
source ~/.minx/.env
uv run scripts/minx-investigate.py --kind investigate \
  --question "what merchants did I spend the most at last month?" \
  --max-tool-calls 6 --wall-clock-s 60
```

---

## 7 · Verify it's actually working

For each domain, after loading data:

| Signal | Healthy |
|---|---|
| `status: succeeded` in investigations table | ✅ |
| `tool_call_count > 0` | ✅ Model used your data |
| `tool_call_count == 0` | ❌ Model didn't pick tools — config issue |
| `citation_refs` non-empty | ✅ Answer is grounded |
| `answer_md` mentions specific merchants/exercises from your data | ✅ |
| `answer_md` is generic prose | ❌ Model is hallucinating |
| `status: budget_exhausted` | ⚠️ Question too vague or budget too low |

Check live with:

```bash
sqlite3 ~/.minx/data/minx.db \
  'SELECT id, kind, status, tool_call_count FROM investigations ORDER BY id DESC LIMIT 10;'
```

---

## 8 · Daily Discord smoke (after model swaps or upgrades)

Full checklist lives in [`minx-hermes/docs/discord-flow-smoke-runbook.md`](../../minx-hermes/docs/discord-flow-smoke-runbook.md). Quick version:

1. Post a free-response prompt in `#ask-minx` — should respond.
2. Drop a CSV in `#finance` — should preview, then ask before importing.
3. Ask a read-only question in each lane — should answer without writes.
4. Ask for a mutation ("update my goal") — should ask for confirmation first.

If any step fails, log the failure in `#minx-ops`.

---

## Troubleshooting

### "Hermes responds but says it can't find my data"
- Stack not running. Check `lsof -iTCP:8000-8003 -sTCP:LISTEN`.
- Wrong DB path. Hermes and MCP must point at the same `~/.minx/data/minx.db`. Set `MINX_DB` if needed.

### "Investigations show `tool_call_count: 0`"
- Model doesn't support tool-calling. Verify `MINX_INVESTIGATION_MODEL` in `~/.minx/.env` is a function-calling-capable model (e.g. `google/gemini-2.5-flash`).
- Re-run `scripts/configure-openrouter.py --model <id>`.

### "Finance import fails with 'unsupported source kind'"
- The CSV format doesn't match the account's pre-seeded `import_profile`. Add `--source-kind <kind>` to the CLI, or check `minx_mcp/finance/importers/` for supported profiles.

### "Discord channel doesn't respond"
- Hermes not running, or channel ID wrong in `~/.hermes/config.yaml`. Re-run `python3 minx-hermes/scripts/minx_flow_config.py --check`.

### "Investigations always end `budget_exhausted`"
- Bump `--max-tool-calls` and `--wall-clock-s`, or sharpen the question.
- Look at the trajectory: `sqlite3 ~/.minx/data/minx.db 'SELECT trajectory_json FROM investigations WHERE id = <last>;' | jq`.

### "I changed code and tests fail"
```bash
cd ~/Documents/minx-mcp && uv run pytest -q
cd ~/Documents/minx-hermes && uv run pytest -q
```

---

## Appendix A — CLI-only path (skip Discord)

If you don't want Discord at all, you only need:

```bash
# 2.1–2.4 above (clone, install, secrets, configure-openrouter)
# Skip 2.5–2.8

# Daily:
cd ~/Documents/minx-mcp && ./scripts/start_hermes_stack.sh   # terminal 1

# Drive investigations:
cd ~/Documents/minx-hermes && source ~/.minx/.env
uv run scripts/minx-investigate.py --kind investigate \
  --question "..." --max-tool-calls 6 --wall-clock-s 60

# Load data:
cd ~/Documents/minx-mcp
uv run scripts/finance-import.py <csv> --account "DCU" --commit
uv run scripts/seed-meals.py ~/meals.json
uv run scripts/seed-training.py ~/sessions.json
```

---

## Appendix B — What's where

| Path | What |
|---|---|
| `~/Documents/minx-mcp/` | MCP server source |
| `~/Documents/minx-hermes/` | Skills, runner scripts |
| `~/.hermes/` | Hermes harness install |
| `~/.hermes/skills/minx/` | Symlinks to minx-hermes skills |
| `~/.hermes/config.yaml` | Discord channel mappings |
| `~/.minx/.env` | Your secrets (chmod 600, not in any repo) |
| `~/.minx/data/minx.db` | Canonical SQLite state |
| `~/.minx/staging/` | Where finance imports get cached |
| `~/Documents/minx-vault/` | Obsidian vault Minx reads/writes |

---

## Appendix C — Reference docs

- [`OPERATIONS.md`](../OPERATIONS.md) — env var reference, ports, maintenance commands
- [`docs/RUNBOOK.md`](RUNBOOK.md) — original CLI runbook (this doc supersedes it for new users)
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — how the pieces talk to each other
- [`minx-hermes/docs/discord-flow-smoke-runbook.md`](../../minx-hermes/docs/discord-flow-smoke-runbook.md) — Discord smoke checklist
- [`minx-hermes/README.md`](../../minx-hermes/README.md) — Hermes-side overview
