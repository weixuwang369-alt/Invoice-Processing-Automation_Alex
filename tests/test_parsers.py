from src.ingestion.parsers import extract_pdf_text, parse_csv, parse_json, parse_xml


def test_parse_json_nested_vendor():
    inv = parse_json(open("data/invoices/invoice_1004.json").read())
    assert inv.vendor == "Precision Parts Ltd."
    assert inv.invoice_number == "INV-1004"
    assert inv.total == 1890.00
    assert [(li.item, li.quantity) for li in inv.line_items] == [("WidgetA", 3), ("WidgetB", 2)]


def test_parse_json_negative_quantity_preserved():
    inv = parse_json(open("data/invoices/invoice_1009.json").read())
    assert inv.line_items[0].quantity == -5
    assert inv.total == -250.00


def test_parse_xml():
    inv = parse_xml(open("data/invoices/invoice_1014.xml").read())
    assert inv.vendor == "TechParts International"
    assert inv.currency == "EUR"
    assert inv.total == 4125.00
    assert len(inv.line_items) == 2


def test_parse_csv_field_value_flavor():
    inv = parse_csv(open("data/invoices/invoice_1006.csv").read())
    assert inv.vendor == "Acme Industrial Supplies"
    assert inv.total == 2750.00
    assert [(li.item, li.quantity, li.unit_price) for li in inv.line_items] == [
        ("WidgetA", 5, 250.00),
        ("WidgetB", 3, 500.00),
    ]


def test_parse_csv_tabular_flavor_with_summary_rows():
    inv = parse_csv(open("data/invoices/invoice_1007.csv").read())
    assert inv.subtotal == 14750.00
    assert inv.tax_amount == 885.00
    assert inv.total == 15525.00
    assert len(inv.line_items) == 3


def test_extract_pdf_text_roundtrips_known_content():
    text = extract_pdf_text("data/invoices/invoice_1011.pdf")
    assert "Summit Manufacturing Co." in text
    assert "INV-1011" in text
