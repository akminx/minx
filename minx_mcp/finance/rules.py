from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from minx_mcp.finance.normalization import normalize_merchant

RuleStage = Literal["normalize", "categorize"]

_STAGE_ORDER = {
    "normalize": 0,
    "categorize": 1,
}


@dataclass(frozen=True)
class Rule:
    stage: RuleStage
    priority: int
    kind: str
    match: str
    value: str


def apply_rules(txn: dict[str, object], rules: list[Rule]) -> dict[str, object]:
    current = dict(txn)
    for rule in sorted(rules, key=lambda item: (_STAGE_ORDER[item.stage], item.priority)):
        current = _apply_rule(current, rule)
    return current


def _apply_rule(txn: dict[str, object], rule: Rule) -> dict[str, object]:
    merchant = txn.get("merchant")
    merchant_text = merchant if isinstance(merchant, str) else ""
    normalized_match = normalize_merchant(rule.match) or rule.match
    current_merchant = normalize_merchant(merchant_text) or merchant_text
    match_key = _match_key(normalized_match)
    merchant_key = _match_key(current_merchant)

    if rule.kind == "rename_merchant":
        if match_key and match_key in merchant_key:
            txn["merchant"] = rule.value
        return txn

    if rule.kind == "categorize_merchant":
        if match_key and match_key in merchant_key and txn.get("category_name") is None:
            txn["category_name"] = rule.value
        return txn

    return txn


def _match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())
