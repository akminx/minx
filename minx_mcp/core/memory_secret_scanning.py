"""Memory-specific secret scanning integration helpers.

This module sits between the leaf ``secret_scanner`` primitive and memory/vault
write paths. It intentionally avoids importing ``MemoryService`` so vault sync
code can share the memory secret policy without depending on service internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_payloads import validate_memory_payload
from minx_mcp.core.secret_scanner import SecretVerdictKind, redact_secrets, scan_for_secrets

__all__ = [
    "MemorySecretScanResult",
    "SecretAuditLocation",
    "SecretErrorLocation",
    "merge_event_payload",
    "prepare_validated_memory_write",
    "raise_secret_detected",
    "redaction_event_payload",
    "sanitize_existing_subject",
    "scan_event_reason",
    "scan_memory_input",
    "scan_payload_only",
]


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
    detected_kinds: tuple[str, ...]
    error_locations: tuple[SecretErrorLocation, ...]
    audit_locations: tuple[SecretAuditLocation, ...]


@dataclass(frozen=True)
class PreparedMemoryWrite:
    memory_type: str
    scope: str
    subject: str
    payload: dict[str, object]
    source: str
    reason: str
    redaction_payload: dict[str, object] | None


@dataclass
class _SecretScanAccumulator:
    blocked: bool = False
    redacted: bool = False
    detected_kinds: set[str] = field(default_factory=set)
    error_locations: list[SecretErrorLocation] = field(default_factory=list)
    audit_fields: set[str] = field(default_factory=set)

    def add_error(self, *, field: str, start: int, end: int, kind: str) -> None:
        self.blocked = True
        self.detected_kinds.add(kind)
        self.error_locations.append(SecretErrorLocation(field=field, start=start, end=end))
        self.audit_fields.add(field)

    def add_redaction(self, *, field: str, kind: str) -> None:
        self.redacted = True
        self.detected_kinds.add(kind)
        self.audit_fields.add(field)


_REDACTED_MEMORY_TYPE = "[REDACTED_MEMORY_TYPE]"
_REDACTED_SCOPE = "[REDACTED_SCOPE]"
_REDACTED_SUBJECT = "[REDACTED_SUBJECT]"
_REDACTED_EXISTING_SUBJECT = "[REDACTED_EXISTING_SUBJECT]"


def scan_memory_input(
    *,
    memory_type: str,
    scope: str,
    subject: str,
    payload: dict[str, object],
    source: str,
    reason: str,
    scan_payload_values: bool = True,
) -> MemorySecretScanResult:
    acc = _SecretScanAccumulator()
    safe_memory_type = _scan_identity_field("memory_type", memory_type, _REDACTED_MEMORY_TYPE, acc)
    safe_scope = _scan_identity_field("scope", scope, _REDACTED_SCOPE, acc)
    safe_subject = _scan_identity_field("subject", subject, _REDACTED_SUBJECT, acc)
    safe_source = _scan_redactable_field("source", source, acc)
    safe_reason = _scan_redactable_field("reason", reason, acc)
    safe_payload = _scan_payload_value(payload, "payload", acc, scan_values=scan_payload_values)
    if acc.blocked:
        verdict = SecretVerdictKind.BLOCK
    elif acc.redacted:
        verdict = SecretVerdictKind.REDACTED
    else:
        verdict = SecretVerdictKind.CLEAN
    return MemorySecretScanResult(
        verdict=verdict,
        memory_type=safe_memory_type,
        scope=safe_scope,
        subject=safe_subject,
        source=safe_source,
        reason=safe_reason,
        payload=cast(dict[str, object], safe_payload),
        detected_kinds=tuple(sorted(acc.detected_kinds)),
        error_locations=tuple(acc.error_locations),
        audit_locations=tuple(SecretAuditLocation(field=field) for field in sorted(acc.audit_fields)),
    )


def scan_payload_only(payload: dict[str, object], *, scan_payload_values: bool = True) -> MemorySecretScanResult:
    return scan_memory_input(
        memory_type="memory",
        scope="memory",
        subject="memory",
        payload=payload,
        source="memory",
        reason="",
        scan_payload_values=scan_payload_values,
    )


def prepare_validated_memory_write(
    *,
    memory_type: str,
    scope: str,
    subject: str,
    payload: dict[str, object],
    source: str,
    reason: str,
) -> PreparedMemoryWrite:
    """Scan, schema-validate, and rescan a memory write input.

    The first pass skips payload values so structured validation can normalize
    the payload shape before the second pass redacts or blocks user-visible
    values. Keeping this sequence in one place prevents the memory service,
    vault scanner, and reconciler from drifting apart.
    """

    raw_scan = scan_memory_input(
        memory_type=memory_type,
        scope=scope,
        subject=subject,
        payload=dict(payload),
        source=source,
        reason=reason,
        scan_payload_values=False,
    )
    if raw_scan.verdict is SecretVerdictKind.BLOCK:
        raise_secret_detected(raw_scan)

    validated_payload = validate_memory_payload(raw_scan.memory_type, raw_scan.payload)
    validated_scan = scan_memory_input(
        memory_type=raw_scan.memory_type,
        scope=raw_scan.scope,
        subject=raw_scan.subject,
        payload=validated_payload,
        source=raw_scan.source,
        reason=raw_scan.reason,
    )
    if validated_scan.verdict is SecretVerdictKind.BLOCK:
        raise_secret_detected(validated_scan)

    return PreparedMemoryWrite(
        memory_type=validated_scan.memory_type,
        scope=validated_scan.scope,
        subject=validated_scan.subject,
        payload=validated_scan.payload,
        source=validated_scan.source,
        reason=validated_scan.reason,
        redaction_payload=redaction_event_payload(raw_scan, validated_scan),
    )


def _scan_identity_field(field: str, value: str, redacted_value: str, acc: _SecretScanAccumulator) -> str:
    verdict = scan_for_secrets(value)
    if verdict.findings:
        for finding in verdict.findings:
            acc.add_error(field=field, start=finding.start, end=finding.end, kind=finding.kind)
        return redacted_value
    return value


def _scan_redactable_field(field: str, value: str, acc: _SecretScanAccumulator) -> str:
    verdict = redact_secrets(value)
    if verdict.verdict is SecretVerdictKind.BLOCK:
        for finding in verdict.findings:
            acc.add_error(field=field, start=finding.start, end=finding.end, kind=finding.kind)
        return value
    if verdict.verdict is SecretVerdictKind.REDACTED:
        for finding in verdict.findings:
            acc.add_redaction(field=field, kind=finding.kind)
        return verdict.text
    return value


def _scan_payload_value(
    value: object,
    field: str,
    acc: _SecretScanAccumulator,
    *,
    scan_values: bool,
) -> object:
    if isinstance(value, dict):
        scanned: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            key_field = f"{field}.{key_text}"
            safe_key = key_text
            key_verdict = scan_for_secrets(key_text)
            if key_verdict.findings:
                safe_key = _unique_redacted_key(scanned)
                key_field = f"{field}.{safe_key}"
                for finding in key_verdict.findings:
                    acc.add_error(field=key_field, start=finding.start, end=finding.end, kind=finding.kind)
            elif safe_key in scanned:
                safe_key = _unique_payload_key(scanned, safe_key)
                key_field = f"{field}.{safe_key}"
            scanned[safe_key] = _scan_payload_value(item, key_field, acc, scan_values=scan_values)
        return scanned
    if isinstance(value, list):
        return [
            _scan_payload_value(item, f"{field}[{index}]", acc, scan_values=scan_values)
            for index, item in enumerate(value)
        ]
    if isinstance(value, str) and scan_values:
        return _scan_redactable_field(field, value, acc)
    return value


def _unique_redacted_key(scanned: dict[str, object]) -> str:
    return _unique_payload_key(scanned, "[REDACTED_KEY]")


def _unique_payload_key(scanned: dict[str, object], base: str) -> str:
    if base not in scanned:
        return base
    suffix = 2
    candidate = _suffixed_payload_key(base, suffix)
    while candidate in scanned:
        suffix += 1
        candidate = _suffixed_payload_key(base, suffix)
    return candidate


def _suffixed_payload_key(base: str, suffix: int) -> str:
    if base.endswith("]"):
        return f"{base[:-1]}_{suffix}]"
    return f"{base}_{suffix}"


def raise_secret_detected(result: MemorySecretScanResult, *, surface: str = "memory") -> None:
    raise InvalidInputError(
        "Secret detected in memory input",
        data={
            "kind": "secret_detected",
            "verdict": "block",
            "surface": surface,
            "detected_kinds": list(result.detected_kinds),
            "locations": [
                {"field": loc.field, "start": loc.start, "end": loc.end}
                for loc in result.error_locations
            ],
        },
    )


def redaction_event_payload(*results: MemorySecretScanResult) -> dict[str, object] | None:
    kinds = sorted(
        {
            kind
            for result in results
            for kind in result.detected_kinds
            if result.verdict is not SecretVerdictKind.CLEAN
        }
    )
    fields = sorted(
        {
            location.field
            for result in results
            if result.verdict is SecretVerdictKind.REDACTED
            for location in result.audit_locations
        }
    )
    if not kinds or not fields:
        return None
    return {"secret_redacted": {"detected_kinds": kinds, "fields": fields}}


def merge_event_payload(base: dict[str, object], *results: MemorySecretScanResult) -> dict[str, object]:
    metadata = redaction_event_payload(*results)
    if metadata is None:
        return base
    return {**base, **metadata}


def scan_event_reason(reason: str) -> tuple[str, dict[str, object] | None]:
    result = scan_memory_input(
        memory_type="memory",
        scope="memory",
        subject="memory",
        payload={},
        source="memory",
        reason=reason,
    )
    if result.verdict is SecretVerdictKind.BLOCK:
        raise_secret_detected(result)
    return result.reason, redaction_event_payload(result)


def sanitize_existing_subject(subject: str) -> str:
    verdict = scan_for_secrets(subject)
    if verdict.findings:
        return _REDACTED_EXISTING_SUBJECT
    return subject


