"""Deterministic local secret scanner primitive (Slice 6h).

This module is intentionally leaf-only: it imports no project modules and does
not call network, LLM, database, vault, or settings code.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "SECRET_DETECTOR_SPECS",
    "ScanVerdict",
    "SecretDetectorSpec",
    "SecretFinding",
    "SecretVerdictKind",
    "redact_secrets",
    "scan_for_secrets",
]


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


@dataclass(frozen=True)
class SecretDetectorSpec:
    kind: str
    default_policy: str
    description: str
    pattern: re.Pattern[str]
    group: int = 0


SECRET_DETECTOR_SPECS: tuple[SecretDetectorSpec, ...] = (
    SecretDetectorSpec(
        kind="aws_access_key_id",
        default_policy="redact",
        description="AWS access key ID prefixes followed by the expected body length.",
        pattern=re.compile(r"(?<![A-Z0-9])(?:AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    ),
    SecretDetectorSpec(
        kind="stripe_key",
        default_policy="redact",
        description="Stripe live/test public or secret key prefixes followed by a token body.",
        pattern=re.compile(r"(?<![A-Za-z0-9])(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,255}(?![A-Za-z0-9])"),
    ),
    SecretDetectorSpec(
        kind="google_api_key",
        default_policy="redact",
        description="Google API key prefix followed by the documented body length.",
        pattern=re.compile(r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    ),
    SecretDetectorSpec(
        kind="github_token",
        default_policy="redact",
        description="GitHub token prefixes followed by a token body.",
        pattern=re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9_]{36,255}(?![A-Za-z0-9_])"),
    ),
    SecretDetectorSpec(
        kind="jwt",
        default_policy="redact",
        description="JWT-like three-part base64url token beginning with a header prefix.",
        pattern=re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?![A-Za-z0-9_-])"),
    ),
    SecretDetectorSpec(
        kind="private_key",
        default_policy="block",
        description="PEM private-key block with bounded multiline body.",
        pattern=re.compile(
            r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----[\s\S]{0,10000}?-----END [A-Z ]{0,40}PRIVATE KEY-----"
        ),
    ),
    SecretDetectorSpec(
        kind="credential_url",
        default_policy="redact",
        description="URL userinfo containing both username and password.",
        pattern=re.compile(r"\b[a-z][a-z0-9+.-]{1,30}://([^\s/@:]{1,256}:[^\s/@]{1,512})@", re.IGNORECASE),
        group=1,
    ),
)


def scan_for_secrets(text: str) -> ScanVerdict:
    """Inspect text without mutation; any finding makes the verdict BLOCK."""
    findings = _find_secrets(text)
    if not findings:
        return ScanVerdict(SecretVerdictKind.CLEAN, text, ())
    return ScanVerdict(SecretVerdictKind.BLOCK, text, findings)


def redact_secrets(text: str) -> ScanVerdict:
    """Redact text only when every finding is safe to redact."""
    findings = _find_secrets(text)
    if not findings:
        return ScanVerdict(SecretVerdictKind.CLEAN, text, ())
    if any(not finding.redactable for finding in findings):
        return ScanVerdict(SecretVerdictKind.BLOCK, text, findings)

    redacted = _apply_redactions(text, findings)
    return ScanVerdict(SecretVerdictKind.REDACTED, redacted, findings)


def _find_secrets(text: str) -> tuple[SecretFinding, ...]:
    candidates: list[SecretFinding] = []
    for spec in SECRET_DETECTOR_SPECS:
        for match in spec.pattern.finditer(text):
            start, end = match.span(spec.group)
            if start == end:
                continue
            candidates.append(
                SecretFinding(
                    kind=spec.kind,
                    start=start,
                    end=end,
                    redactable=spec.default_policy == "redact",
                )
            )
    return _resolve_overlaps(candidates)


def _resolve_overlaps(candidates: list[SecretFinding]) -> tuple[SecretFinding, ...]:
    accepted: list[SecretFinding] = []
    for finding in sorted(candidates, key=lambda item: (-(item.end - item.start), item.start, item.kind)):
        if any(_overlaps(finding, existing) for existing in accepted):
            continue
        accepted.append(finding)
    return tuple(sorted(accepted, key=lambda item: item.start))


def _overlaps(left: SecretFinding, right: SecretFinding) -> bool:
    return left.start < right.end and right.start < left.end


def _apply_redactions(text: str, findings: Sequence[SecretFinding]) -> str:
    redacted = text
    for finding in sorted(findings, key=lambda item: item.start, reverse=True):
        token = f"[REDACTED:{finding.kind}]"
        redacted = redacted[: finding.start] + token + redacted[finding.end :]
    return redacted
