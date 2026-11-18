from datetime import date

import pytest

from app.chat import ChatBot, parse_qty, parse_when
from app.engine.matcher import match_item
from tests.kit import kit_available, load_kit

pytestmark = pytest.mark.skipif(not kit_available(), reason="task kit not available (set KIT_DIR)")
TODAY = date(2026, 11, 18)  # a Wednesday


@pytest.mark.parametrize("text,code", [
    ("brg 6308 ZZ for maintenance", "ITM-1022"), ("hyd hose 1/2 inch 2 wire", "ITM-1035"),
    ("welding rod e6013 3.15mm", "ITM-1015"), ('monitor 24"', "ITM-1057"), ("pneu cylinder 50x100", "ITM-1036"),
    ("taper roller brg 32210", "ITM-1023"), ("Drinking water purifier 50 LPH", None), ("Ultrasonic thickness gauge", None),
])
def test_matching(text, code):
    assert match_item(text, load_kit().items).item_code == code


def test_parsers():
    assert parse_when("by next friday", TODAY)[0] == date(2026, 11, 27)
    assert parse_when("by 30 nov", TODAY)[0] == date(2026, 11, 30)
    assert parse_when("tomorrow", TODAY)[0] == date(2026, 11, 19)
    assert parse_qty("need 200 nitrile gloves")[0] == 200
    assert parse_qty("brg 6308 zz 40 nos")[0] == 40


def test_conversation_asks_for_missing_fields():
    bot = ChatBot(load_kit(), TODAY)
    s, r = bot.reply({}, "need nitrile gloves")
    assert "how many" in r["text"].lower()
    s, r = bot.reply(s, "200")
    assert "plant" in r["text"].lower()
    s, r = bot.reply(s, "Chakan")
    assert "by when" in r["text"].lower()
    s, r = bot.reply(s, "next friday")
    s, r = bot.reply(s, "normal")
    assert r["summary"]["item_code"] == "ITM-1048" and r["summary"]["plant"] == "CHK1"
    s, r = bot.reply(s, "yes")
    assert r.get("ready")


def test_vague_item_offers_choices():
    bot = ChatBot(load_kit(), TODAY)
    s, r = bot.reply({}, "need gloves 100 pairs for bhosari")
    labels = " ".join(o["label"] for o in r["options"])
    assert "Cotton hand gloves" in labels and "Nitrile coated gloves" in labels
