from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from .db import Audit, ContractRow, Dataset, PolicyVersion
from .engine.contract_pdf import parse_contract_pdf
from .engine.data import Contract, DataStore, parse_date, to_float
from .engine.rules import validate_policy

POLICY_DIR = Path(__file__).resolve().parents[1] / "policies"
DATASETS = {
    "item_master": ["item_code", "description", "uom", "category", "item_type", "standard_price"],
    "supplier_master": ["supplier_id", "name", "categories", "status", "on_time_pct", "quality_rating"],
    "po_history": ["po_number", "po_date", "supplier_id", "item_code", "unit_price"],
    "plants": ["plant", "name"],
    "contract_register": ["contract_id", "supplier_id", "start_date", "end_date", "value_cap"],
    "contract_lines": ["contract_id", "item_code", "rate"],
    "pr_batch": ["pr_no", "pr_line", "created_at", "plant", "quantity"],
}


def audit(db: Session, actor: str, action: str, entity: str, details: dict | None = None):
    db.add(Audit(actor=actor or "unknown", action=action, entity=entity, details=details or {}))


def detect_dataset(filename: str, text: str) -> str | None:
    header = [h.strip().lower() for h in next(csv.reader(io.StringIO(text)), [])]
    for name, cols in DATASETS.items():
        if all(c in header for c in cols):
            if name == "contract_register" and "rate" in header:
                continue
            return name
    return None


def save_dataset(db: Session, name: str, text: str, actor: str) -> int:
    rows = list(csv.DictReader(io.StringIO(text)))
    if name == "contract_register":
        for r in rows:
            c = db.get(ContractRow, r["contract_id"].strip()) or ContractRow(contract_id=r["contract_id"].strip(), lines=[])
            c.supplier_id, c.supplier_name = r["supplier_id"].strip(), r.get("supplier_name", "").strip()
            c.start_date, c.end_date = r["start_date"].strip(), r["end_date"].strip()
            c.value_cap, c.consumed_value = r["value_cap"].strip(), (r.get("consumed_value") or "0").strip()
            c.source = "register" if c.source in (None, "register") else c.source
            c.added_by = actor
            db.merge(c)
    elif name == "contract_lines":
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(r["contract_id"].strip(), []).append(
                {"item_code": r["item_code"].strip(), "description": r.get("description", "").strip(),
                 "uom": r.get("uom", "").strip(), "rate": to_float(r["rate"])})
        for cid, lines in by.items():
            c = db.get(ContractRow, cid) or ContractRow(contract_id=cid, source="register")
            c.lines = lines
            db.merge(c)
    else:
        db.merge(Dataset(name=name, content=text, rows=len(rows), uploaded_by=actor, uploaded_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    audit(db, actor, "upload_data", name, {"rows": len(rows)})
    db.flush()
    return len(rows)


def save_contract(db: Session, d: dict, actor: str, source: str, pdf_name: str | None = None) -> ContractRow:
    c = ContractRow(contract_id=d["contract_id"], supplier_id=d["supplier_id"], supplier_name=d.get("supplier_name") or "",
                    start_date=d["start_date"], end_date=d["end_date"], value_cap=str(d["value_cap"]),
                    consumed_value=str(d.get("consumed_value") or 0), source=source,
                    lines=[{"item_code": l["item_code"], "description": l.get("description", ""), "uom": l.get("uom", ""),
                            "rate": float(l["rate"])} for l in d["lines"]], pdf_name=pdf_name, added_by=actor)
    db.merge(c)
    audit(db, actor, "add_contract", d["contract_id"], {"source": source})
    db.flush()
    return c


def build_datastore(db: Session) -> DataStore:
    ds = DataStore()
    get = lambda n: (db.get(Dataset, n).content if db.get(Dataset, n) else None)
    if get("item_master"):
        ds.load_items_csv(get("item_master"))
    if get("supplier_master"):
        ds.load_suppliers_csv(get("supplier_master"))
    if get("po_history"):
        ds.load_po_history_csv(get("po_history"))
    if get("plants"):
        ds.load_plants_csv(get("plants"))
    for c in db.query(ContractRow).all():
        ds.add_contract(Contract(c.contract_id, c.supplier_id or "", c.supplier_name or "", parse_date(c.start_date),
                                 parse_date(c.end_date), to_float(c.value_cap) or 0.0, to_float(c.consumed_value) or 0.0,
                                 c.source or "register", {l["item_code"]: float(l["rate"]) for l in (c.lines or [])}))
    return ds


def data_status(db: Session) -> dict:
    out = {n: (db.get(Dataset, n).rows if db.get(Dataset, n) else 0) for n in ("item_master", "supplier_master", "po_history", "plants")}
    out["contracts"] = db.query(ContractRow).count()
    out["ready"] = all(out[n] for n in ("item_master", "supplier_master", "po_history", "plants")) and out["contracts"] > 0
    return out


def active_policies(db: Session) -> list[dict]:
    rows = db.query(PolicyVersion).filter(PolicyVersion.status == "active").all()
    out = []
    for r in rows:
        b = dict(r.body)
        b["version"], b["effective_date"] = r.version, r.effective_date
        b["approved_at"] = r.approved_at.isoformat() if r.approved_at else ""
        b["_id"] = r.id
        out.append(b)
    return out


def current_policy_row(db: Session, as_of: date | None = None) -> PolicyVersion | None:
    as_of = as_of or date.today()
    rows = db.query(PolicyVersion).filter(PolicyVersion.status == "active").all()
    rows = sorted(rows, key=lambda r: (r.effective_date, r.approved_at or datetime.min))
    cur = None
    for r in rows:
        if parse_date(r.effective_date) <= as_of:
            cur = r
    return cur or (rows[-1] if rows else None)


def latest_policy_row(db: Session) -> PolicyVersion | None:
    rows = db.query(PolicyVersion).filter(PolicyVersion.status == "active").all()
    return sorted(rows, key=lambda r: (r.effective_date, r.approved_at or datetime.min))[-1] if rows else None


def seed(db: Session):
    if not db.query(PolicyVersion).count():
        body = json.loads((POLICY_DIR / "policy_v5.0.json").read_text())
        assert not validate_policy(body)
        db.add(PolicyVersion(version=body["version"], effective_date=body["effective_date"], status="active", body=body,
                             changes=["Initial policy CORVO-PROC-POL-04 v5.0 (seeded from the policy document)"],
                             source_type="seed", created_by="system", approved_by="system", approved_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        audit(db, "system", "seed_policy", "5.0")
    kit = os.environ.get("KIT_DIR")
    if kit and not db.query(Dataset).count():
        load_kit_dir(db, Path(kit), "system")
    db.commit()


def load_kit_dir(db: Session, kit: Path, actor: str) -> dict:
    loaded = {}
    for p in sorted(list(kit.glob("master_data/*.csv")) + list(kit.glob("contracts/*.csv"))):
        text = p.read_text(encoding="utf-8-sig")
        n = detect_dataset(p.name, text)
        if n:
            loaded[n] = save_dataset(db, n, text, actor)
    db.flush()
    for p in sorted(kit.glob("contracts/pdf/*.pdf")):
        d = parse_contract_pdf(p.read_bytes())
        if d["contract_id"] and not db.get(ContractRow, d["contract_id"]) and not d["warnings"]:
            save_contract(db, d, actor, "pdf", p.name)
            loaded.setdefault("pdf_contracts", []).append(d["contract_id"])
    return loaded
