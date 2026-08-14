"""In-memory log of every LLM call the application makes -- one entry per
call to LLMClient.complete_structured(), across ingestion extraction and
both approval passes (draft, critique). Mirrors notifications.py: a
single-user local tool doesn't need durable storage for this, so the log
doesn't survive a server restart (see SOLUTION.md, Known limitations).

Logging lives at the call sites (src/ingestion/llm_extractor.py,
src/approval/agent.py), not inside LLMClient itself -- LLMClient's job is
talking to a provider, not knowing whether a given call is "ingestion" or
"an approval draft"; only the caller knows that. Powers the Action Log tab.
"""

from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_log: list[dict] = []
_id_counter = itertools.count(1)

_PREVIEW_LEN = 500


def _truncate(text: str, limit: int = _PREVIEW_LEN) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def record(
    *,
    provider: str,
    model: str,
    purpose: str,
    context: str | None,
    user_prompt: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> dict:
    entry = {
        "id": next(_id_counter),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "purpose": purpose,
        "context": context,
        "prompt_preview": _truncate(user_prompt),
        "result_summary": _truncate(result_summary) if result_summary else None,
        "error": error,
    }
    with _lock:
        _log.append(entry)
    return entry


def list_all() -> list[dict]:
    with _lock:
        return sorted(_log, key=lambda e: e["id"], reverse=True)


def clear_all() -> int:
    """Used by the Settings full reset. Returns how many entries were cleared."""
    with _lock:
        count = len(_log)
        _log.clear()
    return count
