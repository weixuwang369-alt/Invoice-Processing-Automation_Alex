from __future__ import annotations

from typing import Optional, TypedDict

from src.schemas import ApprovalDecision, ExtractedInvoice, PaymentResult, ValidationResult


class InvoiceState(TypedDict):
    file_path: str
    db_path: str
    processed_at: str
    invoice: Optional[ExtractedInvoice]
    validation: Optional[ValidationResult]
    decision: Optional[ApprovalDecision]
    payment: Optional[PaymentResult]
