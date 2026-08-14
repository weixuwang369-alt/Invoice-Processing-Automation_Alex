"""Payment agent: executes the mock payment API on approval, or logs the
rejection with its reasoning. Either way, writes to the processed_invoices
ledger so a later duplicate submission of the same invoice number is caught
by the validation agent (see INV-1004 vs INV-1004_revised).
"""

from __future__ import annotations

import sqlite3
import threading

from src.schemas import ApprovalDecision, ExtractedInvoice, PaymentResult

# Batch processing runs payment for several invoices concurrently (see
# server.py's _process_batch phase 3). Serializing the ledger write keeps
# the UPSERT's read-then-write correct under that concurrency; the LLM
# calls that dominate each invoice's runtime happen outside this lock.
_ledger_lock = threading.Lock()


def mock_payment(vendor: str, amount: float) -> dict:
    """The mock banking API from the assignment brief, unmodified."""
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


class PaymentAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self, invoice: ExtractedInvoice, decision: ApprovalDecision, processed_at: str) -> PaymentResult:
        """Convenience entry point that dispatches to pay() or log_rejection()."""
        if decision.decision == "approved":
            return self.pay(invoice, decision, processed_at)
        return self.log_rejection(invoice, decision, processed_at)

    def pay(self, invoice: ExtractedInvoice, decision: ApprovalDecision, processed_at: str) -> PaymentResult:
        vendor = invoice.vendor or "Unknown vendor"
        amount = invoice.amount or 0
        response = mock_payment(vendor, amount)
        result = PaymentResult(
            status="paid",
            detail=f"The application paid {vendor} {amount} {invoice.currency}.",
            payment_response=response,
        )
        self._log_ledger(invoice, decision, result, processed_at)
        return result

    def log_rejection(self, invoice: ExtractedInvoice, decision: ApprovalDecision, processed_at: str) -> PaymentResult:
        # escalation_resolution, when set, is the actual final determinant (a
        # person's call) -- prefer it over the model's original summary, which
        # only explains why the decision was escalated in the first place.
        reason = decision.escalation_resolution or decision.summary
        result = PaymentResult(
            status="skipped" if decision.decision == "escalated" else "rejected",
            detail=f"The application withheld payment. Decision: {decision.decision}. Reason: {reason}",
        )
        self._log_ledger(invoice, decision, result, processed_at)
        return result

    def _log_ledger(
        self,
        invoice: ExtractedInvoice,
        decision: ApprovalDecision,
        result: PaymentResult,
        processed_at: str,
    ) -> None:
        if not invoice.invoice_number:
            return
        with _ledger_lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT INTO processed_invoices (invoice_number, vendor, total, status, processed_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(invoice_number) DO UPDATE SET
                         vendor=excluded.vendor, total=excluded.total,
                         status=excluded.status, processed_at=excluded.processed_at
                       WHERE processed_invoices.status != 'paid'""",
                    (invoice.invoice_number, invoice.vendor, invoice.amount, result.status, processed_at),
                )
                conn.commit()
            finally:
                conn.close()
