# APPROACH.md

## How I approached it

1. **Engine first, against the worked examples.** The decision engine is pure Python with no web or database code (`backend/app/engine`). It takes PR lines, master data and a list of policy versions, and returns a decision per line. I built it clause by clause and tested it against all 14 worked decisions and the 3 preferred-supplier lists until every route, supplier, price, value, clubbing group and PO number matched.
2. **The policy is data, not code.** Everything that is a business choice is in the policy JSON:
   - limits and windows
   - rule order
   - conditions on item type, category, plant, value, urgency, contract and history
   - outcomes and supplier strategies
   - ranking weights
   - valuation order
   - consolidation keys
   - supplier status overrides

   The engine only knows how to evaluate conditions and carry out outcomes. A rule like "at CHK1, spot POs only below ₹1 lakh" is a new entry in the rule list, not a code change (see the test in `tests/test_policy_and_api.py`).
3. **Versioning with effective dates.** Each requirement is decided by the version effective on its requirement date, and every output row records that version and clause.
4. **Human in the loop for anything uncertain.**
   - Amendments become *drafts* with a plain-language diff. They activate only after approval.
   - Contract PDFs are extracted, then confirmed by the buyer.
   - Chat confirms the item and fields before creating a PR, and never invents items.
   - Overrides need a reason and cannot pick a blocked supplier.
5. **Then the API, UI, docs and deployment**, with the gaps found along the way recorded in DECISIONS.md.

## Architecture

```
frontend (HTML/JS) ── REST ──> FastAPI (main.py)
                                ├── chat.py           slot-filling intake, item matching, confirmation
                                ├── policy_service.py amendment reading, validation, diff
                                ├── services.py       DB <-> engine glue, data upload, seeding
                                ├── db.py             SQLAlchemy models (SQLite / Postgres)
                                └── engine/           pure, deterministic, unit-tested
                                     matcher · rules · suppliers · engine · contract_pdf
```

Data model: datasets (raw master CSVs), contracts (with lines and source), policy_versions (body, status, effective date, changes, who/when), pr_lines (chat PRs), runs (input plus full result), buyer_actions (approve/override with original and final decision), chat_sessions, audit_log.

## AI tools used

- **Building:** I used Claude (Anthropic) as a coding assistant for scaffolding, writing code and tests, and reviewing edge cases against the policy text. I checked every decision rule against the policy and the worked examples myself, and I can explain and change any part of the code.
- **Inside the app (optional):** if `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY` is set, an LLM is used for:
  - reading a policy amendment (document or typed text) into a proposed new policy JSON, plus a change list and assumptions
  - pulling fields out of long, messy chat messages
  - filling fields a contract PDF parser could not read

  The LLM only **proposes**. Its output is validated by code (known facts, operators, routes), shown as a diff, and approved by a person. The decision engine itself never calls an LLM, so decisions are deterministic and repeatable.
- **Without a key** the app still works fully. Chat uses deterministic parsing. Amendments are read by a rule-based reader for common patterns (limit changes, supplier status, windows, version, effective date), and anything else is done in the rule editor.
