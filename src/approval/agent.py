"""Approval agent: simulates VP-level review with two LLM passes — a draft
decision, then a critique pass that re-examines it before it's final. This
is the self-correction loop the assignment asks for: the second call is
explicitly told to look for mistakes in the first, not just rubber-stamp it.

Policy the LLM is instructed to apply (see DRAFT_SYSTEM_PROMPT):
  1. Any unresolved critical validation issue -> reject.
  2. Any warning-level issue -> escalate to a human reviewer.
  3. Amount >= scrutiny threshold ($10K default) -> extra scrutiny before approval.
  4. Otherwise -> approve.

The displayed reasoning/critique text is not free-form model prose. The LLM
fills in a few short, separately-schema-enforced fields (see DraftDecision /
CritiqueResult), each with its own ASD-STE100 Simplified Technical English
writing instruction, and Python — not the model — assembles those fields
into the final labeled text via _format_reasoning / _format_critique. This
guarantees every invoice's explanation has the same fixed structure and
line order, whichever provider is configured; only the sentence content
inside each field comes from the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src import action_log
from src.config import load_settings
from src.llm_client import LLMClient
from src.schemas import ApprovalDecision, ExtractedInvoice, ValidationResult

_STE_INSTRUCTIONS = """Write every field in ASD-STE100 Simplified Technical English:
- Use short sentences. State one fact per sentence.
- Use active voice. Name the actor (for example, "The invoice has a critical issue.").
- Do not use contractions.
- Use simple, common words. Do not use jargon or idioms.
- Write amounts and issue codes in plain digits and plain terms."""

DRAFT_SYSTEM_PROMPT = f"""You are a VP-level financial reviewer at a manufacturing company
deciding whether to approve a vendor invoice for payment. Apply this policy:
1. Reject if there are unresolved critical validation issues (out-of-stock, insufficient
   stock, unknown items, arithmetic mismatches, duplicate payments).
2. Escalate to a human reviewer if any warning-level issue was found (e.g. non-USD
   currency, an unparseable due date) — these aren't automatic rejects, but shouldn't be
   silently auto-approved either.
3. Invoices of $10,000 or more require additional scrutiny before approval — read
   carefully and note that extra scrutiny was applied.
4. Otherwise approve.

{_STE_INSTRUCTIONS}

Fill in these three fields:
- policy_basis: One sentence. Name which policy rule applies and why.
- key_facts: One or two sentences. State the specific facts behind the decision — the
  issue codes found, the invoice amount, or the scrutiny threshold, as relevant.
- conclusion: One sentence. State the decision and the main reason for it."""

CRITIQUE_SYSTEM_PROMPT = f"""You are a second reviewer checking another approver's decision
for mistakes before it becomes final. Verify: did they correctly apply company policy? Did
they miss a validation issue? Is the reasoning consistent with the dollar amount and the
issues found? If you find a flaw, change the decision. If the original decision is sound,
agree with it.

{_STE_INSTRUCTIONS}

Fill in these two fields:
- policy_check: One sentence. State whether the draft applied the policy correctly.
- finding: One or two sentences. State what you found. If you changed the decision, state
  the reason."""


class DraftDecision(BaseModel):
    decision: Literal["approved", "rejected", "escalated"]
    policy_basis: str
    key_facts: str
    conclusion: str


class CritiqueResult(BaseModel):
    agrees: bool
    final_decision: Literal["approved", "rejected", "escalated"]
    policy_check: str
    finding: str


def _format_reasoning(draft: DraftDecision) -> str:
    """Assembles the draft's fields into a fixed, labeled structure. Python
    controls the layout so every invoice's reasoning reads the same way,
    regardless of what the model returns for each field's content."""
    return (
        f"Decision: {draft.decision}.\n"
        f"Policy: {draft.policy_basis}\n"
        f"Key facts: {draft.key_facts}\n"
        f"Result: {draft.conclusion}"
    )


def _format_critique(critique: CritiqueResult, contradictory: bool = False) -> str:
    if contradictory:
        review = (
            "The critique gave an inconsistent answer. It agreed with the draft, but it chose a "
            "different final decision. The application escalates this invoice for a person to check."
        )
    elif critique.agrees:
        review = "The critique agrees with the draft."
    else:
        review = "The critique changes the draft decision."
    return f"Review: {review}\nPolicy check: {critique.policy_check}\nFinding: {critique.finding}"


def _format_escalation_resolution(decision: Literal["approved", "rejected"]) -> str:
    """Deterministic text for ApprovalDecision.escalation_resolution -- no
    LLM call is involved in resolving an escalation, so this is plain
    Python, same as _format_reasoning/_format_critique. States the caveat
    explicitly (see server.py's /api/escalations endpoints and the
    Edit Approvals tab): in this demo the same user plays the approver."""
    return (
        f"A person reviewed this escalated invoice and {decision} it. "
        f"For this demo, the user makes this decision. In a real deployment, "
        f"the appropriate approver would decide."
    )


class ApprovalAgent:
    def __init__(self, threshold: float | None = None):
        self.threshold = threshold if threshold is not None else load_settings().approval_scrutiny_threshold

    def run(self, invoice: ExtractedInvoice, validation: ValidationResult) -> ApprovalDecision:
        requires_scrutiny = (invoice.amount or 0) >= self.threshold
        llm_client = LLMClient()  # fresh per run — see LLMClient docstring / config.py
        context = invoice.invoice_number or invoice.vendor or "unknown invoice"

        def call(purpose: str, system: str, user: str, schema):
            try:
                result = llm_client.complete_structured(system=system, user=user, schema=schema)
            except Exception as e:
                action_log.record(
                    provider=llm_client.provider, model=llm_client.model, purpose=purpose,
                    context=context, user_prompt=user, error=str(e),
                )
                raise
            action_log.record(
                provider=llm_client.provider, model=llm_client.model, purpose=purpose,
                context=context, user_prompt=user, result_summary=str(result),
            )
            return result

        draft = call("Approval draft", DRAFT_SYSTEM_PROMPT, self._describe(invoice, validation), DraftDecision)

        critique = call(
            "Approval critique",
            CRITIQUE_SYSTEM_PROMPT,
            self._describe_for_critique(invoice, validation, draft),
            CritiqueResult,
        )

        # The critique's own fields can disagree with each other — agrees=True
        # while final_decision differs from the draft it just said it agreed
        # with. Observed live, not hypothetical. Trusting final_decision
        # blindly in that case would silently pay or reject on an answer the
        # model itself didn't actually commit to, so escalate instead: never
        # act on a self-contradictory critique.
        contradictory = critique.agrees and critique.final_decision != draft.decision
        final_decision = "escalated" if contradictory else critique.final_decision
        overturned = final_decision != draft.decision

        return ApprovalDecision(
            decision=final_decision,
            reasoning=_format_reasoning(draft),
            summary=critique.finding if overturned else draft.conclusion,
            requires_scrutiny=requires_scrutiny,
            critique=_format_critique(critique, contradictory=contradictory),
            critique_overturned=overturned,
        )

    def _describe(self, invoice: ExtractedInvoice, validation: ValidationResult) -> str:
        issues = "\n".join(f"- [{i.severity}] {i.code}: {i.message}" for i in validation.issues) or "None"
        return (
            f"Vendor: {invoice.vendor}\nInvoice: {invoice.invoice_number}\n"
            f"Amount: {invoice.amount} {invoice.currency}\n"
            f"Validation passed: {validation.passed}\nValidation issues:\n{issues}"
        )

    def _describe_for_critique(
        self, invoice: ExtractedInvoice, validation: ValidationResult, draft: DraftDecision
    ) -> str:
        return (
            f"{self._describe(invoice, validation)}\n\n"
            f"Original decision: {draft.decision}\n"
            f"Original policy basis: {draft.policy_basis}\n"
            f"Original key facts: {draft.key_facts}\n"
            f"Original conclusion: {draft.conclusion}"
        )
