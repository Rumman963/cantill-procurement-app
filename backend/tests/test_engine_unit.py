import copy
import json
from pathlib import Path

from app.engine.data import DataStore
from app.engine.engine import run_batch
from app.engine.rules import evaluate, validate_policy
from app.engine.contract_pdf import parse_contract_text
from app.policy_service import diff_policies

POLICY = json.loads((Path(__file__).resolve().parents[1] / "policies" / "policy_v5.0.json").read_text())

ITEMS = """item_code,description,uom,category,item_type,standard_price
ITM-1,Ball bearing 6205-2RS,NOS,Bearings,Material,200
ITM-2,Safety helmet,NOS,Safety,Material,100
ITM-3,CNC lathe,NOS,Capital,Capex,3000000
"""
SUPPLIERS = """supplier_id,name,city,categories,status,on_time_pct,quality_rating
S1,Alpha,Pune,Bearings,Active,90,4.5
S2,Beta,Pune,Bearings,Blocked,95,5
S3,Gamma,Pune,Safety,Active,80,4
S4,Delta,Pune,Capital,Active,85,4
"""
POS = """po_number,po_date,supplier_id,item_code,quantity,uom,unit_price,plant,delivered_on_time
1,2026-10-01,S1,ITM-1,10,NOS,190,PUN1,True
2,2026-10-05,S2,ITM-2,10,NOS,95,PUN1,True
"""
REGISTER = """contract_id,supplier_id,supplier_name,start_date,end_date,value_cap,consumed_value
RC-1,S3,Gamma,2026-01-01,2026-12-31,10000,9000
"""
LINES = """contract_id,item_code,description,uom,rate
RC-1,ITM-2,Safety helmet,NOS,90
"""


def store():
    ds = DataStore()
    ds.load_items_csv(ITEMS)
    ds.load_suppliers_csv(SUPPLIERS)
    ds.load_po_history_csv(POS)
    ds.load_plants_csv("plant,name\nPUN1,Pune\nCHK1,Chakan\n")
    ds.load_contract_register_csv(REGISTER)
    ds.load_contract_lines_csv(LINES)
    return ds


def line(pr, item, qty, created="2026-11-02 10:00", need="2026-11-20", urgency="Normal", plant="PUN1", desc="", est=""):
    return {"pr_no": pr, "pr_line": "1", "created_at": created, "requester": "t", "department": "d", "plant": plant,
            "item_code": item, "description": desc, "quantity": qty, "uom": "NOS", "estimated_unit_price": est,
            "need_by_date": need, "urgency": urgency}


def by_pr(res):
    return {r["pr_no"]: r for r in res["rows"]}


def test_policy_file_is_valid():
    assert validate_policy(POLICY) == []


def test_condition_evaluation():
    c = {"all": [{"fact": "value", "op": "lt", "value": 100}, {"any": [{"fact": "plant", "op": "eq", "value": "chk1"}]}]}
    assert evaluate(c, {"value": 50, "plant": "CHK1"})
    assert not evaluate(c, {"value": 150, "plant": "CHK1"})
    assert not evaluate({"all": [{"fact": "value", "op": "lt", "value": 1}]}, {"value": None})


def test_spot_clubbing_and_consolidation():
    res = run_batch(store(), [POLICY], [
        line("A", "ITM-1", 100), line("B", "ITM-1", 100, created="2026-11-03 09:00", need="2026-11-22")])
    r = by_pr(res)
    assert r["A"]["group_id"] == r["B"]["group_id"]
    assert r["A"]["route"] == "SPOT_PO" and r["A"]["supplier_id"] == "S1"
    assert float(r["A"]["group_value_inr"]) == 200 * 190


def test_blocked_supplier_history_does_not_count():
    r = by_pr(run_batch(store(), [POLICY], [line("A", "ITM-2", 200)]))
    assert r["A"]["route"] == "RFQ"
    assert "S2" not in r["A"]["suggested_supplier_ids"]


def test_contract_ceiling():
    r = by_pr(run_batch(store(), [POLICY], [line("A", "ITM-2", 10), line("B", "ITM-2", 10, plant="CHK1")]))
    assert r["A"]["route"] == "CONTRACT_PO"
    assert r["B"]["route"] != "CONTRACT_PO"


def test_capex_new_and_not_processed():
    r = by_pr(run_batch(store(), [POLICY], [
        line("A", "ITM-3", 1), line("B", "", 1, desc="quantum flux widget", est="5000"),
        line("C", "ITM-1", 0), line("D", "ITM-1", 5, plant="XYZ")]))
    assert r["A"]["route"] == "CAPEX_SOURCING"
    assert r["B"]["route"] == "NEW_ITEM_SOURCING" and r["B"]["resolved_item_code"] == "NEW"
    assert r["C"]["route"] == r["D"]["route"] == "NOT_PROCESSED"


def test_amendment_changes_decision_without_code():
    v51 = copy.deepcopy(POLICY)
    v51.update(version="5.1", effective_date="2026-11-01")
    next(r for r in v51["rules"] if r["id"] == "5.4")["when"]["all"][0]["value"] = 10000
    assert validate_policy(v51) == []
    r = by_pr(run_batch(store(), [POLICY, v51], [line("A", "ITM-1", 100)]))
    assert r["A"]["route"] == "RFQ" and r["A"]["policy_version"] == "5.1"
    assert any("5.4" in d for d in diff_policies(POLICY, v51))


def test_contract_pdf_text_parser():
    d = parse_contract_text("""RATE CONTRACT
Contract No. RC-2026-099
This Rate Contract is entered into between Corvo Engineering Pvt Ltd ("the Buyer"), and Test Co, Pune (Supplier
ID S009) ("the Supplier").
1. Term. This contract is valid from 2026-11-15 to 2027-11-14 (both days inclusive).
2. Value ceiling. The total value shall not exceed INR 20,00,000 (excluding GST).
1 ITM-1036 Pneumatic cylinder 50x100 NOS 3,537.76
""")
    assert d["contract_id"] == "RC-2026-099" and d["supplier_id"] == "S009"
    assert d["value_cap"] == 2000000 and d["lines"][0]["rate"] == 3537.76
