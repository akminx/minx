# Slice 6h: Secret Scanner Primitive + Memory/Vault Write Gate

**Date:** 2026-04-24
**Status:** Implemented 2026-04-27
**Depends on:** Slice 6g shipped: shared content fingerprint primitive and memory write-path dedup
**Related docs:**

- `HANDOFF.md` — Slice 6g-6l sequenced roadmap, planned MCP surface, implemented-slices table
- `docs/superpowers/specs/2026-04-22-slice6g-content-fingerprint-dedup.md` — template for primitive/spec discipline
- `minx_mcp/core/fingerprint.py` — leaf primitive precedent
- `minx_mcp/core/memory_service.py` — memory write paths: `create_memory`, `update_payload`, `ingest_proposals`
- `minx_mcp/core/memory_secret_scanning.py` — public memory-policy integration helpers shared by memory service, vault scanner, and vault reconciler
- `minx_mcp/vault_writer.py` — vault frontmatter and markdown body write paths

## 0) What Changed In v4

v4 fixes the blockers from the third adversarial review:

- Conflict responses that include DB-backed strings now must scan/sanitize those strings before adding them to `ConflictError.data`; `existing_subject` is either omitted or replaced with a fixed placeholder when the existing row contains a detector match.
- `ingest_proposals` explicitly carries sanitized/redacted proposal fields through **every** branch, including `_content_equivalence_merge` and its `prior_identity` event payload.
- The existing-data scanner operator command is `python scripts/scan_memory_for_secrets.py` so it works with the current non-package `scripts/` layout.
- The detector set has an in-repo source of truth: `SECRET_DETECTOR_SPECS` in `minx_mcp/core/secret_scanner.py`, with tests asserting every required credential family is represented.
- Validation-failure reports use fixed safe reason codes and sanitized fields; `MemoryProposalFailure.reason` no longer stores arbitrary `str(exc)` for secret-adjacent proposal failures.
- `ingest_proposals` ordering now states where confidence validation occurs relative to the early scanner.
- Operator docs state that the existing-data scanner command is run from the repo root.
- `create_memory` keeps its current structural validation ordering: invalid confidence can fail before secret scanning, while secret-bearing valid writes are still blocked/redacted before persistence.
- Snapshot exception logging must not include raw proposal content on unexpected ingest failures.

### v3 archive

v3 fixes the remaining blockers from the second adversarial review:

- `ingest_proposals` now scans identity/audit fields immediately after the raw structural lookup and before rejected-prior suppression, failure creation, or logging.
- Payload dict keys are scanned block-only before validation and after validation, so unknown memory types and validation-error paths cannot persist or echo secret-shaped keys.
- The spec separates blocked-response offsets from persisted audit field names with two distinct location shapes.
- Event examples now describe the actual row columns: `event_type = "created"` and `payload_json` containing the redaction metadata object.
- The existing-data scanner includes `memory_events.payload_json`, where historical replacement payloads may already live.
- The credential-URL second-pass guarantee is scoped to detector-matching query values; generic sensitive query-parameter names are out of scope for v3.

### v2 archive

v2 fixed the blocking issues from the first adversarial review:

- Replaces the proposed new `secret_redacted` event type with a `secret_redacted` metadata field on existing allowed event types (`created` and `payload_updated`). This avoids adding a migration for 6h and respects the current `memory_events.event_type` CHECK constraint.
- Scans persisted identifier fields (`memory_type`, `scope`, `subject`) block-only before any proposal logging, so secret-bearing rejected/invalid proposals cannot leak through `MemoryProposalFailure` or snapshot warnings.
- Removes secret-derived redaction fingerprints from persisted content. Redactions use `[REDACTED:<kind>]`.
- Defines exact scanner API semantics: `scan_for_secrets` reports findings without mutation; `redact_secrets` performs redaction only when every finding is redactable.
- Changes audit metadata to field-level locations only. Original offsets are allowed only in blocked `InvalidInputError` responses where the caller already supplied the secret-bearing text.
- Scans vault frontmatter keys block-only, scans the exact serialized scalar text for values, and blocks secret-shaped markdown body writes.

### v1 archive

v1 defines the Slice 6h boundary before implementation:

- Add a stdlib-only, leaf secret-scanner primitive at `minx_mcp/core/secret_scanner.py`.
- Gate synchronous memory writes before content can reach the database or later enrichment surfaces.
- Gate vault frontmatter and markdown body writes, with a stricter block-only policy for user-authored vault content.
- Define a typed verdict model (`clean` / `redacted` / `block`) and the `InvalidInputError.data` shape for blocked writes.
- Add redaction audit metadata to existing memory events without storing raw secrets.
- Add a one-shot existing-data scanner that reports already-persisted memory secrets without auto-mutating historical rows.

This spec intentionally stays design-only. No implementation code is touched until an adversarial review passes.

## 1) Goal

Introduce a shared secret-scanner primitive and hook it into the current synchronous write boundaries so credentials cannot be persisted into memory payloads, memory subjects, vault frontmatter, or vault markdown bodies before future enrichment queues and embeddings ship data to OpenRouter.

The concrete outcome for Slice 6h:

- New module `minx_mcp/core/secret_scanner.py` with `scan_for_secrets(text: str) -> ScanVerdict` and `redact_secrets(text: str) -> ScanVerdict`.
- `MemoryService.create_memory`, `MemoryService.update_payload`, and `MemoryService.ingest_proposals` scan persisted identifiers, reason/source, and payload text fields before writing.
- `VaultWriter.replace_frontmatter` / `stage_replace_frontmatter` scan serialized frontmatter values before staging the markdown write.
- `VaultWriter.write_markdown` and `VaultWriter.replace_section` block secret-shaped markdown body text before touching vault files.
- Block verdicts raise `InvalidInputError` with structured data and no raw secret material.
- Redacted memory writes are allowed, persisted with redacted values, and add `secret_redacted` metadata to the existing `created` or `payload_updated` event.
- A one-shot `scripts/scan_memory_for_secrets.py` scans existing memory rows and exits non-zero if secrets are found.

Slice 6h does not add embeddings, enrichment queue behavior, a new database table, a migration, historical vault scanning, or a public MCP tool by default.

## 2) Boundary

Core owns the scanner primitive and the write-path consumers. Harness contract is mostly unchanged: existing memory tools still return normal success envelopes and blocked writes surface as `INVALID_INPUT`.

The new `INVALID_INPUT` sub-case is distinguishable by `data.kind == "secret_detected"`. The message remains generic enough not to echo secrets:

```json
{
  "kind": "secret_detected",
  "verdict": "block",
  "surface": "memory",
  "detected_kinds": ["private_key"],
  "locations": [
    {"field": "payload.note", "start": 12, "end": 64}
  ]
}
```

Blocked-write locations carry field names and original offsets only because the caller already supplied the rejected text and may need to repair it. Persisted events, script output, and logs use field names only. No surface carries matched values, prefixes beyond the detector kind, entropy fragments, token hashes, redaction fingerprints, or previews.

### 2.1 Why `InvalidInputError`, Not A New Error Class

The current contract layer has `InvalidInputError`, `ConflictError`, `NotFoundError`, and `LLMError`. A secret-bearing write is invalid caller input, not a lifecycle conflict and not an internal bug. Reusing `InvalidInputError` preserves the existing MCP envelope while allowing callers to branch on `data.kind`.

### 2.2 No Default MCP Tool

The scanner is internal by default, matching `HANDOFF.md`. An optional admin dry-run tool (`secret_scan_verdict(text)`) is out of scope for v4 implementation. It can be added later if operators ask for it; the primitive API is sufficient for tests and internal callers.

## 3) Why This Slice Comes Before 6k/6l

Slice 6k introduces durable background work. Slice 6l introduces the first memory-layer OpenRouter embedding calls. Once secret-bearing content is enqueued or sent to a provider, the system cannot recall it. Slice 6h therefore blocks at synchronous local write paths first.

The gate must be:

- **Local:** no network calls, no LLM judgment, no external dependency.
- **Deterministic:** same input produces the same verdict and redaction.
- **Low latency:** regex/stdlib checks only, expected under 1 ms for normal memory payloads and frontmatter.
- **Conservative:** block high-risk secret classes when safe redaction cannot preserve useful structure.

## 4) The Primitive

### 4.1 Module

`minx_mcp/core/secret_scanner.py` is a leaf module. It imports only stdlib modules (`dataclasses`, `enum`, `hashlib`, `json`, `re`) and does not import `MemoryService`, `VaultWriter`, `contracts`, MCP tools, or settings.

Exports:

```python
from collections.abc import Sequence


class SecretVerdictKind(StrEnum):
    CLEAN = "clean"
    REDACTED = "redacted"
    BLOCK = "block"


@dataclass(frozen=True)
class SecretFinding:
    kind: str
    start: int
    end: int
    redactable: bool


@dataclass(frozen=True)
class ScanVerdict:
    verdict: SecretVerdictKind
    text: str
    findings: Sequence[SecretFinding]


def scan_for_secrets(text: str) -> ScanVerdict:
    """Inspect text without mutation; returns CLEAN or BLOCK plus findings."""


def redact_secrets(text: str) -> ScanVerdict:
    """Return REDACTED only when every finding is redactable; otherwise BLOCK."""
```

`SECRET_DETECTOR_SPECS` is the in-repo source of truth for detector coverage. It is a frozen tuple/dict of detector descriptors with at least `kind`, `default_policy`, and a short non-secret `description`. Tests assert that its kinds exactly cover the credential families in §4.2. Future detector changes must update `SECRET_DETECTOR_SPECS`, the §4.2 table, and the unit tests in the same commit.

`ScanVerdict.text` is always:

- identical to input for `clean`;
- redacted text for `redacted`;
- identical to input for `block` because callers must not persist it.

`scan_for_secrets` never returns `REDACTED`; it is for block-only surfaces and for pre-validation checks that only need safe metadata. `redact_secrets` returns `CLEAN`, `REDACTED`, or `BLOCK` and is the only primitive memory payload/source/reason integrations use when redaction is allowed. Callers can force block-only behavior for a field by calling `scan_for_secrets` and treating any finding as invalid input.

### 4.2 Detector Set

The detector set is exactly the repo-wide hardcoded-credentials recognition list, implemented as anchored or bounded regexes with tests. The spec intentionally describes patterns without embedding complete credential-looking literals.


| Kind                | Detection rule                                                                                                    | Default policy                                                                              |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `aws_access_key_id` | AWS access-key prefixes from the workspace rule followed by the expected uppercase alphanumeric body length       | redacted on redactable memory fields; block on memory identity fields and vault writes      |
| `stripe_key`        | Stripe public/secret live/test prefixes followed by a token body                                                  | redacted on redactable memory fields; block on memory identity fields and vault writes      |
| `google_api_key`    | Google API key prefix followed by the documented token body length                                                | redacted on redactable memory fields; block on memory identity fields and vault writes      |
| `github_token`      | GitHub token prefixes followed by a token body                                                                    | redacted on redactable memory fields; block on memory identity fields and vault writes      |
| `jwt`               | Three base64url dot-separated sections beginning with a JWT header prefix                                         | redacted on redactable memory fields; block on memory identity fields and vault writes      |
| `private_key`       | PEM private-key block from a `BEGIN PRIVATE KEY`-style marker through its matching `END PRIVATE KEY`-style marker | block everywhere                                                                            |
| `credential_url`    | URL with userinfo containing both username and password before `@`                                                | redacted on redactable memory fields; block on memory identity fields and vault writes      |


All regexes use bounded character classes and explicit lengths where the credential family has a stable length. No detector uses nested unbounded quantifiers. The private-key detector uses non-greedy matching across newlines with an upper bound so a malformed note cannot trigger catastrophic scanning over an entire vault file.

### 4.3 Redaction Token

Redaction replaces each redactable match with:

```text
[REDACTED:<kind>]
```

No secret-derived fingerprint, hash, length, or preview is persisted. Correlating repeated redactions is out of scope for v4 because any stable secret-derived handle could become provider-visible when Slice 6l embeds memory text.

For a connection string with inline credentials, the `credential_url` finding spans only the password-bearing userinfo, not the entire URL. Redaction preserves the scheme, host, port, path, and query while replacing userinfo with `[REDACTED:credential_url]@`. The scanner then runs a second pass over the redacted URL text so query-string values that match the detector set are still found and redacted. Generic sensitive query parameter names such as `token=` or `password=` are out of scope unless their values match one of the credential families above. If parsing is ambiguous, the verdict is `block` rather than an unsafe partial rewrite.

For JWTs and token-like strings, the whole token is replaced.

Private-key blocks are never redacted because preserving a syntactically valid PEM with a substituted body is more likely to confuse downstream tools than help the user.

### 4.4 Scanning Structured Memory Content

The primitive scans strings. Memory integration owns flattening structured inputs into named fields:

```python
@dataclass(frozen=True)
class SecretErrorLocation:
    field: str
    start: int
    end: int


@dataclass(frozen=True)
class SecretAuditLocation:
    field: str


@dataclass(frozen=True)
class MemorySecretScanResult:
    verdict: SecretVerdictKind
    memory_type: str
    scope: str
    subject: str
    source: str
    reason: str
    payload: dict[str, object]
    detected_kinds: Sequence[str]
    error_locations: Sequence[SecretErrorLocation]
    audit_locations: Sequence[SecretAuditLocation]


def scan_memory_input(
    *,
    memory_type: str,
    scope: str,
    subject: str,
    payload: dict[str, object],
    source: str,
    reason: str,
) -> MemorySecretScanResult:
    """Return redacted copies plus field-aware locations for memory integration."""
```

`MemorySecretScanResult`, `SecretErrorLocation`, `SecretAuditLocation`, and `scan_memory_input` live in `minx_mcp/core/memory_secret_scanning.py`. They import the primitive from `minx_mcp.core.secret_scanner`; they are not part of the leaf `secret_scanner` export surface.

Fields scanned:

- `subject`
- `memory_type`
- `scope`
- `source`
- `reason`
- every dict key in `payload`, recursively through lists/dicts, block-only
- every string value in `payload`, recursively through lists/dicts

`memory_type`, `scope`, and `subject` are block-only identity fields. Payload dict keys are block-only because keys are persisted for unknown memory types and may appear in validation errors for known types. Payload/source/reason values may be redacted when every finding is redactable. Non-string payload values are not stringified for memory scanning. This avoids false positives from numeric IDs and keeps the scanner focused on content a user or detector actually supplied as text. Dict/list traversal is deterministic by insertion order; redaction preserves structure.

### 4.5 Sanitizing Existing Database Strings

The ingress scanner prevents new secret-bearing fields from being written, but legacy rows may pre-date Slice 6h. Any error, log, report, or event payload that includes a string read from an existing database row must treat that value as untrusted output and either:

1. omit the value entirely when an identifier is enough; or
2. run `scan_for_secrets` on the value and replace it with a fixed placeholder such as `"[REDACTED_EXISTING_SUBJECT]"` when any finding is present.

No DB-backed string may be copied into `ConflictError.data`, `MemoryProposalFailure`, `MemoryProposalSuppression`, `memory_events.payload_json`, or logs unless it is already known to be clean or has been sanitized through this rule.

### 4.6 Non-Goals

- Not a full DLP engine.
- Not entropy-based secret detection in v4.
- Not historical vault body scanning. Existing vault files are not walked for body secrets by this slice.
- Not scanning arbitrary files outside configured vault writer paths.
- Not a guarantee that no secret-like content can ever be stored. It is a deterministic guard for the credential families listed above.

## 5) Memory Write-Path Changes

### 5.1 `create_memory`

`create_memory` currently validates payload, non-empty strings, confidence, actor, computes a content fingerprint, and delegates to `_insert_memory_and_events`.

Slice 6h inserts scanning before any payload validation error can echo a secret-bearing key and before fingerprint computation:

1. Validate `memory_type`, `scope`, `subject`, `source`, confidence, actor.
2. Scan `memory_type`, `scope`, `subject`, `source`, `reason`, and raw payload dict keys.
3. If any finding is in `memory_type`, `scope`, `subject`, or a payload key, or any finding is otherwise `block`: raise `InvalidInputError("Secret detected in memory input", data=secret_detected_payload)` where `secret_detected_payload` has the concrete §7 memory error shape.
4. Validate/coerce payload with `validate_memory_payload`.
5. Scan validated payload dict keys again (block-only) and string values (redactable when allowed).
6. If redacted: use redacted source/reason/payload for fingerprint computation and persistence, then put `secret_redacted` metadata in the `created` event payload in the same transaction.
7. If clean: current flow unchanged.

The redacted value participates in `content_fingerprint`. This is deliberate: persisted content identity must reflect the actual persisted content, not the unpersisted secret.

This deliberately preserves the current structural-validation ordering for direct `create_memory` calls: an invalid confidence value may return the existing confidence validation error before the scanner runs. That is acceptable because confidence is numeric and is not persisted as secret-bearing text. For valid structural inputs, scanning still happens before payload validation, fingerprinting, database writes, logs, events, or MCP success responses can expose secret-bearing text.

### 5.2 `update_payload`

`update_payload` scans raw replacement payload keys before schema validation, then scans the validated replacement payload before computing the replacement fingerprint:

- `block` raises `InvalidInputError`; no transaction starts and the prior row remains unchanged.
- `redacted` writes the redacted replacement payload, recomputes fingerprint over the redacted payload, and adds `secret_redacted` metadata to the existing `payload_updated` event payload.
- `clean` keeps current behavior.

`update_payload` does not scan the existing row. Historical rows are handled by the one-shot scanner in §8.

### 5.3 `ingest_proposals`

`ingest_proposals` scans persisted identifiers and audit strings before payload validation and before any warning log. Payload scanning still runs after payload validation and before content fingerprint computation. This preserves the existing ordering where rejected structural priors are suppressed before payload validation, but prevents invalid proposals from leaking secret-bearing subjects/sources through logs.

The early scan happens after the raw structural lookup and before confidence validation. A malformed confidence value still becomes an invalid proposal, but only after the proposal's identity/audit fields and raw payload keys have been scanned and sanitized. Confidence itself is numeric and is not scanned.

Per proposal:

1. Structural lookup uses the raw proposal triple so existing rejection semantics still work.
2. Immediately scan `proposal.memory_type`, `proposal.scope`, `proposal.subject`, `proposal.source`, `proposal.reason`, and raw payload dict keys.
3. Any finding in `memory_type`, `scope`, `subject`, or a raw payload key, or any non-redactable `redact_secrets` result for `source` or `reason`: append a sanitized `MemoryProposalFailure` using the §7 safe failure shape, log a warning with detector kinds but without secret values, continue.
4. If structural prior is rejected and step 2 was clean: suppress and continue. The snapshot suppression log formats only the clean subject.
5. Validate confidence and payload. If either validation fails, log sanitized memory_type/scope/subject/source only and append `MemoryProposalFailure(reason="invalid_confidence")` or `MemoryProposalFailure(reason="invalid_payload")` instead of storing arbitrary exception text.
6. Scan validated payload dict keys again (block-only) and string values.
7. Payload `block`: append a sanitized `MemoryProposalFailure` with reason `"secret_detected"`, log detector kinds but no raw values, continue.
8. Payload/source/reason `redacted`: continue through the existing fingerprint lookup/insert/merge flow using redacted source/reason/payload and add `secret_redacted` metadata to `created` or `payload_updated` if a row is created or updated.
9. `clean`: current 6g flow unchanged.

Blocked detector proposals are failures, not suppressions. Suppression means "the user's prior rejection is working"; a secret-bearing detector proposal is invalid input that should be visible in the report.

All branches after step 2 must use the scanned result's safe values, not the original proposal object, whenever they format output or persist data. This includes:

- fresh insert;
- same-triple in-place merge;
- content-fingerprint rejected-prior suppression;
- content-equivalence merge through `_content_equivalence_merge`;
- `payload_updated` event payloads, including `prior_identity`.

If `_content_equivalence_merge` emits `prior_identity`, that object must contain only sanitized/redacted proposal identity fields. It must never copy raw pre-scan `proposal.memory_type`, `proposal.scope`, or `proposal.subject` into `memory_events.payload_json`.

The entire `payload_updated` event JSON follows the same rule, not just `prior_identity`. If the event body embeds `payload`, `prior_payload`, or any other row-derived string content, those values must be the sanitized/redacted values that will be safe under §4.5. A merge implementation must not sanitize `prior_identity` while leaving a sibling `payload` object with raw historical content.

### 5.4 Memory Redaction Event Metadata

`memory_events.event_type` is CHECK-constrained by migration `018_vault_index.sql`, so Slice 6h does not add a new event type. Redaction audit data is stored inside existing allowed event payloads.

For `create_memory` / insert paths, `_insert_memory_and_events` gains an optional `created_event_payload: dict[str, object] | None = None` kwarg. It still owns the `BEGIN IMMEDIATE` transaction and inserts `created` before optional `promoted`, but the `created` payload becomes:

```json
{
  "secret_redacted": {
    "detected_kinds": ["stripe_key"],
    "fields": ["payload.value"]
  }
}
```

That JSON is the `payload_json` value for a `memory_events` row whose `event_type` column is `"created"`.

For `update_payload` and ingest merge paths, the existing `payload_updated` event payload gains a sibling `secret_redacted` object next to the existing `payload` key. The event payload never includes the original secret, original offsets, secret length, redaction hashes, or previews. `detected_kinds` and `fields` are sorted and de-duplicated for deterministic assertions.

For ingest merge paths, this guarantee applies to the full event body: `payload`, `prior_identity`, and any future sibling keys are all sanitized before insertion into `memory_events.payload_json`.

### 5.5 Subject Redaction Policy

Memory `memory_type`, `scope`, and `subject` are identity fields and already appear in DTOs, logs, and conflict envelopes. A detected secret in any of these fields is therefore `block`, not redacted. This is stricter than payload/reason/source behavior and prevents creating awkward memory identities like `[REDACTED:github_token]`.

Memory payload, source, and reason can be redacted when every finding is redactable.

### 5.6 Conflict Response Sanitization

Slice 6h also hardens existing conflict paths. `_insert_memory_and_events` currently distinguishes structural-triple conflicts from content-fingerprint conflicts with state-based probes. Those probes may read strings from historical rows, and historical rows may pre-date the scanner.

Rules:

- New input fields included in `ConflictError.data` are the already-scanned sanitized values.
- Existing row identifiers may include numeric IDs (`memory_id`, `blocking_memory_id`) without scanning.
- Existing row string fields, including `existing_subject`, must not be copied raw into `ConflictError.data`.
- Preferred behavior for `content_fingerprint` conflicts is to omit `existing_subject` entirely and return `memory_id` only. If compatibility requires retaining the key, set it to `"[REDACTED_EXISTING_SUBJECT]"` whenever `scan_for_secrets(existing_subject)` has any finding.
- Tests must seed a pre-6h row with a secret-shaped subject and assert a clean conflicting create/update cannot echo that subject through the MCP envelope.

## 6) Vault Write Changes

`VaultWriter.replace_frontmatter` delegates to `stage_replace_frontmatter`, which serializes frontmatter and stages a locked file write. Slice 6h scans frontmatter before staging, using the same text that serialization will write, and also scans the preserved markdown body so frontmatter-only changes cannot re-stage a note that already contains secret-shaped content.

`VaultWriter.replace_section` scans both the replacement body and the target heading as vault body text before constructing markdown, so secret-shaped headings cannot bypass the vault body gate.

Vault policy is block-only:

- Any finding in frontmatter keys or serialized values raises `InvalidInputError("Secret detected in vault frontmatter", data=secret_detected_payload)` where `secret_detected_payload` has the concrete §7 vault-frontmatter error shape.
- No automatic redaction occurs for vault frontmatter because the content is user-authored, committed to a personal knowledge base, and silent mutation could destroy intentional metadata.
- The existing lock/stage behavior is unchanged; on block, no lock should be held after the exception and no temp file should be left behind.

Fields scanned:

- every key, block-only
- every value after applying `_serialize_yaml_scalar(value)`, including dict/list JSON serialization

This matches the current writer behavior: `_serialize_frontmatter` validates simple keys and writes `f"{key}: {_serialize_yaml_scalar(value)}"`. Scanning raw Python values would miss secrets that appear only after `str(value)` or JSON serialization.

Markdown body policy is also block-only:

- `VaultWriter.write_markdown` scans the body after frontmatter, then writes atomically only if the body is clean.
- `VaultWriter.replace_section` scans the replacement body before reading or writing the note.
- Blocked markdown body writes raise `InvalidInputError("Secret detected in vault body", data=secret_detected_payload)` and leave existing files unchanged. If the target file does not exist, no new file is created.
- The body scanner only guards text being written through `VaultWriter`; it does not scan untouched sections during a section replacement and does not perform historical vault sweeps.

## 7) Error Contract

Blocked memory writes raise:

```python
InvalidInputError(
    "Secret detected in memory input",
    data={
        "kind": "secret_detected",
        "verdict": "block",
        "surface": "memory",
        "detected_kinds": ["private_key"],
        "locations": [{"field": "payload.note", "start": 12, "end": 64}],
    },
)
```

Blocked vault frontmatter writes use `surface: "vault_frontmatter"`. Blocked vault markdown body writes use `surface: "vault_body"`.

For MCP callers, `wrap_tool_call` passes this through as:

```json
{
  "success": false,
  "data": {
    "kind": "secret_detected",
    "verdict": "block",
    "surface": "memory",
    "detected_kinds": ["private_key"],
    "locations": [{"field": "payload.note", "start": 12, "end": 64}]
  },
  "error": "Secret detected in memory input",
  "error_code": "INVALID_INPUT"
}
```

Redacted writes do not raise. Programmatic callers learn about redaction through the persisted content and the `secret_redacted` event.

`MemoryProposalFailure` records created for secret-detected proposals must be sanitized before they reach `IngestProposalsReport` or `snapshot.py` formatting:

```python
MemoryProposalFailure(
    memory_type=safe_memory_type,
    scope=safe_scope,
    subject="[REDACTED_SUBJECT]",
    reason="secret_detected",
)
```

`safe_memory_type` and `safe_scope` are the original values only if their pre-validation scan is clean; otherwise they are `"[REDACTED_MEMORY_TYPE]"` and `"[REDACTED_SCOPE]"`. Snapshot warnings must format these sanitized fields only.

Unexpected exception logging in `snapshot.py` must also avoid raw proposal content. The snapshot layer may log the exception class/message and counts, but it must not format `proposal.subject`, `proposal.source`, `proposal.reason`, payload keys, payload values, or an unsanitized `IngestProposalsReport` when `ingest_proposals` raises unexpectedly.

For non-secret validation failures inside `ingest_proposals`, `MemoryProposalFailure.reason` must be a fixed safe code:

- `"invalid_confidence"` for confidence validation failures;
- `"invalid_payload"` for payload validation failures;
- `"secret_detected"` for scanner failures.

Do not store `str(exc)` in `MemoryProposalFailure.reason` after Slice 6h. Pydantic validation messages may include field paths or key names, and payload keys are attacker-controlled.

## 8) Existing-Data Scanner

### 8.1 Script Shape

`scripts/scan_memory_for_secrets.py`:

- Accepts optional DB path argument; defaults to `get_settings().db_path` from `minx_mcp.config`.
- Reads `memories` rows ordered by `id`.
- Reads `memory_events` rows ordered by `id`.
- Scans memory `memory_type`, `scope`, `subject`, `source`, `reason`, payload dict keys, and payload string fields.
- Scans `memory_events.payload_json` dict keys and string values, because historical `payload_updated` events can contain full replacement payloads even if the current memory row is later cleaned.
- Prints a summary with counts by `kind` plus row id and field name only.
- Exits `0` if no findings, `2` if findings are present.
- Does not mutate rows in v4.

The supported operator invocation is:

```bash
python scripts/scan_memory_for_secrets.py
```

Run the command from the repository root. This intentionally does not use `python -m scripts.scan_memory_for_secrets` because `scripts/` is not currently an importable package in this repository.

### 8.2 Why Report-Only

Historical memory rows may already have been used in event trails, content fingerprints, or user-facing decisions. Auto-redacting them in a one-shot script would require a second set of merge/fingerprint/audit semantics and could hide evidence an operator needs to clean up. Report-only is safer for v4:

1. Operator sees exactly which rows/fields need action.
2. Operator can use existing `memory_reject`, `memory_expire`, or a future targeted scrub tool.
3. No new migration or backfill mutation is required for 6h.

### 8.3 Operator Step

Add a post-upgrade note to `HANDOFF.md`:

> After pulling Slice 6h, run `python scripts/scan_memory_for_secrets.py` once from the repository root. Slice 6h blocks new secret-bearing memory and vault writes, but historical memory rows may pre-date the scanner. The script is read-only and reports row ids, fields, and detector kinds without printing raw secrets. Exit code `2` means findings require manual review.

## 9) Performance

### 9.1 Write Cost

Memory writes scan a small set of strings and a bounded payload dict. The detector list is fixed-size, regex-only, and stdlib. Expected overhead is below the SQLite write transaction cost for normal memory payloads.

### 9.2 ReDoS Control

Every regex must be reviewed for:

- no nested unbounded quantifiers;
- explicit token lengths where applicable;
- bounded private-key block scanning;
- tests with a long non-matching input that completes quickly.

### 9.3 No Network Calls

The scanner never calls OpenRouter, a cloud DLP API, or any external service. This is a pre-6l local safety gate.

## 10) Test Plan

### 10.1 Unit Tests — `tests/test_secret_scanner.py`

- `clean` input returns `SecretVerdictKind.CLEAN`, original text, no findings.
- One parametrized case per detector kind returns either `redacted` or `block` according to §4.2.
- Redaction token format is `[REDACTED:<kind>]`; no secret-derived hash or preview appears in persisted text.
- Repeated identical secrets produce the same non-secret-derived replacement string.
- Two different secrets of the same kind produce the same replacement string.
- Private-key block returns `block`.
- Connection string redaction preserves scheme/host/path while removing userinfo.
- A URL containing userinfo and a detector-matching query-string token redacts both; the detector-matching query token must not survive the URL redaction pass.
- Scanner returns findings sorted by `start`.
- Overlapping findings are resolved deterministically by longest match, then earliest start.
- Long non-matching input completes without catastrophic backtracking.
- `SECRET_DETECTOR_SPECS` contains exactly one descriptor for each §4.2 detector kind and no undocumented detector kinds.

Test fixtures must be synthetic. Do not paste real credentials, and do not commit full contiguous credential-looking token literals. Tests use helper builders that assemble fake matches from non-contiguous pieces inside the test function (for example, prefix pieces plus repeated inert characters). Every integration test that generates a fake secret also asserts that logs, errors, events, and script output do not contain the generated value.

### 10.2 Memory Integration Tests — `tests/test_memory_service.py`

- `create_memory` with a clean payload preserves current behavior.
- `create_memory` with a redactable secret in `payload.value` persists the redacted payload and stores `secret_redacted` metadata in the `created` event payload.
- `create_memory` with a secret in `subject` raises `InvalidInputError` with `data.kind == "secret_detected"` and writes no row.
- `create_memory` with a secret in `memory_type` or `scope` raises `InvalidInputError` and writes no row.
- `create_memory` with a secret-shaped payload dict key raises before `validate_memory_payload` can echo the key in a validation error.
- `create_memory` with a private-key block in `payload.note` raises `InvalidInputError` and writes no row.
- `create_memory` redaction computes `content_fingerprint` over the redacted payload; recomputing from persisted row matches the stored fingerprint, and no `secret_redacted` event type is inserted.
- `update_payload` with a redactable secret writes the redacted replacement payload, updates `content_fingerprint`, and stores `secret_redacted` metadata inside the `payload_updated` event.
- `update_payload` with a block finding raises and leaves payload/fingerprint/event count unchanged.
- `ingest_proposals` with a redactable payload inserts/merges using redacted content and stores `secret_redacted` metadata in `created` or `payload_updated`.
- `ingest_proposals` with a block finding records a `MemoryProposalFailure`, not a suppression, and writes no row for that proposal.
- `ingest_proposals` with a secret in subject/source/reason and an invalid payload scans before validation, returns/logs sanitized failure fields, and never logs the raw secret.
- `ingest_proposals` with a rejected structural prior and a secret-bearing subject returns a sanitized `MemoryProposalFailure`, not a suppression, and snapshot warnings do not include the raw subject.
- `ingest_proposals` with a secret-shaped nested payload dict key records a sanitized `MemoryProposalFailure` and writes no row.
- Rejected structural prior still suppresses before payload validation after the clean identity/audit pre-scan, preserving the existing "rejection beats payload validation" 6g ordering without leaking secrets.
- `ingest_proposals` with invalid confidence or invalid payload records fixed reason codes (`"invalid_confidence"` / `"invalid_payload"`) and never stores arbitrary exception text in `MemoryProposalFailure.reason`.
- `ingest_proposals` content-equivalence merge with a redacted payload/source/reason writes only redacted values and includes `secret_redacted` metadata in the resulting `payload_updated` event.
- `ingest_proposals` content-equivalence merge never writes raw pre-scan proposal identity fields into `prior_identity`.
- A content-fingerprint conflict against a pre-6h row whose `existing_subject` contains a detector match does not return that raw subject in `ConflictError.data` or the MCP envelope.

### 10.3 MCP Boundary Tests — `tests/test_core_memory_tools.py`

- `memory_create` with a blocked secret returns `success: false`, `error_code: "INVALID_INPUT"`, and `data.kind == "secret_detected"`.
- The response data includes detector kinds and locations but does not include the matched secret or any secret-derived hash.
- `memory_create` with a redacted payload returns success and the returned memory DTO contains the redacted value.

### 10.4 Vault Tests — `tests/test_vault_writer.py`

- `replace_frontmatter` blocks frontmatter string values containing a secret and leaves the file unchanged.
- `replace_frontmatter` blocks frontmatter keys containing a secret and leaves the file unchanged.
- `stage_replace_frontmatter` releases the lock when scanning raises.
- Nested frontmatter dict/list values are scanned after `_serialize_yaml_scalar` JSON serialization.
- Block error data uses `surface: "vault_frontmatter"` and contains no raw secret.
- `write_markdown` blocks body text containing a secret, leaves existing files unchanged, and does not create new files on block.
- `replace_section` blocks replacement body text containing a secret and leaves the existing section unchanged.
- Markdown-body block error data uses `surface: "vault_body"` and contains no raw secret.

### 10.5 Existing-Data Scanner Tests — `tests/test_scan_memory_for_secrets.py`

- Empty/clean DB exits 0.
- DB with one seeded historical secret exits 2 and reports row id, field, and kind only.
- DB with a historical secret in `memory_events.payload_json` exits 2 and reports event id, field, and kind only.
- Script does not mutate rows.
- Malformed payload JSON is reported as a scan failure without aborting the whole run; exit code is 2.

### 10.6 Regression Gates

- `uv run mypy minx_mcp`
- `uv run ruff check minx_mcp tests`
- `uv run pytest tests/ -x -q`
- Existing Slice 6g tests still pass, especially fingerprint golden tests and memory content-equivalence tests.

## 11) Forward Compatibility

Future slices must treat the scanner as a synchronous ingress gate:

1. 6k enrichment queue enqueue paths must either accept only already-scanned memory IDs or explicitly call the scanner before queue insert.
2. 6l embedding workers must never scan by calling an LLM; they should trust 6h's local write gate and may defensively skip rows whose persisted content still trips `scan_for_secrets`.
3. Any new memory-like table with user/detector text must scan before persistence.
4. If detector patterns change, unit tests must add fixed examples and the existing-data scanner remains read-only unless a separate migration/scrub spec is written.

## 12) Rollback

Slice 6h is additive and mostly code-level:

1. Remove `minx_mcp/core/secret_scanner.py`.
2. Revert memory-service scanning calls and `secret_redacted` event-payload metadata.
3. Revert vault frontmatter/body scanning calls.
4. Remove `scripts/scan_memory_for_secrets.py`.
5. Remove tests added in §10.
6. Remove the `HANDOFF.md` post-upgrade operator step and update the implemented-slices row back to "not implemented" if this slice is backed out before shipping.

No schema rollback is required in v4 because no new migration is introduced.

## 13) Out of Scope

- Secret scanning in vault note bodies.
- Redacting historical rows automatically.
- A public/admin MCP dry-run tool.
- Entropy-based generic token detection.
- Provider-side DLP checks.
- Enrichment queue or embedding worker changes beyond the forward-compatibility rules.

## 14) Verification Checklist

- `minx_mcp/core/secret_scanner.py` exists as a leaf stdlib-only primitive.
- Detector set covers every credential family from `codeguard-1-hardcoded-credentials`.
- `SECRET_DETECTOR_SPECS` is the in-repo detector source of truth and tests assert it matches §4.2.
- No code, test fixture, or spec embeds a real credential; synthetic fixed-length examples are constructed to be obviously fake.
- Memory identity-field findings (`memory_type`, `scope`, `subject`) and payload-key findings block; memory payload/source/reason value findings redact only when every finding is redactable.
- Private-key findings block everywhere.
- Redaction tokens never include raw secret text.
- `InvalidInputError.data` uses `kind="secret_detected"` and contains only surface, verdict, detector kinds, and locations.
- `MemoryService.create_memory`, `update_payload`, and `ingest_proposals` scan before content fingerprint computation and before any proposal warning that formats subject/source.
- `ingest_proposals` uses sanitized/redacted proposal fields on all branches, including `_content_equivalence_merge` and `prior_identity`.
- `ConflictError.data` never echoes unscanned existing row string fields such as `existing_subject`.
- `MemoryProposalFailure.reason` uses fixed safe reason codes for scanner/confidence/payload validation failures.
- Snapshot formatting of `IngestProposalsReport` never receives raw secret-bearing subject/scope/type strings.
- Snapshot unexpected-exception logging does not format raw proposals, raw payloads, or unsanitized ingest reports.
- Redacted memory writes compute `content_fingerprint` from persisted redacted content.
- `secret_redacted` event metadata contains kinds and field names only.
- `VaultWriter.stage_replace_frontmatter`, `VaultWriter.write_markdown`, and `VaultWriter.replace_section` block before staging writes and release locks on scan failure.
- `scripts/scan_memory_for_secrets.py` is read-only, supports `python scripts/scan_memory_for_secrets.py`, and exits 2 on findings.
- `scripts/scan_memory_for_secrets.py` scans both `memories` and `memory_events.payload_json`.
- `HANDOFF.md` gains the Slice 6h post-upgrade operator step if the scanner script ships.
- `uv run pytest tests/ -x -q`, `uv run ruff check minx_mcp tests`, and `uv run mypy minx_mcp` all pass.

