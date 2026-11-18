from __future__ import annotations

from typing import Any

FACTS_DOC = {
    "item_code": "Matched item code (str)",
    "item_type": "Item type: Material / Service / Capex",
    "category": "Item category, e.g. Safety & PPE",
    "plant": "Plant code, e.g. PUN1 or CHK1",
    "department": "Department of the first PR line",
    "uom": "Unit of measure",
    "value": "Requirement value in INR (policy 3.1)",
    "total_quantity": "Total quantity of the requirement",
    "line_count": "Number of PR lines clubbed in the requirement",
    "is_emergency": "True if any line is marked Emergency",
    "is_new_item": "True if the item is not in the item master",
    "contract_usable": "A rate contract applies and has ceiling left (4.1-4.3)",
    "contract_applicable": "A rate contract applies (ignoring ceiling)",
    "days_since_last_active_purchase": "Days since the last PO for the item from an Active supplier (None if never)",
    "days_since_last_purchase": "Days since the last PO for the item from any supplier (None if never)",
    "preferred_supplier_count": "Number of suppliers on the preferred list",
    "requirement_date": "Requirement date (YYYY-MM-DD)",
    "need_by_days": "Days between requirement date and earliest need-by date",
}

OPS = ["eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "is_true", "is_false",
       "exists", "not_exists", "contains"]


def _cmp(a: Any, op: str, b: Any) -> bool:
    if op == "is_true":
        return a is True
    if op == "is_false":
        return a is False or a is None
    if op == "exists":
        return a is not None
    if op == "not_exists":
        return a is None
    if a is None:
        return False
    if isinstance(a, str) and isinstance(b, str) and op in ("eq", "ne"):
        a, b = a.strip().lower(), b.strip().lower()
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op in ("in", "not_in"):
        vals = b if isinstance(b, list) else [b]
        norm = [v.strip().lower() if isinstance(v, str) else v for v in vals]
        av = a.strip().lower() if isinstance(a, str) else a
        return (av in norm) if op == "in" else (av not in norm)
    if op == "contains":
        return isinstance(a, str) and str(b).lower() in a.lower()
    try:
        if op == "lt":
            return a < b
        if op == "lte":
            return a <= b
        if op == "gt":
            return a > b
        if op == "gte":
            return a >= b
    except TypeError:
        return False
    raise ValueError(f"Unknown operator {op}")


def evaluate(cond: dict | None, facts: dict) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(evaluate(c, facts) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, facts) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], facts)
    return _cmp(facts.get(cond["fact"]), cond["op"], cond.get("value"))


def describe(cond: dict | None) -> str:
    if not cond:
        return "always"
    if "all" in cond:
        parts = [describe(c) for c in cond["all"]]
        return " AND ".join(parts) if parts else "always"
    if "any" in cond:
        return "(" + " OR ".join(describe(c) for c in cond["any"]) + ")"
    if "not" in cond:
        return f"NOT ({describe(cond['not'])})"
    f, op, v = cond["fact"], cond["op"], cond.get("value")
    words = {"eq": "is", "ne": "is not", "in": "is one of", "not_in": "is not one of", "lt": "<",
             "lte": "≤", "gt": ">", "gte": "≥", "contains": "contains"}
    if op == "is_true":
        return f"{f}"
    if op == "is_false":
        return f"not {f}"
    if op == "exists":
        return f"{f} is known"
    if op == "not_exists":
        return f"{f} is unknown"
    if isinstance(v, (int, float)) and f == "value":
        v = f"₹{v:,.0f}"
    return f"{f} {words.get(op, op)} {v}"


def validate_policy(policy: dict) -> list[str]:
    errs = []
    if not policy.get("version"):
        errs.append("version is required")
    if not policy.get("effective_date"):
        errs.append("effective_date is required")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        errs.append("rules must be a non-empty list")
        return errs
    ids = set()
    valid_routes = {"CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"}

    def check_cond(c, rid):
        if not c:
            return
        for k in ("all", "any"):
            if k in c:
                for cc in c[k]:
                    check_cond(cc, rid)
                return
        if "not" in c:
            check_cond(c["not"], rid)
            return
        if c.get("fact") not in FACTS_DOC:
            errs.append(f"rule {rid}: unknown fact '{c.get('fact')}'")
        if c.get("op") not in OPS:
            errs.append(f"rule {rid}: unknown operator '{c.get('op')}'")

    for r in rules:
        rid = r.get("id")
        if not rid or rid in ids:
            errs.append(f"rule id missing or duplicated: {rid}")
        ids.add(rid)
        if r.get("route") not in valid_routes:
            errs.append(f"rule {rid}: route must be one of {sorted(valid_routes)}")
        if r.get("decision") not in ("PO", "SOURCING_REQUEST"):
            errs.append(f"rule {rid}: decision must be PO or SOURCING_REQUEST")
        mode = (r.get("supplier") or {}).get("mode")
        if mode not in ("contract", "preferred", "suggest", "none", "fixed"):
            errs.append(f"rule {rid}: supplier.mode must be contract/preferred/suggest/none/fixed")
        check_cond(r.get("when"), rid)
    return errs
