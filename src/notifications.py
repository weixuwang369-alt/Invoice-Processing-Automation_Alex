"""In-memory notification feed for the web UI's bell icon. Mirrors the
_jobs dict in server.py: a single-user local tool doesn't need durable
storage for this, so notifications don't survive a server restart (see
SOLUTION.md, Known limitations). Any code path that wants to notify the
user (invoice completion, batch completion, an edit needing approval)
calls add() here; the frontend polls list_all() and plays a sound/toast
for anything newer than the last id it has seen.
"""

from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_notifications: list[dict] = []
_id_counter = itertools.count(1)


def add(type: str, message: str, run_id: str | None = None, edit_request_id: str | None = None) -> dict:
    record = {
        "id": next(_id_counter),
        "type": type,
        "message": message,
        "run_id": run_id,
        "edit_request_id": edit_request_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "read": False,
    }
    with _lock:
        _notifications.append(record)
    return record


def list_all() -> list[dict]:
    with _lock:
        return sorted(_notifications, key=lambda n: n["id"], reverse=True)


def mark_all_read() -> None:
    with _lock:
        for n in _notifications:
            n["read"] = True


def clear_all() -> int:
    """Used by the Settings full reset. Returns how many notifications
    were cleared, for the reset confirmation feedback."""
    with _lock:
        count = len(_notifications)
        _notifications.clear()
    return count
