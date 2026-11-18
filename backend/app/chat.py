from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import llm
from .engine.data import DataStore, parse_date
from .engine.matcher import match_item, score_item

PLANT_WORDS = {
    "chakan": "CHK1", "chk1": "CHK1", "chk": "CHK1",
    "bhosari": "PUN1", "pun1": "PUN1", "pune": "PUN1",
}
UNITS = r"(nos|no|pcs|pc|pieces?|units?|pairs?|prs|kgs?|kilos?|ltrs?|litres?|liters?|l|mtrs?|meters?|metres?|m|sets?|rolls?|ro?l|reams?|cyl|cylinders?|bkts?|buckets?|brls?|barrels?|drums?|jobs?|months?|mon|qtrs?|quarters?|boxes|box)"
UNIT_MAP = {"no": "NOS", "nos": "NOS", "pcs": "NOS", "pc": "NOS", "piece": "NOS", "pieces": "NOS", "unit": "NOS",
            "units": "NOS", "pair": "PAIR", "pairs": "PAIR", "prs": "PAIR", "kg": "KG", "kgs": "KG", "kilo": "KG",
            "kilos": "KG", "ltr": "LTR", "ltrs": "LTR", "litre": "LTR", "litres": "LTR", "liter": "LTR",
            "liters": "LTR", "l": "LTR", "mtr": "MTR", "mtrs": "MTR", "meter": "MTR", "meters": "MTR",
            "metre": "MTR", "metres": "MTR", "m": "MTR", "set": "SET", "sets": "SET", "roll": "ROL", "rolls": "ROL",
            "rol": "ROL", "rl": "ROL", "ream": "REAM", "reams": "REAM", "cyl": "CYL", "cylinder": "CYL",
            "cylinders": "CYL", "bkt": "BKT", "bkts": "BKT", "bucket": "BKT", "buckets": "BKT", "brl": "BRL",
            "brls": "BRL", "barrel": "BRL", "barrels": "BRL", "drum": "BRL", "drums": "BRL", "job": "JOB",
            "jobs": "JOB", "month": "MON", "months": "MON", "mon": "MON", "qtr": "QTR", "qtrs": "QTR",
            "quarter": "QTR", "quarters": "QTR"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
YES = {"yes", "y", "yeah", "yep", "ok", "okay", "confirm", "correct", "right", "sure", "haan", "ha", "submit", "create", "go ahead", "done"}
NO = {"no", "n", "nope", "nahi", "wrong", "incorrect", "cancel"}
FILLER = r"\b(i|we|need|needs|needed|want|require|required|requirement|please|pls|kindly|order|buy|get|some|of|for|the|a|an|at|to|in|by|plant|our|me|us|urgently|urgent|asap|emergency|immediately|raise|pr|request|purchase|can|you|hi|hello|hey|deliver|delivered|delivery|on|before)\b"


def parse_when(text: str, today: date) -> tuple[date | None, str]:
    t = text.lower()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        return parse_date(m.group(1)), m.group(0)
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b", t)
    if m and int(m.group(2)) <= 12 and int(m.group(1)) <= 31:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            d = date(y, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and d < today:
                d = date(y + 1, d.month, d.day)
            return d, m.group(0)
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{4}))?", t) or \
        re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", t)
    if m:
        g = m.groups()
        if g[0].isdigit():
            day, mon, yr = int(g[0]), MONTHS[g[1]], g[2]
        else:
            mon, day, yr = MONTHS[g[0]], int(g[1]), g[2]
        y = int(yr) if yr else today.year
        try:
            d = date(y, mon, day)
            if not yr and d < today:
                d = date(y + 1, mon, day)
            return d, m.group(0)
        except ValueError:
            pass
    if re.search(r"\btoday\b", t):
        return today, "today"
    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2), "day after tomorrow"
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1), "tomorrow"
    m = re.search(r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", t)
    if m:
        n = int(m.group(1)) * (7 if m.group(2).startswith("week") else 1)
        return today + timedelta(days=n), m.group(0)
    m = re.search(r"\b(next|this|coming)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        wd = WEEKDAYS.index(m.group(2))
        delta = (wd - today.weekday()) % 7
        if delta == 0:
            delta = 7
        if m.group(1) == "next" and delta < 7 and (today + timedelta(days=delta)).isocalendar()[1] == today.isocalendar()[1]:
            delta += 7
        return today + timedelta(days=delta), m.group(0)
    if re.search(r"\bnext week\b", t):
        return today + timedelta(days=(7 - today.weekday())), "next week"
    if re.search(r"\bend of (the )?month\b", t):
        nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        return nxt - timedelta(days=1), "end of month"
    return None, ""


def parse_qty(text: str) -> tuple[float | None, str | None, str]:
    t = text.lower()
    m = re.search(r"(?<![\w.\-/x])(\d+(?:\.\d+)?)\s*" + UNITS + r"\b(?!\s*(?:mm|inch|x\d))", t)
    if m:
        return float(m.group(1)), UNIT_MAP.get(m.group(2).rstrip("."), None), m.group(0)
    m = re.search(r"\b(?:need|want|require|order|buy|get|send|qty|quantity)\s*(?:of\s*)?[:=]?\s*(\d+(?:\.\d+)?)\b(?!\s*(?:mm|inch|\"|x\d|hp|ton|kw|a\b|lph|micron|gsm))", t)
    if m:
        return float(m.group(1)), None, m.group(0)
    m = re.search(r"^\s*(\d+(?:\.\d+)?)\s+(?![\d])(?!mm|inch|x)", t)
    if m:
        return float(m.group(1)), None, m.group(0)
    m = re.search(r"\bx\s*(\d+)\s*$", t)
    if m:
        return float(m.group(1)), None, m.group(0)
    return None, None, ""


def parse_plant(text: str, plants: dict) -> tuple[str | None, str]:
    t = text.lower()
    for w, code in PLANT_WORDS.items():
        if re.search(rf"\b{w}\b", t) and code in plants:
            return code, w
    for code in plants:
        if re.search(rf"\b{code.lower()}\b", t):
            return code, code.lower()
    return None, ""


def parse_urgency(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\b(emergency|breakdown|line down|machine down)\b", t):
        return "Emergency"
    if re.search(r"\b(urgent|urgently|asap|immediately)\b", t):
        return "Urgent?"
    if re.search(r"\b(normal|not urgent|regular|routine|no rush)\b", t):
        return "Normal"
    return None


def item_text(text: str, removed: list[str]) -> str:
    t = text.lower()
    for r in removed:
        if r:
            t = t.replace(r.lower(), " ")
    for w in list(PLANT_WORDS) + WEEKDAYS + ["next", "this", "coming", "week", "tomorrow", "today", "normal", "routine"]:
        t = re.sub(rf"\b{w}\b", " ", t)
    t = re.sub(FILLER, " ", t)
    t = re.sub(r"[,!?]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .-")


def llm_extract(text: str) -> dict:
    sys = ("Extract a purchase request from the message. Return ONLY JSON with keys item_text (the item "
           "description words only, exactly as written, no quantity/plant/date), quantity (number or null), "
           "uom (or null), plant_words (or null), need_by_text (or null), urgency_words (or null). "
           "Never invent values that are not in the message.")
    try:
        return llm.extract_json(llm.complete(sys, text, max_tokens=300, timeout=20))
    except Exception:
        return {}


class ChatBot:
    def __init__(self, ds: DataStore, today: date):
        self.ds = ds
        self.today = today

    def item_label(self, code: str) -> str:
        it = self.ds.items[code]
        return f"{it.description} ({code}, {it.uom})"

    def reply(self, state: dict, msg: str) -> tuple[dict, dict]:
        s = dict(state or {})
        text = (msg or "").strip()
        low = text.lower().strip(" .!")
        if low in ("reset", "start over", "restart", "new request", "cancel request"):
            return {}, {"text": "Okay, starting fresh. What do you need? For example: \"need 200 nitrile gloves for Chakan by next Friday\".", "options": []}

        awaiting = s.get("awaiting")
        if awaiting == "item_choice":
            pick = None
            if re.fullmatch(r"itm-\d+", low):
                pick = low.upper() if low.upper() in self.ds.items else None
            elif low.isdigit() and 1 <= int(low) <= len(s.get("candidates", [])):
                pick = s["candidates"][int(low) - 1]
            elif "new" in low or "none" in low or "not listed" in low:
                s.update(item_code=None, is_new=True, new_desc=s.get("item_query"), awaiting=None)
            if pick:
                s.update(item_code=pick, is_new=False, awaiting=None)
            elif not s.get("is_new"):
                s["awaiting"] = None
        elif awaiting == "confirm_new":
            if low in YES or low.startswith("yes"):
                s.update(item_code=None, is_new=True, new_desc=s.get("item_query"), awaiting=None)
                return self._next(s, prefix="Noted as a NEW item (not in the item master). It will go to sourcing; the buyer will find suppliers.")
            if low in NO:
                s.update(awaiting=None, item_query=None)
                return s, {"text": "No problem. Please describe the item differently, or give the item code if you know it (e.g. ITM-1048).", "options": []}
        elif awaiting == "urgency_confirm":
            if low in YES or "emergency" in low:
                s.update(urgency="Emergency", awaiting=None)
            else:
                s.update(urgency="Normal", awaiting=None)
            return self._next(s)
        elif awaiting == "urgency":
            if "emerg" in low or low in YES:
                s.update(urgency="Emergency", awaiting=None)
                return self._next(s)
            if "normal" in low or low in NO or "routine" in low:
                s.update(urgency="Normal", awaiting=None)
                return self._next(s)
        elif awaiting == "estimate":
            v = re.search(r"[\d,]+(?:\.\d+)?", text.replace("₹", ""))
            if v:
                s.update(est_price=float(v.group(0).replace(",", "")), awaiting=None)
                return self._next(s)
            if "don" in low or "not sure" in low or "skip" in low:
                s.update(est_price=None, est_skipped=True, awaiting=None)
                return self._next(s)
        elif awaiting == "confirm":
            if low in YES or low.startswith("yes") or low.startswith("confirm"):
                s["awaiting"] = None
                s["confirmed"] = True
                return s, {"text": "", "ready": True, "options": []}
            if low in NO:
                s["awaiting"] = "edit"
                return s, {"text": "What should I change? You can type it, e.g. \"quantity 250\", \"plant Bhosari\", \"by 30 Nov\", or \"emergency\".", "options": []}

        removed = []
        qty, uom, qraw = parse_qty(text)
        if awaiting in ("quantity",) and qty is None:
            m = re.search(r"(\d+(?:\.\d+)?)", text)
            if m:
                qty, qraw = float(m.group(1)), m.group(0)
        if qty is not None and awaiting in (None, "quantity", "edit", "item"):
            if qty <= 0:
                return s, {"text": "Quantity must be more than zero. How many do you need?", "options": []}
            s["qty"] = qty
            if uom:
                s["uom_said"] = uom
            removed.append(qraw)
        plant, praw = parse_plant(text, self.ds.plants)
        if plant:
            s["plant"] = plant
            removed.append(praw)
        when, wraw = parse_when(text, self.today)
        if when:
            if when < self.today:
                return s, {"text": f"{when.isoformat()} is in the past. When do you need it by?", "options": []}
            s["need_by"] = when.isoformat()
            removed.append(wraw)
        urg = parse_urgency(text)
        if urg == "Urgent?":
            s["urgency_hint"] = True
        elif urg:
            s["urgency"] = urg

        code = re.search(r"\bitm-\d{4}\b", low)
        rest = item_text(text, removed)
        need_item = not s.get("item_code") and not s.get("is_new")
        if code and code.group(0).upper() in self.ds.items:
            s.update(item_code=code.group(0).upper(), is_new=False, awaiting=None)
        elif code:
            return s, {"text": f"{code.group(0).upper()} is not in the item master. Please check the code or describe the item.", "options": []}
        elif rest and len(rest) >= 3 and (need_item or awaiting in ("edit", "item")) and not re.fullmatch(r"[\d\s.]+", rest):
            if llm.available() and len(rest.split()) > 6:
                ex = llm_extract(text)
                if ex.get("item_text"):
                    rest = ex["item_text"]
            res = self._match(s, rest)
            if res:
                return res
        return self._next(s)

    def _match(self, s: dict, query: str):
        m = match_item(query, self.ds.items)
        s["item_query"] = query
        if m.item_code:
            s.update(item_code=m.item_code, is_new=False, awaiting=None, match_note=f"matched '{query}'")
            return None
        cands = [c for c, sc in m.candidates if sc >= 0.35][:4]
        if not cands:
            words = [w for w in re.findall(r"[a-z]{4,}", query.lower())]
            cands = [c for c, it in self.ds.items.items() if any(w in it.description.lower() for w in words)][:4]
        if cands:
            s.update(awaiting="item_choice", candidates=cands)
            opts = [{"label": self.item_label(c), "value": c} for c in cands] + \
                   [{"label": "None of these — it's a new item", "value": "new item"}]
            return s, {"text": f"I couldn't match \"{query}\" exactly. Which of these do you mean?", "options": opts}
        s.update(awaiting="confirm_new")
        return s, {"text": f"I couldn't find \"{query}\" in the item master. Is this a new item that Corvo has not bought before? "
                           f"(New items always go to sourcing.)",
                   "options": [{"label": "Yes, it's a new item", "value": "yes"},
                               {"label": "No, let me describe it again", "value": "no"}]}

    def _next(self, s: dict, prefix: str = "") -> tuple[dict, dict]:
        pre = (prefix + " ") if prefix else ""
        if s.get("item_code"):
            it = self.ds.items[s["item_code"]]
            if s.get("uom_said") and s["uom_said"] != it.uom and not s.get("uom_warned"):
                s["uom_warned"] = True
                pre += f"Note: {it.description} is bought in {it.uom}, so I'll record the quantity in {it.uom}. "
        if not s.get("item_code") and not s.get("is_new"):
            s["awaiting"] = "item"
            return s, {"text": pre + "What item do you need? A description or item code is fine.", "options": []}
        if not s.get("qty"):
            s["awaiting"] = "quantity"
            unit = self.ds.items[s["item_code"]].uom if s.get("item_code") else "units"
            what = self.item_label(s["item_code"]) if s.get("item_code") else s.get("new_desc")
            return s, {"text": pre + f"Got it: {what}. How many {unit} do you need?", "options": []}
        if not s.get("plant"):
            s["awaiting"] = "plant"
            return s, {"text": pre + "Which plant is this for?", "options": [
                {"label": f"{c} — {n}", "value": c} for c, n in self.ds.plants.items()]}
        if not s.get("need_by"):
            s["awaiting"] = "need_by"
            return s, {"text": pre + "By when do you need it? (e.g. \"next Friday\", \"30 Nov\", \"2026-12-05\")", "options": []}
        if not s.get("urgency"):
            if s.get("urgency_hint"):
                s["awaiting"] = "urgency_confirm"
                return s, {"text": pre + "You mentioned it's urgent. Should I mark it as an Emergency? "
                                         "(Emergency is for breakdowns/production stoppage; it lets Procurement buy without quotes up to the emergency limit.)",
                           "options": [{"label": "Yes, Emergency", "value": "yes"}, {"label": "No, Normal", "value": "no"}]}
            s["awaiting"] = "urgency"
            return s, {"text": pre + "Is this Normal or an Emergency (breakdown / production stoppage)?",
                       "options": [{"label": "Normal", "value": "normal"}, {"label": "Emergency", "value": "emergency"}]}
        if s.get("is_new") and s.get("est_price") is None and not s.get("est_skipped"):
            s["awaiting"] = "estimate"
            return s, {"text": pre + "Since this is a new item, what's your estimated unit price in ₹? (Used to value the sourcing request; say \"not sure\" to skip.)", "options": []}
        s["awaiting"] = "confirm"
        return s, {"text": pre + "Please confirm this purchase request:", "summary": self.summary(s),
                   "options": [{"label": "Confirm & raise PR", "value": "yes"}, {"label": "Change something", "value": "no"}]}

    def summary(self, s: dict) -> dict:
        it = self.ds.items.get(s.get("item_code") or "")
        return {"item_code": s.get("item_code") or "NEW",
                "description": it.description if it else s.get("new_desc"),
                "quantity": s.get("qty"), "uom": it.uom if it else (s.get("uom_said") or "NOS"),
                "plant": s.get("plant"), "plant_name": self.ds.plants.get(s.get("plant"), ""),
                "need_by_date": s.get("need_by"), "urgency": s.get("urgency"),
                "estimated_unit_price": s.get("est_price")}
