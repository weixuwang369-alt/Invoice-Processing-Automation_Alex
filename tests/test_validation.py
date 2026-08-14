import sqlite3

from src.schemas import ExtractedInvoice, LineItem
from src.validation.agent import ValidationAgent


def make_invoice(**overrides) -> ExtractedInvoice:
    defaults = dict(
        invoice_number="INV-TEST",
        vendor="Test Vendor",
        line_items=[LineItem(item="WidgetA", quantity=1, unit_price=250.0)],
        subtotal=250.0,
        tax_amount=0.0,
        total=250.0,
    )
    defaults.update(overrides)
    return ExtractedInvoice(**defaults)


def test_passes_with_no_issues(db_path):
    invoice = make_invoice()
    result = ValidationAgent(db_path).run(invoice)
    assert result.passed
    assert result.issues == []


def test_unknown_item_is_critical(db_path):
    invoice = make_invoice(line_items=[LineItem(item="SuperGizmo", quantity=1)])
    result = ValidationAgent(db_path).run(invoice)
    assert not result.passed
    assert any(i.code == "unknown_item" for i in result.issues)


def test_zero_stock_item_flagged_out_of_stock(db_path):
    invoice = make_invoice(line_items=[LineItem(item="FakeItem", quantity=1)])
    result = ValidationAgent(db_path).run(invoice)
    assert any(i.code == "out_of_stock" for i in result.issues)


def test_negative_quantity_is_critical(db_path):
    invoice = make_invoice(line_items=[LineItem(item="WidgetA", quantity=-5)])
    result = ValidationAgent(db_path).run(invoice)
    assert any(i.code == "invalid_quantity" for i in result.issues)


def test_stock_check_aggregates_across_split_line_items(db_path):
    # WidgetA stock is 15. Three lines summing to 22 must fail even though
    # each individual line (15, 5, 2) is below stock on its own.
    invoice = make_invoice(
        line_items=[
            LineItem(item="WidgetA", quantity=15, unit_price=250.0),
            LineItem(item="WidgetA", quantity=5, unit_price=240.0),
            LineItem(item="WidgetA", quantity=2, unit_price=250.0),
        ]
    )
    result = ValidationAgent(db_path).run(invoice)
    assert any(i.code == "insufficient_stock" for i in result.issues)


def test_per_line_quantities_within_stock_alone_do_not_false_positive(db_path):
    invoice = make_invoice(
        line_items=[
            LineItem(item="WidgetA", quantity=5, unit_price=250.0),
            LineItem(item="WidgetB", quantity=3, unit_price=500.0),
        ],
        subtotal=2750.0,
        total=2750.0,
    )
    result = ValidationAgent(db_path).run(invoice)
    assert result.passed


def test_arithmetic_mismatch_detected(db_path):
    invoice = make_invoice(subtotal=1000.0, tax_amount=0.0, total=-250.0)
    result = ValidationAgent(db_path).run(invoice)
    assert any(i.code == "arithmetic_mismatch" for i in result.issues)


def test_shipping_included_in_arithmetic_check(db_path):
    invoice = make_invoice(subtotal=6700.0, tax_amount=335.0, shipping=150.0, total=7185.0)
    result = ValidationAgent(db_path).run(invoice)
    assert not any(i.code == "arithmetic_mismatch" for i in result.issues)


def test_non_usd_currency_is_a_warning_not_critical(db_path):
    invoice = make_invoice(currency="EUR")
    result = ValidationAgent(db_path).run(invoice)
    assert result.passed  # warnings don't fail validation
    assert any(i.code == "non_usd_currency" and i.severity == "warning" for i in result.issues)


def test_unparseable_due_date_flagged(db_path):
    invoice = make_invoice(due_date="yesterday")
    result = ValidationAgent(db_path).run(invoice)
    assert any(i.code == "unparseable_due_date" for i in result.issues)


def test_duplicate_invoice_blocked_after_paid(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO processed_invoices VALUES (?, ?, ?, 'paid', ?)",
        ("INV-DUP", "Test Vendor", 1890.0, "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    invoice = make_invoice(invoice_number="INV-DUP")
    result = ValidationAgent(db_path).run(invoice)
    assert not result.passed
    assert any(i.code == "duplicate_invoice" for i in result.issues)


def test_no_seen_in_batch_arg_is_unaffected(db_path):
    # Protects the CLI/single-invoice/recheck paths, none of which pass
    # seen_in_batch — omitting it must behave exactly like passing None.
    invoice = make_invoice()
    assert ValidationAgent(db_path).run(invoice) == ValidationAgent(db_path).run(invoice, seen_in_batch=None)


def test_first_occurrence_in_batch_passes(db_path):
    invoice = make_invoice(invoice_number="INV-BATCH")
    result = ValidationAgent(db_path).run(invoice, seen_in_batch=set())
    assert result.passed


def test_second_occurrence_in_batch_flagged_as_duplicate(db_path):
    invoice = make_invoice(invoice_number="INV-BATCH")
    result = ValidationAgent(db_path).run(invoice, seen_in_batch={"INV-BATCH"})
    assert not result.passed
    issue = next(i for i in result.issues if i.code == "duplicate_invoice")
    assert "more than once in this batch" in issue.message


def test_already_paid_takes_priority_over_seen_in_batch(db_path):
    # When both apply, the ledger's "already paid on {date}" message is more
    # actionable than the generic batch-duplicate message.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO processed_invoices VALUES (?, ?, ?, 'paid', ?)",
        ("INV-DUP", "Test Vendor", 1890.0, "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    invoice = make_invoice(invoice_number="INV-DUP")
    result = ValidationAgent(db_path).run(invoice, seen_in_batch={"INV-DUP"})
    issue = next(i for i in result.issues if i.code == "duplicate_invoice")
    assert "already paid" in issue.message
