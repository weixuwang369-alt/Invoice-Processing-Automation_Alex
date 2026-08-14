"""Shared data contracts passed between agents."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    item: str
    quantity: float
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    note: Optional[str] = None


class ExtractedInvoice(BaseModel):
    """Normalized output of the ingestion stage, regardless of source format."""

    invoice_number: Optional[str] = None
    vendor: Optional[str] = None
    date: Optional[str] = None
    due_date: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    shipping: Optional[float] = None
    total: Optional[float] = None
    currency: str = "USD"
    payment_terms: Optional[str] = None
    notes: Optional[str] = None

    extraction_method: Literal["deterministic", "llm", "regex"] = "deterministic"
    extraction_warnings: list[str] = Field(default_factory=list)
    source_file: Optional[str] = None

    @property
    def amount(self) -> Optional[float]:
        """The single figure downstream stages reason about for thresholds."""
        return self.total if self.total is not None else self.subtotal


class ValidationIssue(BaseModel):
    severity: Literal["info", "warning", "critical"]
    code: str
    message: str


class ValidationResult(BaseModel):
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected", "escalated"]
    reasoning: str
    summary: str
    requires_scrutiny: bool = False
    critique: Optional[str] = None
    critique_overturned: bool = False
    # Set only when a person resolves an "escalated" decision (see
    # server.py's /api/escalations endpoints) -- distinguishes a human's
    # final call from the model's own reasoning in `reasoning`/`critique`,
    # which stay untouched as the original audit trail.
    escalation_resolution: Optional[str] = None


class PaymentResult(BaseModel):
    status: Literal["paid", "rejected", "skipped"]
    detail: str
    payment_response: Optional[dict] = None
