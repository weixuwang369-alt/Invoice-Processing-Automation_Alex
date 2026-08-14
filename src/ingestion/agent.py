"""Ingestion agent: routes each invoice to the cheapest extraction method
that can handle its format correctly, then normalizes item names uniformly.

Routing rule: if the format carries its own schema (JSON/XML/CSV), parse it
deterministically — zero ambiguity, zero LLM cost, 100% reliable. Only when
the input is free-form (.txt, or text pulled out of a PDF) does the agent
call Grok, because that's the only path where "understand what a human
sloppily typed" is actually required. This routing decision is what
eval/run_extraction_eval.py measures and justifies.
"""

from __future__ import annotations

import os
import sqlite3

from src.config import load_settings
from src.ingestion.llm_extractor import extract_with_llm
from src.ingestion.normalize import normalize_item_name
from src.ingestion.parsers import extract_pdf_text, parse_csv, parse_json, parse_xml
from src.schemas import ExtractedInvoice

_DETERMINISTIC_PARSERS = {
    ".json": parse_json,
    ".xml": parse_xml,
    ".csv": parse_csv,
}


class IngestionAgent:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or load_settings().inventory_db_path

    def run(self, file_path: str) -> ExtractedInvoice:
        ext = os.path.splitext(file_path)[1].lower()

        if ext in _DETERMINISTIC_PARSERS:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            invoice = _DETERMINISTIC_PARSERS[ext](text)
        elif ext == ".pdf":
            text = extract_pdf_text(file_path)
            invoice = extract_with_llm(text, context=os.path.basename(file_path))
        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            invoice = extract_with_llm(text, context=os.path.basename(file_path))
        else:
            raise ValueError(f"Unsupported invoice format: {ext}")

        invoice.source_file = file_path
        self._normalize_items(invoice)
        return invoice

    def _normalize_items(self, invoice: ExtractedInvoice) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            for line_item in invoice.line_items:
                line_item.item = normalize_item_name(line_item.item, conn)
        finally:
            conn.close()
