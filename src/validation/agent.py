"""Validation agent: checks an extracted invoice against the inventory
database and the invoice's own arithmetic. No LLM call — this is a
deterministic business-rules stage, which also means it's fully testable
without mocking anything.

Checks, in order:
  1. Unknown items (not in inventory after normalization)
  2. Non-positive / malformed quantities
  3. Stock sufficiency — quantities are aggregated per item first, so an
     item split across several line items (see INV-1013: WidgetA appears in
     3 lines totalling 22 against 15 in stock) is checked against its true
     combined demand, not line-by-line.
  4. Arithmetic consistency — subtotal + tax == total. Catches both a
     deliberately wrong PDF total (INV-1013's PDF rendering) and inputs
     where extraction picked up an internally-inconsistent total (INV-1009).
  5. Unparseable due date (e.g. "yesterday") — data integrity / suspicious.
  6. Non-USD currency — flagged for manual FX review, not auto-rejected.
  7. Duplicate invoice number already present in the processed_invoices
     ledger, or already seen earlier in the same batch (see INV-1004 vs
     INV-1004_revised, which share an invoice number) — guards against
     paying the same invoice twice.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

from src.schemas import ExtractedInvoice, ValidationIssue, ValidationResult

_AMOUNT_TOLERANCE = 0.02


def _try_parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%B %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


class ValidationAgent:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self, invoice: ExtractedInvoice, seen_in_batch: set[str] | None = None) -> ValidationResult:
        conn = sqlite3.connect(self.db_path)
        try:
            issues: list[ValidationIssue] = []
            issues += self._check_items(invoice, conn)
            issues += self._check_arithmetic(invoice)
            issues += self._check_due_date(invoice)
            issues += self._check_currency(invoice)
            issues += self._check_duplicate(invoice, conn, seen_in_batch)
        finally:
            conn.close()

        passed = not any(i.severity == "critical" for i in issues)
        return ValidationResult(passed=passed, issues=issues)

    def _check_items(self, invoice: ExtractedInvoice, conn: sqlite3.Connection) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        demand: dict[str, float] = defaultdict(float)

        for li in invoice.line_items:
            if li.quantity is None or li.quantity <= 0:
                issues.append(
                    ValidationIssue(
                        severity="critical",
                        code="invalid_quantity",
                        message=f"The quantity for '{li.item}' is not valid. The quantity is {li.quantity}.",
                    )
                )
                continue
            demand[li.item] += li.quantity

        for item, requested in demand.items():
            row = conn.execute("SELECT stock FROM inventory WHERE item = ?", (item,)).fetchone()
            if row is None:
                issues.append(
                    ValidationIssue(
                        severity="critical",
                        code="unknown_item",
                        message=f"The item '{item}' is not in the inventory database.",
                    )
                )
                continue

            stock = row[0]
            if stock == 0:
                issues.append(
                    ValidationIssue(
                        severity="critical",
                        code="out_of_stock",
                        message=f"The item '{item}' has zero stock. The invoice requests {requested}.",
                    )
                )
            elif requested > stock:
                issues.append(
                    ValidationIssue(
                        severity="critical",
                        code="insufficient_stock",
                        message=f"The item '{item}' does not have enough stock. The invoice requests {requested}. The stock is {stock}.",
                    )
                )

        return issues

    def _check_arithmetic(self, invoice: ExtractedInvoice) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if invoice.subtotal is not None and invoice.tax_amount is not None and invoice.total is not None:
            shipping = invoice.shipping or 0
            expected = invoice.subtotal + invoice.tax_amount + shipping
            if abs(expected - invoice.total) > _AMOUNT_TOLERANCE:
                if shipping:
                    breakdown = (
                        f"The subtotal ({invoice.subtotal}) plus the tax ({invoice.tax_amount}) plus "
                        f"the shipping ({shipping}) equals {expected}."
                    )
                else:
                    breakdown = f"The subtotal ({invoice.subtotal}) plus the tax ({invoice.tax_amount}) equals {expected}."
                issues.append(
                    ValidationIssue(
                        severity="critical",
                        code="arithmetic_mismatch",
                        message=f"{breakdown} The invoice total is {invoice.total}. These values do not match.",
                    )
                )
        return issues

    def _check_due_date(self, invoice: ExtractedInvoice) -> list[ValidationIssue]:
        if invoice.due_date and _try_parse_date(invoice.due_date) is None:
            return [
                ValidationIssue(
                    severity="warning",
                    code="unparseable_due_date",
                    message=f"The due date '{invoice.due_date}' is not a valid date.",
                )
            ]
        return []

    def _check_currency(self, invoice: ExtractedInvoice) -> list[ValidationIssue]:
        if invoice.currency and invoice.currency.upper() != "USD":
            return [
                ValidationIssue(
                    severity="warning",
                    code="non_usd_currency",
                    message=f"The invoice currency is {invoice.currency}, not USD. This invoice needs a currency review.",
                )
            ]
        return []

    def _check_duplicate(
        self,
        invoice: ExtractedInvoice,
        conn: sqlite3.Connection,
        seen_in_batch: set[str] | None = None,
    ) -> list[ValidationIssue]:
        if not invoice.invoice_number:
            return []
        row = conn.execute(
            "SELECT status, processed_at FROM processed_invoices WHERE invoice_number = ?",
            (invoice.invoice_number,),
        ).fetchone()
        if row and row[0] == "paid":
            return [
                ValidationIssue(
                    severity="critical",
                    code="duplicate_invoice",
                    message=f"Invoice {invoice.invoice_number} is already paid. The payment date is {row[1]}.",
                )
            ]
        if seen_in_batch and invoice.invoice_number in seen_in_batch:
            return [
                ValidationIssue(
                    severity="critical",
                    code="duplicate_invoice",
                    message=f"Invoice {invoice.invoice_number} appears more than once in this batch.",
                )
            ]
        return []
