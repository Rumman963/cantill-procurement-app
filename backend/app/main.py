from __future__ import annotations

import copy
import csv
import io
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import llm, policy_service
from .chat import ChatBot
from .db import Audit, BuyerAction, ChatSession, ContractRow, PolicyVersion, PRLineRow, Run, SessionLocal, init_db
from .engine.contract_pdf import parse_contract_pdf, pdf_text
from .engine.data import parse_date
from .engine.engine import OUTPUT_COLUMNS, preferred_export, run_batch
from .engine.rules import FACTS_DOC, OPS, validate_policy
from .engine.suppliers import preferred_list
from .services import (active_policies, audit, build_datastore, current_policy_row, data_status, detect_dataset,
                       latest_policy_row, save_contract, save_dataset, seed)

IST = ZoneInfo("Asia/Kolkata")
app = FastAPI(title="Corvo PR-to-PO", version="1.0")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def user(x_user: str | None = Header(default=None)) -> str:
    return (x_user or "anonymous").strip()[:120]


def today() -> date:
    return datetime.now(IST).date()


@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    cur = current_policy_row(db, today())
    return {"ok": True, "app_version": "1.3", "data": data_status(db), "llm": llm.provider(), "today": today().isoformat(),
            "current_policy": cur.version if cur else None}


@app.get("/api/llm/check")
async def llm_check():
    return await run_in_threadpool(llm.check)


@app.post("/api/data/upload")
async def upload_data(files: list[UploadFile] = File(...), db: Session = Depends(get_db), actor: str = Depends(user)):
    done, skipped = {}, []
    pdfs = []
    for f in files:
        raw = await f.read()
        if f.filename.lower().endswith(".pdf"):
            pdfs.append((f.filename, raw))
            continue
        text = raw.decode("utf-8-sig", errors="replace")
        name = detect_dataset(f.filename, text)
        if not name or name == "pr_batch":
            skipped.append(f.filename)
            continue
        done[name] = save_dataset(db, name, text, actor)
    db.flush()
    for fn, raw in pdfs:
        d = parse_contract_pdf(raw)
        if d["warnings"] or not d["contract_id"]:
            skipped.append(f"{fn} (needs review in Contracts)")
            continue
        if not db.get(ContractRow, d["contract_id"]):
            save_contract(db, d, actor, "pdf", fn)
            done.setdefault("pdf_contracts", []).append(d["contract_id"])
    db.commit()
    return {"loaded": done, "skipped": skipped, "status": data_status(db)}


@app.get("/api/items")
def items(q: str = "", db: Session = Depends(get_db)):
    ds = build_datastore(db)
    out = [vars(i) for i in ds.items.values() if not q or q.lower() in (i.description + i.item_code).lower()]
    return out


@app.get("/api/suppliers")
def suppliers(db: Session = Depends(get_db)):
    return [vars(s) for s in build_datastore(db).suppliers.values()]


@app.get("/api/plants")
def plants(db: Session = Depends(get_db)):
    return build_datastore(db).plants


@app.get("/api/contracts")
def contracts(db: Session = Depends(get_db)):
    ds = build_datastore(db)
    out = []
    for c in db.query(ContractRow).order_by(ContractRow.contract_id).all():
        s = ds.suppliers.get(c.supplier_id)
        out.append({"contract_id": c.contract_id, "supplier_id": c.supplier_id, "supplier_name": c.supplier_name or (s.name if s else ""),
                    "supplier_status": s.status if s else "Unknown", "start_date": c.start_date, "end_date": c.end_date,
                    "value_cap": float(c.value_cap or 0), "consumed_value": float(c.consumed_value or 0),
                    "source": c.source, "lines": c.lines or [], "pdf_name": c.pdf_name, "added_by": c.added_by,
                    "valid_today": bool(c.start_date and c.end_date and c.start_date <= today().isoformat() <= c.end_date)})
    return out


@app.post("/api/contracts/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)):
    raw = await file.read()
    d = parse_contract_pdf(raw)
    if (d["warnings"]) and llm.available():
        try:
            txt = pdf_text(raw)
            ex = llm.extract_json(await run_in_threadpool(llm.complete,
                "Extract the rate contract. Return ONLY JSON: {contract_id, supplier_id, supplier_name, start_date (YYYY-MM-DD), "
                "end_date, value_cap (number INR), lines:[{item_code, description, uom, rate}]}. Do not invent values.", txt, 1500))
            for k, v in ex.items():
                if v and not d.get(k):
                    d[k] = v
            d["warnings"] = [w + " (filled by AI, please verify)" for w in d["warnings"]]
        except Exception:
            pass
    d["pdf_name"] = file.filename
    return d


@app.post("/api/contracts")
def add_contract(payload: dict = Body(...), db: Session = Depends(get_db), actor: str = Depends(user)):
    req = ["contract_id", "supplier_id", "start_date", "end_date", "value_cap", "lines"]
    miss = [k for k in req if not payload.get(k)]
    if miss:
        raise HTTPException(400, f"Missing: {', '.join(miss)}")
    ds = build_datastore(db)
    if payload["supplier_id"] not in ds.suppliers:
        raise HTTPException(400, f"Supplier {payload['supplier_id']} is not in the supplier master")
    bad = [l["item_code"] for l in payload["lines"] if l.get("item_code") not in ds.items]
    if bad:
        raise HTTPException(400, f"Items not in item master: {', '.join(bad)}")
    if not parse_date(payload["start_date"]) or not parse_date(payload["end_date"]):
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    existing = db.get(ContractRow, payload["contract_id"])
    save_contract(db, payload, actor, payload.get("source") or ("pdf" if payload.get("pdf_name") else "manual"), payload.get("pdf_name"))
    db.commit()
    return {"ok": True, "replaced": bool(existing)}


def pv_dict(r: PolicyVersion, cur_id=None) -> dict:
    return {"id": r.id, "version": r.version, "effective_date": r.effective_date, "status": r.status,
            "changes": r.changes, "source_type": r.source_type, "source_text": r.source_text, "based_on": r.based_on,
            "created_by": r.created_by, "created_at": r.created_at.isoformat() if r.created_at else None,
            "approved_by": r.approved_by, "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "review_note": r.review_note, "body": r.body, "in_force_today": r.id == cur_id}


@app.get("/api/policies")
def policies(db: Session = Depends(get_db)):
    cur = current_policy_row(db, today())
    rows = db.query(PolicyVersion).order_by(PolicyVersion.id.desc()).all()
    return {"versions": [pv_dict(r, cur.id if cur else None) for r in rows],
            "facts": FACTS_DOC, "ops": OPS, "routes": policy_service.ROUTES, "llm": llm.provider()}


@app.get("/api/policies/{pid}")
def policy(pid: int, db: Session = Depends(get_db)):
    r = db.get(PolicyVersion, pid)
    if not r:
        raise HTTPException(404)
    d = pv_dict(r)
    d["rules_text"] = [policy_service.rule_text(x) for x in r.body.get("rules", [])]
    return d


def _make_draft(db, body: dict, base: PolicyVersion, source_type: str, source_text: str, actor: str, extra_changes=None):
    errs = validate_policy(body)
    if errs:
        raise HTTPException(400, {"errors": errs})
    if not parse_date(body.get("effective_date")):
        raise HTTPException(400, {"errors": ["effective_date must be YYYY-MM-DD"]})
    if db.query(PolicyVersion).filter(PolicyVersion.version == str(body["version"]), PolicyVersion.status == "active").count():
        raise HTTPException(400, {"errors": [f"Version {body['version']} already exists; use a new version number"]})
    changes = policy_service.diff_policies(base.body if base else None, body)
    r = PolicyVersion(version=str(body["version"]), effective_date=body["effective_date"], status="draft", body=body,
                      changes=changes + [f"Reader note: {x}" for x in (extra_changes or [])], source_type=source_type,
                      source_text=source_text, based_on=base.id if base else None, created_by=actor)
    db.add(r)
    audit(db, actor, "draft_policy", str(body["version"]), {"source": source_type})
    db.commit()
    return r


@app.post("/api/policies/amend")
async def amend(text: str = Form(""), file: UploadFile | None = File(None), db: Session = Depends(get_db),
                actor: str = Depends(user)):
    content = text or ""
    if file is not None:
        raw = await file.read()
        content = (pdf_text(raw) if file.filename.lower().endswith(".pdf") else raw.decode("utf-8-sig", errors="replace")) + "\n" + content
        if file.filename.lower().endswith(".json"):
            body = json.loads(raw)
            base = latest_policy_row(db)
            r = _make_draft(db, body, base, "document", content[:20000], actor)
            return {"draft": pv_dict(r), "method": "policy JSON upload", "assumptions": []}
    if not content.strip():
        raise HTTPException(400, "Provide amendment text or a document")
    base = latest_policy_row(db)
    res = await run_in_threadpool(policy_service.propose, copy.deepcopy(base.body), content, today())
    body = res["policy"]
    try:
        r = _make_draft(db, body, base, "document" if file else "chat", content[:20000], actor,
                        [f"assumption: {a}" for a in res.get("assumptions", [])])
    except HTTPException as e:
        return JSONResponse(status_code=400, content={"error": e.detail, "proposal": body, "method": res["method"],
                                                      "assumptions": res.get("assumptions", [])})
    return {"draft": pv_dict(r), "method": res["method"], "assumptions": res.get("assumptions", []),
            "reader_changes": res.get("llm_changes", [])}


@app.post("/api/policies/draft")
def draft_from_editor(payload: dict = Body(...), db: Session = Depends(get_db), actor: str = Depends(user)):
    base = db.get(PolicyVersion, payload.get("based_on")) if payload.get("based_on") else latest_policy_row(db)
    old = db.get(PolicyVersion, payload.get("replaces_draft")) if payload.get("replaces_draft") else None
    r = _make_draft(db, payload["body"], base, "editor", payload.get("note", "") or (old.source_text if old else ""), actor)
    if old and old.status == "draft":
        old.status, old.review_note = "rejected", f"Superseded by edited draft #{r.id}"
        db.commit()
    return {"draft": pv_dict(r)}


@app.post("/api/policies/{pid}/approve")
def approve(pid: int, payload: dict = Body(default={}), db: Session = Depends(get_db), actor: str = Depends(user)):
    r = db.get(PolicyVersion, pid)
    if not r or r.status != "draft":
        raise HTTPException(400, "Only drafts can be approved")
    if db.query(PolicyVersion).filter(PolicyVersion.version == r.version, PolicyVersion.status == "active").count():
        raise HTTPException(400, f"Version {r.version} is already active")
    r.status, r.approved_by, r.approved_at = "active", actor, datetime.now(timezone.utc).replace(tzinfo=None)
    r.review_note = payload.get("note")
    audit(db, actor, "approve_policy", r.version, {"effective_date": r.effective_date})
    db.commit()
    return pv_dict(r)


@app.post("/api/policies/{pid}/reject")
def reject(pid: int, payload: dict = Body(default={}), db: Session = Depends(get_db), actor: str = Depends(user)):
    r = db.get(PolicyVersion, pid)
    if not r or r.status != "draft":
        raise HTTPException(400, "Only drafts can be rejected")
    r.status, r.review_note = "rejected", payload.get("note")
    audit(db, actor, "reject_policy", r.version, {"note": payload.get("note")})
    db.commit()
    return pv_dict(r)


def _read_batch(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise HTTPException(400, "Empty CSV")
    need = ["pr_no", "pr_line", "created_at", "plant", "quantity", "need_by_date"]
    miss = [c for c in need if c not in rows[0]]
    if miss:
        raise HTTPException(400, f"CSV is missing columns: {', '.join(miss)}")
    return rows


def _run(db, lines, source, label, actor, policy_version=None) -> Run:
    if not data_status(db)["ready"]:
        raise HTTPException(400, "Master data is not loaded yet. Upload it on the Data page first.")
    pols = active_policies(db)
    try:
        res = run_batch(build_datastore(db), pols, lines, force_version=policy_version or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    run = Run(label=label, source=source, policy_mode=f"forced v{policy_version}" if policy_version else "effective-dated",
              line_count=len(lines), input_lines=lines, result=res, created_by=actor)
    db.add(run)
    db.flush()
    audit(db, actor, "process_batch", f"run {run.id}", {"lines": len(lines), "policy_mode": run.policy_mode})
    db.commit()
    return run


@app.post("/api/process-batch")
async def process_batch(file: UploadFile = File(...), policy_version: str = Query(default="", description="Force one version, e.g. 5.0; default picks the version effective on each requirement date"),
                        format: str = Query(default="json"), label: str = Query(default=""),
                        db: Session = Depends(get_db), actor: str = Depends(user)):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    lines = _read_batch(text)
    run = _run(db, lines, "csv", label or file.filename, actor, policy_version)
    if format == "csv":
        return _csv_response(run.result["rows"], f"decisions_run{run.id}.csv")
    return {"run_id": run.id, "policy_mode": run.policy_mode, "decisions": run.result["rows"],
            "summary": _summary(run.result)}


def _summary(res: dict) -> dict:
    from collections import Counter
    return {"lines": len(res["rows"]), "requirements": len(res["requirements"]), "purchase_orders": len(res["purchase_orders"]),
            "routes": dict(Counter(r["route"] for r in res["rows"]))}


def _csv_response(rows: list[dict], name: str, cols=None) -> Response:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols or OUTPUT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/runs")
def runs(db: Session = Depends(get_db)):
    return [{"id": r.id, "label": r.label, "source": r.source, "policy_mode": r.policy_mode, "line_count": r.line_count,
             "created_by": r.created_by, "created_at": r.created_at.isoformat(), "summary": _summary(r.result)}
            for r in db.query(Run).order_by(Run.id.desc()).limit(50).all()]


@app.get("/api/runs/{rid}")
def get_run(rid: int, db: Session = Depends(get_db)):
    r = db.get(Run, rid)
    if not r:
        raise HTTPException(404)
    acts = db.query(BuyerAction).filter(BuyerAction.run_id == rid).order_by(BuyerAction.id).all()
    by_group = {}
    for a in acts:
        by_group.setdefault(a.group_id, []).append({"action": a.action, "comment": a.comment, "document_no": a.document_no,
                                                    "actor": a.actor, "at": a.at.isoformat(), "final": a.final})
    return {"id": r.id, "label": r.label, "source": r.source, "policy_mode": r.policy_mode, "created_by": r.created_by,
            "created_at": r.created_at.isoformat(), "result": r.result, "summary": _summary(r.result), "actions": by_group}


@app.get("/api/runs/{rid}/export.csv")
def export_run(rid: int, db: Session = Depends(get_db)):
    r = db.get(Run, rid)
    if not r:
        raise HTTPException(404)
    return _csv_response(r.result["rows"], f"decisions_run{rid}.csv")


@app.post("/api/runs/{rid}/requirements/{gid}/action")
def buyer_action(rid: int, gid: str, payload: dict = Body(...), db: Session = Depends(get_db), actor: str = Depends(user)):
    r = db.get(Run, rid)
    if not r:
        raise HTTPException(404)
    req = next((q for q in r.result["requirements"] if q["group_id"] == gid), None)
    if not req:
        raise HTTPException(404, "Requirement not found")
    action = payload.get("action")
    if action not in ("approve", "override"):
        raise HTTPException(400, "action must be approve or override")
    comment = (payload.get("comment") or "").strip()
    if action == "override" and len(comment) < 5:
        raise HTTPException(400, "An override needs a written reason")
    original = {k: req[k] for k in ("decision", "route", "supplier_id", "unit_price", "suggested_supplier_ids", "po_id", "value")}
    final = dict(original)
    if action == "override":
        ds = build_datastore(db)
        for k in ("route", "supplier_id", "unit_price", "suggested_supplier_ids"):
            if payload.get(k) not in (None, ""):
                final[k] = payload[k]
        final["decision"] = "PO" if final["route"] in ("CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO") else "SOURCING_REQUEST"
        if final["decision"] != "PO":
            final["supplier_id"], final["unit_price"], final["po_id"] = "", None, ""
        sid = final.get("supplier_id")
        if final["decision"] == "PO":
            if not sid:
                raise HTTPException(400, "A PO needs a supplier")
            st = ds.suppliers.get(sid)
            if not st or st.status != "Active":
                raise HTTPException(400, f"Supplier {sid} is {st.status if st else 'unknown'}: policy 5.7 forbids POs to it")
    prev = db.query(BuyerAction).filter(BuyerAction.run_id == rid).count()
    doc = (final.get("po_id") or f"PO-{rid:03d}-{prev + 1:04d}") if final["decision"] == "PO" else f"{final['route'].split('_')[0] if final['route'] != 'CAPEX_SOURCING' else 'CAPEX'}-{rid:03d}-{prev + 1:04d}"
    a = BuyerAction(run_id=rid, group_id=gid, action=action, comment=comment, original=original, final=final,
                    actor=actor, document_no=doc)
    db.add(a)
    audit(db, actor, f"{action}_requirement", f"run {rid} {gid}", {"comment": comment, "final": final, "document_no": doc})
    db.commit()
    return {"ok": True, "document_no": doc, "final": final}


@app.get("/api/preferred-suppliers")
def preferred(item_code: str, as_of: str = "", db: Session = Depends(get_db)):
    d = parse_date(as_of) or today()
    ds = build_datastore(db)
    from .engine.engine import PolicyBook
    pol = PolicyBook(active_policies(db)).for_date(d)
    lst = preferred_list(ds, pol, item_code, d)
    return {"item_code": item_code, "as_of": d.isoformat(), "policy_version": pol["version"],
            "suppliers": [{"rank": i + 1, "supplier_id": p.supplier_id, "name": p.name, "score": p.score,
                           "po_count": p.po_count, "last_po_date": p.last_po_date.isoformat() if p.last_po_date else None,
                           "recency": p.recency, "has_contract": p.has_contract, "on_time_pct": p.on_time_pct,
                           "quality_rating": p.quality_rating, "reference_price": p.reference_price,
                           "reference_source": p.reference_source, "components": p.components}
                          for i, p in enumerate(lst)]}


@app.get("/api/preferred-suppliers/export.csv")
def preferred_csv(as_of: str = "", top: int = 3, db: Session = Depends(get_db)):
    d = parse_date(as_of) or today()
    rows = preferred_export(build_datastore(db), active_policies(db), d, top)
    return _csv_response(rows, f"preferred_suppliers_{d.isoformat()}.csv", ["item_code", "rank", "supplier_id", "reference_price"])


@app.post("/api/chat")
def chat(payload: dict = Body(...), db: Session = Depends(get_db), actor: str = Depends(user)):
    if not data_status(db)["ready"]:
        raise HTTPException(400, "Master data is not loaded yet. Upload it on the Data page first.")
    sid = payload.get("session_id") or uuid.uuid4().hex[:12]
    sess = db.get(ChatSession, sid) or ChatSession(id=sid, state={}, history=[], user=actor)
    msg = payload.get("message", "")
    ds = build_datastore(db)
    bot = ChatBot(ds, today())
    state, resp = bot.reply(sess.state or {}, msg)
    created = None
    if resp.get("ready"):
        created, decision = _create_pr_from_chat(db, ds, state, actor, payload.get("department", ""))
        resp = {"text": decision, "options": [{"label": "Raise another request", "value": "new request"}], "created": created}
        state = {}
    hist = list(sess.history or []) + [{"role": "user", "text": msg}, {"role": "assistant", **resp}]
    sess.state, sess.history, sess.updated_at = state, hist[-80:], datetime.now(timezone.utc).replace(tzinfo=None)
    db.merge(sess)
    db.commit()
    return {"session_id": sid, **resp}


def _create_pr_from_chat(db, ds, s, actor, department):
    n = db.query(PRLineRow).count() + 1
    now = datetime.now(IST).replace(tzinfo=None)
    it = ds.items.get(s.get("item_code") or "")
    line = {"pr_no": f"PR-CHAT-{n:05d}", "pr_line": "1", "created_at": now.strftime("%Y-%m-%d %H:%M"),
            "requester": actor, "department": department, "plant": s["plant"], "item_code": s.get("item_code") or "",
            "description": it.description if it else s.get("new_desc", ""), "quantity": s["qty"],
            "uom": it.uom if it else (s.get("uom_said") or "NOS"), "estimated_unit_price": s.get("est_price") or "",
            "need_by_date": s["need_by"], "urgency": s.get("urgency") or "Normal"}
    db.add(PRLineRow(pr_no=line["pr_no"], data=line, source="chat", created_by=actor))
    audit(db, actor, "create_pr", line["pr_no"], line)
    db.flush()
    open_lines = [r.data for r in db.query(PRLineRow).filter(PRLineRow.status == "open").order_by(PRLineRow.id).all()]
    res = run_batch(ds, active_policies(db), open_lines)
    row = next(r for r in res["rows"] if r["pr_no"] == line["pr_no"])
    req = next((q for q in res["requirements"] if q["group_id"] == row["group_id"]), None)
    friendly = {"CONTRACT_PO": "a PO against a rate contract", "SPOT_PO": "a direct (spot) PO", "EMERGENCY_PO": "an emergency PO",
                "RFQ": "a request for quotations (RFQ)", "RFP": "a formal RFP", "CAPEX_SOURCING": "capex sourcing (needs capex approval)",
                "NEW_ITEM_SOURCING": "new-item sourcing"}.get(row["route"], row["route"])
    txt = f"✅ Raised {line['pr_no']} for {line['quantity']:g} {line['uom']} of {line['description']} at {line['plant']}, needed by {line['need_by_date']}."
    if row["route"] == "NOT_PROCESSED":
        txt += f" It could not be processed: {row['reason']}"
    else:
        txt += f"\n\nUnder policy v{row['policy_version']} this is expected to go to **{friendly}**"
        if row["supplier_id"]:
            txt += f" with {row['supplier_id']} ({ds.suppliers[row['supplier_id']].name}) at ₹{float(row['unit_price']):,.2f}"
        txt += f". Estimated value ₹{float(row['group_value_inr']):,.0f}."
        if req and len(req["lines"]) > 1:
            txt += f" It was combined with {len(req['lines']) - 1} similar open request(s) for the same item and plant (no-splitting rule 2.2)."
        txt += " Procurement will review it in the buyer workspace."
    return line, txt


@app.get("/api/prs")
def open_prs(db: Session = Depends(get_db)):
    return [{"id": r.id, **r.data, "status": r.status, "source": r.source} for r in db.query(PRLineRow).order_by(PRLineRow.id.desc()).all()]


@app.post("/api/prs/process")
def process_open(db: Session = Depends(get_db), actor: str = Depends(user)):
    lines = [r.data for r in db.query(PRLineRow).filter(PRLineRow.status == "open").order_by(PRLineRow.id).all()]
    if not lines:
        raise HTTPException(400, "No open chat requests")
    run = _run(db, lines, "chat", f"Chat requests ({len(lines)})", actor)
    return {"run_id": run.id}


@app.get("/api/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db)):
    return [{"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "entity": a.entity, "details": a.details}
            for a in db.query(Audit).order_by(Audit.id.desc()).limit(limit).all()]


FRONT = Path(__file__).resolve().parents[2] / "frontend"
if FRONT.exists():
    app.mount("/static", StaticFiles(directory=FRONT), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONT / "index.html")
