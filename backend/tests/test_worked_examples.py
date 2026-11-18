from datetime import date

import pytest

from app.engine.engine import preferred_export, run_batch
from app.engine.suppliers import preferred_list
from tests.kit import kit_available, load_kit, policy_v5, sample_batch

pytestmark = pytest.mark.skipif(not kit_available(), reason="task kit not available (set KIT_DIR)")


# PR-174/175 shift the example PO numbers by one, see DECISIONS.md A4
APPENDED = {"PR-26-00174", "PR-26-00175"}


@pytest.fixture(scope="module")
def result():
    return run_batch(load_kit(), [policy_v5()], [x for x in sample_batch() if x["pr_no"] not in APPENDED])


@pytest.fixture(scope="module")
def full():
    return run_batch(load_kit(), [policy_v5()], sample_batch())


def row(result, pr, line=1):
    return next(r for r in result["rows"] if r["pr_no"] == pr and str(r["pr_line"]) == str(line))


EXAMPLES = [
    ("PR-26-00019", "CONTRACT_PO", "S001", 9169, 137531, "PO-0011", None),
    ("PR-26-00045", "CONTRACT_PO", "S021", 2439, 26829, "PO-0072", None),
    ("PR-26-00004", "RFQ", "", None, 220256, "", "S014;S013;S016"),
    ("PR-26-00026", "RFQ", "", None, 220256, "", "S014;S013;S016"),
    ("PR-26-00034", "CONTRACT_PO", "S007", 7, 32547, "PO-0003", None),
    ("PR-26-00032", "CONTRACT_PO", "S007", 7, 32547, "PO-0003", None),
    ("PR-26-00146", "SPOT_PO", "S024", 166, 126472, "PO-0001", None),
    ("PR-26-00144", "SPOT_PO", "S024", 166, 126472, "PO-0001", None),
    ("PR-26-00145", "SPOT_PO", "S024", 166, 126472, "PO-0001", None),
    ("PR-26-00106", "SPOT_PO", "S027", 22, 156170, "PO-0002", None),
    ("PR-26-00088", "SPOT_PO", "S023", 392, 97955, "PO-0023", None),
    ("PR-26-00059", "EMERGENCY_PO", "S014", 106, 253184, "PO-0024", None),
    ("PR-26-00089", "RFQ", "", None, 336562, "", "S043;S044;S042"),
    ("PR-26-00050", "RFQ", "", None, 80300, "", "S003;S002;S005"),
    ("PR-26-00100", "RFP", "", None, 2070780, "", "S020;S017;S019;S018"),
    ("PR-26-00047", "CAPEX_SOURCING", "", None, 6036704, "", "S045;S047;S020;S048;S046"),
    ("PR-26-00170", "NEW_ITEM_SOURCING", "", None, 141100, "", ""),
    ("PR-26-00031", "CONTRACT_PO", "S007", 7, 23227, "PO-0003", None),
    ("PR-26-00035", "CONTRACT_PO", "S007", 7, 23227, "PO-0003", None),
]


@pytest.mark.parametrize("pr,route,sup,price,value,po,sugg", EXAMPLES)
def test_worked_example(result, pr, route, sup, price, value, po, sugg):
    r = row(result, pr)
    assert r["route"] == route, r["reason"]
    assert r["supplier_id"] == sup
    if price is not None:
        assert round(float(r["unit_price"])) == price
    assert round(float(r["group_value_inr"])) == value
    assert r["po_id"] == po
    if sugg is not None:
        assert r["suggested_supplier_ids"] == sugg


def test_vci_bags_not_clubbed_across_plants(result):
    assert row(result, "PR-26-00106")["group_id"] != row(result, "PR-26-00103")["group_id"]


def test_hex_bolt_requirements_clubbed(result):
    assert row(result, "PR-26-00034")["group_id"] == row(result, "PR-26-00032")["group_id"]
    assert row(result, "PR-26-00031")["group_id"] == row(result, "PR-26-00035")["group_id"]
    assert row(result, "PR-26-00031")["group_id"] != row(result, "PR-26-00034")["group_id"]


@pytest.mark.parametrize("item,expected", [
    ("ITM-1048", ["S029", "S030"]),
    ("ITM-1036", ["S023", "S022", "S024"]),
    ("ITM-1016", ["S009", "S010"]),
])
def test_preferred_lists(item, expected):
    got = [p.supplier_id for p in preferred_list(load_kit(), policy_v5(), item, date(2026, 11, 1))]
    assert got == expected


def test_no_blocked_supplier_anywhere(result):
    ds = load_kit()
    bad = {s for s, v in ds.suppliers.items() if v.status != "Active"}
    for r in result["rows"]:
        assert r["supplier_id"] not in bad
        assert not (set(r["suggested_supplier_ids"].split(";")) & bad)


def test_invalid_lines_not_processed(result):
    assert row(result, "PR-26-00172")["route"] == "NOT_PROCESSED"   # quantity 0
    assert row(result, "PR-26-00173")["route"] == "NOT_PROCESSED"   # plant PUN2


def test_preferred_export_shape():
    rows = preferred_export(load_kit(), [policy_v5()], date(2026, 11, 1))
    assert set(rows[0]) == {"item_code", "rank", "supplier_id", "reference_price"}
    assert max(r["rank"] for r in rows) <= 3


def test_split_servo_drive_prs_are_clubbed(full):
    a, b = row(full, "PR-26-00174"), row(full, "PR-26-00175")
    assert a["group_id"] == b["group_id"] and a["route"] == "SPOT_PO"
    assert round(float(a["group_value_inr"])) == 131676


def test_full_batch_decisions_unchanged_except_numbering(result, full):
    f = {(r["pr_no"], r["pr_line"]): r for r in full["rows"]}
    for r in result["rows"]:
        g = f[(r["pr_no"], r["pr_line"])]
        assert (r["route"], r["supplier_id"], r["group_value_inr"]) == (g["route"], g["supplier_id"], g["group_value_inr"])
