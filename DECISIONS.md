# DECISIONS.md

This file lists what the policy (v5.0), the brief or the data leave open, what I decided, and why. Each decision is implemented in code and covered by a test where possible. A test or file reference is given in brackets.

All 14 worked examples and the 3 example preferred-supplier lists are reproduced exactly: routes, suppliers, prices, values, clubbing, PO consolidation and PO numbers (`tests/test_worked_examples.py`).

---

## A. Data issues found in the sample batch

| # | What I noticed | Decision | Why |
|---|---|---|---|
| A1 | **PR-26-00172** has quantity **0**. | `NOT_PROCESSED`: "Quantity must be greater than zero". | A zero-quantity line has no value and cannot be ordered. Clubbing it would change nothing, so it is safer to send it back to the requester. |
| A2 | **PR-26-00173** is for plant **PUN2**, which is not in `plants.csv`. | `NOT_PROCESSED`: "Unknown plant". | Guessing PUN1 could deliver to the wrong site. The policy covers only PUN1 and CHK1. |
| A3 | **PR-26-00174 / 00175**: same requester, same servo drive, same need-by date, raised 4 minutes apart under two different departments. This is a textbook split. | Clubbed into one requirement under 2.2 and valued on the total (2 × ₹65,837.89 = ₹1,31,676). Still below ₹2 lakh, so SPOT_PO. | The no-splitting rule exists for exactly this case. |
| A4 | With PR-174/175 included, **every PO number in the worked examples is one higher** than published (e.g. PO-0012 instead of PO-0011). Excluding those two PRs reproduces the published numbers exactly. | Kept the policy's numbering rule (POs numbered in requirement creation order). PR-174/175 created the first PO of the batch (02 Nov 08:10). | The two PRs were evidently appended to the kit after the examples were numbered. All decisions are identical either way (`test_full_batch_decisions_unchanged_except_numbering`). |
| A5 | Some descriptions say "**- urgent**" (PR-18, 23, 139, 167/2), but their `urgency` field is `Normal`. | Only the `urgency` field makes a line an emergency. | Policy 2.3 says "marked Emergency", and the field is the formal marking. Emergency is a privilege (no quotes up to ₹5 lakh), so free text should not unlock it. In chat, the word "urgent" triggers a question asking the requester to confirm Emergency. |
| A6 | "(repeat)", "as per sample", "for maintenance" appear in descriptions. | Treated as noise for item matching. Repeat orders are processed normally and clubbed if they fit 2.2. | They describe the request, not the item. |
| A7 | 39 lines have no item code, only short forms ("brg 6308 ZZ", "hyd hose", "pneu cylinder", `monitor 24"`, "welding rod e6013"). | Deterministic matcher: normalise, expand known short forms (brg→bearing, hyd→hydraulic, pneu→pneumatic, rod→electrode, `"`→inch), then score tokens. **Every size/model token the requester typed (anything with a digit) must appear in the item.** Below the threshold, or when two items tie, the line is a NEW item. | A wrong match is worse than "new item". A wrong match buys the wrong part, while a new-item request just gets a buyer's attention. All 37 matchable lines match. "Drinking water purifier" and "Ultrasonic thickness gauge" are NEW. |
| A8 | Item code given **and** a description. | The code wins (1.1). An unknown code falls back to description matching. | Policy 1.1 says use the code if given. |
| A9 | New item with **no estimated price**. | Valued at ₹0 and still NEW_ITEM_SOURCING, with the reason saying no estimate was given. | 1.2 requires the requester's estimate, and there is no other basis. The route doesn't depend on value. In chat, the requester is asked for an estimate. |
| A10 | Lines created after "today" (up to 20 Nov). | Processed normally. | A batch is evaluated on its own timestamps, not the wall clock. |
| A11 | Missing need-by date (not in the sample, but possible in the blind batch). | Processed, but never clubbed. | The 2.2 need-by test cannot be checked, so I don't assume it passes. |
| A12 | Duplicate `pr_no` + `pr_line` in one file. | The first occurrence is processed; the duplicate is `NOT_PROCESSED`. | Prevents double counting. |

## B. Clubbing (section 2)

- "Within 14 calendar days" is measured on **dates**: `created_date − first_line_created_date ≤ 14`. The need-by test is `|need_by − first_need_by| ≤ 7` days, both bounds inclusive.
- Lines are taken in order of `created_at`, then `pr_no`, then `pr_line`. Each line is compared only with the **first line** of each requirement, not the latest. If more than one requirement fits, the earliest wins. This reproduces worked examples 4 and 14, where 4 hex-bolt lines form 2 requirements.
- New items are never clubbed (1.2). Different plants are never clubbed (worked example 6).
- If any line is Emergency, the whole requirement is an emergency (2.3).
- Clubbing windows come from the policy version in force on the requirement's first line. This way a requirement is judged under one consistent version.

## C. Valuation and contracts (sections 3 and 4)

- **Last-PO price (3.1b)**: the most recent PO for the item from **any supplier, any plant**, dated within the 365 days before the requirement date (inclusive). Ties are broken by PO number. The policy asks for "the price on the most recent PO", not one from an Active supplier, because this is a price reference, not a supplier choice.
- **"Bought from an Active supplier in the last 365 days" (5.3/5.4)** uses current supplier status from the supplier master, at any plant. Status history isn't in the data.
- **Contract applicability (4.1)** requires the supplier to be Active. Two cases follow:
  - **RC-2026-005** (Metalink, **Blocked**) is never applicable. It is also not used for valuation, because 3.1a says "applicable contract". Aluminium plate is therefore valued at standard price: 220 × ₹365 = ₹80,300, which matches worked example 10.
  - **RC-2025-020** (expired 30 Sep) is ignored for buying, but it still counts as "held a contract" in the preferred-supplier list (6.1b: "even if it has since expired").
- **Ceilings (4.2)**: consumption starts at the register's `consumed_value` (as at 31 Oct). Contracts that exist only as PDFs (**RC-2026-031**, **RC-2026-033**) start at zero (4.2). Ceiling is consumed in order of requirement creation time, and **only by requirements that actually end in CONTRACT_PO**. A capex requirement, for example, does not use up ceiling. The check is `consumed + qty × contract rate ≤ ceiling`, inclusive.
- **Several contracts (4.3)**: take the lowest rate that still fits the ceiling. Ties go to the lower contract id. If none fits, the requirement is valued at the lowest applicable rate and later rules decide (worked examples 3 and 4).
- **Each run starts from the register baseline.** A batch run is a what-if decision over that batch. Approving a PO in the workspace does not write consumption back to the register, because that would make re-running the same batch give different answers. In production this would be posted from the ERP's actual POs.
- **RC-2026-033** (laptops/monitors from **20 Nov**): PR-26-00044 (laptop, 20 Nov) clubs into a requirement whose first line is 16 Nov. Per 2.3 the requirement date is 16 Nov, so the contract does not apply yet and the requirement goes to RFQ. I followed the policy literally rather than splitting a requirement to reach a contract.
- Contracts deliver to both plants (stated in the contract PDFs), so plant is not a contract condition.
- Contract PDFs are parsed deterministically, and the buyer confirms before saving. The register and PDFs agree on all 10 contracts that appear in both.
- A policy parameter `contracts.min_headroom_pct` (default 0) is available. It lets an amendment reserve part of a ceiling without a code change.

## D. Decision rules (section 5)

- Rules are evaluated strictly in the stored order: 1.2 (new item) → 5.1 → 5.2 → … → 5.6.
- **5.5 vs 5.6 boundary**: "below ₹10,00,000" is RFQ, and exactly ₹10,00,000 is RFP. 5.3 uses ≤ ₹5,00,000 and 5.4 uses < ₹2,00,000, exactly as written.
- If a rule fits but cannot be carried out, it is **skipped** and the next rule decides. Examples: a "preferred supplier" rule with an empty preferred list, a "contract" rule with no usable contract, or a "fixed supplier" rule naming a non-Active supplier. This enforces 5.7 (never a PO to a Blocked or Inactive supplier). If no rule fits at all, the safe default is RFQ.
- **Fewer eligible suppliers than the suggestion count**: suggest all that exist. Example: encoder RFP gets 4 suggestions, because only 4 Active suppliers carry Electrical Spares. This matches worked example 11.
- Capex: always 5 suggestions, with the note "needs capex approval".

## E. Preferred supplier list (section 6), the ranking method

**Who is listed (6.1):** Active suppliers with a PO for the item in the 548 days up to the date, **or** a contract for the item that started on or before the date (even if expired).

**Score**, where weights and bands are stored in the policy and can be amended:

```
score = 0.35 × frequency   (POs for the item in 548 days ÷ highest count among listed suppliers)
      + 0.25 × recency     (1.0 if last PO ≤ 90 days ago, 0.5 if ≤ 365 days, else 0)
      + 0.20 × contract    (1 if it has held a contract for the item)
      + 0.10 × on-time %   (on_time_pct / 100)
      + 0.10 × quality     (quality_rating / 5)
```

Ties are broken by most recent PO, then supplier id.

**Why this design:**

- The buyer's question is "who should I order from directly, without quotes?". Proven, repeated, recent supply of this exact item is the strongest evidence. So frequency and recency together carry 60% of the weight.
- A contract shows the supplier passed a formal sourcing event for the item, so it is worth 20%.
- Ratings are supplier-wide, not item-specific, so they act as tie-breakers (20% total) rather than dominating. A supplier with great ratings but one stale PO should not outrank the incumbent.
- The recency bands (90 / 365 days) are the ones the worked examples use.
- Frequency is normalised to the best supplier on the list, so the score is comparable across items with very different volumes.
- The method reproduces all three example lists (`test_preferred_lists`). Each list in the UI shows the score broken down into its 5 parts, so a buyer can see exactly why a supplier ranks where it does.

**Reference price (6.3):** the supplier's latest PO price in 548 days, otherwise the rate in its most recent contract for the item.

**Top-up for sourcing (6.4):** after the preferred list, add Active suppliers whose categories include the item's category, ranked by `0.5 × on-time/100 + 0.5 × quality/5` ("best delivery and quality record first"). This reproduces the suggestion order in worked examples 3, 9, 10, 11 and 12. New items get no suggestions.

## F. PO consolidation (section 7)

- Requirements ending in a PO are combined when supplier, plant and route are all the same and the requirement was created ≤ 24 hours after the **first** requirement on that PO (inclusive). Requirements are taken in creation order and join the earliest open PO they qualify for.
- A contract PO and a spot PO to the same supplier are **not** combined, because the route differs, as 7.1 says.
- PO numbers are `PO-0001`, `PO-0002`, … in requirement creation order. Requirement ids are `REQ-0001`, … in creation order.

## G. Policy versions and amendments

- Versions have a number, an effective date, a status (draft / active / rejected), and a record of who created and approved them and when. The source text and a plain-language change list are stored with each version.
- **The engine picks the version effective on each requirement date.** Requirements dated before an amendment's effective date keep using the earlier version, and each output row shows which version decided it. Two active versions with the same effective date resolve to the later-approved one.
- **Run 1 / Run 2 note:** if a blind-test amendment is effective *after* the batch's requirement dates, the effective-dated mode will (correctly) not apply it. The Batch page and the API therefore offer "force version" (`?policy_version=5.1`) for when the instructions say to apply an amendment to the whole batch. For Run 1, forcing `5.0` guarantees v5.0 regardless of what was approved later.
- Amendments can arrive as a document (PDF/TXT/MD), a sentence typed in, or edits in the rule editor. If an AI key is configured, the AI drafts the new policy JSON. Otherwise a rule-based reader handles common patterns (limit changes on a clause, supplier status, windows, version, effective date) and lists anything it could not read. **Nothing activates without validation and a human approval.** Validation checks known facts, operators, routes and supplier modes, so a draft can't be approved with a typo that silently never matches.
- New kinds of rule need no code. A rule is any combination (`all`/`any`/`not`) of conditions on: item type, category, item code, plant, department, value, quantity, line count, emergency, new item, contract usable/applicable, days since last purchase, preferred-list size, requirement date, or need-by lead time. Its outcome is any route plus a supplier strategy (contract / Nth preferred / suggest N / fixed supplier / none). Windows, ranking weights, valuation order, consolidation keys and supplier status overrides are also data. Plant-only rules are tested in `test_new_plant_specific_rule_without_code_change`.

## H. Chat intake

- The assistant **never invents an item**. It takes an exact item code, a confident match (then shows it in the confirmation), a choice from up to 4 candidates, or an explicit "yes, it's a new item". It always asks for any missing quantity, plant, need-by date and urgency, plus an estimate for new items, and confirms before creating the PR.
- Dates are relative to today in IST. "Next Friday" means Friday of next week, and the resolved date is shown in the confirmation so the requester can correct it. Past dates and zero quantities are refused.
- Quantity is recorded in the item master's unit. If the requester used a different unit, they're told.
- Chat PRs are evaluated together with all other open chat PRs, so the no-splitting rule also applies to chat.

## I. Buyer workspace

- Approve creates the PO number (or an RFQ/RFP/CAPEX request number). Overrides require a written reason, keep the original decision alongside the new one, and **cannot place a PO with a Blocked or Inactive supplier**. Every action and every policy, contract or data change is in the audit log with who and when.
- Users identify themselves by name in the header (no passwords). Authentication was out of scope for this exercise.

## J. Numbers and output

- Values and prices are output with 2 decimals, unrounded beyond that. The worked examples show rounded rupees (₹1,37,531 = 15 × 9,168.71 = 1,37,530.65).
- `NOT_PROCESSED` rows have `decision = NOT_PROCESSED`, `route = NOT_PROCESSED`, and the reason in `reason`.

## K. Blind test notes (19 Nov 2026)

- **Run 1:** `hidden_pr_batch.csv` processed with policy v5.0 forced (`policy_version=5.0`). 190 lines → 153 requirements. PR-26-00674 (quantity 0) and PR-26-00675 (plant PUN2) are `NOT_PROCESSED`.
- **Amendment No. 1 (v5.1, effective 2026-12-01)** was entered through the app's policy editor, reviewed in "What will change", and approved. The AI reader could not be used: Gemini returned HTTP 503 and then timed out. How each change was recorded:
  - **A1:** rule 5.4 now reads (not Packaging AND value < ₹2,00,000) OR (Packaging AND value < ₹1,00,000), with purchase history. Packaging at or above ₹1 lakh falls through to sourcing. Emergency rule 5.3 is unchanged, because A1 names only rule 5.4.
  - **A2:** rule 5.5 is RFQ below ₹8,00,000. Rule 5.6 (RFP) catches ₹8,00,000 and above.
  - **A3:** new rules 5.2A / 5.2A-RFP sit immediately after 5.2. At CHK1, Electrical Spares worth ₹50,000 or more go to RFQ (below ₹8 lakh) or RFP (₹8 lakh and above). Contract POs under 5.2 are still checked first, as the amendment allows.
- **Run 2:** same batch, effective-dated. Requirements dated before 1 Dec stay on v5.0 (90 lines); from 1 Dec they use v5.1 (98 lines). 19 lines change: 15 Packaging and CHK1 Electrical spot POs move to RFQ, and 4 RFQs between ₹8 and 10 lakh move to RFP.
- **Preferred lists:** exported as of 2026-12-01 (top 3 per item).
