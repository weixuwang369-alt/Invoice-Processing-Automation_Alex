"""FastAPI backend for the invoice processing UI. Reuses the exact same
LangGraph pipeline as the CLI (main.py) — this is a second entrypoint into
the same agents, not a parallel implementation.

Run:
    python server.py
    # or: uvicorn server:app --reload
Then open http://localhost:8000
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from setup_inventory_db import ALIAS_SEED, INVENTORY_SEED, SCHEMA
from src import action_log
from src import edit_requests as edit_requests_store
from src import invoice_folder, key_store, notifications
from src.approval.agent import _format_escalation_resolution
from src.config import PROVIDER_DEFAULTS, PROVIDER_MODELS, load_settings
from src.invoice_folder import SUPPORTED_EXTENSIONS
from src.observability.logger import write_run_log
from src.orchestration.graph import (
    BatchStopped,
    approve_and_pay_with_progress,
    ingest_only,
    recheck_with_progress,
    run_with_progress,
    validate_with_batch_duplicates,
)
from src.payment.agent import PaymentAgent
from src.schemas import ApprovalDecision, ExtractedInvoice

app = FastAPI(title="Acme Invoice Processing")

BATCH_NOTIFY_MODES = ("per_invoice", "on_complete")


def _db_path() -> str:
    return load_settings().inventory_db_path


def _log_dir() -> str:
    return load_settings().log_dir


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingsUpdate(BaseModel):
    provider: str
    api_key: str | None = None
    model: str | None = None
    batch_notify_mode: str | None = None


@app.get("/api/settings")
def get_settings():
    settings = load_settings()
    return {
        "active_provider": settings.llm_provider,
        "active_model": settings.llm_model,
        "configured": bool(settings.llm_api_key),
        "providers": key_store.status()["providers"],
        "available_providers": list(PROVIDER_DEFAULTS.keys()),
        "available_models": PROVIDER_MODELS,
        "batch_notify_mode": key_store.get_batch_notify_mode(),
        "batch_notify_modes": list(BATCH_NOTIFY_MODES),
    }


@app.post("/api/settings")
def save_settings(update: SettingsUpdate):
    if update.provider not in PROVIDER_DEFAULTS:
        raise HTTPException(400, f"Unknown provider: {update.provider}.")
    if update.model and update.model not in PROVIDER_MODELS[update.provider]:
        raise HTTPException(400, f"Unknown model for provider {update.provider}: {update.model}.")
    if update.batch_notify_mode and update.batch_notify_mode not in BATCH_NOTIFY_MODES:
        raise HTTPException(400, f"Unknown batch notification mode: {update.batch_notify_mode}.")
    key_store.save(update.provider, update.api_key)
    key_store.save_model(update.provider, update.model)
    key_store.save_batch_notify_mode(update.batch_notify_mode)
    return get_settings()


@app.delete("/api/settings/{provider}/key")
def delete_key(provider: str):
    if provider not in PROVIDER_DEFAULTS:
        raise HTTPException(400, f"Unknown provider: {provider}.")
    key_store.clear_key(provider)
    return get_settings()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@app.get("/api/inventory")
def get_inventory():
    if not os.path.exists(_db_path()):
        return {"initialized": False, "items": []}
    conn = sqlite3.connect(_db_path())
    rows = conn.execute("SELECT item, stock, unit_price, category FROM inventory").fetchall()
    conn.close()
    return {
        "initialized": True,
        "items": [{"item": r[0], "stock": r[1], "unit_price": r[2], "category": r[3]} for r in rows],
    }


class InventoryItemUpdate(BaseModel):
    item: str
    stock: int
    unit_price: float | None = None
    category: str | None = None


class InventoryUpdate(BaseModel):
    items: list[InventoryItemUpdate]


@app.post("/api/inventory/update")
def update_inventory(update: InventoryUpdate):
    """Free-form editing of stock/price/category — this is a simulated
    environment with no real ERP to sync from, so inventory.db is the
    system of record and the seed data is just its state-0 baseline
    (restorable any time via /api/inventory/reset or /api/full-reset).
    `item` is the lookup key the validation agent normalizes against
    (src/ingestion/normalize.py) and item_aliases references by foreign
    key, so it is intentionally not editable here — only existing items
    can be updated, never renamed or created through this endpoint."""
    if not os.path.exists(_db_path()):
        raise HTTPException(400, "The inventory database is not set up. Call POST /api/inventory/reset first.")
    conn = sqlite3.connect(_db_path())
    conn.executemany(
        "UPDATE inventory SET stock = ?, unit_price = ?, category = ? WHERE item = ?",
        [(i.stock, i.unit_price, i.category, i.item) for i in update.items],
    )
    conn.commit()
    conn.close()
    return get_inventory()


def _reset_inventory_db() -> None:
    """(Re)creates inventory.db from the seed data, wiping the processed_invoices
    ledger too. Shared by /api/inventory/reset (quick, between-batch-runs
    reset) and /api/full-reset (everything, see below)."""
    conn = sqlite3.connect(_db_path())
    conn.executescript(
        "DROP TABLE IF EXISTS inventory; DROP TABLE IF EXISTS item_aliases; DROP TABLE IF EXISTS processed_invoices;"
    )
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO inventory (item, stock, unit_price, category) VALUES (?, ?, ?, ?)", INVENTORY_SEED
    )
    conn.executemany("INSERT INTO item_aliases (alias, canonical_item) VALUES (?, ?)", ALIAS_SEED)
    conn.commit()
    conn.close()


@app.post("/api/inventory/reset")
def reset_inventory():
    _reset_inventory_db()
    return get_inventory()


# ---------------------------------------------------------------------------
# Invoice folder
# ---------------------------------------------------------------------------


@app.get("/api/invoice-folder")
def get_invoice_folder():
    status = invoice_folder.get_status()
    status["file_count"] = len(invoice_folder.list_files(status["folder_path"]))
    return status


@app.post("/api/invoice-folder/reset")
def reset_invoice_folder():
    invoice_folder.reset_to_default()
    return get_invoice_folder()


@app.get("/api/invoices")
def list_invoices():
    files = invoice_folder.list_files(invoice_folder.get_main_folder())
    return [{"name": os.path.basename(f), "format": os.path.splitext(f)[1].lstrip(".")} for f in files]


def _serialize(state: dict, display_name: str | None = None, run_id: str | None = None, edited_from: str | None = None) -> dict:
    return {
        "run_id": run_id,
        "file": display_name or os.path.basename(state["file_path"]),
        "processed_at": state["processed_at"],
        "invoice": state["invoice"].model_dump() if state["invoice"] else None,
        "validation": state["validation"].model_dump() if state["validation"] else None,
        "approval": state["decision"].model_dump() if state["decision"] else None,
        "payment": state["payment"].model_dump() if state["payment"] else None,
        "edited_from": edited_from,
    }


def _run_id_from_log_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _display_file(record: dict, path: str) -> str:
    """The name to show for a run log record. For an uploaded file,
    record["source_file"] is a server-generated temp path with a
    meaningless basename -- record["display_name"] (see write_run_log)
    carries the name actually uploaded, when there is one."""
    return record.get("display_name") or os.path.basename(record.get("source_file") or path)


def _notify_invoice_completed(result: dict, type: str = "invoice_completed") -> None:
    decision = result["approval"]["decision"] if result["approval"] else "unknown"
    notifications.add(
        type,
        f"The application finished processing {result['file']}. Decision: {decision}.",
        run_id=result["run_id"],
    )


def _notify_if_escalated(result: dict) -> None:
    """Escalation means the application withheld payment and needs a person
    to look at this specific invoice — that must never get silently folded
    into a batch summary or skipped because batch_notify_mode is set to
    "on_complete". So this fires unconditionally, on top of whatever the
    routine invoice_completed notification already does."""
    approval = result.get("approval")
    if approval and approval["decision"] == "escalated":
        notifications.add(
            "invoice_escalated",
            f"Invoice {result['file']} needs a person to review it. The application escalated it and withheld payment. "
            f"For this demo, you review it in the Edit Approvals tab. In a real deployment, it would go to the "
            f"appropriate approver.",
            run_id=result["run_id"],
        )


# ---------------------------------------------------------------------------
# Single invoice — runs as a background job, same as batch, so the page can
# poll for live per-stage progress (ingestion -> validation -> approval ->
# payment) instead of blocking on one request. See _run_single_job().
# ---------------------------------------------------------------------------

_single_jobs: dict[str, dict] = {}


def _serialize_single_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "file": job["file"],
        "result": job["result"],
        "error": job["error"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _run_single_job(job_id: str, file_path: str, display_name: str | None, tmp_path: str | None) -> None:
    job = _single_jobs[job_id]

    def on_stage(stage: str) -> None:
        job["stage"] = stage

    try:
        processed_at = datetime.now(timezone.utc).isoformat()
        state = run_with_progress(file_path, _db_path(), processed_at, on_stage=on_stage)
        log_path = write_run_log(state, _log_dir(), display_name=display_name)
        result = _serialize(state, display_name=display_name, run_id=_run_id_from_log_path(log_path))
        job["result"] = result
        job["status"] = "completed"
        _notify_invoice_completed(result)
        _notify_if_escalated(result)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        job["stage"] = None
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/api/process/start")
async def start_process(invoice_name: str | None = None, file: UploadFile | None = None):
    if not os.path.exists(_db_path()):
        raise HTTPException(400, "The inventory database is not set up. Call POST /api/inventory/reset first.")
    if not invoice_name and not file:
        raise HTTPException(400, "Provide an `invoice_name` from /api/invoices, or upload a `file`.")

    tmp_path = None
    display_name = None
    if invoice_name:
        file_path = os.path.join(invoice_folder.get_main_folder(), invoice_name)
        if not os.path.exists(file_path):
            raise HTTPException(404, f"The file '{invoice_name}' is not in the main invoice folder.")
    else:
        display_name = file.filename or "uploaded file"
        suffix = os.path.splitext(file.filename or "")[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        file_path = tmp_path

    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "status": "running",
        "stage": "ingestion",
        "file": display_name or os.path.basename(file_path),
        "result": None,
        "error": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    _single_jobs[job_id] = job
    thread = threading.Thread(target=_run_single_job, args=(job_id, file_path, display_name, tmp_path), daemon=True)
    thread.start()
    return _serialize_single_job(job)


@app.get("/api/process/status/{job_id}")
def process_status(job_id: str):
    job = _single_jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"No processing job found with id {job_id}.")
    return _serialize_single_job(job)


_BATCH_CONCURRENCY = 4  # capped so a large batch doesn't hammer the LLM provider's rate limits
_STOP_POLL_INTERVAL = 0.2  # seconds — how quickly a Stop click is noticed, see _drain()


def _drain(futures: list, should_stop, handle) -> None:
    """Waits for a batch of futures to complete, calling handle(result) for
    each — but re-checks should_stop() every _STOP_POLL_INTERVAL instead of
    blocking on the next completion (as_completed() would), which could
    otherwise be several seconds away during an LLM-heavy phase. The moment
    should_stop() is true, this returns immediately without waiting for
    whatever's still pending.

    Futures still running at that point are simply abandoned, not
    cancelled — Python can't interrupt a thread mid-HTTP-call. They keep
    running against a detached executor (see callers) and either finish
    naturally or self-discard via BatchStopped; either way, since nothing
    ever calls handle() for them, nothing they produce reaches `results`.
    """
    pending = set(futures)
    while pending:
        if should_stop and should_stop():
            return
        done, pending = wait_futures(pending, timeout=_STOP_POLL_INTERVAL, return_when=FIRST_COMPLETED)
        for future in done:
            handle(future.result())


def _process_batch(
    files: list[tuple[str, str | None]],
    on_progress=None,
    on_stage=None,
    on_result=None,
    on_error=None,
    should_stop=None,
) -> dict:
    """files: list of (file_path_on_disk, display_name_or_None). Processes each
    independently — one bad file doesn't fail the batch, mirroring main.py --all.

    Three phases, not one pass per file, specifically so that two invoices
    sharing an invoice number within the SAME batch are both correctly
    resolved instead of racing each other for the ledger (see
    SOLUTION.md, "Batch concurrency and duplicate detection"):

      1. Ingest every file concurrently. Extraction can itself be an LLM
         call (free-text/PDF invoices), so this can be the slowest phase
         for a batch of unstructured files — parallelizing it is a real
         win, not just phase 3.
      2. Validate every successfully-ingested invoice, one at a time, in
         original file order. This is what makes duplicate detection
         correct: the first occurrence of an invoice number in the batch
         passes, and every later occurrence in the SAME batch is flagged
         right here — before either one ever reaches approval or payment.
         This phase does no LLM calls, so running it sequentially costs
         nothing.
      3. Approve and pay every validated invoice concurrently — the
         expensive part (up to two sequential LLM calls each). Safe to
         parallelize because phase 2 already resolved every duplicate
         before any of this starts.

    on_progress(display_name, index) fires as each file starts ingestion in
    phase 1, and again as it starts approval in phase 3 — so "current file"
    reflects whichever phase is actually running, at the cost of being "the
    most recently started file" rather than "the only file in flight" once
    several run at once. on_stage(stage, file_name) fires per file per
    stage — unlike on_progress, this one identifies which file it's about,
    so a caller can track every file currently in flight, not just the most
    recently started one. on_result(result) / on_error(error) fire as each
    file's full pipeline (or its ingestion) finishes — in actual completion
    order, not necessarily original file order, once phases 1 and 3 run
    several files at once.

    should_stop() is checked constantly, not just once per file: at the top
    of each file's phase-1/phase-3 work (so queued-but-not-yet-started files
    never start), every _STOP_POLL_INTERVAL while waiting on in-flight work
    (see _drain(), above — this is what makes Stop take effect in a fraction
    of a second rather than waiting for whatever's mid-LLM-call), and once
    more between phases (so a stop mid-ingestion skips validation/approval
    entirely). A file already mid-flight when should_stop() becomes true is
    abandoned, not waited for — and since phase 3 also checks should_stop()
    right after approval finishes but before payment (see BatchStopped in
    src/orchestration/graph.py), nothing reaches the payment API or the
    ledger once a stop has been requested, even for a file whose approval
    call was already in progress at that moment. An abandoned file simply
    never appears in `results` — not logged, not counted, not shown."""
    errors: list[dict] = []
    ingested: dict[int, tuple[str, str | None, dict]] = {}
    results: list[dict] = []

    def ingest_one(index: int, file_path: str, display_name: str | None) -> dict:
        name = display_name or os.path.basename(file_path)
        if should_stop and should_stop():
            return {"index": index, "status": "skipped"}
        if on_progress:
            on_progress(name, index)
        if on_stage:
            on_stage("ingestion", name)
        try:
            processed_at = datetime.now(timezone.utc).isoformat()
            state = ingest_only(file_path, _db_path(), processed_at)
            return {"index": index, "status": "ok", "file_path": file_path, "display_name": display_name, "state": state}
        except Exception as e:
            return {"index": index, "status": "error", "error": {"file": name, "error": str(e)}}

    def handle_ingest(r: dict) -> None:
        if r["status"] == "ok":
            ingested[r["index"]] = (r["file_path"], r["display_name"], r["state"])
        elif r["status"] == "error":
            errors.append(r["error"])
            if on_error:
                on_error(r["error"])
        # "skipped" — dropped silently, matching "no new work starts once stopped."

    pool = ThreadPoolExecutor(max_workers=_BATCH_CONCURRENCY)
    futures = [pool.submit(ingest_one, i, fp, dn) for i, (fp, dn) in enumerate(files, start=1)]
    _drain(futures, should_stop, handle_ingest)
    pool.shutdown(wait=False, cancel_futures=True)

    stopped = bool(should_stop and should_stop())

    # Phase 2: sequential, in original file order, so seen_in_batch reflects
    # "appeared earlier in this batch" correctly regardless of the
    # (unordered) completion order phase 1 just finished in. Skipped
    # entirely if a stop already landed during phase 1 — validating invoices
    # that are about to be discarded anyway is pointless.
    to_approve: list[tuple[int, str, str | None, dict]] = []
    if not stopped:
        seen_in_batch: set[str] = set()
        for index, (file_path, display_name) in enumerate(files, start=1):
            if index not in ingested:
                continue
            _, _, state = ingested[index]
            name = display_name or os.path.basename(file_path)
            if on_stage:
                on_stage("validation", name)
            state = validate_with_batch_duplicates(state, seen_in_batch=seen_in_batch)
            number = state["invoice"].invoice_number if state["invoice"] else None
            if number:
                seen_in_batch.add(number)
            to_approve.append((index, file_path, display_name, state))
        stopped = bool(should_stop and should_stop())

    # Phase 3: concurrent again, now that every batch-internal duplicate is
    # already resolved. Skipped entirely if already stopped.
    def approve_one(index: int, file_path: str, display_name: str | None, state: dict) -> dict:
        name = display_name or os.path.basename(file_path)
        if should_stop and should_stop():
            return {"index": index, "status": "skipped"}
        if on_progress:
            on_progress(name, index)
        try:
            file_stage = (lambda s: on_stage(s, name)) if on_stage else None
            final_state = approve_and_pay_with_progress(state, on_stage=file_stage, should_stop=should_stop)
            log_path = write_run_log(final_state, _log_dir(), display_name=display_name)
            result = _serialize(final_state, display_name=display_name, run_id=_run_id_from_log_path(log_path))
            return {"index": index, "status": "ok", "result": result}
        except BatchStopped:
            return {"index": index, "status": "skipped"}
        except Exception as e:
            return {"index": index, "status": "error", "error": {"file": name, "error": str(e)}}

    def handle_approve(r: dict) -> None:
        if r["status"] == "ok":
            results.append(r["result"])
            if on_result:
                on_result(r["result"])
        elif r["status"] == "error":
            errors.append(r["error"])
            if on_error:
                on_error(r["error"])
        # "skipped" — dropped silently (purged): either never started, or
        # approved but stopped before payment (see BatchStopped, above).

    if not stopped and to_approve:
        pool = ThreadPoolExecutor(max_workers=_BATCH_CONCURRENCY)
        futures = [pool.submit(approve_one, index, file_path, display_name, state) for index, file_path, display_name, state in to_approve]
        _drain(futures, should_stop, handle_approve)
        pool.shutdown(wait=False, cancel_futures=True)

    approved = [r for r in results if r["approval"]["decision"] == "approved"]
    flagged = [r for r in results if r["approval"]["decision"] != "approved"]
    summary = {
        "total_processed": len(results),
        "total_errors": len(errors),
        "counts": {
            "approved": len(approved),
            "rejected": len([r for r in results if r["approval"]["decision"] == "rejected"]),
            "escalated": len([r for r in results if r["approval"]["decision"] == "escalated"]),
        },
        "amount_approved": sum(r["invoice"]["total"] or 0 for r in approved if r["invoice"]),
        "amount_flagged": sum((r["invoice"]["total"] or 0) for r in flagged if r["invoice"]),
    }
    return {"results": results, "errors": errors, "summary": summary}


# ---------------------------------------------------------------------------
# Batch jobs — a folder can hold hundreds of invoices, each needing 1-2 LLM
# calls, so batch processing runs on a background thread and reports progress
# through these in-memory job records. One job runs at a time; this is a
# single-user local tool, not a multi-tenant queue (see SOLUTION.md).
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_last_job_id: str | None = None


def _serialize_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "total": job["total"],
        "processed": job["processed"],
        "current_file": job["current_file"],
        "stage": job["stage"],
        "in_progress": [{"file": f, "stage": s} for f, s in job["in_progress"].items()],
        "results": job["results"],
        "errors": job["errors"],
        "summary": job["summary"],
        "skipped": job.get("skipped", []),
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _run_batch_job(job_id: str, files: list[tuple[str, str | None]], tmp_dir: str | None) -> None:
    job = _jobs[job_id]
    notify_mode = key_store.get_batch_notify_mode()

    def on_progress(name: str, index: int) -> None:
        job["current_file"] = name

    def on_stage(stage: str, file_name: str | None = None) -> None:
        job["stage"] = stage
        if file_name:
            job["in_progress"][file_name] = stage

    def on_result(result: dict) -> None:
        job["results"].append(result)
        job["processed"] = len(job["results"]) + len(job["errors"])
        job["in_progress"].pop(result.get("file"), None)
        if notify_mode == "per_invoice":
            _notify_invoice_completed(result)
        _notify_if_escalated(result)

    def on_error(error: dict) -> None:
        job["errors"].append(error)
        job["processed"] = len(job["results"]) + len(job["errors"])
        job["in_progress"].pop(error.get("file"), None)

    def should_stop() -> bool:
        return job["stop_requested"]

    try:
        outcome = _process_batch(
            files, on_progress=on_progress, on_stage=on_stage, on_result=on_result, on_error=on_error, should_stop=should_stop
        )
        job["summary"] = outcome["summary"]
        job["processed"] = len(outcome["results"]) + len(outcome["errors"])
        job["status"] = "stopped" if job["stop_requested"] else "completed"
        if notify_mode != "per_invoice" and outcome["results"]:
            s = outcome["summary"]
            notifications.add(
                "batch_completed",
                f"The batch finished. The application processed {s['total_processed']} invoice(s). "
                f"It approved {s['counts']['approved']}. It rejected {s['counts']['rejected']}. "
                f"It escalated {s['counts']['escalated']}.",
            )
    except Exception as e:
        job["status"] = "error"
        job["errors"].append({"file": job["current_file"] or "batch", "error": str(e)})
    finally:
        job["current_file"] = None
        job["stage"] = None
        job["in_progress"].clear()
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _start_job(files: list[tuple[str, str | None]], skipped: list[str], tmp_dir: str | None) -> dict:
    global _last_job_id
    with _jobs_lock:
        if _last_job_id and _jobs[_last_job_id]["status"] == "running":
            raise HTTPException(409, "A batch is already running. Stop it before you start a new one.")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "status": "running",
            "total": len(files),
            "processed": 0,
            "current_file": None,
            "stage": None,
            "in_progress": {},
            "results": [],
            "errors": [],
            "summary": None,
            "skipped": skipped,
            "stop_requested": False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }
        _jobs[job_id] = job
        _last_job_id = job_id

    thread = threading.Thread(target=_run_batch_job, args=(job_id, files, tmp_dir), daemon=True)
    thread.start()
    return _serialize_job(job)


@app.post("/api/batch/start")
def start_batch():
    """Starts a background job that processes every invoice in the current
    main invoice folder. Poll GET /api/batch/status/{id} for progress."""
    if not os.path.exists(_db_path()):
        raise HTTPException(400, "The inventory database is not set up. Call POST /api/inventory/reset first.")

    files = invoice_folder.list_files(invoice_folder.get_main_folder())
    return _start_job([(f, None) for f in files], skipped=[], tmp_dir=None)


@app.post("/api/batch/start-upload")
async def start_batch_upload(
    files: list[UploadFile],
    set_as_main: bool = Form(False),
    folder_label: str | None = Form(None),
):
    """Starts a background job that processes an uploaded folder's files. If
    set_as_main is true, the files are copied into the main invoice folder
    before the job starts, so the promotion survives even if the job is
    stopped partway through."""
    if not os.path.exists(_db_path()):
        raise HTTPException(400, "The inventory database is not set up. Call POST /api/inventory/reset first.")

    supported = [f for f in files if os.path.splitext(f.filename or "")[1].lower() in SUPPORTED_EXTENSIONS]
    skipped = [f.filename for f in files if f not in supported]
    if not supported:
        raise HTTPException(400, "No supported invoice files were found. Supported formats are .txt, .json, .csv, .xml, and .pdf.")

    tmp_dir = tempfile.mkdtemp(prefix="batch-job-")
    batch = []
    for f in supported:
        dest = os.path.join(tmp_dir, f.filename or f"upload{os.path.splitext(f.filename or '')[1]}")
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        batch.append((dest, f.filename))

    if set_as_main:
        invoice_folder.persist_uploaded_files(batch, label=folder_label)

    return _start_job(batch, skipped=skipped, tmp_dir=tmp_dir)


@app.get("/api/batch/status/{job_id}")
def batch_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"No batch job found with id {job_id}.")
    return _serialize_job(job)


@app.get("/api/batch/current")
def batch_current():
    """Lets the page reattach to a job it lost track of, e.g. after a reload."""
    if not _last_job_id:
        return None
    return _serialize_job(_jobs[_last_job_id])


@app.post("/api/batch/stop/{job_id}")
def stop_batch(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"No batch job found with id {job_id}.")
    if job["status"] == "running":
        job["stop_requested"] = True
    return _serialize_job(job)


# ---------------------------------------------------------------------------
# Processed invoices — a read-only audit trail over logs/, the structured
# JSON run trace every invoice already gets (src/observability/logger.py).
# Both /api/process and the batch pipeline write there, so this is a single
# history across single-invoice and batch runs, keyed by the log filename.
# ---------------------------------------------------------------------------

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _log_file_for(run_id: str) -> str:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(400, "That id is not valid.")
    path = os.path.join(_log_dir(), f"{run_id}.json")
    if not os.path.exists(path):
        raise HTTPException(404, f"No processed invoice was found with id {run_id}.")
    return path


@app.get("/api/processed-invoices")
def list_processed_invoices():
    paths = sorted(glob.glob(os.path.join(_log_dir(), "*.json")), reverse=True)
    rows = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        invoice = record.get("ingestion") or {}
        rows.append(
            {
                "run_id": _run_id_from_log_path(path),
                "file": _display_file(record, path),
                "processed_at": record.get("processed_at"),
                "invoice": {
                    "invoice_number": invoice.get("invoice_number"),
                    "vendor": invoice.get("vendor"),
                    "total": invoice.get("total"),
                    "currency": invoice.get("currency"),
                },
                "validation": record.get("validation"),
                "approval": record.get("approval"),
                "payment": record.get("payment"),
                "edited_from": record.get("edited_from"),
            }
        )
    return rows


@app.get("/api/processed-invoices/{run_id}")
def get_processed_invoice(run_id: str):
    path = _log_file_for(run_id)
    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)
    return {
        "run_id": run_id,
        "file": _display_file(record, path),
        "processed_at": record.get("processed_at"),
        "invoice": record.get("ingestion"),
        "validation": record.get("validation"),
        "approval": record.get("approval"),
        "payment": record.get("payment"),
        "edited_from": record.get("edited_from"),
    }


# ---------------------------------------------------------------------------
# Manual edits — a mock, self-approval workflow layered on top of the
# processed-invoices audit trail. Editing a field never mutates the original
# run log; it creates a pending EditRequest, and only once "approved" (by
# the same user, in this demo — see the notification/UI copy) does the
# system recheck validation -> approval -> payment against the edited value,
# writing a brand-new run log linked back to the original via edited_from.
# ---------------------------------------------------------------------------


class EditRequestCreate(BaseModel):
    run_id: str
    edited_invoice: dict


_EDIT_DIFF_SKIP_FIELDS = {"extraction_method", "extraction_warnings", "source_file"}


def _diff_invoice(original: dict, edited: dict) -> dict:
    changes = {}
    for key in set(original.keys()) | set(edited.keys()):
        if key in _EDIT_DIFF_SKIP_FIELDS:
            continue
        if original.get(key) != edited.get(key):
            changes[key] = {"old": original.get(key), "new": edited.get(key)}
    return changes


@app.post("/api/edit-requests")
def create_edit_request(body: EditRequestCreate):
    path = _log_file_for(body.run_id)
    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)
    original_invoice = record.get("ingestion")
    if not original_invoice:
        raise HTTPException(400, "This invoice has no extracted data to edit.")

    try:
        original = ExtractedInvoice(**original_invoice)
        edited = ExtractedInvoice(**{**original_invoice, **body.edited_invoice})
    except Exception as e:
        raise HTTPException(422, f"The edited invoice is not valid: {e}")

    # Diff pydantic-coerced dumps, not raw form strings — otherwise "500" vs
    # 500.0 shows up as a spurious change on every numeric field.
    changes = _diff_invoice(original.model_dump(), edited.model_dump())
    if not changes:
        raise HTTPException(400, "Nothing changed.")

    edit_request = edit_requests_store.create(body.run_id, changes, edited.model_dump())
    notifications.add(
        "edit_pending",
        f"An edit to invoice {original.invoice_number or body.run_id} needs approval. For this demo, "
        f"you approve your own edit requests. In a real deployment, a separate approver would do this.",
        run_id=body.run_id,
        edit_request_id=edit_request["id"],
    )
    return edit_request


@app.get("/api/edit-requests")
def list_edit_requests(status: str | None = None):
    records = edit_requests_store.list_all()
    if status:
        records = [r for r in records if r["status"] == status]
    return records


# ---------------------------------------------------------------------------
# Approving an edit request runs a real recheck (validation -> approval ->
# payment) through the LLM, same cost as processing one invoice -- so, like
# single-invoice and batch, it runs as a background job with live per-stage
# progress rather than blocking the request. The one difference: its first
# stage is a synthetic "recheck" fired before run_with_progress's usual
# three, specifically so the UI can say "passing this back through the
# system" up front -- confirming for the user that approving an edit
# actually re-enters the pipeline, not just applies the field silently.
# ---------------------------------------------------------------------------

_edit_jobs: dict[str, dict] = {}


def _serialize_edit_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "edit_request_id": job["edit_request_id"],
        "file": job["file"],
        "result": job["result"],
        "error": job["error"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _run_edit_recheck_job(job_id: str, edit_id: str, display_name: str | None) -> None:
    job = _edit_jobs[job_id]

    def on_stage(stage: str) -> None:
        job["stage"] = stage

    try:
        on_stage("recheck")
        edit_request = edit_requests_store.get(edit_id)
        invoice = ExtractedInvoice(**edit_request["edited_invoice"])
        processed_at = datetime.now(timezone.utc).isoformat()
        state = recheck_with_progress(invoice, _db_path(), processed_at, on_stage=on_stage)
        log_path = write_run_log(state, _log_dir(), edited_from=edit_request["run_id"], display_name=display_name)
        new_run_id = _run_id_from_log_path(log_path)
        result = _serialize(state, display_name=display_name, run_id=new_run_id, edited_from=edit_request["run_id"])

        edit_requests_store.set_decision(edit_id, "approved", new_run_id=new_run_id)
        _notify_invoice_completed(result)
        _notify_if_escalated(result)
        job["result"] = {"edit_request": edit_requests_store.get(edit_id), "result": result}
        job["status"] = "completed"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        job["stage"] = None
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/api/edit-requests/{edit_id}/approve")
def approve_edit_request(edit_id: str):
    edit_request = edit_requests_store.get(edit_id)
    if not edit_request:
        raise HTTPException(404, f"No edit request found with id {edit_id}.")
    if edit_request["status"] != "pending":
        raise HTTPException(409, "This edit request was already decided.")
    if any(j["edit_request_id"] == edit_id and j["status"] == "running" for j in _edit_jobs.values()):
        raise HTTPException(409, "This edit request is already being rechecked.")

    original_path = _log_file_for(edit_request["run_id"])
    with open(original_path, "r", encoding="utf-8") as f:
        original_record = json.load(f)
    display_name = original_record.get("display_name")

    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "status": "running",
        "stage": "recheck",
        "edit_request_id": edit_id,
        "file": _display_file(original_record, original_path),
        "result": None,
        "error": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    _edit_jobs[job_id] = job
    thread = threading.Thread(target=_run_edit_recheck_job, args=(job_id, edit_id, display_name), daemon=True)
    thread.start()
    return _serialize_edit_job(job)


@app.get("/api/edit-jobs/{job_id}")
def edit_job_status(job_id: str):
    job = _edit_jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"No edit job found with id {job_id}.")
    return _serialize_edit_job(job)


@app.post("/api/edit-requests/{edit_id}/reject")
def reject_edit_request(edit_id: str):
    edit_request = edit_requests_store.get(edit_id)
    if not edit_request:
        raise HTTPException(404, f"No edit request found with id {edit_id}.")
    if edit_request["status"] != "pending":
        raise HTTPException(409, "This edit request was already decided.")
    return edit_requests_store.set_decision(edit_id, "rejected")


# ---------------------------------------------------------------------------
# Escalations — an "escalated" ApprovalDecision means the model itself
# couldn't confidently approve or reject, so a person has to. Escalated
# invoices need no separate store: the run log's own approval.decision
# field IS the pending/resolved state, so the Edit Approvals tab finds
# them by filtering GET /api/processed-invoices client-side, and resolving
# one just overwrites that same run's approval/payment sections in place
# (no new run_id -- nothing about the extracted invoice data changed,
# only who made the final call). Mirrors the edit-request approval flow's
# self-approval caveat: in this demo the same user resolves it.
# ---------------------------------------------------------------------------


def _resolve_escalation(run_id: str, decision_value: str) -> dict:
    path = _log_file_for(run_id)
    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)

    approval = record.get("approval")
    if not approval or approval["decision"] != "escalated":
        raise HTTPException(409, "This invoice is not waiting for escalation review.")
    if not record.get("ingestion"):
        raise HTTPException(400, "This invoice has no extracted data to act on.")

    invoice = ExtractedInvoice(**record["ingestion"])
    decision = ApprovalDecision(**approval)
    decision.decision = decision_value
    decision.escalation_resolution = _format_escalation_resolution(decision_value)

    resolved_at = datetime.now(timezone.utc).isoformat()
    payment_agent = PaymentAgent(_db_path())
    payment = (
        payment_agent.pay(invoice, decision, resolved_at)
        if decision_value == "approved"
        else payment_agent.log_rejection(invoice, decision, resolved_at)
    )

    record["approval"] = decision.model_dump()
    record["payment"] = payment.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, default=str)

    result = {
        "run_id": run_id,
        "file": _display_file(record, path),
        "processed_at": record.get("processed_at"),
        "invoice": invoice.model_dump(),
        "validation": record.get("validation"),
        "approval": decision.model_dump(),
        "payment": payment.model_dump(),
        "edited_from": record.get("edited_from"),
    }
    verb = "approved" if decision_value == "approved" else "denied"
    notifications.add(
        "invoice_completed",
        f"A person {verb} the escalated invoice {result['file']}.",
        run_id=run_id,
    )
    return result


@app.post("/api/escalations/{run_id}/approve")
def approve_escalation(run_id: str):
    return _resolve_escalation(run_id, "approved")


@app.post("/api/escalations/{run_id}/deny")
def deny_escalation(run_id: str):
    return _resolve_escalation(run_id, "rejected")


# ---------------------------------------------------------------------------
# Notifications — a small in-memory feed the frontend bell icon polls (see
# src/notifications.py). In-memory is consistent with the _jobs store above:
# fine for a single-user local tool, doesn't survive a restart (see
# SOLUTION.md, Known limitations).
# ---------------------------------------------------------------------------


@app.get("/api/notifications")
def list_notifications():
    return notifications.list_all()


@app.post("/api/notifications/read-all")
def read_all_notifications():
    notifications.mark_all_read()
    return notifications.list_all()


# ---------------------------------------------------------------------------
# Action log — every LLM call the application makes (ingestion extraction,
# approval draft, approval critique), recorded at the call site (see
# src/action_log.py). Same in-memory tradeoff as notifications, above.
# ---------------------------------------------------------------------------


@app.get("/api/action-log")
def list_action_log():
    return action_log.list_all()


# ---------------------------------------------------------------------------
# Full reset — the nuclear option, triggered from Settings. Unlike
# /api/inventory/reset (a quick reset of just inventory.db, meant to be
# used between batch runs), this clears every processing history the app
# has: the run logs behind Processed Invoices, edit requests, and
# notifications, on top of the same inventory/ledger reset. The frontend
# gates this behind a type-to-confirm step — see static/app.js.
# ---------------------------------------------------------------------------


def _clear_logs() -> int:
    paths = glob.glob(os.path.join(_log_dir(), "*.json"))
    for p in paths:
        os.remove(p)
    return len(paths)


@app.post("/api/full-reset")
def full_reset():
    global _last_job_id
    if _last_job_id and _jobs.get(_last_job_id, {}).get("status") == "running":
        raise HTTPException(409, "A batch is running. Stop it before a full reset.")
    if any(job["status"] == "running" for job in _single_jobs.values()):
        raise HTTPException(409, "An invoice is still processing. Wait for it to finish before a full reset.")

    _reset_inventory_db()
    logs_cleared = _clear_logs()
    edit_requests_cleared = edit_requests_store.clear_all()
    notifications_cleared_count = notifications.clear_all()
    action_log_cleared = action_log.clear_all()

    with _jobs_lock:
        _jobs.clear()
        _last_job_id = None
    _single_jobs.clear()

    return {
        "inventory": get_inventory(),
        "logs_cleared": logs_cleared,
        "edit_requests_cleared": edit_requests_cleared,
        "notifications_cleared": notifications_cleared_count,
        "action_log_cleared": action_log_cleared,
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
