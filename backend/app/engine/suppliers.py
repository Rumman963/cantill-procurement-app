from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .data import DataStore


def supplier_status(ds: DataStore, policy: dict, sid: str) -> str:
    over = (policy.get("supplier_status_overrides") or {}).get(sid)
    if over:
        return over
    s = ds.suppliers.get(sid)
    return s.status if s else "Unknown"


def is_eligible(ds: DataStore, policy: dict, sid: str) -> bool:
    if sid not in ds.suppliers:
        return False
    excluded = set(policy.get("excluded_supplier_statuses") or ["Blocked", "Inactive"])
    st = supplier_status(ds, policy, sid)
    return st not in excluded and st != "Unknown"


@dataclass
class RankedSupplier:
    supplier_id: str
    name: str
    score: float
    po_count: int
    last_po_date: date | None
    recency: float
    has_contract: bool
    on_time_pct: float
    quality_rating: float
    reference_price: float | None
    reference_source: str  # "PO 7300xxx dd" or "Contract RC-.."
    components: dict = field(default_factory=dict)

    def explain(self) -> str:
        c = self.components
        return (f"{self.po_count} PO(s) in lookback, last {self.last_po_date or 'never'} "
                f"(recency {self.recency}), contract {'yes' if self.has_contract else 'no'}, "
                f"on-time {self.on_time_pct:.0f}%, quality {self.quality_rating}; "
                f"score {self.score:.3f} = " + " + ".join(f"{k} {v:.3f}" for k, v in c.items()))


def preferred_list(ds: DataStore, policy: dict, item_code: str, as_of: date) -> list[RankedSupplier]:
    cfg = policy.get("preferred_suppliers") or {}
    w = cfg.get("weights") or {"frequency": .35, "recency": .25, "contract": .2, "on_time": .1, "quality": .1}
    bands = sorted(cfg.get("recency_bands") or [{"within_days": 90, "score": 1}, {"within_days": 365, "score": .5}],
                   key=lambda b: b["within_days"])
    lookback = int((policy.get("parameters") or {}).get("preferred_list_lookback_days", 548))
    start = as_of - timedelta(days=lookback)

    pos: dict[str, list] = {}
    for p in ds.po_history:
        if p.item_code == item_code and start <= p.po_date <= as_of:
            pos.setdefault(p.supplier_id, []).append(p)
    contracts: dict[str, list] = {}
    for c in ds.contracts.values():
        if item_code in c.lines and c.start_date and c.start_date <= as_of:
            contracts.setdefault(c.supplier_id, []).append(c)

    sids = [s for s in set(pos) | set(contracts) if is_eligible(ds, policy, s)]
    if not sids:
        return []
    max_cnt = max((len(pos.get(s, [])) for s in sids), default=0) or 1
    out = []
    for sid in sids:
        sp = ds.suppliers[sid]
        plist = sorted(pos.get(sid, []), key=lambda p: (p.po_date, p.po_number))
        last = plist[-1] if plist else None
        rec = 0.0
        if last:
            age = (as_of - last.po_date).days
            for b in bands:
                if age <= b["within_days"]:
                    rec = float(b["score"])
                    break
        has_con = sid in contracts
        comps = {
            "frequency": w.get("frequency", 0) * len(plist) / max_cnt,
            "recency": w.get("recency", 0) * rec,
            "contract": w.get("contract", 0) * (1.0 if has_con else 0.0),
            "on_time": w.get("on_time", 0) * sp.on_time_pct / 100.0,
            "quality": w.get("quality", 0) * sp.quality_rating / 5.0,
        }
        if last:
            ref, src = last.unit_price, f"PO {last.po_number} ({last.po_date})"
        else:
            c = sorted(contracts[sid], key=lambda c: (c.start_date, c.contract_id))[-1]
            ref, src = c.lines[item_code], f"Contract {c.contract_id}"
        out.append(RankedSupplier(sid, sp.name, round(sum(comps.values()), 4), len(plist),
                                  last.po_date if last else None, rec, has_con, sp.on_time_pct,
                                  sp.quality_rating, ref, src, {k: round(v, 4) for k, v in comps.items()}))
    out.sort(key=lambda r: (-r.score, -(r.last_po_date.toordinal() if r.last_po_date else 0), r.supplier_id))
    return out


def category_topup(ds: DataStore, policy: dict, category: str, exclude: set[str]) -> list[str]:
    cw = (policy.get("preferred_suppliers") or {}).get("category_topup_weights") or {"on_time": .5, "quality": .5}
    cands = []
    for s in ds.suppliers.values():
        if s.supplier_id in exclude or not is_eligible(ds, policy, s.supplier_id):
            continue
        if category and category.lower() in [c.lower() for c in s.categories]:
            sc = cw.get("on_time", .5) * s.on_time_pct / 100 + cw.get("quality", .5) * s.quality_rating / 5
            cands.append((round(sc, 6), s.supplier_id))
    cands.sort(key=lambda x: (-x[0], x[1]))
    return [sid for _, sid in cands]


def suggest_suppliers(ds: DataStore, policy: dict, item_code: str, category: str, as_of: date,
                      count: int) -> tuple[list[str], list[RankedSupplier]]:
    pref = preferred_list(ds, policy, item_code, as_of)
    ids = [p.supplier_id for p in pref][:count]
    if len(ids) < count:
        ids += category_topup(ds, policy, category, set(ids))[: count - len(ids)]
    return ids, pref
