import json
import os
from pathlib import Path

from app.engine.contract_pdf import parse_contract_pdf, to_contract
from app.engine.data import DataStore

KIT = Path(os.environ.get("KIT_DIR", "../../kit/Cantill_FullStack_Task_Kit"))
POLICY = Path(__file__).resolve().parents[1] / "policies" / "policy_v5.0.json"


def kit_available() -> bool:
    return (KIT / "master_data" / "item_master.csv").exists()


def load_kit() -> DataStore:
    ds = DataStore()
    rd = lambda p: (KIT / p).read_text(encoding="utf-8-sig")
    ds.load_items_csv(rd("master_data/item_master.csv"))
    ds.load_suppliers_csv(rd("master_data/supplier_master.csv"))
    ds.load_po_history_csv(rd("master_data/po_history.csv"))
    ds.load_plants_csv(rd("master_data/plants.csv"))
    ds.load_contract_register_csv(rd("contracts/contract_register.csv"))
    ds.load_contract_lines_csv(rd("contracts/contract_lines.csv"))
    for pdf in sorted((KIT / "contracts" / "pdf").glob("*.pdf")):
        d = parse_contract_pdf(pdf.read_bytes())
        if d["contract_id"] and d["contract_id"] not in ds.contracts:
            ds.add_contract(to_contract(d))
    return ds


def policy_v5() -> dict:
    return json.loads(POLICY.read_text())


def sample_batch() -> list[dict]:
    import csv
    return list(csv.DictReader((KIT / "pr_batches" / "sample_pr_batch.csv").open(encoding="utf-8-sig")))
