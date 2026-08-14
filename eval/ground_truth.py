"""Hand-labeled expected fields for every sample invoice, read directly from
data/invoices/*. Used by run_extraction_eval.py to score each extraction
arm. Line items are the values as stated in the source (pre-normalization);
the scorer normalizes both sides before comparing so alias variants like
"Widget A" score correctly against "WidgetA".
"""

from __future__ import annotations

GROUND_TRUTH: dict[str, dict] = {
    "invoice_1001.txt": {
        "vendor": "Widgets Inc.",
        "invoice_number": "INV-1001",
        "due_date": "2026-02-01",
        "total": 5000.00,
        "line_items": [("WidgetA", 10), ("WidgetB", 5)],
    },
    "invoice_1002.txt": {
        "vendor": "Gadgets Co.",
        "invoice_number": "INV-1002",
        "due_date": "2026-01-30",
        "total": 15000.00,
        "line_items": [("GadgetX", 20)],
    },
    "invoice_1003.txt": {
        "vendor": "Fraudster LLC",
        "invoice_number": "INV-1003",
        "due_date": None,  # "yesterday" — intentionally unparseable
        "total": 100000.00,
        "line_items": [("FakeItem", 100)],
    },
    "invoice_1004.json": {
        "vendor": "Precision Parts Ltd.",
        "invoice_number": "INV-1004",
        "due_date": "2026-02-22",
        "total": 1890.00,
        "line_items": [("WidgetA", 3), ("WidgetB", 2)],
    },
    "invoice_1004_revised.json": {
        "vendor": "Precision Parts Ltd.",
        "invoice_number": "INV-1004",
        "due_date": "2026-02-22",
        "total": 5940.00,
        "line_items": [("WidgetA", 3), ("WidgetB", 2), ("GadgetX", 5)],
    },
    "invoice_1005.json": {
        "vendor": "Global Supply Chain Partners",
        "invoice_number": "INV-1005",
        "due_date": "2026-03-18",
        "total": 15225.00,
        "line_items": [("WidgetA", 14), ("GadgetX", 8), ("WidgetB", 10)],
    },
    "invoice_1006.csv": {
        "vendor": "Acme Industrial Supplies",
        "invoice_number": "INV-1006",
        "due_date": "2026-02-10",
        "total": 2750.00,
        "line_items": [("WidgetA", 5), ("WidgetB", 3)],
    },
    "invoice_1007.csv": {
        "vendor": "MegaWidgets Corp",
        "invoice_number": "INV-1007",
        "due_date": "2026-02-28",
        "total": 15525.00,
        "line_items": [("WidgetA", 20), ("WidgetB", 15), ("GadgetX", 3)],
    },
    "invoice_1008.txt": {
        "vendor": "NoProd Industries",
        "invoice_number": "INV-1008",
        "due_date": "2026-01-20",
        "total": 9900.00,
        "line_items": [("SuperGizmo", 12), ("MegaSprocket", 6)],
    },
    "invoice_1009.json": {
        "vendor": None,
        "invoice_number": "INV-1009",
        "due_date": None,
        "total": -250.00,
        "line_items": [("WidgetA", -5), ("WidgetB", 2)],
    },
    "invoice_1010.txt": {
        "vendor": "Consolidated Materials Group",
        "invoice_number": "INV-1010",
        "due_date": "2026-02-26",
        "total": 7185.00,
        "line_items": [("WidgetA", 8), ("WidgetB", 4), ("GadgetX", 2), ("WidgetA", 4)],
    },
    "invoice_1011.txt": {
        "vendor": "Summit Manufacturing Co.",
        "invoice_number": "INV-1011",
        "due_date": "2026-02-20",
        "total": 3000.00,
        "line_items": [("WidgetA", 6), ("WidgetB", 3)],
    },
    "invoice_1011.pdf": {
        "vendor": "Summit Manufacturing Co.",
        "invoice_number": "INV-1011",
        "due_date": "2026-02-20",
        "total": 3000.00,
        "line_items": [("WidgetA", 6), ("WidgetB", 3)],
    },
    "invoice_1012.txt": {
        "vendor": "QuickShip Distributers",
        "invoice_number": "INV-1012",
        "due_date": "2026-02-25",
        "total": 9975.00,
        "line_items": [("WidgetA", 12), ("WidgetB", 7), ("GadgetX", 4)],
    },
    "invoice_1012.pdf": {
        "vendor": "QuickShip Distributers",
        "invoice_number": "INV-1012",
        "due_date": "2026-02-25",
        "total": 9975.00,
        "line_items": [("WidgetA", 12), ("WidgetB", 7), ("GadgetX", 4)],
    },
    "invoice_1013.json": {
        "vendor": "Atlas Industrial Supply",
        "invoice_number": "INV-1013",
        "due_date": "2026-03-24",
        "total": 22562.80,
        "line_items": [
            ("WidgetA", 15), ("WidgetB", 10), ("GadgetX", 5),
            ("WidgetA", 5), ("WidgetB", 8), ("GadgetX", 3),
            ("WidgetA", 2), ("GadgetX", 1),
        ],
    },
    "invoice_1013.pdf": {
        "vendor": "Atlas Industrial Supply",
        "invoice_number": "INV-1013",
        "due_date": "2026-03-24",
        "total": 22562.80,
        "line_items": [
            ("WidgetA", 15), ("WidgetB", 10), ("GadgetX", 5),
            ("WidgetA", 5), ("WidgetB", 8), ("GadgetX", 3),
            ("WidgetA", 2), ("GadgetX", 1),
        ],
    },
    "invoice_1014.xml": {
        "vendor": "TechParts International",
        "invoice_number": "INV-1014",
        "due_date": "2026-02-26",
        "total": 4125.00,
        "line_items": [("WidgetA", 4), ("WidgetB", 6)],
    },
    "invoice_1015.csv": {
        "vendor": "Reliable Components Inc.",
        "invoice_number": "INV-1015",
        "due_date": "2026-02-28",
        "total": 6500.00,
        "line_items": [("WidgetA", 10), ("WidgetB", 5), ("GadgetX", 2)],
    },
    "invoice_1016.json": {
        "vendor": "Widgets Inc.",
        "invoice_number": "INV-1016",
        "due_date": "2026-02-27",
        "total": 3233.00,
        "line_items": [("WidgetA", 4), ("WidgetB", 2), ("WidgetC", 3)],
    },
}
