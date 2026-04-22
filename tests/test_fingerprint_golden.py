"""Golden vectors for the Slice 6g content_fingerprint primitive.

Regression gate against silent normalization drift: any change to
``normalize_for_fingerprint`` or to the SHA-256 formula in
``content_fingerprint`` must either match these pinned digests or
ship alongside a documented re-fingerprinting migration per §11 of
the Slice 6g spec.

Placeholder sentinel rejection: the fixture may hold vectors whose
digest is ``"<computed at implementation time — fill before merge>"``
during spec drafting. This loader rejects such entries so a
placeholder never ships green.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from minx_mcp.core.fingerprint import content_fingerprint, normalize_for_fingerprint

_PLACEHOLDER = "<computed at implementation time — fill before merge>"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "fingerprint_golden.json"


def _load_vectors() -> list[dict]:
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "fixture root must be a JSON object"
    assert data.get("version") == 1, "fixture version must be 1"
    vectors = data.get("vectors")
    assert isinstance(vectors, list) and vectors, "fixture must define at least one vector"
    return vectors


def test_fixture_has_no_placeholder_sentinels() -> None:
    """Gate on placeholder digests before hashing anything."""
    vectors = _load_vectors()
    placeholders = [v["name"] for v in vectors if v.get("expected_sha256") == _PLACEHOLDER]
    assert not placeholders, (
        "fingerprint_golden.json still carries placeholder sentinels for "
        f"{placeholders}; fill them with real digests before merging"
    )


@pytest.mark.parametrize("vector", _load_vectors(), ids=lambda v: v["name"])
def test_content_fingerprint_matches_golden_vector(vector: dict) -> None:
    """The primitive's output must equal the pinned digest for every vector."""
    parts = vector["parts"]
    assert isinstance(parts, list), f"{vector['name']}: parts must be a JSON array"
    expected = vector["expected_sha256"]
    actual = content_fingerprint(*parts)
    assert actual == expected, (
        f"{vector['name']}: content_fingerprint({parts!r}) = {actual} "
        f"(expected {expected}; if this change is intentional, a re-"
        "fingerprinting migration is required per Slice 6g spec §11)"
    )


def test_fingerprint_equivalence_relationships() -> None:
    """§10.1 non-fixture equivalence checks: ``"Netflix"`` casing + whitespace trimming."""
    canonical = content_fingerprint("netflix")
    assert content_fingerprint("Netflix") == canonical
    assert content_fingerprint("NETFLIX") == canonical
    # Leading/trailing whitespace is stripped; internal runs are preserved
    # as a single space (they would differentiate "net flix" vs "netflix").
    assert content_fingerprint("  netflix  ") == canonical
    assert content_fingerprint("\tnetflix\n") == canonical

    # Whitespace collapse for multi-word content: tabs/newlines/multiple
    # spaces all collapse to a single space.
    multi = content_fingerprint("net flix")
    assert content_fingerprint("net\tflix") == multi
    assert content_fingerprint("net\nflix") == multi
    assert content_fingerprint("net   flix") == multi
    # But "netflix" != "net flix" (the space is meaningful inside the run).
    assert content_fingerprint("netflix") != multi


def test_fingerprint_nfc_equivalence() -> None:
    """NFC vs NFD of the same character must fingerprint identically."""
    composed = "caf\u00e9"  # NFC: é is single codepoint
    decomposed = "cafe\u0301"  # NFD: e + combining acute
    assert unicodedata.normalize("NFC", decomposed) == composed
    assert content_fingerprint(composed) == content_fingerprint(decomposed)


def test_fingerprint_separator_safety() -> None:
    """Boundary shifts must change the digest (NUL separator lands differently)."""
    # "ab\0c" vs "a\0bc" — both are 4 bytes but not the same bytes.
    assert content_fingerprint("ab", "c") != content_fingerprint("a", "bc")


def test_fingerprint_part_count_edge_cases() -> None:
    """§4.3 fixed-point and separator-appearance equivalences."""
    # Fixed point: no parts collapses to same digest as one empty part.
    assert content_fingerprint() == content_fingerprint("")
    # Adding a second empty part introduces the NUL separator.
    assert content_fingerprint("", "") != content_fingerprint("")
    assert content_fingerprint("a") != content_fingerprint("a", "")


def test_normalize_for_fingerprint_whitespace_collapse() -> None:
    """Whitespace collapse + casefold pipeline (spec §4)."""
    assert normalize_for_fingerprint("a   b") == "a b"
    assert normalize_for_fingerprint("a\tb") == "a b"
    assert normalize_for_fingerprint("a\n b") == "a b"
    assert normalize_for_fingerprint("  hello  ") == "hello"
    assert normalize_for_fingerprint(None) == ""
    assert normalize_for_fingerprint("") == ""


def test_normalize_casefold_unicode() -> None:
    """German eszett and Turkish dotted/undotted I must casefold correctly."""
    # casefold: "ß" -> "ss"
    assert normalize_for_fingerprint("ß") == normalize_for_fingerprint("ss")
    # casefold is NOT just lower(): dotted-I edge cases still collapse
    assert content_fingerprint("Ş") == content_fingerprint("ş")


def test_empty_fingerprint_is_sha256_of_empty_bytes() -> None:
    """content_fingerprint() equals sha256 of empty bytestring (documented in §4.3)."""
    expected = hashlib.sha256(b"").hexdigest()
    assert content_fingerprint() == expected
