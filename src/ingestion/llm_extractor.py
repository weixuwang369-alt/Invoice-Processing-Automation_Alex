"""LLM-backed extraction for free-form text: the one ingestion path where an
LLM earns its cost. .txt invoices and PDF-extracted text in this dataset use
inconsistent labels (Vendor/Vndr/From), inconsistent item-line grammars, and
contain typos ("2O26", "$3,500.O0", "Distributers"). A JSON/CSV/XML parser
has nothing to key off of here; Grok reads it the way a human would.
"""

from __future__ import annotations

from src import action_log
from src.llm_client import LLMClient
from src.schemas import ExtractedInvoice

SYSTEM_PROMPT = """You extract structured invoice data from messy, unstructured text.
The source may contain typos, OCR artifacts (e.g. letter O for digit 0), inconsistent
labels, or be embedded in an email. Extract exactly what is present — do not invent
values. If a field is missing, omit it. For each line item, extract the item name as
written (you do not need to normalize it), quantity, and unit price if present."""


def extract_with_llm(text: str, context: str | None = None) -> ExtractedInvoice:
    client = LLMClient()  # constructed fresh so a key saved via the UI mid-session is picked up
    user_prompt = f"Extract the invoice fields from this text:\n\n{text}"
    try:
        result = client.complete_structured(system=SYSTEM_PROMPT, user=user_prompt, schema=ExtractedInvoice)
    except Exception as e:
        action_log.record(
            provider=client.provider, model=client.model, purpose="Ingestion extraction",
            context=context, user_prompt=user_prompt, error=str(e),
        )
        raise
    result.extraction_method = "llm"
    action_log.record(
        provider=client.provider, model=client.model, purpose="Ingestion extraction",
        context=context, user_prompt=user_prompt, result_summary=str(result),
    )
    return result
