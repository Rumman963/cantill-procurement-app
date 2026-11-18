from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .data import Item

ABBREVIATIONS = {
    "brg": "bearing", "brgs": "bearing", "bearings": "bearing",
    "hyd": "hydraulic", "pneu": "pneumatic", "pneum": "pneumatic",
    "rod": "electrode", "rods": "electrode", "elec": "electrode",
    "ss": "ss", "ms": "ms", "qty": "",
    "gloves": "gloves", "glove": "gloves",
    "cyl": "cylinder", "cylr": "cylinder",
    "mtr": "", "nos": "", "pcs": "",
    "svc": "service", "services": "service",
}

NOISE_PATTERNS = [
    r"\bas per (the )?sample\b", r"\bfor maintenance\b", r"\bfor production\b",
    r"\(repeat\)", r"\brepeat( order)?\b", r"\burgent(ly)?\b", r"\basap\b",
    r"\bplease\b", r"\bpls\b", r"\bkindly\b", r"\brequired\b", r"\bneed(ed)?\b",
]

STOPWORDS = {"for", "the", "a", "an", "of", "with", "and", "to", "per", "x", "-", "type", "new", "some"}


def normalize(text: str) -> str:
    t = (text or "").lower()
    t = t.replace('"', " inch ").replace("”", " inch ").replace("''", " inch ")
    for p in NOISE_PATTERNS:
        t = re.sub(p, " ", t)
    t = re.sub(r"[(),;:/]", lambda m: " / " if m.group(0) == "/" else " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    return t


def tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+(?:[./][a-z0-9]+)*", normalize(text).replace("-", " "))
    out = []
    for tok in raw:
        tok = ABBREVIATIONS.get(tok, tok)
        if not tok or tok in STOPWORDS:
            continue
        out.append(tok)
    return out


def has_digit(tok: str) -> bool:
    return any(ch.isdigit() for ch in tok)


def _tok_match(q: str, cands: list[str]) -> bool:
    if q in cands:
        return True
    for c in cands:
        if has_digit(q) or has_digit(c):
            if q.replace("-", "") == c.replace("-", ""):
                return True
            continue
        if len(q) >= 4 and len(c) >= 4 and SequenceMatcher(None, q, c).ratio() >= 0.85:
            return True
        if len(q) >= 3 and (c.startswith(q) or q.startswith(c)) and min(len(q), len(c)) >= 3:
            return True
    return False


@dataclass
class MatchResult:
    item_code: str | None
    score: float
    method: str  # code | exact | fuzzy | none
    candidates: list[tuple[str, float]]

    @property
    def is_new(self) -> bool:
        return self.item_code is None


def score_item(query: str, item: Item) -> float:
    q = tokens(query)
    it = tokens(item.description)
    if not q or not it:
        return 0.0
    # size/model tokens (with digits) must all be present
    q_digits = [t for t in q if has_digit(t)]
    for t in q_digits:
        if not _tok_match(t, it):
            return 0.0
    w = lambda t: 2.0 if has_digit(t) else 1.0
    q_hit = sum(w(t) for t in q if _tok_match(t, it))
    q_tot = sum(w(t) for t in q)
    i_hit = sum(w(t) for t in it if _tok_match(t, q))
    i_tot = sum(w(t) for t in it)
    precision = q_hit / q_tot
    recall = i_hit / i_tot
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def match_item(description: str, items: dict[str, Item], item_code: str | None = None,
               threshold: float = 0.72) -> MatchResult:
    code = (item_code or "").strip().upper()
    if code and code in items:
        return MatchResult(code, 1.0, "code", [(code, 1.0)])
    desc = description or ""
    norm = normalize(desc)
    for it in items.values():
        if normalize(it.description) == norm:
            return MatchResult(it.item_code, 1.0, "exact", [(it.item_code, 1.0)])
    scored = sorted(((it.item_code, score_item(desc, it)) for it in items.values()),
                    key=lambda x: (-x[1], x[0]))
    top = [s for s in scored if s[1] > 0][:5]
    if top and top[0][1] >= threshold:
        if len(top) > 1 and top[1][1] >= top[0][1] - 0.02:
            return MatchResult(None, top[0][1], "ambiguous", top)
        return MatchResult(top[0][0], top[0][1], "fuzzy", top)
    return MatchResult(None, top[0][1] if top else 0.0, "none", top)
