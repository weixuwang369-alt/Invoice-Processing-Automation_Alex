import pytest

from src import edit_requests


@pytest.fixture
def temp_edit_store(tmp_path, monkeypatch):
    path = str(tmp_path / "edit_requests.json")
    monkeypatch.setattr(edit_requests, "STORE_PATH", path)
    return path


def test_create_returns_a_pending_record(temp_edit_store):
    record = edit_requests.create("run-1", {"total": {"old": 100, "new": 200}}, {"total": 200})
    assert record["status"] == "pending"
    assert record["run_id"] == "run-1"
    assert record["new_run_id"] is None
    assert record["id"]


def test_list_all_returns_newest_first(temp_edit_store):
    first = edit_requests.create("run-1", {}, {})
    second = edit_requests.create("run-2", {}, {})
    ids = [r["id"] for r in edit_requests.list_all()]
    assert ids[0] == second["id"]
    assert ids[1] == first["id"]


def test_get_returns_none_for_unknown_id(temp_edit_store):
    assert edit_requests.get("does-not-exist") is None


def test_set_decision_approves_and_links_new_run_id(temp_edit_store):
    record = edit_requests.create("run-1", {}, {})
    updated = edit_requests.set_decision(record["id"], "approved", new_run_id="run-2")
    assert updated["status"] == "approved"
    assert updated["new_run_id"] == "run-2"
    assert updated["decided_at"] is not None
    assert edit_requests.get(record["id"])["status"] == "approved"


def test_set_decision_rejects_without_a_new_run(temp_edit_store):
    record = edit_requests.create("run-1", {}, {})
    updated = edit_requests.set_decision(record["id"], "rejected")
    assert updated["status"] == "rejected"
    assert updated["new_run_id"] is None


def test_clear_all_removes_every_record_regardless_of_status(temp_edit_store):
    pending = edit_requests.create("run-1", {}, {})
    resolved = edit_requests.create("run-2", {}, {})
    edit_requests.set_decision(resolved["id"], "approved", new_run_id="run-3")

    count = edit_requests.clear_all()

    assert count == 2
    assert edit_requests.list_all() == []
    assert edit_requests.get(pending["id"]) is None
