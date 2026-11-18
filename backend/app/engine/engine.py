from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .data import Contract, DataStore, parse_date, parse_datetime, to_float
from .matcher import match_item
from .rules import describe, evaluate
from .suppliers import is_eligible, preferred_list, suggest_suppliers, supplier_status

OUTPUT_COLUMNS = ["pr_no", "pr_line", "resolved_item_code", "group_id", "po_id", "decision", "route",
                  "supplier_id", "unit_price", "group_value_inr", "suggested_supplier_ids",
                  "policy_version", "reason"]


@dataclass
class PRLine:
    pr_no: str
    pr_line: str
    created_at: Optional[datetime]
    requester: str
    department: str
    plant: str
    item_code: str
    description: str
    quantity: Optional[float]
    uom: str
    estimated_unit_price: Optional[float]
    need_by_date: Optional[date]
    urgency: str
    raw: dict = field(default_factory=dict)
    resolved_item: Optional[str] = None
    match_method: str = ""
    match_score: float = 0.0
    not_processed_reason: str = ""

    @property
    def key(self):
        return (self.pr_no, str(self.pr_line))

    @property
    def is_emergency(self) -> bool:
        return (self.urgency or "").strip().lower() == "emergency"

    @staticmethod
    def from_dict(r: dict) -> "PRLine":
        g = lambda k: (r.get(k) if r.get(k) is not None else "")
        return PRLine(
            pr_no=str(g("pr_no")).strip(), pr_line=str(g("pr_line")).strip(),
            created_at=parse_datetime(g("created_at")), requester=str(g("requester")).strip(),
            department=str(g("department")).strip(), plant=str(g("plant")).strip().upper(),
            item_code=str(g("item_code")).strip().upper(), description=str(g("description")).strip(),
            quantity=to_float(g("quantity")), uom=str(g("uom")).strip().upper(),
            estimated_unit_price=to_float(g("estimated_unit_price")),
            need_by_date=parse_date(g("need_by_date")), urgency=str(g("urgency")).strip() or "Normal",
            raw=dict(r))


@dataclass
class Requirement:
    group_id: str
    item_code: Optional[str]
    plant: str
    lines: list[PRLine]
    policy: dict
    is_new: bool = False
    po_id: str = ""
    decision: str = ""
    route: str = ""
    rule_id: str = ""
    rule_name: str = ""
    supplier_id: str = ""
    supplier_name: str = ""
    unit_price: Optional[float] = None
    value: float = 0.0
    value_price: Optional[float] = None
    value_source: str = ""
    suggested: list[str] = field(default_factory=list)
    contract_id: str = ""
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    preferred: list = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    @property
    def first(self) -> PRLine:
        return self.lines[0]

    @property
    def created_at(self) -> datetime:
        return self.first.created_at

    @property
    def req_date(self) -> date:
        return self.first.created_at.date()

    @property
    def total_qty(self) -> float:
        return sum(l.quantity or 0 for l in self.lines)

    @property
    def is_emergency(self) -> bool:
        return any(l.is_emergency for l in self.lines)

    @property
    def policy_version(self) -> str:
        return str(self.policy.get("version", ""))


class PolicyBook:

    def __init__(self, policies: list[dict], force_version: str | None = None):
        if not policies:
            raise ValueError("No approved policy version available")
        self.policies = sorted(policies, key=lambda p: (str(p["effective_date"]), str(p.get("approved_at") or ""),
                                                        _vkey(p["version"])))
        self.force = None
        if force_version:
            m = [p for p in self.policies if str(p["version"]) == str(force_version)]
            if not m:
                raise ValueError(f"Policy version {force_version} not found")
            self.force = m[-1]

    def for_date(self, d: date) -> dict:
        if self.force:
            return self.force
        chosen = None
        for p in self.policies:
            if parse_date(p["effective_date"]) <= d:
                chosen = p
        return chosen or self.policies[0]


def _vkey(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def _params(policy: dict) -> dict:
    p = {"clubbing_creation_window_days": 14, "clubbing_need_by_window_days": 7,
         "valuation_po_lookback_days": 365, "preferred_list_lookback_days": 548,
         "consolidation_window_hours": 24}
    p.update(policy.get("parameters") or {})
    return p


def inr(x: float | None) -> str:
    if x is None:
        return "-"
    s = f"{x:,.2f}"
    return "₹" + s


class Engine:
    def __init__(self, ds: DataStore, book: PolicyBook):
        self.ds = ds
        self.book = book
        # 4.2: ceilings start from the register for every run
        self.consumed = {cid: c.consumed_value for cid, c in ds.contracts.items()}

    # 1.1 validate + match
    def prepare(self, lines: list[PRLine]) -> tuple[list[PRLine], list[PRLine]]:
        ok, bad = [], []
        seen = set()
        for l in lines:
            reason = ""
            if not l.pr_no or not l.pr_line:
                reason = "Missing PR number or line"
            elif l.key in seen:
                reason = "Duplicate PR number and line in the batch; first occurrence processed"
            elif l.created_at is None:
                reason = "Missing or unreadable creation time"
            elif l.plant not in self.ds.plants:
                reason = f"Unknown plant '{l.plant}' (not in plant master)"
            elif l.quantity is None or l.quantity <= 0:
                reason = f"Quantity must be greater than zero (got '{l.raw.get('quantity', l.quantity)}')"
            elif not l.item_code and not l.description:
                reason = "No item code or description"
            seen.add(l.key)
            if reason:
                l.not_processed_reason = reason
                bad.append(l)
                continue
            m = match_item(l.description, self.ds.items, l.item_code)
            l.resolved_item = m.item_code
            l.match_method = m.method
            l.match_score = m.score
            ok.append(l)
        return ok, bad

    # 2.2 clubbing
    def club(self, lines: list[PRLine]) -> list[Requirement]:
        order = sorted(lines, key=lambda l: (l.created_at, l.pr_no, _int(l.pr_line)))
        reqs: list[Requirement] = []
        for l in order:
            if l.resolved_item is None:
                reqs.append(Requirement("", None, l.plant, [l], self.book.for_date(l.created_at.date()), is_new=True))
                continue
            joined = None
            for r in reqs:
                if r.is_new or r.item_code != l.resolved_item:
                    continue
                pol = r.policy
                cl = pol.get("clubbing") or {}
                if cl.get("enabled", True) is False:
                    continue
                match_on = cl.get("match_on") or ["item_code", "plant"]
                if "plant" in match_on and r.plant != l.plant:
                    continue
                prm = _params(pol)
                if (l.created_at.date() - r.req_date).days > prm["clubbing_creation_window_days"]:
                    continue
                fn, ln = r.first.need_by_date, l.need_by_date
                if fn is None or ln is None or abs((ln - fn).days) > prm["clubbing_need_by_window_days"]:
                    continue
                joined = r
                break
            if joined:
                joined.lines.append(l)
            else:
                reqs.append(Requirement("", l.resolved_item, l.plant, [l], self.book.for_date(l.created_at.date())))
        for i, r in enumerate(reqs, 1):
            r.group_id = f"REQ-{i:04d}"
        return reqs

    def applicable_contracts(self, r: Requirement) -> list[Contract]:
        pol = r.policy
        need_active = (pol.get("contracts") or {}).get("require_active_supplier", True)
        out = []
        for c in self.ds.contracts.values():
            if r.item_code not in c.lines or not c.start_date or not c.end_date:
                continue
            if not (c.start_date <= r.req_date <= c.end_date):
                continue
            if need_active and not is_eligible(self.ds, pol, c.supplier_id):
                r.trace.append(f"{c.contract_id} covers the item but supplier {c.supplier_id} is "
                               f"{supplier_status(self.ds, pol, c.supplier_id)} (4.1, 5.7)")
                continue
            out.append(c)
        return sorted(out, key=lambda c: (c.lines[r.item_code], c.contract_id))

    def last_po(self, item: str, as_of: date, days: int, active_only: bool, policy: dict):
        start = as_of - timedelta(days=days)
        best = None
        for p in self.ds.po_history:
            if p.item_code != item or not (start <= p.po_date <= as_of):
                continue
            if active_only and not is_eligible(self.ds, policy, p.supplier_id):
                continue
            if best is None or (p.po_date, p.po_number) > (best.po_date, best.po_number):
                best = p
        return best

    # 3-6 value, contract, rule, supplier
    def decide(self, r: Requirement):
        pol = r.policy
        prm = _params(pol)
        ds = self.ds
        if r.is_new:
            l = r.first
            r.value_price = l.estimated_unit_price
            r.value = round((l.quantity or 0) * (l.estimated_unit_price or 0), 2)
            r.value_source = "requester's estimated unit price (1.2)" if l.estimated_unit_price else \
                "no estimate given, valued at 0 (see DECISIONS.md)"
            item = None
        else:
            item = ds.items[r.item_code]
        qty = r.total_qty

        # 4 contracts, 3 valuation
        usable = None
        applicable = [] if r.is_new else self.applicable_contracts(r)
        for c in applicable:
            rate = c.lines[r.item_code]
            left = c.value_cap - self.consumed.get(c.contract_id, c.consumed_value)
            headroom = float((pol.get("contracts") or {}).get("min_headroom_pct", 0) or 0) / 100.0 * c.value_cap
            if qty * rate <= left - headroom + 1e-6:
                usable = c
                break
            r.trace.append(f"{c.contract_id} applies but ceiling left {inr(left)} < {inr(qty * rate)} needed (4.2)")
        if not r.is_new:
            for src in pol.get("valuation_order") or ["contract", "last_po", "standard_price"]:
                if src == "contract" and applicable:
                    c = usable or applicable[0]
                    r.value_price = c.lines[r.item_code]
                    r.value_source = f"contract {c.contract_id} rate" + ("" if usable else " (lowest applicable, all out of ceiling)")
                    break
                if src == "last_po":
                    p = self.last_po(r.item_code, r.req_date, prm["valuation_po_lookback_days"], False, pol)
                    if p:
                        r.value_price = p.unit_price
                        r.value_source = f"last PO {p.po_number} on {p.po_date}"
                        break
                if src == "standard_price":
                    r.value_price = item.standard_price
                    r.value_source = "item master standard price"
                    break
            r.value = round(qty * (r.value_price or 0), 2)

        last_active = None if r.is_new else self.last_po(r.item_code, r.req_date, 100000, True, pol)
        last_any = None if r.is_new else self.last_po(r.item_code, r.req_date, 100000, False, pol)
        pref = [] if r.is_new else preferred_list(ds, pol, r.item_code, r.req_date)
        r.preferred = pref
        needs = [l.need_by_date for l in r.lines if l.need_by_date]
        r.facts = {
            "item_code": r.item_code, "item_type": item.item_type if item else None,
            "category": item.category if item else None, "plant": r.plant,
            "department": r.first.department, "uom": item.uom if item else r.first.uom,
            "value": r.value, "total_quantity": qty, "line_count": len(r.lines),
            "is_emergency": r.is_emergency, "is_new_item": r.is_new,
            "contract_usable": usable is not None, "contract_applicable": bool(applicable),
            "days_since_last_active_purchase": (r.req_date - last_active.po_date).days if last_active else None,
            "days_since_last_purchase": (r.req_date - last_any.po_date).days if last_any else None,
            "preferred_supplier_count": len(pref), "requirement_date": r.req_date.isoformat(),
            "need_by_days": (min(needs) - r.req_date).days if needs else None,
        }

        # 5: first matching rule decides
        for rule in pol.get("rules") or []:
            if not evaluate(rule.get("when"), r.facts):
                continue
            sup = rule.get("supplier") or {"mode": "none"}
            mode = sup.get("mode", "none")
            if mode == "contract":
                if not usable:
                    r.trace.append(f"rule {rule['id']} fits but no usable contract; skipped")
                    continue
                r.supplier_id, r.unit_price, r.contract_id = usable.supplier_id, usable.lines[r.item_code], usable.contract_id
            elif mode == "preferred":
                rank = int(sup.get("rank", 1))
                if len(pref) < rank:
                    r.trace.append(f"rule {rule['id']} fits but preferred list has no rank {rank}; skipped")
                    continue
                p = pref[rank - 1]
                r.supplier_id, r.unit_price = p.supplier_id, p.reference_price
                r.notes.append(f"{_ordinal(rank)} preferred supplier {p.supplier_id} ({p.name}), reference price "
                               f"{inr(p.reference_price)} from {p.reference_source}")
            elif mode == "fixed":
                sid = sup.get("supplier_id", "")
                if not is_eligible(ds, pol, sid):
                    r.trace.append(f"rule {rule['id']} names supplier {sid} which is not eligible; skipped")
                    continue
                p = next((x for x in pref if x.supplier_id == sid), None)
                r.supplier_id = sid
                r.unit_price = p.reference_price if p else r.value_price
                r.notes.append(f"supplier {sid} named by the rule, price {inr(r.unit_price)}")
            elif mode == "suggest":
                count = int(sup.get("count", 3))
                ids, _ = suggest_suppliers(ds, pol, r.item_code, item.category if item else "", r.req_date, count)
                r.suggested = ids
            r.decision, r.route = rule["decision"], rule["route"]
            r.rule_id, r.rule_name = str(rule["id"]), rule.get("name", "")
            if rule.get("note"):
                r.notes.append(rule["note"])
            break
        else:
            r.decision, r.route, r.rule_id = "SOURCING_REQUEST", "RFQ", "fallback"
            r.notes.append("No policy rule matched; sent to quotes as a safe default")

        if r.route == "CONTRACT_PO" and r.contract_id:
            self.consumed[r.contract_id] = self.consumed.get(r.contract_id, 0) + r.value
        if r.supplier_id:
            r.supplier_name = ds.suppliers[r.supplier_id].name if r.supplier_id in ds.suppliers else ""
        r.reason = self._reason(r, usable, last_active)

    def _reason(self, r: Requirement, usable, last_active) -> str:
        parts = [f"Policy v{r.policy_version} clause {r.rule_id}"
                 + (f" ({r.rule_name})" if r.rule_name else "") + "."]
        if r.is_new:
            parts.append("Item not in item master (1.2).")
        if len(r.lines) > 1:
            parts.append(f"{len(r.lines)} PR lines clubbed (2.2): " + ", ".join(f"{l.pr_no}/{l.pr_line}" for l in r.lines) + ".")
        parts.append(f"Value {inr(r.value)} = qty {r.total_qty:g} x {inr(r.value_price)} ({r.value_source}).")
        if r.route == "CONTRACT_PO":
            c = self.ds.contracts[r.contract_id]
            left = c.value_cap - self.consumed[r.contract_id]
            parts.append(f"Contract {r.contract_id} with {r.supplier_id} at {inr(r.unit_price)}; ceiling left after this {inr(left)}.")
        else:
            for t in r.trace:
                if "ceiling" in t or "covers the item" in t:
                    parts.append(t + ".")
        if r.route in ("SPOT_PO", "EMERGENCY_PO") and last_active:
            parts.append(f"Bought from an Active supplier within window: PO {last_active.po_number} on {last_active.po_date}.")
        if r.route in ("RFQ", "RFP") and r.facts.get("days_since_last_active_purchase") is None and not r.is_new:
            parts.append("No purchase history from an Active supplier.")
        elif r.route in ("RFQ",) and (r.facts.get("days_since_last_active_purchase") or 0) > 365:
            parts.append(f"Last Active-supplier purchase {r.facts['days_since_last_active_purchase']} days ago.")
        if r.is_emergency:
            parts.append("Emergency requirement.")
        parts += [n + "." for n in r.notes if n]
        if r.suggested:
            parts.append("Suggested: " + ", ".join(r.suggested) + ".")
        return " ".join(parts).replace("..", ".")

    # 7 PO consolidation
    def consolidate(self, reqs: list[Requirement]):
        po_reqs = sorted([r for r in reqs if r.decision == "PO"],
                         key=lambda r: (r.created_at, r.first.pr_no, _int(r.first.pr_line)))
        open_pos: list[dict] = []
        n = 0
        for r in po_reqs:
            pol = r.policy
            cons = pol.get("consolidation") or {}
            hours = _params(pol)["consolidation_window_hours"]
            match_on = cons.get("match_on") or ["supplier_id", "plant", "route"]
            target = None
            if cons.get("enabled", True):
                for po in open_pos:
                    if all(getattr(po["first"], k if k != "supplier_id" else "supplier_id") == getattr(r, k)
                           for k in match_on) and \
                            (r.created_at - po["first"].created_at) <= timedelta(hours=hours):
                        target = po
                        break
            if target:
                target["reqs"].append(r)
                r.po_id = target["po_id"]
            else:
                n += 1
                po = {"po_id": f"PO-{n:04d}", "first": r, "reqs": [r]}
                open_pos.append(po)
                r.po_id = po["po_id"]
        return open_pos

    def run(self, raw_lines: list[dict]) -> dict:
        lines = [PRLine.from_dict(x) for x in raw_lines]
        ok, bad = self.prepare(lines)
        reqs = self.club(ok)
        for r in sorted(reqs, key=lambda r: (r.created_at, r.first.pr_no, _int(r.first.pr_line))):
            self.decide(r)
        pos = self.consolidate(reqs)
        by_line = {}
        for r in reqs:
            for l in r.lines:
                by_line[l.key] = r
        rows = []
        for l in lines:
            if l.not_processed_reason:
                rows.append({**{c: "" for c in OUTPUT_COLUMNS}, "pr_no": l.pr_no, "pr_line": l.pr_line,
                             "resolved_item_code": "", "decision": "NOT_PROCESSED", "route": "NOT_PROCESSED",
                             "policy_version": "", "reason": l.not_processed_reason})
                continue
            r = by_line[l.key]
            rows.append({
                "pr_no": l.pr_no, "pr_line": l.pr_line,
                "resolved_item_code": r.item_code or "NEW",
                "group_id": r.group_id, "po_id": r.po_id if r.decision == "PO" else "",
                "decision": r.decision, "route": r.route,
                "supplier_id": r.supplier_id if r.decision == "PO" else "",
                "unit_price": f"{r.unit_price:.2f}" if (r.decision == "PO" and r.unit_price is not None) else "",
                "group_value_inr": f"{r.value:.2f}",
                "suggested_supplier_ids": ";".join(r.suggested),
                "policy_version": r.policy_version, "reason": r.reason,
            })
        return {"rows": rows, "requirements": [self.req_to_dict(r) for r in reqs],
                "purchase_orders": [{"po_id": p["po_id"], "supplier_id": p["first"].supplier_id,
                                     "plant": p["first"].plant, "route": p["first"].route,
                                     "groups": [x.group_id for x in p["reqs"]],
                                     "value": round(sum(x.value for x in p["reqs"]), 2)} for p in pos],
                "not_processed": [{"pr_no": l.pr_no, "pr_line": l.pr_line, "reason": l.not_processed_reason} for l in bad]}

    def req_to_dict(self, r: Requirement) -> dict:
        return {
            "group_id": r.group_id, "item_code": r.item_code or "NEW",
            "item_description": self.ds.items[r.item_code].description if r.item_code else r.first.description,
            "plant": r.plant, "requirement_date": r.req_date.isoformat(),
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"), "is_emergency": r.is_emergency,
            "total_quantity": r.total_qty, "uom": r.first.uom, "value": r.value,
            "value_price": r.value_price, "value_source": r.value_source,
            "decision": r.decision, "route": r.route, "rule_id": r.rule_id, "rule_name": r.rule_name,
            "policy_version": r.policy_version, "po_id": r.po_id, "supplier_id": r.supplier_id,
            "supplier_name": r.supplier_name, "unit_price": r.unit_price, "contract_id": r.contract_id,
            "suggested_supplier_ids": r.suggested, "reason": r.reason, "trace": r.trace,
            "facts": r.facts,
            "lines": [{"pr_no": l.pr_no, "pr_line": l.pr_line, "created_at": l.created_at.strftime("%Y-%m-%d %H:%M"),
                       "requester": l.requester, "department": l.department, "description": l.description,
                       "item_code_given": l.item_code, "quantity": l.quantity, "uom": l.uom,
                       "need_by_date": l.need_by_date.isoformat() if l.need_by_date else None,
                       "urgency": l.urgency, "match_method": l.match_method, "match_score": l.match_score}
                      for l in r.lines],
            "preferred": [{"rank": i + 1, "supplier_id": p.supplier_id, "name": p.name, "score": p.score,
                           "reference_price": p.reference_price, "reference_source": p.reference_source,
                           "explain": p.explain()} for i, p in enumerate(r.preferred[:5])],
        }


def _int(s) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def run_batch(ds: DataStore, policies: list[dict], lines: list[dict], force_version: str | None = None) -> dict:
    return Engine(ds, PolicyBook(policies, force_version)).run(lines)


def preferred_export(ds: DataStore, policies: list[dict], as_of: date, top: int = 3,
                     force_version: str | None = None) -> list[dict]:
    pol = PolicyBook(policies, force_version).for_date(as_of)
    out = []
    for code in sorted(ds.items):
        for i, p in enumerate(preferred_list(ds, pol, code, as_of)[:top], 1):
            out.append({"item_code": code, "rank": i, "supplier_id": p.supplier_id,
                        "reference_price": f"{p.reference_price:.2f}"})
    return out
