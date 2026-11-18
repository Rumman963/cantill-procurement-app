from __future__ import annotations

import io
import re

from .data import Contract, parse_date


def pdf_text(data: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_contract_text(text: str) -> dict:
    t = re.sub(r"[ \t]+", " ", text)
    flat = re.sub(r"\s+", " ", text)
    out = {"contract_id": None, "supplier_id": None, "supplier_name": None, "start_date": None,
           "end_date": None, "value_cap": None, "consumed_value": 0.0, "lines": [], "warnings": []}
    m = re.search(r"Contract No\.?\s*([A-Z]{2}-\d{4}-\d{3})", flat)
    if m:
        out["contract_id"] = m.group(1)
    m = re.search(r'and (.+?),\s*[A-Za-z ]*\(Supplier ID\s*(S\d+)\)', flat)
    if m:
        out["supplier_name"] = m.group(1).strip().replace("\n", " ")
        out["supplier_id"] = m.group(2)
    else:
        m = re.search(r"Supplier ID\s*(S\d+)", flat)
        if m:
            out["supplier_id"] = m.group(1)
    m = re.search(r"valid from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", flat)
    if m:
        out["start_date"], out["end_date"] = m.group(1), m.group(2)
    m = re.search(r"shall not exceed INR\s*([\d,]+(?:\.\d+)?)", flat)
    if m:
        out["value_cap"] = _num(m.group(1))
    for m in re.finditer(r"^\s*\d+\s+(ITM-\d+)\s+(.+?)\s+([A-Z]{2,4})\s+([\d,]+\.\d+)\s*$", t, re.M):
        out["lines"].append({"item_code": m.group(1), "description": m.group(2).strip(),
                             "uom": m.group(3), "rate": _num(m.group(4))})
    for k in ("contract_id", "supplier_id", "start_date", "end_date", "value_cap"):
        if not out[k]:
            out["warnings"].append(f"Could not read {k}; please fill it in")
    if not out["lines"]:
        out["warnings"].append("No schedule lines found; please add them")
    return out


def parse_contract_pdf(data: bytes) -> dict:
    return parse_contract_text(pdf_text(data))


def to_contract(d: dict, source: str = "pdf") -> Contract:
    return Contract(d["contract_id"], d["supplier_id"], d.get("supplier_name") or "",
                    parse_date(d["start_date"]), parse_date(d["end_date"]), float(d["value_cap"]),
                    float(d.get("consumed_value") or 0), source,
                    {l["item_code"]: float(l["rate"]) for l in d["lines"]})
