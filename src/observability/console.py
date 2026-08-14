"""Rich-formatted CLI output — per-invoice detail panels and a batch summary
table shaped like the scenario table in the assignment README, so `--all`
doubles as a live demo of the system's judgment across every sample.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.orchestration.state import InvoiceState

console = Console()

_DECISION_STYLE = {
    "approved": "bold green",
    "escalated": "bold yellow",
    "rejected": "bold red",
}
_PAYMENT_STYLE = {
    "paid": "bold green",
    "skipped": "bold yellow",
    "rejected": "bold red",
}
_SEVERITY_STYLE = {"critical": "bold red", "warning": "yellow", "info": "cyan"}


def render_invoice_result(state: InvoiceState) -> None:
    invoice = state["invoice"]
    validation = state["validation"]
    decision = state["decision"]
    payment = state["payment"]

    body = Text()
    body.append(f"Vendor:       {invoice.vendor or '—'}\n")
    body.append(f"Invoice #:    {invoice.invoice_number or '—'}\n")
    body.append(f"Amount:       {invoice.amount if invoice.amount is not None else '—'} {invoice.currency}\n")
    body.append(f"Extraction:   {invoice.extraction_method}\n\n")

    body.append("Validation:   ")
    body.append("PASSED\n" if validation.passed else "FLAGGED\n", style="bold green" if validation.passed else "bold red")
    for issue in validation.issues:
        body.append(f"  [{issue.severity}] {issue.code}: {issue.message}\n", style=_SEVERITY_STYLE[issue.severity])

    body.append("\nApproval:     ")
    body.append(f"{decision.decision.upper()}\n", style=_DECISION_STYLE[decision.decision])
    body.append(f"  Reasoning: {decision.reasoning}\n")
    if decision.critique_overturned:
        body.append(f"  Critique overturned draft: {decision.critique}\n", style="bold magenta")
    else:
        body.append(f"  Critique: {decision.critique}\n", style="dim")

    body.append("\nPayment:      ")
    body.append(f"{payment.status.upper()}\n", style=_PAYMENT_STYLE[payment.status])
    body.append(f"  {payment.detail}")

    console.print(Panel(body, title=f"[bold]{state['file_path']}[/bold]", border_style="blue"))


def render_summary_table(states: list[InvoiceState]) -> None:
    table = Table(title="Invoice Processing Summary")
    table.add_column("Invoice")
    table.add_column("Vendor")
    table.add_column("Amount", justify="right")
    table.add_column("Validation")
    table.add_column("Decision")
    table.add_column("Payment")

    for state in states:
        invoice, validation, decision, payment = (
            state["invoice"],
            state["validation"],
            state["decision"],
            state["payment"],
        )
        table.add_row(
            invoice.invoice_number or state["file_path"],
            invoice.vendor or "—",
            f"{invoice.amount:,.2f}" if invoice.amount is not None else "—",
            Text("passed", style="green") if validation.passed else Text(f"{len(validation.issues)} issue(s)", style="red"),
            Text(decision.decision, style=_DECISION_STYLE[decision.decision]),
            Text(payment.status, style=_PAYMENT_STYLE[payment.status]),
        )

    console.print(table)


def render_error(file_path: str, error: Exception) -> None:
    console.print(Panel(f"[bold red]{error}[/bold red]", title=f"[bold]{file_path}[/bold] — FAILED", border_style="red"))
