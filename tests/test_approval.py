import pytest

from src import action_log
from src.approval.agent import (
    ApprovalAgent,
    CritiqueResult,
    DraftDecision,
    _format_critique,
    _format_escalation_resolution,
    _format_reasoning,
)
from src.schemas import ExtractedInvoice, ValidationIssue, ValidationResult


@pytest.fixture(autouse=True)
def temp_action_log(monkeypatch):
    monkeypatch.setattr(action_log, "_log", [])


def make_invoice(total=5000.0) -> ExtractedInvoice:
    return ExtractedInvoice(invoice_number="INV-TEST", vendor="Test Vendor", total=total)


def passing_validation() -> ValidationResult:
    return ValidationResult(passed=True, issues=[])


def failing_validation() -> ValidationResult:
    return ValidationResult(
        passed=False,
        issues=[ValidationIssue(severity="critical", code="unknown_item", message="'Foo' not found.")],
    )


def responder(draft: DraftDecision, critique: CritiqueResult):
    def _respond(system, user, schema):
        return draft if schema is DraftDecision else critique
    return _respond


def test_agrees_with_draft_when_critique_matches(patch_llm):
    draft = DraftDecision(decision="approved", policy_basis="Rule 4 applies.", key_facts="No issues were found.", conclusion="The invoice looks fine.")
    critique = CritiqueResult(agrees=True, final_decision="approved", policy_check="The draft applied the policy correctly.", finding="The finding is consistent.")
    patch_llm(responder(draft, critique))

    decision = ApprovalAgent(threshold=10000).run(make_invoice(), passing_validation())
    assert decision.decision == "approved"
    assert "The invoice looks fine." in decision.reasoning
    assert decision.summary == "The invoice looks fine."
    assert decision.critique_overturned is False


def test_critique_can_overturn_the_draft(patch_llm):
    draft = DraftDecision(decision="approved", policy_basis="Rule 4 applies.", key_facts="The draft missed the issue.", conclusion="The invoice looks fine.")
    critique = CritiqueResult(agrees=False, final_decision="rejected", policy_check="The draft did not apply the policy correctly.", finding="The draft ignored a critical issue.")
    patch_llm(responder(draft, critique))

    decision = ApprovalAgent(threshold=10000).run(make_invoice(), failing_validation())
    assert decision.decision == "rejected"
    assert decision.critique_overturned is True
    assert "ignored" in decision.critique
    assert decision.summary == "The draft ignored a critical issue."  # summary follows the critique once overturned


def test_run_logs_a_draft_and_a_critique_action(patch_llm):
    draft = DraftDecision(decision="approved", policy_basis="Rule 4 applies.", key_facts="No issues were found.", conclusion="The invoice looks fine.")
    critique = CritiqueResult(agrees=True, final_decision="approved", policy_check="Correct.", finding="No issues.")
    patch_llm(responder(draft, critique))

    ApprovalAgent(threshold=10000).run(make_invoice(), passing_validation())

    entries = action_log.list_all()
    assert len(entries) == 2
    purposes = {e["purpose"] for e in entries}
    assert purposes == {"Approval draft", "Approval critique"}
    assert all(e["context"] == "INV-TEST" for e in entries)
    assert all(e["error"] is None for e in entries)


def test_requires_scrutiny_flag_set_above_threshold(patch_llm):
    draft = DraftDecision(decision="approved", policy_basis="Rule 3 applies.", key_facts="The amount is large but clean.", conclusion="The invoice is approved.")
    critique = CritiqueResult(agrees=True, final_decision="approved", policy_check="Correct.", finding="No issues.")
    patch_llm(responder(draft, critique))

    decision = ApprovalAgent(threshold=10000).run(make_invoice(total=15000.0), passing_validation())
    assert decision.requires_scrutiny is True


def test_requires_scrutiny_flag_unset_below_threshold(patch_llm):
    draft = DraftDecision(decision="approved", policy_basis="Rule 4 applies.", key_facts="The amount is small.", conclusion="The invoice is approved.")
    critique = CritiqueResult(agrees=True, final_decision="approved", policy_check="Correct.", finding="No issues.")
    patch_llm(responder(draft, critique))

    decision = ApprovalAgent(threshold=10000).run(make_invoice(total=500.0), passing_validation())
    assert decision.requires_scrutiny is False


def test_format_reasoning_has_a_fixed_labeled_structure():
    draft = DraftDecision(decision="rejected", policy_basis="Rule 1 applies.", key_facts="Stock is short by 7 units.", conclusion="The invoice is rejected.")
    text = _format_reasoning(draft)
    assert text == (
        "Decision: rejected.\n"
        "Policy: Rule 1 applies.\n"
        "Key facts: Stock is short by 7 units.\n"
        "Result: The invoice is rejected."
    )


def test_format_reasoning_is_reproducible_for_identical_input():
    draft = DraftDecision(decision="approved", policy_basis="Rule 4 applies.", key_facts="No issues were found.", conclusion="The invoice is approved.")
    assert _format_reasoning(draft) == _format_reasoning(draft)


def test_format_critique_states_whether_it_agreed():
    agreeing = CritiqueResult(agrees=True, final_decision="approved", policy_check="Correct.", finding="No issues.")
    disagreeing = CritiqueResult(agrees=False, final_decision="rejected", policy_check="Incorrect.", finding="The draft missed a critical issue.")
    assert "agrees with the draft" in _format_critique(agreeing)
    assert "changes the draft decision" in _format_critique(disagreeing)


def test_format_critique_flags_a_contradictory_response():
    contradictory = CritiqueResult(agrees=True, final_decision="approved", policy_check="Correct.", finding="The rejection is correct.")
    text = _format_critique(contradictory, contradictory=True)
    assert "inconsistent" in text
    assert "escalates" in text


def test_contradictory_critique_escalates_instead_of_trusting_final_decision(patch_llm):
    # agrees=True but final_decision disagrees with the draft it just said it
    # agreed with — a live inconsistency, not a hypothetical (see SOLUTION.md).
    draft = DraftDecision(decision="rejected", policy_basis="Rule 1 applies.", key_facts="Stock is short by 7 units.", conclusion="The invoice is rejected.")
    critique = CritiqueResult(agrees=True, final_decision="approved", policy_check="The draft applied the policy correctly.", finding="The rejection decision is correct.")
    patch_llm(responder(draft, critique))

    decision = ApprovalAgent(threshold=10000).run(make_invoice(), failing_validation())
    assert decision.decision == "escalated"
    assert decision.critique_overturned is True
    assert "inconsistent" in decision.critique


def test_non_contradictory_disagreement_still_trusts_final_decision(patch_llm):
    # A critique that openly disagrees (agrees=False) and overturns the draft
    # is the normal self-correction path, not the contradiction guardrail —
    # this must not also escalate.
    draft = DraftDecision(decision="approved", policy_basis="Rule 4 applies.", key_facts="The draft missed the issue.", conclusion="The invoice looks fine.")
    critique = CritiqueResult(agrees=False, final_decision="rejected", policy_check="The draft did not apply the policy correctly.", finding="The draft ignored a critical issue.")
    patch_llm(responder(draft, critique))

    decision = ApprovalAgent(threshold=10000).run(make_invoice(), failing_validation())
    assert decision.decision == "rejected"
    assert decision.critique_overturned is True


def test_format_escalation_resolution_states_the_decision_and_the_caveat():
    approved_text = _format_escalation_resolution("approved")
    rejected_text = _format_escalation_resolution("rejected")
    assert "approved it" in approved_text
    assert "rejected it" in rejected_text
    for text in (approved_text, rejected_text):
        assert "the user makes this decision" in text
        assert "a real deployment" in text
