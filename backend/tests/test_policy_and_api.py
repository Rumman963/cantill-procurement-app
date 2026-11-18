import copy
import os
from datetime import date

import pytest

from app.engine.engine import run_batch
from app.policy_service import diff_policies, rule_based_propose
from tests.kit import KIT, kit_available, load_kit, policy_v5, sample_batch

pytestmark = pytest.mark.skipif(not kit_available(), reason="task kit not available (set KIT_DIR)")


def routes(res):
    return {(r["pr_no"], r["pr_line"]): r for r in res["rows"]}


def test_new_plant_specific_rule_without_code_change():
    v5 = policy_v5()
    v51 = copy.deepcopy(v5)
    v51.update(version="5.1", effective_date="2026-11-01")
    # CHK1-only: spot POs only below 1 lakh -> insert before 5.4
    i = [r["id"] for r in v51["rules"]].index("5.4")
    v51["rules"].insert(i, {"id": "5.4a", "name": "CHK1 spot limit", "when": {"all": [
        {"fact": "plant", "op": "eq", "value": "CHK1"}, {"fact": "value", "op": "gte", "value": 100000}]},
        "decision": "SOURCING_REQUEST", "route": "RFQ", "supplier": {"mode": "suggest", "count": 3}})
    a = routes(run_batch(load_kit(), [v5], sample_batch()))
    b = routes(run_batch(load_kit(), [v5, v51], sample_batch()))
    k = ("PR-26-00146", "1")
    assert a[k]["route"] == "SPOT_PO" and b[k]["route"] == "RFQ" and b[k]["policy_version"] == "5.1"
    k = ("PR-26-00088", "1")
    assert b[k]["route"] == "SPOT_PO"


def test_effective_date_respected():
    v5 = policy_v5()
    v51 = copy.deepcopy(v5)
    v51.update(version="5.1", effective_date="2026-11-10")
    v51["supplier_status_overrides"] = {"S014": "Blocked"}
    b = routes(run_batch(load_kit(), [v5, v51], sample_batch()))
    assert b[("PR-26-00059", "1")]["supplier_id"] == "S014"           # 6 Nov -> v5.0
    assert b[("PR-26-00059", "1")]["policy_version"] == "5.0"
    for r in b.values():
        if r["policy_version"] == "5.1":
            assert r["supplier_id"] != "S014" and "S014" not in r["suggested_supplier_ids"]


def test_forced_version():
    v5 = policy_v5()
    v51 = copy.deepcopy(v5)
    v51.update(version="5.1", effective_date="2026-11-01")
    res = run_batch(load_kit(), [v5, v51], sample_batch(), force_version="5.0")
    assert {r["policy_version"] for r in res["rows"]} <= {"5.0", ""}


def test_rule_based_amendment_reader_and_diff():
    v5 = policy_v5()
    res = rule_based_propose(v5, "Version 5.1 effective from 2026-11-10. Clause 5.4 limit is reduced to ₹1,50,000. "
                                 "Supplier S029 is now Inactive.", date(2026, 11, 18))
    p = res["policy"]
    assert p["version"] == "5.1" and p["effective_date"] == "2026-11-10"
    assert p["supplier_status_overrides"]["S029"] == "Inactive"
    r54 = next(r for r in p["rules"] if r["id"] == "5.4")
    assert r54["when"]["all"][0]["value"] == 150000
    d = diff_policies(v5, p)
    assert any("5.4" in x for x in d) and any("S029" in x for x in d)


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KIT_DIR", str(KIT.resolve()))
    import importlib
    import app.db as dbm
    monkeypatch.setattr(dbm, "DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    eng = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    monkeypatch.setattr(dbm, "engine", eng)
    monkeypatch.setattr(dbm, "SessionLocal", sessionmaker(bind=eng, autoflush=False, expire_on_commit=False))
    import app.main as m
    importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:
        H = {"X-User": "tester"}
        assert c.get("/api/health").json()["data"]["ready"]
        csv_bytes = (KIT / "pr_batches" / "sample_pr_batch.csv").read_bytes()
        r = c.post("/api/process-batch", files={"file": ("b.csv", csv_bytes)}, headers=H).json()
        assert r["summary"]["lines"] == 186
        out = c.post("/api/process-batch?format=csv", files={"file": ("b.csv", csv_bytes)}).text
        assert out.splitlines()[0].startswith("pr_no,pr_line,resolved_item_code,group_id,po_id,decision,route")
        j = c.post("/api/chat", json={"message": "need 2 flux capacitor for chakan"}, headers=H).json()
        assert "couldn't" in j["text"].lower() and "item master" in j["text"].lower()
        d = c.post("/api/policies/amend", data={"text": "Version 5.1 effective from 2026-11-01. Clause 5.4 limit is reduced to ₹1,00,000."}, headers=H).json()
        assert d["draft"]["status"] == "draft"
        assert c.post(f"/api/policies/{d['draft']['id']}/approve", json={}, headers=H).json()["status"] == "active"
        r2 = c.post("/api/process-batch", files={"file": ("b.csv", csv_bytes)}, headers=H).json()
        hyd = next(x for x in r2["decisions"] if x["pr_no"] == "PR-26-00146")
        assert hyd["route"] == "RFQ" and hyd["policy_version"] == "5.1"
        rid = r2["run_id"]
        assert c.post(f"/api/runs/{rid}/requirements/REQ-0001/action", json={"action": "override", "comment": ""}, headers=H).status_code == 400
        assert c.post(f"/api/runs/{rid}/requirements/REQ-0001/action", json={"action": "override", "comment": "test reason", "route": "SPOT_PO", "supplier_id": "S004"}, headers=H).status_code == 400
        pref = c.get("/api/preferred-suppliers/export.csv?as_of=2026-11-01").text.splitlines()
        assert pref[0] == "item_code,rank,supplier_id,reference_price"


def test_llm_amendment_path_is_validated(monkeypatch):
    import json
    from app import llm, policy_service
    v5 = policy_v5()
    proposed = copy.deepcopy(v5)
    i = [r["id"] for r in proposed["rules"]].index("5.4")
    proposed["rules"].insert(i, {"id": "5.4a", "name": "CHK1 spot limit", "when": {"all": [
        {"fact": "plant", "op": "eq", "value": "CHK1"}, {"fact": "value", "op": "gte", "value": 100000}]},
        "decision": "SOURCING_REQUEST", "route": "RFQ", "supplier": {"mode": "suggest", "count": 3}})
    fake = "```json\n" + json.dumps({"version": "5.1", "effective_date": "2026-11-05", "changes": ["CHK1 spot limit"],
                                     "assumptions": [], "policy": proposed}) + "\n```"
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "provider", lambda: "fake")
    monkeypatch.setattr(llm, "complete", lambda *a, **k: fake)
    res = policy_service.propose(v5, "From 5 Nov, spot POs at Chakan only below 1 lakh. Version 5.1", date(2026, 11, 18))
    assert res["policy"]["version"] == "5.1" and res["method"].startswith("AI reader")
    assert any("NEW rule [5.4a]" in x for x in diff_policies(v5, res["policy"]))
    bad = copy.deepcopy(proposed)
    bad["rules"][0]["when"] = {"all": [{"fact": "made_up_fact", "op": "eq", "value": 1}]}
    monkeypatch.setattr(llm, "complete", lambda *a, **k: json.dumps({"version": "5.1", "effective_date": "2026-11-05", "policy": bad}))
    res2 = policy_service.propose(v5, "Version 5.1 effective from 2026-11-05.", date(2026, 11, 18))
    assert res2["method"].startswith("rule-based") and "invalid" in res2["assumptions"][0]
