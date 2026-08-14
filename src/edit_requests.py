"""JSON-file store for manual field edits proposed against a previously
processed invoice, each gated behind a mock approval step (see SOLUTION.md,
"Manual edits"). Mirrors the lightweight JSON-store pattern used by
key_store.py / invoice_folder.py — a gitignored file next to the app, not a
durability guarantee, which is the right tradeoff for this single-user
local tool.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

STORE_PATH = os.getenv("EDIT_REQUESTS_STORE_PATH", "edit_requests.json")


def _load() -> list[dict]:
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write(records: list[dict]) -> None:
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)


def create(run_id: str, changes: dict, edited_invoice: dict) -> dict:
    records = _load()
    record = {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "changes": changes,
        "edited_invoice": edited_invoice,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "decided_at": None,
        "new_run_id": None,
    }
    records.append(record)
    _write(records)
    return record


def list_all() -> list[dict]:
    return sorted(_load(), key=lambda r: r["requested_at"], reverse=True)


def get(edit_id: str) -> dict | None:
    return next((r for r in _load() if r["id"] == edit_id), None)


def set_decision(edit_id: str, status: str, new_run_id: str | None = None) -> dict | None:
    records = _load()
    for r in records:
        if r["id"] == edit_id:
            r["status"] = status
            r["decided_at"] = datetime.now(timezone.utc).isoformat()
            r["new_run_id"] = new_run_id
            _write(records)
            return r
    return None


def clear_all() -> int:
    """Used by the Settings full reset — deletes every edit request,
    resolved or not. Returns how many were cleared, for the reset
    confirmation feedback."""
    count = len(_load())
    _write([])
    return count
