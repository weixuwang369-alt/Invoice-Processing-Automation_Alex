"""Deterministic, non-LLM extraction for formats where the schema is already known.

JSON/XML/CSV invoices are unambiguous — a parser gets them right 100% of the
time, for free, in milliseconds. Spending an LLM call on them would be slower,
costlier, and strictly less reliable. Grok is reserved for the genuinely
ambiguous case: free-form text (.txt, and PDF text after extraction) where
labels, spelling, and layout vary invoice to invoice. See src/ingestion/agent.py
for the routing decision and eval/run_extraction_eval.py for the evidence.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET

from src.schemas import ExtractedInvoice, LineItem

_MONEY_RE = re.compile(r"[^0-9.\-]")


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = _MONEY_RE.sub("", str(value))
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def parse_json(text: str) -> ExtractedInvoice:
    data = json.loads(text)
    vendor = data.get("vendor")
    vendor_name = vendor.get("name") if isinstance(vendor, dict) else vendor

    line_items = [
        LineItem(
            item=li.get("item", ""),
            quantity=_to_float(li.get("quantity")) or 0,
            unit_price=_to_float(li.get("unit_price")),
            amount=_to_float(li.get("amount")),
            note=li.get("note"),
        )
        for li in data.get("line_items", [])
    ]

    return ExtractedInvoice(
        invoice_number=data.get("invoice_number"),
        vendor=vendor_name or None,
        date=data.get("date"),
        due_date=data.get("due_date"),
        line_items=line_items,
        subtotal=_to_float(data.get("subtotal")),
        tax_amount=_to_float(data.get("tax_amount")),
        total=_to_float(data.get("total")),
        currency=data.get("currency") or "USD",
        payment_terms=data.get("payment_terms") or None,
        notes=data.get("notes"),
        extraction_method="deterministic",
    )


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


def parse_xml(text: str) -> ExtractedInvoice:
    root = ET.fromstring(text)

    def find_text(elem, path):
        node = elem.find(path)
        return node.text.strip() if node is not None and node.text else None

    header = root.find("header")
    line_items = [
        LineItem(
            item=find_text(item, "name") or "",
            quantity=_to_float(find_text(item, "quantity")) or 0,
            unit_price=_to_float(find_text(item, "unit_price")),
        )
        for item in root.findall("line_items/item")
    ]
    totals = root.find("totals")

    return ExtractedInvoice(
        invoice_number=find_text(header, "invoice_number") if header is not None else None,
        vendor=find_text(header, "vendor") if header is not None else None,
        date=find_text(header, "date") if header is not None else None,
        due_date=find_text(header, "due_date") if header is not None else None,
        line_items=line_items,
        subtotal=_to_float(find_text(totals, "subtotal")) if totals is not None else None,
        tax_amount=_to_float(find_text(totals, "tax_amount")) if totals is not None else None,
        total=_to_float(find_text(totals, "total")) if totals is not None else None,
        currency=(find_text(header, "currency") if header is not None else None) or "USD",
        payment_terms=find_text(root, "payment_terms"),
        extraction_method="deterministic",
    )


# ---------------------------------------------------------------------------
# CSV — two observed flavors: "field,value" key-value dumps, and tabular
# one-row-per-line-item exports with trailing summary rows.
# ---------------------------------------------------------------------------


def parse_csv(text: str) -> ExtractedInvoice:
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]

    if fieldnames == ["field", "value"]:
        return _parse_csv_field_value(text)
    return _parse_csv_tabular(text)


def _parse_csv_field_value(text: str) -> ExtractedInvoice:
    reader = csv.DictReader(io.StringIO(text))
    top: dict[str, str] = {}
    line_items: list[LineItem] = []
    current: dict | None = None

    for row in reader:
        field = (row.get("field") or "").strip().lower()
        value = (row.get("value") or "").strip()
        if field == "item":
            if current:
                line_items.append(LineItem(**current))
            current = {"item": value, "quantity": 0, "unit_price": None}
        elif field == "quantity" and current is not None:
            current["quantity"] = _to_float(value) or 0
        elif field == "unit_price" and current is not None:
            current["unit_price"] = _to_float(value)
        else:
            top[field] = value

    if current:
        line_items.append(LineItem(**current))

    return ExtractedInvoice(
        invoice_number=top.get("invoice_number"),
        vendor=top.get("vendor"),
        date=top.get("date"),
        due_date=top.get("due_date"),
        line_items=line_items,
        subtotal=_to_float(top.get("subtotal")),
        tax_amount=_to_float(top.get("tax")),
        total=_to_float(top.get("total")),
        payment_terms=top.get("payment_terms") or None,
        extraction_method="deterministic",
    )


def _parse_csv_tabular(text: str) -> ExtractedInvoice:
    reader = csv.DictReader(io.StringIO(text))
    line_items: list[LineItem] = []
    header: dict[str, str] = {}
    summary: dict[str, float | None] = {}

    for row in reader:
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        item_name = norm.get("item", "")

        if item_name:
            if not header:
                header = norm
            line_items.append(
                LineItem(
                    item=item_name,
                    quantity=_to_float(norm.get("qty")) or 0,
                    unit_price=_to_float(norm.get("unit price")),
                    amount=_to_float(norm.get("line total")),
                )
            )
        else:
            # Trailing summary row: label lives wherever the last populated
            # column is, value in the one after it (see module docstring).
            values = list(norm.values())
            non_empty = [v for v in values if v]
            if len(non_empty) >= 2:
                label, value = non_empty[-2].lower(), non_empty[-1]
                if "subtotal" in label:
                    summary["subtotal"] = _to_float(value)
                elif "tax" in label:
                    summary["tax_amount"] = _to_float(value)
                elif "total" in label:
                    summary["total"] = _to_float(value)

    return ExtractedInvoice(
        invoice_number=header.get("invoice number"),
        vendor=header.get("vendor"),
        date=header.get("date"),
        due_date=header.get("due date"),
        line_items=line_items,
        subtotal=summary.get("subtotal"),
        tax_amount=summary.get("tax_amount"),
        total=summary.get("total"),
        extraction_method="deterministic",
    )


# ---------------------------------------------------------------------------
# PDF text extraction (local, no network, no OCR binary required — the
# sample PDFs are text-based, not scanned images; see SOLUTION.md for the
# documented extension path to add OCR for scanned invoices).
# ---------------------------------------------------------------------------


def extract_pdf_text(path: str) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


# ---------------------------------------------------------------------------
# Regex best-effort baseline — deliberately naive. Used only as the "why not
# just regex everything" comparison arm in eval/run_extraction_eval.py, not
# in the production ingestion path.
# ---------------------------------------------------------------------------

_PATTERNS = {
    "vendor": re.compile(r"(?:vendor|vndr|from)\s*:\s*(.+)", re.IGNORECASE),
    "invoice_number": re.compile(r"(?:invoice number|invoice|inv #|inv no)\s*:?\s*#?\s*([A-Z0-9\-]+)", re.IGNORECASE),
    "due_date": re.compile(r"(?:due date|due dt|due)\s*:\s*(.+)", re.IGNORECASE),
    "total": re.compile(r"(?:total amount|total|amt)\s*:\s*\$?([0-9,]+\.?[0-9]*)", re.IGNORECASE),
}
_ITEM_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 ]*?)\s+(?:qty:?\s*)?(\d+)\s*(?:@|qty:|x)?\s*\$?([0-9,]+\.?[0-9]*)",
    re.IGNORECASE,
)


def regex_extract(text: str) -> ExtractedInvoice:
    fields: dict[str, str] = {}
    for name, pattern in _PATTERNS.items():
        match = pattern.search(text)
        if match:
            fields[name] = match.group(1).strip().splitlines()[0]

    line_items: list[LineItem] = []
    for line in text.splitlines():
        match = _ITEM_LINE_RE.match(line)
        if match and match.group(1).strip().lower() not in ("invoice", "item", "description"):
            line_items.append(
                LineItem(
                    item=match.group(1).strip(),
                    quantity=_to_float(match.group(2)) or 0,
                    unit_price=_to_float(match.group(3)),
                )
            )

    return ExtractedInvoice(
        invoice_number=fields.get("invoice_number"),
        vendor=fields.get("vendor"),
        due_date=fields.get("due_date"),
        total=_to_float(fields.get("total")),
        line_items=line_items,
        extraction_method="regex",
        extraction_warnings=["Naive regex extraction — no semantic normalization."],
    )
