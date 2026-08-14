"""Structured JSON run traces — one file per processed invoice, saved to
logs/. This is the audit trail an auditor or engineer would actually want:
every stage's inputs/outputs, in one place, per invoice.
"""

from __future__ import annotations

import json
import os
import re

from src.orchestration.state import InvoiceState


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text).strip("_")


def write_run_log(
    state: InvoiceState, log_dir: str, edited_from: str | None = None, display_name: str | None = None
) -> str:
    os.makedirs(log_dir, exist_ok=True)

    invoice = state.get("invoice")
    validation = state.get("validation")
    decision = state.get("decision")
    payment = state.get("payment")

    record = {
        "source_file": state["file_path"],
        "processed_at": state["processed_at"],
        "ingestion": invoice.model_dump() if invoice else None,
        "validation": validation.model_dump() if validation else None,
        "approval": decision.model_dump() if decision else None,
        "payment": payment.model_dump() if payment else None,
        "edited_from": edited_from,
        # For an uploaded file, state["file_path"] is a server-generated temp
        # path (e.g. /tmp/tmpabc123.json) -- its basename is meaningless.
        # display_name carries the name the user actually uploaded, so any
        # LATER lookup of this record (Processed Invoices, an escalation
        # resolution, ...) can still show it instead of the temp name.
        "display_name": display_name,
    }

    stem = _slug(os.path.splitext(os.path.basename(state["file_path"]))[0])
    ts = _slug(state["processed_at"])
    path = os.path.join(log_dir, f"{ts}_{stem}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, default=str)
    return path
