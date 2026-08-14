"""LangGraph wiring for the four-stage pipeline: Ingestion -> Validation ->
Approval -> Payment, with a genuine conditional edge after Approval — an
approved invoice routes to the `pay` node (calls the mock payment API), and
a rejected/escalated one routes to `reject` (logs the reasoning, no
payment call). Both converge back to the ledger and then END.

Ingestion/parsing errors are allowed to raise and propagate to the caller
(main.py catches them per-invoice in --all mode) rather than being modeled
as extra graph branches — keeps the graph focused on the one decision that
actually forks the business process.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.approval.agent import ApprovalAgent
from src.ingestion.agent import IngestionAgent
from src.orchestration.state import InvoiceState
from src.payment.agent import PaymentAgent
from src.validation.agent import ValidationAgent


def _ingest_node(state: InvoiceState) -> dict:
    invoice = IngestionAgent(state["db_path"]).run(state["file_path"])
    return {"invoice": invoice}


def _validate_node(state: InvoiceState) -> dict:
    validation = ValidationAgent(state["db_path"]).run(state["invoice"])
    return {"validation": validation}


def _approve_node(state: InvoiceState) -> dict:
    decision = ApprovalAgent().run(state["invoice"], state["validation"])
    return {"decision": decision}


def _pay_node(state: InvoiceState) -> dict:
    payment = PaymentAgent(state["db_path"]).pay(state["invoice"], state["decision"], state["processed_at"])
    return {"payment": payment}


def _reject_node(state: InvoiceState) -> dict:
    payment = PaymentAgent(state["db_path"]).log_rejection(state["invoice"], state["decision"], state["processed_at"])
    return {"payment": payment}


def _route_after_approval(state: InvoiceState) -> str:
    return "pay" if state["decision"].decision == "approved" else "reject"


def build_graph():
    graph = StateGraph(InvoiceState)
    graph.add_node("ingest", _ingest_node)
    graph.add_node("validate", _validate_node)
    graph.add_node("approve", _approve_node)
    graph.add_node("pay", _pay_node)
    graph.add_node("reject", _reject_node)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "validate")
    graph.add_edge("validate", "approve")
    graph.add_conditional_edges("approve", _route_after_approval, {"pay": "pay", "reject": "reject"})
    graph.add_edge("pay", END)
    graph.add_edge("reject", END)

    return graph.compile()


def run_with_progress(file_path: str, db_path: str, processed_at: str, on_stage=None) -> InvoiceState:
    """Runs the same four stages as build_graph(), one node function at a
    time, calling on_stage(stage_name) right before each stage starts. The
    compiled LangGraph has no per-node progress hook, and the web UI's job
    endpoints need one to show live "which step is this on" status — so this
    calls the exact same node functions directly instead of going through
    graph.invoke(). Business logic lives only in the node functions/agents;
    this and build_graph() are just two ways to sequence them.
    """
    state: InvoiceState = {
        "file_path": file_path,
        "db_path": db_path,
        "processed_at": processed_at,
        "invoice": None,
        "validation": None,
        "decision": None,
        "payment": None,
    }
    if on_stage:
        on_stage("ingestion")
    state.update(_ingest_node(state))
    if on_stage:
        on_stage("validation")
    state.update(_validate_node(state))
    if on_stage:
        on_stage("approval")
    state.update(_approve_node(state))
    if on_stage:
        on_stage("payment")
    state.update(_pay_node(state) if _route_after_approval(state) == "pay" else _reject_node(state))
    return state


def ingest_only(file_path: str, db_path: str, processed_at: str) -> InvoiceState:
    """Just the ingest stage. Used by server.py's batch pipeline phase 1,
    where every file in the batch is ingested (concurrently) before any of
    them reach validation — see validate_with_batch_duplicates().
    """
    state: InvoiceState = {
        "file_path": file_path,
        "db_path": db_path,
        "processed_at": processed_at,
        "invoice": None,
        "validation": None,
        "decision": None,
        "payment": None,
    }
    state.update(_ingest_node(state))
    return state


def validate_with_batch_duplicates(state: InvoiceState, seen_in_batch: set[str] | None = None) -> InvoiceState:
    """Validates an already-ingested invoice, additionally treating an
    invoice number that already appeared earlier in the same batch as a
    duplicate — not only one already paid in a previous run. Used by
    server.py's batch pipeline phase 2, which runs this sequentially in
    original file order so every duplicate within a batch is resolved
    before any of the batch's invoices reach approval or payment.
    """
    state = dict(state)
    state["validation"] = ValidationAgent(state["db_path"]).run(state["invoice"], seen_in_batch=seen_in_batch)
    return state


class BatchStopped(Exception):
    """Raised by approve_and_pay_with_progress when a stop was requested
    after approval finished but before payment. server.py's batch pipeline
    catches this and discards the invoice entirely, rather than treating it
    as a result or an error — see "Stopping a batch immediately" in
    SOLUTION.md.
    Approval itself (up to two sequential LLM calls) can't be interrupted
    mid-flight, but this guarantees nothing reaches the payment API or the
    ledger for an invoice once a stop has been requested.
    """


def approve_and_pay_with_progress(state: InvoiceState, on_stage=None, should_stop=None) -> InvoiceState:
    """Runs approval, then pay or reject, against an already-validated
    invoice. Used by server.py's batch pipeline phase 3, which runs this
    concurrently across invoices — safe because phase 2 has already
    resolved every duplicate before any invoice reaches this point.

    should_stop(), if given, is checked once — right after approval
    finishes, before payment — and raises BatchStopped rather than
    proceeding. This is the one point in the pipeline with a real side
    effect (the mock payment call and the ledger write), so it's the one
    point that must never run once a stop has been requested.
    """
    state = dict(state)
    if on_stage:
        on_stage("approval")
    state.update(_approve_node(state))
    if should_stop and should_stop():
        raise BatchStopped()
    if on_stage:
        on_stage("payment")
    state.update(_pay_node(state) if _route_after_approval(state) == "pay" else _reject_node(state))
    return state


def recheck_with_progress(invoice, db_path: str, processed_at: str, on_stage=None) -> InvoiceState:
    """Re-runs validation -> approval -> payment against an already-extracted
    invoice, skipping ingestion entirely. Used when an approved edit request
    supplies a corrected field value in place of what was extracted from the
    raw file — there is nothing left to re-extract, so this starts one stage
    later than run_with_progress().
    """
    state: InvoiceState = {
        "file_path": invoice.source_file or "",
        "db_path": db_path,
        "processed_at": processed_at,
        "invoice": invoice,
        "validation": None,
        "decision": None,
        "payment": None,
    }
    if on_stage:
        on_stage("validation")
    state.update(_validate_node(state))
    if on_stage:
        on_stage("approval")
    state.update(_approve_node(state))
    if on_stage:
        on_stage("payment")
    state.update(_pay_node(state) if _route_after_approval(state) == "pay" else _reject_node(state))
    return state
