import sqlite3

from src.approval.agent import CritiqueResult, DraftDecision
from src.orchestration.graph import BatchStopped, approve_and_pay_with_progress, recheck_with_progress, run_with_progress
from src.schemas import ExtractedInvoice, ValidationResult


def approving_responder(system, user, schema):
    if schema is DraftDecision:
        return DraftDecision(
            decision="approved", policy_basis="Rule 4 applies.", key_facts="No issues were found.", conclusion="The invoice is approved."
        )
    return CritiqueResult(agrees=True, final_decision="approved", policy_check="Correct.", finding="No issues.")


def test_run_with_progress_reports_all_four_stages_in_order(db_path, patch_llm):
    patch_llm(approving_responder)
    stages = []

    state = run_with_progress("data/invoices/invoice_1004.json", db_path, "2026-01-01T00:00:00Z", on_stage=stages.append)

    assert stages == ["ingestion", "validation", "approval", "payment"]
    assert state["invoice"] is not None
    assert state["validation"] is not None
    assert state["decision"] is not None
    assert state["payment"] is not None


def test_recheck_with_progress_skips_ingestion(db_path, patch_llm):
    patch_llm(approving_responder)
    stages = []
    invoice = ExtractedInvoice(invoice_number="INV-RECHECK", vendor="Test Vendor", total=100.0, source_file="data/invoices/invoice_1004.json")

    state = recheck_with_progress(invoice, db_path, "2026-01-01T00:00:00Z", on_stage=stages.append)

    assert stages == ["validation", "approval", "payment"]
    assert state["invoice"] is invoice
    assert state["validation"] is not None
    assert state["decision"] is not None
    assert state["payment"] is not None


def make_validated_state(db_path):
    invoice = ExtractedInvoice(invoice_number="INV-STOP-TEST", vendor="Test Vendor", total=100.0)
    return {
        "file_path": "data/invoices/invoice_1004.json",
        "db_path": db_path,
        "processed_at": "2026-01-01T00:00:00Z",
        "invoice": invoice,
        "validation": ValidationResult(passed=True, issues=[]),
        "decision": None,
        "payment": None,
    }


def test_approve_and_pay_with_progress_stops_before_payment_when_requested(db_path, patch_llm):
    # A batch Stop click can land between approval finishing and payment
    # starting — see server.py's _process_batch and BatchStopped's
    # docstring. This must never reach PaymentAgent: no mock_payment call,
    # no ledger write.
    patch_llm(approving_responder)
    state = make_validated_state(db_path)

    try:
        approve_and_pay_with_progress(state, should_stop=lambda: True)
        assert False, "expected BatchStopped to be raised"
    except BatchStopped:
        pass

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT * FROM processed_invoices WHERE invoice_number = ?", ("INV-STOP-TEST",)
    ).fetchone()
    conn.close()
    assert row is None, "a stopped invoice must never reach the ledger"


def test_approve_and_pay_with_progress_completes_normally_without_should_stop(db_path, patch_llm):
    patch_llm(approving_responder)
    state = make_validated_state(db_path)

    result = approve_and_pay_with_progress(state, should_stop=lambda: False)

    assert result["payment"] is not None
    assert result["payment"].status == "paid"
