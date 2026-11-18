from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


def parse_date(s: str | None) -> Optional[date]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(s: str | None) -> Optional[datetime]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def to_float(s) -> Optional[float]:
    if s is None:
        return None
    try:
        t = str(s).replace(",", "").replace("₹", "").strip()
        return float(t) if t else None
    except ValueError:
        return None


@dataclass
class Item:
    item_code: str
    description: str
    uom: str
    category: str
    item_type: str
    standard_price: float


@dataclass
class Supplier:
    supplier_id: str
    name: str
    city: str
    categories: list[str]
    status: str
    on_time_pct: float
    quality_rating: float


@dataclass
class PORecord:
    po_number: str
    po_date: date
    supplier_id: str
    item_code: str
    quantity: float
    uom: str
    unit_price: float
    plant: str
    delivered_on_time: bool


@dataclass
class Contract:
    contract_id: str
    supplier_id: str
    supplier_name: str
    start_date: date
    end_date: date
    value_cap: float
    consumed_value: float
    source: str = "register"  # register | pdf | manual
    lines: dict[str, float] = field(default_factory=dict)  # item_code -> rate


@dataclass
class DataStore:
    items: dict[str, Item] = field(default_factory=dict)
    suppliers: dict[str, Supplier] = field(default_factory=dict)
    po_history: list[PORecord] = field(default_factory=list)
    plants: dict[str, str] = field(default_factory=dict)
    contracts: dict[str, Contract] = field(default_factory=dict)

    def load_items_csv(self, text: str):
        for r in csv.DictReader(io.StringIO(text)):
            self.items[r["item_code"].strip()] = Item(
                r["item_code"].strip(), r["description"].strip(), r["uom"].strip(),
                r["category"].strip(), r["item_type"].strip(), to_float(r["standard_price"]) or 0.0)

    def load_suppliers_csv(self, text: str):
        for r in csv.DictReader(io.StringIO(text)):
            self.suppliers[r["supplier_id"].strip()] = Supplier(
                r["supplier_id"].strip(), r["name"].strip(), r.get("city", "").strip(),
                [c.strip() for c in r["categories"].split(";") if c.strip()],
                r["status"].strip(), to_float(r["on_time_pct"]) or 0.0, to_float(r["quality_rating"]) or 0.0)

    def load_po_history_csv(self, text: str):
        for r in csv.DictReader(io.StringIO(text)):
            self.po_history.append(PORecord(
                r["po_number"].strip(), parse_date(r["po_date"]), r["supplier_id"].strip(),
                r["item_code"].strip(), to_float(r["quantity"]) or 0.0, r["uom"].strip(),
                to_float(r["unit_price"]) or 0.0, r["plant"].strip(),
                str(r.get("delivered_on_time", "")).strip().lower() in ("true", "1", "yes")))

    def load_plants_csv(self, text: str):
        for r in csv.DictReader(io.StringIO(text)):
            self.plants[r["plant"].strip()] = r.get("name", "").strip()

    def load_contract_register_csv(self, text: str):
        for r in csv.DictReader(io.StringIO(text)):
            cid = r["contract_id"].strip()
            c = self.contracts.get(cid)
            lines = c.lines if c else {}
            self.contracts[cid] = Contract(
                cid, r["supplier_id"].strip(), r.get("supplier_name", "").strip(),
                parse_date(r["start_date"]), parse_date(r["end_date"]),
                to_float(r["value_cap"]) or 0.0, to_float(r.get("consumed_value")) or 0.0,
                "register", lines)

    def load_contract_lines_csv(self, text: str):
        for r in csv.DictReader(io.StringIO(text)):
            cid = r["contract_id"].strip()
            c = self.contracts.get(cid)
            if c is None:
                c = Contract(cid, "", "", None, None, 0.0, 0.0, "register", {})
                self.contracts[cid] = c
            c.lines[r["item_code"].strip()] = to_float(r["rate"]) or 0.0

    def add_contract(self, c: Contract):
        self.contracts[c.contract_id] = c
