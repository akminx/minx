from minx_mcp.finance.normalization import normalize_merchant
from minx_mcp.finance.rules import Rule, apply_rules


def test_normalize_merchant_collapses_variants() -> None:
    assert normalize_merchant("SQ *JOES CAFE 1234") == "Joe's Cafe"
    assert normalize_merchant("JOES CAFE AUSTIN") == "Joe's Cafe"


def test_staged_rules_apply_in_priority_order() -> None:
    txn = {"merchant": "SQ *JOES CAFE 1234", "raw_merchant": "SQ *JOES CAFE 1234", "category_name": None}
    rules = [
        Rule(stage="normalize", priority=10, kind="rename_merchant", match="JOES CAFE", value="Joe's Cafe"),
        Rule(stage="categorize", priority=20, kind="categorize_merchant", match="Joe's Cafe", value="Dining Out"),
    ]

    result = apply_rules(txn, rules)

    assert result["merchant"] == "Joe's Cafe"
    assert result["category_name"] == "Dining Out"
