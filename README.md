# Corvo PR → PO

A web app where employees raise purchase requests by chatting, and the system decides, by following Corvo's procurement policy, whether to place a purchase order or start a sourcing request. **The policy is stored as versioned data.** When Procurement uploads an amendment, the app shows what will change, and after approval it decides differently from the effective date, with no code change.

- **Stack:** Python 3.12 · FastAPI · SQLAlchemy (SQLite locally, Postgres in production) · plain HTML/JS frontend (no build step) · optional AI provider (Anthropic / OpenAI / Gemini)
- **Docs:** [DECISIONS.md](DECISIONS.md) (how gaps in the policy were resolved, plus the supplier ranking method) · [APPROACH.md](APPROACH.md) (architecture, AI use)

## Run it

```bash
cp .env.example .env              # optional: add an AI key
KIT_PATH=/path/to/Cantill_FullStack_Task_Kit docker compose up --build
# open http://localhost:8000
```

`KIT_PATH` is optional. If set, master data, contracts and contract PDFs are loaded on first start. Without it, upload the files on the **Data** page (select all CSVs and PDFs at once; they are recognised by their columns). Task data is **not** in this repo.

Without Docker:

```bash
cd backend
pip install -r requirements.txt
KIT_DIR=/path/to/Cantill_FullStack_Task_Kit uvicorn app.main:app --reload
```

Tests (the worked examples, policy changes, chat and the API):

```bash
cd backend && KIT_DIR=/path/to/Cantill_FullStack_Task_Kit pytest -q
```

## What's where

| Screen | What it does |
|---|---|
| **Raise request** | Chat intake. Matches the item master, asks for anything missing, never invents items, confirms, creates the PR, and tells the requester the likely route. |
| **Buyer workspace** | Requirements with their PR lines, decision, value, supplier and price or suggested suppliers, and *why* (clause, policy version, contract or PO relied on, preferred-list scores). One next action: **Approve** (creates the PO/RFQ number) or **Override** (reason required). Full history. |
| **Batch & API** | Upload a PR batch CSV, choose the policy mode, download the decisions CSV. |
| **Policy** | Versions with effective dates. Amend by document, by typing the change, or in the rule editor. Shows a plain-language diff, then approve or reject. |
| **Contracts** | Register plus PDF contracts, with ceiling usage. Add a contract from its signed PDF (extracted, then confirmed). |
| **Preferred suppliers** | Ranked list per item as of any date, with the score breakdown. Top-3 CSV export. |
| **Data / Audit log** | Master-data upload and status. Every action with who and when. |

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/process-batch` | multipart `file` (PR batch CSV). Query: `policy_version=5.0` to force a version, `format=csv` to get the CSV directly. |
| `GET` | `/api/runs/{id}/export.csv` | Decisions of a run, in the required format. |
| `GET` | `/api/preferred-suppliers/export.csv?as_of=YYYY-MM-DD` | `item_code, rank, supplier_id, reference_price` (top 3). |
| `POST` | `/api/policies/amend` | `text` and/or `file`: creates a draft version with the change list. |
| `POST` | `/api/policies/{id}/approve` | Activates a draft from its effective date. |
| `POST` | `/api/chat` | `{session_id, message}` |
| `POST` | `/api/contracts/extract-pdf`, `/api/contracts` | Read a contract PDF, then save the confirmed contract. |

Interactive docs are at `/docs`. Send `X-User: <name>` to record who acted.

```bash
curl -F "file=@batch.csv" "http://localhost:8000/api/process-batch?format=csv&policy_version=5.0" -o run1.csv
```

## Blind-test runbook

1. **Run 1:** go to Batch & API → upload the new batch → Policy: *Force v5.0* → Process → **Download decisions CSV** → save as `yourname__run1.csv`.
2. **Amendment:** go to Policy → upload the amendment document (or paste its text) → **Read amendment** → check "What will change" (use *Adjust in editor* if anything is missing) → **Approve**.
3. **Run 2:** go to Batch & API → same batch → *Version effective on each requirement date* (or force the new version if the amendment says it applies to the whole batch) → Process → Download → `yourname__run2.csv`.
4. **Preferred lists:** go to Preferred suppliers → set *As of* to the given date → **Export top-3 CSV** → `yourname__preferred.csv`.
5. Put the three files in `submission/`, then commit and push. Only CSVs are committed; no code changes.

## Deploy (Render, free)

1. Push this repo to GitHub (public).
2. Render → **New → Blueprint** → pick the repo. `render.yaml` creates the web service (Docker) and a free Postgres database.
3. Optionally set `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.
4. Open the URL → **Data** page → upload the kit's CSVs and contract PDFs once (they are stored in Postgres).

## Project layout

```
backend/app/engine/   pure decision engine (no web/DB code): matcher, rules, suppliers, engine, contract PDF reader
backend/app/          API (main.py), DB models, chat, policy versions/amendments, LLM client
backend/policies/     policy_v5.0.json: the policy as data (seeded as the first version)
backend/tests/        worked examples, policy changes, chat, API
frontend/             index.html, app.js, styles.css
```
