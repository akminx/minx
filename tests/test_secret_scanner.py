from __future__ import annotations

import importlib
import time

import pytest


def _scanner_module():
    try:
        return importlib.import_module("minx_mcp.core.secret_scanner")
    except ModuleNotFoundError as exc:
        pytest.fail(f"secret scanner module is missing: {exc}")


def _aws_key() -> str:
    return "".join(("AK", "IA", "A" * 16))


def _stripe_key() -> str:
    return "".join(("sk", "_test_", "a" * 24))


def _google_key() -> str:
    return "".join(("AI", "za", "A" * 35))


def _github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


def _jwt() -> str:
    return ".".join(("eyJ" + "a" * 17, "b" * 20, "c" * 20))


def _private_key_block() -> str:
    return "\n".join(
        (
            "-----" + "BEGIN PRIVATE KEY" + "-----",
            "a" * 64,
            "-----" + "END PRIVATE KEY" + "-----",
        )
    )


def _credential_url() -> str:
    return "".join(("postgres", "://", "user", ":", "pass", "@", "example.test", ":5432/db"))


def _credential_url_with_scheme(scheme: str) -> str:
    return "".join((scheme, "://", "user", ":", "pass", "@", "example.test", ":5432/db"))


def test_clean_input_returns_clean_verdict_and_original_text() -> None:
    scanner = _scanner_module()

    verdict = scanner.scan_for_secrets("ordinary memory content")

    assert verdict.verdict is scanner.SecretVerdictKind.CLEAN
    assert verdict.text == "ordinary memory content"
    assert verdict.findings == ()


@pytest.mark.parametrize(
    ("kind", "factory"),
    [
        ("aws_access_key_id", _aws_key),
        ("stripe_key", _stripe_key),
        ("google_api_key", _google_key),
        ("github_token", _github_token),
        ("jwt", _jwt),
        ("credential_url", _credential_url),
    ],
)
def test_redact_secrets_replaces_redactable_families_without_secret_material(kind: str, factory) -> None:
    scanner = _scanner_module()
    secret = factory()

    verdict = scanner.redact_secrets(f"before {secret} after")

    assert verdict.verdict is scanner.SecretVerdictKind.REDACTED
    assert f"[REDACTED:{kind}]" in verdict.text
    assert secret not in verdict.text
    assert [finding.kind for finding in verdict.findings] == [kind]


def test_private_key_blocks_are_blocked_not_redacted() -> None:
    scanner = _scanner_module()
    secret = _private_key_block()

    verdict = scanner.redact_secrets(f"note\n{secret}\nend")

    assert verdict.verdict is scanner.SecretVerdictKind.BLOCK
    assert verdict.text == f"note\n{secret}\nend"
    assert verdict.findings[0].kind == "private_key"


def test_scan_for_secrets_reports_findings_without_mutating_text() -> None:
    scanner = _scanner_module()
    secret = _github_token()
    text = f"token {secret}"

    verdict = scanner.scan_for_secrets(text)

    assert verdict.verdict is scanner.SecretVerdictKind.BLOCK
    assert verdict.text == text
    assert verdict.findings[0].kind == "github_token"


def test_credential_url_redaction_preserves_safe_url_parts_and_redacts_matching_query_token() -> None:
    scanner = _scanner_module()
    query_secret = _google_key()
    base_url = _credential_url()
    text = f"{base_url}?api={query_secret}"

    verdict = scanner.redact_secrets(text)

    assert verdict.verdict is scanner.SecretVerdictKind.REDACTED
    expected_prefix = "".join(("postgres", "://", "[REDACTED:credential_url]", "@", "example.test", ":5432/db?api="))
    assert verdict.text.startswith(expected_prefix)
    assert "[REDACTED:google_api_key]" in verdict.text
    assert "user:pass" not in verdict.text
    assert query_secret not in verdict.text


@pytest.mark.parametrize("scheme", ["POSTGRES", "Https"])
def test_credential_url_detection_is_case_insensitive_for_scheme(scheme: str) -> None:
    scanner = _scanner_module()
    secret = _credential_url_with_scheme(scheme)

    verdict = scanner.redact_secrets(secret)

    assert verdict.verdict is scanner.SecretVerdictKind.REDACTED
    assert "[REDACTED:credential_url]" in verdict.text
    assert "user:pass" not in verdict.text


def test_findings_are_sorted_and_overlap_prefers_longest_match() -> None:
    scanner = _scanner_module()
    url = f"https://user:{_github_token()}@example.test/path"
    text = f"{_aws_key()} then {url}"

    verdict = scanner.redact_secrets(text)

    assert verdict.verdict is scanner.SecretVerdictKind.REDACTED
    assert [finding.kind for finding in verdict.findings] == ["aws_access_key_id", "credential_url"]
    assert _github_token() not in verdict.text


def test_overlap_resolution_prefers_longest_match_before_earliest_start() -> None:
    scanner = _scanner_module()
    short_earlier = scanner.SecretFinding(kind="short", start=1, end=6, redactable=True)
    long_later = scanner.SecretFinding(kind="long", start=2, end=12, redactable=True)

    findings = scanner._resolve_overlaps([short_earlier, long_later])

    assert findings == (long_later,)


def test_detector_specs_are_the_documented_source_of_truth() -> None:
    scanner = _scanner_module()

    kinds = [spec.kind for spec in scanner.SECRET_DETECTOR_SPECS]

    assert sorted(kinds) == [
        "aws_access_key_id",
        "credential_url",
        "github_token",
        "google_api_key",
        "jwt",
        "private_key",
        "stripe_key",
    ]
    assert len(kinds) == len(set(kinds))


def test_long_non_matching_input_scans_quickly() -> None:
    scanner = _scanner_module()
    text = "not-a-secret " * 20_000

    started = time.perf_counter()
    verdict = scanner.scan_for_secrets(text)
    elapsed = time.perf_counter() - started

    assert verdict.verdict is scanner.SecretVerdictKind.CLEAN
    assert elapsed < 0.25
