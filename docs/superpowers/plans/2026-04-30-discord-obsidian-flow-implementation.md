# Discord Obsidian Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved Discord channel and Obsidian vault structure redesign for Minx.

**Architecture:** Hermes config owns logical channel routing and prompts. `minx-hermes` owns validation/helpers. Obsidian remains a projection layer under `~/Documents/minx-vault`, with canonical facts staying in Minx MCP/SQLite.

**Tech Stack:** Python 3.12, PyYAML, pytest, Hermes `config.yaml`, Obsidian Markdown files.

---

### Task 1: Update The Spec

**Files:**
- Modify: `docs/superpowers/specs/2026-04-30-discord-obsidian-hermes-flow-redesign.md`

- [ ] Add a concrete "Recommended Final Shape" section listing Discord channel names and Obsidian folders.
- [ ] Verify no placeholders with `rg -n "TBD|TODO|REPLACE|FILL" docs/superpowers/specs/2026-04-30-discord-obsidian-hermes-flow-redesign.md`.

### Task 2: Add Tested Hermes Flow Config Helper

**Files:**
- Create: `~/Documents/minx-hermes/scripts/minx_flow_config.py`
- Create: `~/Documents/minx-hermes/tests/test_minx_flow_config.py`
- Modify: `~/Documents/minx-hermes/scripts/configure-finance-import-flow.sh`

- [ ] Write tests that assert old logical channel keys are renamed to `ask_minx`, `finance`, `training`, and `capture`.
- [ ] Run the tests and confirm they fail before implementation.
- [ ] Implement the helper and update finance import lookup to prefer `finance`.
- [ ] Run tests and confirm they pass.

### Task 3: Apply Live Hermes Config

**Files:**
- Modify: `~/.hermes/config.yaml`

- [ ] Run the helper against the live config.
- [ ] Verify channel prompts and quick command aliases still exist.
- [ ] Verify `skills.external_dirs` still points at `~/Documents/minx-hermes/skills`.

### Task 4: Reshape Obsidian Vault

**Files:**
- Modify/create files under `~/Documents/minx-vault`

- [ ] Move current domain README notes into the new projection folders.
- [ ] Add `Minx/Dashboard.md` and `Minx/Inbox.md`.
- [ ] Add README files for `Reports`, `Wiki`, `Memory`, and `Ops`.
- [ ] Update `Home.md` and `Minx/README.md` links.

### Task 5: Verify

**Commands:**
- `uv run --extra dev pytest tests/test_minx_flow_config.py tests/test_runner_config.py tests/test_project_metadata.py -q` in `~/Documents/minx-hermes`
- `python3 scripts/minx_flow_config.py --check --config ~/.hermes/config.yaml` in `~/Documents/minx-hermes`
- `find ~/Documents/minx-vault -maxdepth 3 -type d | sort`
- `hermes doctor`
