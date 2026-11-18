from __future__ import annotations

import copy
import json
import re
from datetime import date

from . import llm
from .engine.rules import FACTS_DOC, OPS, describe, validate_policy

ROUTES = ["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"]


def _fmt(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def rule_text(r: dict) -> str:
    sup = r.get("supplier") or {}
    mode = sup.get("mode")
    who = {"contract": "contract supplier at contract rate",
           "preferred": f"preferred supplier #{sup.get('rank', 1)} at reference price",
           "suggest": f"suggest {sup.get('count', 3)} suppliers",
           "fixed": f"supplier {sup.get('supplier_id')}", "none": "no supplier suggestions"}.get(mode, mode)
    return f"[{r.get('id')}] IF {describe(r.get('when'))} THEN {r.get('route')} ({who})" + \
        (f"; note: {r['note']}" if r.get("note") else "")


def diff_policies(old: dict | None, new: dict) -> list[str]:
    if not old:
        return [f"Initial policy version {new.get('version')} effective {new.get('effective_date')}"]
    out = []
    if old.get("version") != new.get("version"):
        out.append(f"Version {old.get('version')} → {new.get('version')}")
    if old.get("effective_date") != new.get("effective_date"):
        out.append(f"Effective from {new.get('effective_date')} (previous version effective {old.get('effective_date')})")
    op, np_ = old.get("parameters") or {}, new.get("parameters") or {}
    for k in sorted(set(op) | set(np_)):
        if op.get(k) != np_.get(k):
            out.append(f"Parameter {k.replace('_', ' ')}: {_fmt(op.get(k, '—'))} → {_fmt(np_.get(k, '—'))}")
    os_, ns = old.get("supplier_status_overrides") or {}, new.get("supplier_status_overrides") or {}
    for k in sorted(set(os_) | set(ns)):
        if os_.get(k) != ns.get(k):
            out.append(f"Supplier {k} status set to {ns.get(k, 'as in supplier master')}")
    if (old.get("excluded_supplier_statuses") or []) != (new.get("excluded_supplier_statuses") or []):
        out.append(f"Excluded supplier statuses: {old.get('excluded_supplier_statuses')} → {new.get('excluded_supplier_statuses')}")
    for key, label in (("clubbing", "Clubbing settings"), ("consolidation", "PO consolidation settings"),
                       ("valuation_order", "Valuation order"), ("contracts", "Contract settings"),
                       ("preferred_suppliers", "Preferred-supplier ranking")):
        if old.get(key) != new.get(key):
            out.append(f"{label}: {_fmt(old.get(key))} → {_fmt(new.get(key))}")
    orules = {r["id"]: r for r in old.get("rules") or []}
    nrules = {r["id"]: r for r in new.get("rules") or []}
    for rid, r in nrules.items():
        if rid not in orules:
            pos = [x["id"] for x in new["rules"]].index(rid)
            after = new["rules"][pos - 1]["id"] if pos > 0 else None
            out.append(f"NEW rule {rule_text(r)}" + (f" — checked after rule {after}" if after else " — checked first"))
        elif orules[rid] != r:
            out.append(f"CHANGED rule {rid}: was {rule_text(orules[rid])}; now {rule_text(r)}")
    for rid, r in orules.items():
        if rid not in nrules:
            out.append(f"REMOVED rule {rule_text(r)}")
    common_old = [r for r in orules if r in nrules]
    common_new = [r for r in nrules if r in orules]
    if common_old != common_new:
        out.append("Rule order changed: " + " → ".join(common_new))
    return out or ["No effective change"]


SYSTEM = """You convert procurement policy amendments into an updated machine-readable policy.
You are given the CURRENT policy JSON and an AMENDMENT text. Return ONLY JSON:
{"version": "<new version, e.g. 5.1>", "effective_date": "YYYY-MM-DD",
 "changes": ["plain-language bullet per change"],
 "assumptions": ["anything ambiguous and how you resolved it"],
 "policy": { ...the COMPLETE updated policy JSON... }}
Rules for editing:
- Keep the same structure. Change only what the amendment says. Keep existing rule ids.
- Rules are evaluated in list order; the first that fits decides. Insert new rules where the amendment says
  they apply (e.g. a plant-specific exception goes immediately BEFORE the general rule it overrides).
- New rule ids: use the clause number from the amendment if given, else "<base>a" (e.g. "5.4a").
- Conditions: {"all":[...]}, {"any":[...]}, {"not":{...}} of {"fact":F,"op":OP,"value":V}.
  Facts: FACTS. Operators: OPS. Money values are plain INR numbers (₹1,50,000 -> 150000; 1.5 lakh -> 150000).
- Rule outcome: "decision" PO or SOURCING_REQUEST; "route" one of ROUTES;
  "supplier": {"mode":"contract"} | {"mode":"preferred","rank":1} | {"mode":"suggest","count":N} |
  {"mode":"fixed","supplier_id":"S0xx"} | {"mode":"none"}. Optional "note" text.
- "Bought from an Active supplier in the last N days" = {"fact":"days_since_last_active_purchase","op":"lte","value":N}.
- Windows/limits that are not in rules live in "parameters"; supplier status changes go in
  "supplier_status_overrides" {"S0xx":"Blocked"}.
- If the amendment does not state a version, increment the minor version. If it does not state an effective
  date, use the amendment's date if given, otherwise TODAY.
"""


def llm_propose(current: dict, text: str, today: date) -> dict:
    sys = SYSTEM.replace("FACTS", json.dumps(FACTS_DOC)).replace("OPS", json.dumps(OPS)).replace(
        "ROUTES", json.dumps(ROUTES)).replace("TODAY", today.isoformat())
    out = llm.complete(sys, f"CURRENT POLICY:\n{json.dumps(current)}\n\nAMENDMENT:\n{text}", max_tokens=16000, timeout=150)
    data = llm.extract_json(out)
    pol = data.get("policy") or {}
    pol["version"] = str(data.get("version") or pol.get("version"))
    pol["effective_date"] = data.get("effective_date") or pol.get("effective_date")
    return {"policy": pol, "llm_changes": data.get("changes") or [], "assumptions": data.get("assumptions") or [],
            "method": f"AI reader ({llm.provider()} {llm.last_model or ''})".strip()}


def parse_inr(s: str) -> float | None:
    s = s.strip().lower().replace("rs.", "").replace("rs", "").replace("inr", "").replace("₹", "").strip()
    m = re.match(r"([\d,]*\.?\d+)\s*(lakh|lakhs|lac|l|crore|cr|k)?\b", s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    mult = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "l": 1e5, "crore": 1e7, "cr": 1e7, "k": 1e3}.get(m.group(2) or "", 1)
    return v * mult


MONEY = r"((?:₹|rs\.?|inr)\s*[\d,]+(?:\.\d+)?(?:\s*(?:lakh|lakhs|lac|crore|cr))?|[\d,.]+\s*(?:lakh|lakhs|lac|crore|cr))"
PARAM_WORDS = {
    "clubbing_creation_window_days": [r"club\w*.*?(\d+)\s*(?:calendar\s*)?days?.*?(?:first line|creat)", r"within\s*(\d+)\s*calendar days"],
    "clubbing_need_by_window_days": [r"need[- ]by.*?within\s*(\d+)\s*days", r"need[- ]by\s*window.*?(\d+)"],
    "consolidation_window_hours": [r"within\s*(\d+)\s*hours"],
    "preferred_list_lookback_days": [r"(\d+)\s*days.*?preferred", r"preferred.*?(\d+)\s*days"],
}


def rule_based_propose(current: dict, text: str, today: date) -> dict:
    pol = copy.deepcopy(current)
    notes, unread = [], []
    t = text.replace("’", "'")
    low = t.lower()
    m = re.search(r"version\s*([\d]+\.[\d]+)", low)
    pol["version"] = m.group(1) if m else _bump(current.get("version", "5.0"))
    m = re.search(r"effective(?:\s+from|\s+date|\s+on)?\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4})", low)
    pol["effective_date"] = _date(m.group(1)) if m else today.isoformat()
    for sent in re.split(r"(?<=[.;\n])\s+", t):
        s = sent.lower()
        rid = re.search(r"(?:clause|rule|section)\s*(\d+\.\d+[a-z]?)", s)
        monies = [parse_inr(x) for x in re.findall(MONEY, s)]
        monies = [x for x in monies if x]
        st = re.search(r"\b(s\d{3})\b.*?\b(blocked|inactive|active)\b", s)
        if st:
            pol.setdefault("supplier_status_overrides", {})[st.group(1).upper()] = st.group(2).capitalize()
            notes.append(f"Supplier {st.group(1).upper()} → {st.group(2).capitalize()}")
            continue
        if rid and monies:
            r = next((x for x in pol["rules"] if str(x["id"]).lower() == rid.group(1)), None)
            if r:
                new = monies[-1]
                changed = _set_value_condition(r.get("when"), new)
                if changed:
                    notes.append(f"Rule {r['id']} value limit → ₹{new:,.0f}")
                    continue
        hit = False
        for pk, pats in PARAM_WORDS.items():
            for p in pats:
                mm = re.search(p, s)
                if mm and ("change" in s or "amend" in s or "now" in s or "replace" in s or "revise" in s):
                    pol["parameters"][pk] = int(mm.group(1))
                    notes.append(f"{pk} → {mm.group(1)}")
                    hit = True
                    break
            if hit:
                break
        if not hit and len(s.strip()) > 25 and re.search(r"\b(shall|must|will|change|new rule|apply|replace|only)\b", s):
            unread.append(sent.strip())
    return {"policy": pol, "llm_changes": notes,
            "assumptions": ([f"Could not read automatically, please apply in the editor: {u}" for u in unread]),
            "method": "rule-based reader"}


def _set_value_condition(cond, new) -> bool:
    if not cond:
        return False
    for k in ("all", "any"):
        if k in cond:
            for c in cond[k]:
                if _set_value_condition(c, new):
                    return True
            return False
    if "not" in cond:
        return _set_value_condition(cond["not"], new)
    if cond.get("fact") == "value" and cond.get("op") in ("lt", "lte", "gt", "gte"):
        cond["value"] = new
        return True
    return False


def _bump(v: str) -> str:
    try:
        a, b = str(v).split(".")[:2]
        return f"{a}.{int(b) + 1}"
    except ValueError:
        return f"{v}.1"


def _date(s: str) -> str:
    from datetime import datetime
    for f in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.strip().title() if f != "%Y-%m-%d" else s.strip(), f).date().isoformat()
        except ValueError:
            pass
    return s


def propose(current: dict, text: str, today: date) -> dict:
    errors_llm = None
    if llm.available():
        try:
            res = llm_propose(current, text, today)
            errs = validate_policy(res["policy"])
            if not errs:
                return res
            errors_llm = errs
        except Exception as e:
            errors_llm = [llm.redact(e)]
    res = rule_based_propose(current, text, today)
    if errors_llm:
        res["assumptions"].insert(0, llm.redact(f"AI reader unavailable or its proposal was invalid ({'; '.join(errors_llm)[:300]}); used rule-based reader"))
    return res
