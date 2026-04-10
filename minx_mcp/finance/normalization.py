from __future__ import annotations

import re

_KNOWN_CANONICAL_ALIASES = {
    "Joes Cafe": "Joe's Cafe",
}

_SUFFIX_PATTERNS = (
    re.compile(r"\s+#?\d+$"),
    re.compile(r"\s+(AUSTIN|TX|TEXAS)$"),
)


def normalize_merchant(raw_merchant: str | None) -> str | None:
    if raw_merchant is None:
        return None

    stripped = raw_merchant.strip()
    if not stripped:
        return None

    canonical = stripped.upper()
    changed = canonical != stripped
    canonical = re.sub(r"^(SQ \*|SQ\*)", "", canonical).strip()
    changed = changed or canonical != stripped.upper()
    for pattern in _SUFFIX_PATTERNS:
        before = canonical
        canonical = pattern.sub("", canonical).strip()
        changed = changed or canonical != before
    canonical = re.sub(r"\s+", " ", canonical)
    changed = changed or canonical != stripped.upper()
    if not changed:
        return stripped
    title_cased = canonical.title()
    return _KNOWN_CANONICAL_ALIASES.get(title_cased, title_cased)
