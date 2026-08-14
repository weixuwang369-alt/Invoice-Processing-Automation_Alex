import pytest

from src import action_log


@pytest.fixture(autouse=True)
def temp_action_log(monkeypatch):
    """action_log.py holds its list at module scope — point every test at a
    fresh list so one test's entries never leak into another's."""
    monkeypatch.setattr(action_log, "_log", [])


def make_entry(**overrides):
    defaults = dict(
        provider="xai",
        model="grok-3-mini",
        purpose="Approval draft",
        context="INV-1001",
        user_prompt="Vendor: Acme\nAmount: 100",
    )
    defaults.update(overrides)
    return action_log.record(**defaults)


def test_record_returns_an_entry_with_an_id_and_timestamp():
    entry = make_entry()
    assert entry["id"]
    assert entry["timestamp"]
    assert entry["provider"] == "xai"
    assert entry["purpose"] == "Approval draft"
    assert entry["context"] == "INV-1001"
    assert entry["error"] is None


def test_record_truncates_a_long_prompt():
    long_prompt = "x" * 900
    entry = make_entry(user_prompt=long_prompt)
    assert len(entry["prompt_preview"]) <= action_log._PREVIEW_LEN + 1
    assert entry["prompt_preview"].endswith("…")


def test_record_truncates_a_long_result_summary():
    long_result = "y" * 900
    entry = make_entry(result_summary=long_result)
    assert len(entry["result_summary"]) <= action_log._PREVIEW_LEN + 1
    assert entry["result_summary"].endswith("…")


def test_record_with_no_result_summary_leaves_it_none():
    entry = make_entry()
    assert entry["result_summary"] is None


def test_record_with_an_error_keeps_it_verbatim():
    entry = make_entry(error="rate limited")
    assert entry["error"] == "rate limited"


def test_list_all_returns_newest_first():
    first = make_entry(context="INV-1001")
    second = make_entry(context="INV-1002")
    ids = [e["id"] for e in action_log.list_all()]
    assert ids == [second["id"], first["id"]]


def test_ids_are_unique_and_increasing():
    a = make_entry()
    b = make_entry()
    assert b["id"] > a["id"]


def test_clear_all_empties_the_log_and_returns_the_count():
    make_entry()
    make_entry()

    count = action_log.clear_all()

    assert count == 2
    assert action_log.list_all() == []


def test_clear_all_does_not_reset_the_id_counter():
    first = make_entry()
    action_log.clear_all()
    next_one = make_entry()
    assert next_one["id"] > first["id"]
