"""Local, gitignored store for the active LLM provider + its API key, so the
key can be entered once through the front-end and never re-displayed. This
is intentionally simple (a chmod-600 JSON file next to the app) rather than
an OS keychain — appropriate for a local single-user demo, not a multi-user
deployment. See SOLUTION.md for the tradeoff.
"""

from __future__ import annotations

import json
import os
import stat

STORE_PATH = os.getenv("LOCAL_KEY_STORE_PATH", os.path.join(".secrets", "local_config.json"))


def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(STORE_PATH) or ".", exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(STORE_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner read/write only


def get_active_provider() -> str | None:
    return _load().get("provider")


def get_key(provider: str) -> str | None:
    return _load().get("keys", {}).get(provider)


def save(provider: str, api_key: str | None) -> None:
    data = _load()
    data["provider"] = provider
    if api_key:
        data.setdefault("keys", {})[provider] = api_key
    _write(data)


def clear_key(provider: str) -> None:
    data = _load()
    data.get("keys", {}).pop(provider, None)
    _write(data)


# Fixed-width mask: always 8 dots + up to 4 tail characters, regardless of the
# real key's length. A real key is 40-120+ characters — mirroring that length
# 1:1 in dots overflowed the settings modal (see SOLUTION.md, "Front-end").
_MASK_DOTS = 8


def masked(provider: str) -> str | None:
    key = get_key(provider)
    if not key:
        return None
    tail = key[-4:] if len(key) >= 4 else key
    return f"{'•' * _MASK_DOTS}{tail}"


def get_model(provider: str) -> str | None:
    return _load().get("models", {}).get(provider)


def save_model(provider: str, model: str | None) -> None:
    data = _load()
    if model:
        data.setdefault("models", {})[provider] = model
    _write(data)


_DEFAULT_BATCH_NOTIFY_MODE = "on_complete"


def get_batch_notify_mode() -> str:
    return _load().get("batch_notify_mode") or _DEFAULT_BATCH_NOTIFY_MODE


def save_batch_notify_mode(mode: str | None) -> None:
    data = _load()
    if mode:
        data["batch_notify_mode"] = mode
    _write(data)


def status() -> dict:
    """Provider + whether each provider has a stored key, without ever exposing the key itself."""
    data = _load()
    keys = data.get("keys", {})
    return {
        "active_provider": data.get("provider"),
        "providers": {
            p: {"has_key": bool(keys.get(p)), "masked": masked(p), "model": get_model(p)}
            for p in ("xai", "openai", "anthropic")
        },
    }
