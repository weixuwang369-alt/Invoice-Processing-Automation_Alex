import pytest

from src import notifications


@pytest.fixture(autouse=True)
def temp_notifications(monkeypatch):
    """notifications.py holds its list at module scope — point every test at
    a fresh list so one test's notifications never leak into another's."""
    monkeypatch.setattr(notifications, "_notifications", [])


def test_add_returns_an_unread_record_with_an_id():
    record = notifications.add("invoice_completed", "Done.", run_id="run-1")
    assert record["read"] is False
    assert record["type"] == "invoice_completed"
    assert record["message"] == "Done."
    assert record["run_id"] == "run-1"
    assert record["id"]


def test_list_all_returns_newest_first():
    first = notifications.add("invoice_completed", "First.")
    second = notifications.add("invoice_completed", "Second.")
    ids = [n["id"] for n in notifications.list_all()]
    assert ids == [second["id"], first["id"]]


def test_mark_all_read_clears_every_unread_flag():
    notifications.add("invoice_completed", "First.")
    notifications.add("invoice_completed", "Second.")
    notifications.mark_all_read()
    assert all(n["read"] for n in notifications.list_all())


def test_ids_are_unique_and_increasing():
    a = notifications.add("invoice_completed", "A")
    b = notifications.add("invoice_completed", "B")
    assert b["id"] > a["id"]


def test_clear_all_empties_the_feed_and_returns_the_count():
    notifications.add("invoice_completed", "A")
    notifications.add("invoice_completed", "B")

    count = notifications.clear_all()

    assert count == 2
    assert notifications.list_all() == []


def test_clear_all_does_not_reset_the_id_counter():
    first = notifications.add("invoice_completed", "A")
    notifications.clear_all()
    next_one = notifications.add("invoice_completed", "B")
    assert next_one["id"] > first["id"]
