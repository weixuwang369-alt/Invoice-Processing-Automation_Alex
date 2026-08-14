"""Tracks which folder the app currently ingests invoices from — the
"main invoice folder." Defaults to the bundled dataset; the web UI's Batch
tab can promote an uploaded folder to be the new default, persisted in a
local, gitignored file so it survives a server restart (same pattern as
src/key_store.py, different domain).
"""

from __future__ import annotations

import glob
import json
import os
import shutil

STORE_PATH = os.getenv("INVOICE_FOLDER_STORE_PATH", os.path.join(".secrets", "invoice_folder.json"))
DEFAULT_FOLDER = "data/invoices"
DEFAULT_LABEL = "Default folder (bundled invoices)"
SUPPORTED_EXTENSIONS = {".txt", ".json", ".csv", ".xml", ".pdf"}

# Where an uploaded folder's files get copied when promoted to "main" —
# browsers can't expose an uploaded folder's real filesystem path, so this
# is the server-side home for whatever was uploaded.
MANAGED_FOLDER = "uploaded_invoices"


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


def get_main_folder() -> str:
    return _load().get("folder_path", DEFAULT_FOLDER)


def list_files(folder: str) -> list[str]:
    """Every processable invoice file directly inside `folder`, sorted."""
    if not os.path.isdir(folder):
        return []
    return sorted(
        f for f in glob.glob(os.path.join(folder, "*"))
        if os.path.isfile(f) and os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
    )


def get_status() -> dict:
    data = _load()
    return {
        "folder_path": data.get("folder_path", DEFAULT_FOLDER),
        "label": data.get("label", DEFAULT_LABEL),
        "is_custom": bool(data.get("is_custom", False)),
    }


def reset_to_default() -> None:
    _write({})


def persist_uploaded_files(files_with_names: list[tuple[str, str]], label: str) -> str:
    """files_with_names: (source_path_on_disk, original_filename) pairs.
    Replaces MANAGED_FOLDER's contents with these files and promotes it to
    the main invoice folder. Returns the managed folder path."""
    if os.path.exists(MANAGED_FOLDER):
        shutil.rmtree(MANAGED_FOLDER)
    os.makedirs(MANAGED_FOLDER, exist_ok=True)

    for src, name in files_with_names:
        dest = os.path.join(MANAGED_FOLDER, os.path.basename(name))
        shutil.copyfile(src, dest)

    _write({"folder_path": MANAGED_FOLDER, "label": label or "Uploaded folder", "is_custom": True})
    return MANAGED_FOLDER
