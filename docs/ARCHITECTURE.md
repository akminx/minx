# Minx Architecture

Minx is a local-first personal operating system built around four MCP servers and a separate agentic harness. The boundary is the core design choice: durable data and deterministic domain logic stay in `minx`; conversation, scheduling, tool choice, and final prose stay in `minx-hermes` or another MCP harness.

## System Context

```mermaid
flowchart TB
    user["User"] --> interfaces["Discord / CLI / Obsidian"]
    interfaces --> harness["minx-hermes or another MCP harness"]

    harness --> model["OpenAI-compatible model endpoint"]
    harness --> policy["Tool policy, budgets, confirmations, final prose"]

    harness --> core["Core MCP :8001"]
    harness --> finance["Finance MCP :8000"]
    harness --> meals["Meals MCP :8002"]
    harness --> training["Training MCP :8003"]

    core --> sqlite["SQLite"]
    finance --> sqlite
    meals --> sqlite
    training --> sqlite

    core <--> vault["Obsidian vault"]
    meals <--> vault

    core --> safety["Validation, fingerprints, secret scanning"]
    finance --> safety
    meals --> safety
    training --> safety
```

Read this as an ownership map. The harness can ask questions, choose tools, request confirmations, call the model, and compose the answer. The MCP servers own deterministic domain logic, validation, migrations, durable records, render hints, and audit trails. SQLite is the structured source of truth; the vault is a human-readable/editable surface projected from, and reconciled back into, structured state.

## Repositories

| Repo | Owns |
|---|---|
| `minx` | MCP servers, SQLite schema, domain services, Core read models, memory, goals, vault primitives, render templates, playbook and investigation audit storage |
| `minx-hermes` | Hermes skills, Discord lane guidance, investigation runner, tool-call policy, MCP client fan-out, hard budgets, and smoke scripts |
| Hermes upstream/runtime | Harness process, user interface, slash command runtime, cron scheduler, and live config |

## MCP Servers

| Server | Default HTTP port | Responsibility |
|---|---:|---|
| Finance | 8000 | Statement imports, categorization, money arithmetic, reports, and finance read APIs |
| Core | 8001 | Snapshots, goals, durable memory, vault primitives, render contracts, playbooks, and investigations |
| Meals | 8002 | Pantry, recipes, meals, and nutrition profiles |
| Training | 8003 | Exercise catalog, sessions, programs, and progress summaries |

Each server can run over stdio for local MCP clients or HTTP for the Hermes stack. `scripts/start_hermes_stack.sh` starts the four HTTP servers together.

## Runtime Tool Flow

```mermaid
sequenceDiagram
    actor User
    participant Harness as minx-hermes / MCP harness
    participant Model as Model endpoint
    participant Core as Core MCP
    participant Domain as Finance / Meals / Training MCP
    participant DB as SQLite

    User->>Harness: Ask a question or request an action
    Harness->>Model: Plan next step within budgets
    Model-->>Harness: Tool call intent
    Harness->>Core: Cross-domain reads, memory, goals, audits
    Harness->>Domain: Domain-specific tool calls
    Core->>DB: Read/write structured state
    Domain->>DB: Read/write structured state
    Core-->>Harness: Structured facts and render hints
    Domain-->>Harness: Structured domain results
    Harness->>Core: Store digest-only investigation/playbook step
    Harness-->>User: Final prose and confirmations
```

The runtime path is intentionally split. Minx returns structured data, stable template ids, citations, and small render slots; the harness turns that into user-facing language. Investigation and playbook runs keep digest-only audit records in Core so later review does not depend on the original model context.

## Persistence And Sync Flow

```mermaid
flowchart LR
    subgraph MCP["MCP servers"]
        core["Core tools"]
        finance["Finance tools"]
        meals["Meals tools"]
        training["Training tools"]
    end

    subgraph Gates["Safety gates"]
        validation["Schema and business validation"]
        secrets["Secret blocking / redaction"]
        fingerprints["Canonical memory fingerprints"]
    end

    subgraph Store["Durable local state"]
        sqlite["SQLite"]
        vault["Obsidian vault"]
    end

    core --> validation
    finance --> validation
    meals --> validation
    training --> validation
    validation --> secrets
    secrets --> fingerprints
    fingerprints --> sqlite
    validation --> sqlite

    sqlite --> scanner["Vault scanner"]
    vault --> scanner
    scanner --> secrets
    scanner --> sqlite

    sqlite --> reconciler["Vault reconciler"]
    vault --> reconciler
    reconciler --> secrets
    reconciler --> writer["Vault writer"]
    writer --> vault
    reconciler --> sqlite

    meals --> recipes["Recipe vault indexing"]
    recipes <--> vault
    recipes --> sqlite
```

Structured rows live in SQLite. The vault is useful because humans can read and edit notes, but vault changes still pass through parsing, validation, secret scanning, fingerprinting, and conflict handling before becoming canonical memory state. SQLite plus filesystem writes are recoverable but not globally atomic, so scanner/reconciler paths prefer per-note warnings, retries, and explicit conflict records over aborting whole runs.

## Render Boundary

Core returns `response_template` and `response_slots` for user-visible events, but it does not own final prose. That makes templates stable contracts instead of hidden strings scattered through domain logic. If a response needs new wording semantics, add a new template id in `minx_mcp/core/render_templates.py` rather than changing an existing id's meaning.

## Model Boundary

Minx does not hard-code an architecture-level model. The harness uses an OpenAI-compatible chat endpoint, commonly via OpenRouter, and the model id is deployment configuration. Setup examples use `google/gemini-2.5-flash` as the recommended OpenRouter model because it is a practical default for fast tool-calling investigations, but any compatible model can be configured and tested.

Embeddings are optional. When `MINX_OPENROUTER_API_KEY` is configured, Core can enqueue and process memory embeddings through `OpenRouterEmbedder`; otherwise memory search falls back to deterministic FTS5.

## Safety And Reliability

- SQLite migrations are packaged and applied on first connection.
- Money is stored as integer cents.
- Finance imports are constrained to the staging root.
- Memory capture defaults to candidate status before confirmation.
- Secret-shaped values are blocked or redacted before memory, vault, or embedding writes.
- Investigation steps store digests and small summaries, not raw tool outputs or transcripts.
- Hard tool-call and wall-clock budgets live in the harness; Core enforces a high soft cap as defense in depth.
- The system is local and single-user by design. It does not claim multi-tenant auth, cloud durability, or remote access isolation.

## Current Truth Versus Design Records

Current behavior is documented in:

- `README.md`
- `docs/ARCHITECTURE.md`
- `STATUS.md`
- `OPERATIONS.md`
- `docs/RUNBOOK.md`
- `docs/AGENT_GUIDE.md`
- `HANDOFF.md` for short-lived session notes only

Dated files under `docs/superpowers/specs/` and `docs/superpowers/plans/` are design records. They are valuable for understanding how the project was built, but they are not automatically authoritative if they conflict with the current docs or code.
