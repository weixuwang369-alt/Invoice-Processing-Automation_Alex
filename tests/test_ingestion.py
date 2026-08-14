import sqlite3

import pytest

from src import action_log
from src.ingestion.agent import IngestionAgent
from src.ingestion.normalize import normalize_item_name
from src.schemas import ExtractedInvoice, LineItem


@pytest.fixture(autouse=True)
def temp_action_log(monkeypatch):
    monkeypatch.setattr(action_log, "_log", [])


def test_normalize_exact_and_alias_and_fuzzy_matches(db_path):
    conn = sqlite3.connect(db_path)
    assert normalize_item_name("WidgetA", conn) == "WidgetA"
    assert normalize_item_name("Widget A", conn) == "WidgetA"
    assert normalize_item_name("Gadget X", conn) == "GadgetX"
    assert normalize_item_name("gadget-x", conn) == "GadgetX"
    conn.close()


def test_normalize_leaves_unknown_items_untouched(db_path):
    conn = sqlite3.connect(db_path)
    assert normalize_item_name("SuperGizmo", conn) == "SuperGizmo"
    conn.close()


def test_ingestion_routes_structured_formats_deterministically(db_path):
    agent = IngestionAgent(db_path)
    invoice = agent.run("data/invoices/invoice_1004.json")
    assert invoice.extraction_method == "deterministic"
    assert invoice.vendor == "Precision Parts Ltd."


def test_ingestion_routes_free_text_through_extractor(db_path, patch_llm):
    canned = ExtractedInvoice(vendor="Widgets Inc", invoice_number="INV-1001")
    patch_llm(lambda system, user, schema: canned)

    agent = IngestionAgent(db_path)
    invoice = agent.run("data/invoices/invoice_1001.txt")
    assert invoice.extraction_method == "llm"
    assert invoice.vendor == "Widgets Inc"


def test_free_text_extraction_logs_an_action_with_the_file_name_as_context(db_path, patch_llm):
    canned = ExtractedInvoice(vendor="Widgets Inc", invoice_number="INV-1001")
    patch_llm(lambda system, user, schema: canned)

    IngestionAgent(db_path).run("data/invoices/invoice_1001.txt")

    entries = action_log.list_all()
    assert len(entries) == 1
    assert entries[0]["purpose"] == "Ingestion extraction"
    assert entries[0]["context"] == "invoice_1001.txt"
    assert entries[0]["error"] is None


def test_structured_formats_do_not_call_the_llm_or_log_an_action(db_path):
    IngestionAgent(db_path).run("data/invoices/invoice_1004.json")
    assert action_log.list_all() == []


def test_ingestion_normalizes_messy_item_names_from_free_text(db_path, patch_llm):
    canned = ExtractedInvoice(
        vendor="Consolidated Materials Group",
        line_items=[
            LineItem(item="WidgetA", quantity=8, unit_price=250.0),
            LineItem(item="WidgetA (rush order)", quantity=4, unit_price=300.0),
        ],
    )
    patch_llm(lambda system, user, schema: canned)

    agent = IngestionAgent(db_path)
    invoice = agent.run("data/invoices/invoice_1010.txt")
    items = [li.item for li in invoice.line_items]
    assert "WidgetA (rush order)" not in items
    assert items.count("WidgetA") == 2  # base line + rush-order line, both normalized
