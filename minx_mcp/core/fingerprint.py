"""Shared content-fingerprint primitive (Slice 6g).

This module is a leaf dependency: it has no imports from the rest of
``minx_mcp``. The first consumer is the memories table dedup path (see
``minx_mcp/core/memory_service.py``); future consumers include journal
entries (Slice 7), investigation steps (Slice 9c), and vault frontmatter
(Slice 6h).

The primitive establishes the canonical normalization + hashing contract
so every content-dedup surface reuses the same equivalence classes.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = ["content_fingerprint", "normalize_for_fingerprint"]


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_fingerprint(text: str | None) -> str:
    """Canonical text form for content-based dedup.

    Deterministic transforms, applied in order:
    1. None or empty -> empty string.
    2. Unicode NFC (canonical composition). Collapses combining-mark
       variants (NFD "cafe" + combining acute vs NFC "café") into a
       single form.
    3. casefold() - Unicode-correct case folding. Not lower(). ß -> ss,
       İ -> i̇, etc.
    4. Whitespace collapse: any run of Unicode whitespace (spaces,
       tabs, newlines) -> single U+0020. Implementation:
       re.sub(r"\\s+", " ", text). The regex \\s matches the Unicode
       whitespace class by default in Python 3.
    5. Strip leading and trailing whitespace.

    Does NOT:
    - Strip punctuation (``run 5k`` != ``run, 5k``; intent differs).
    - Remove diacritics beyond what NFC handles (``café`` != ``cafe``;
      accented spellings are meaningful in food/meals content).
    - Normalize numbers (``$5`` != ``5 dollars``; that is a semantic
      equivalence, not a lexical one).
    """
    if not text:
        return ""
    # 2. NFC
    result = unicodedata.normalize("NFC", text)
    # 3. casefold
    result = result.casefold()
    # 4. whitespace collapse
    result = _WHITESPACE_RE.sub(" ", result)
    # 5. strip
    return result.strip()


def content_fingerprint(*parts: str | None) -> str:
    """SHA-256 of normalized parts joined with U+0000 (NUL).

    Exact formula::

        normalized = [normalize_for_fingerprint(p) for p in parts]
        payload = "\\0".join(normalized)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    Consequences of this formula:

    - ``content_fingerprint()`` with no parts produces
      ``sha256(b"")`` = ``e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855``
      (the SHA-256 of the empty string, because ``"\\0".join([])`` is
      ``""``).
    - ``content_fingerprint("")`` produces ``sha256(b"")`` - same digest
      as ``content_fingerprint()``. A single empty part joined with
      nothing is still an empty string.
    - ``content_fingerprint("", "")`` produces ``sha256(b"\\0")`` -
      different from ``content_fingerprint("")`` because the separator
      appears.
    - Adding or removing parts always changes the digest beyond the
      one-empty-slot -> zero-slots boundary (which is the only fixed
      point in the formula).
    - ``content_fingerprint("ab", "c")`` produces ``sha256(b"ab\\0c")``
      and ``content_fingerprint("a", "bc")`` produces
      ``sha256(b"a\\0bc")``; they differ because the NUL byte lands in
      different positions.

    Returns a lowercase hex digest (64 chars).
    """
    normalized = [normalize_for_fingerprint(p) for p in parts]
    payload = "\0".join(normalized)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
