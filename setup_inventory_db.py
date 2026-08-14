"""Creates the inventory database the validation agent checks invoices against.

Extends the README's minimum schema (item, stock) with unit_price/category for
richer validation, an item_aliases table so "Widget A" / "Gadget X" / "WidgetA
(rush order)" all resolve to the same inventory key, and a processed_invoices
ledger so the system can catch duplicate-payment attempts (see INV-1004 vs
INV-1004_revised, which share an invoice number).

Usage: python setup_inventory_db.py [--db-path inventory.db] [--force]
"""

from __future__ import annotations

import argparse
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    item TEXT PRIMARY KEY,
    stock INTEGER NOT NULL,
    unit_price REAL,
    category TEXT
);

CREATE TABLE IF NOT EXISTS item_aliases (
    alias TEXT PRIMARY KEY,
    canonical_item TEXT NOT NULL REFERENCES inventory(item)
);

CREATE TABLE IF NOT EXISTS processed_invoices (
    invoice_number TEXT PRIMARY KEY,
    vendor TEXT,
    total REAL,
    status TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
"""

INVENTORY_SEED = [
    ("WidgetA", 15, 250.00, "Widgets"),
    ("WidgetB", 10, 500.00, "Widgets"),
    ("GadgetX", 5, 750.00, "Gadgets"),
    ("FakeItem", 0, 1000.00, "Unknown"),
]

# Maps the messy spellings actually present in data/invoices/* to the canonical
# inventory key. This is the deterministic half of item-name normalization;
# the LLM extractor handles the cases regex/lookup tables can't anticipate.
ALIAS_SEED = [
    ("Widget A", "WidgetA"),
    ("WidgetA (rush order)", "WidgetA"),
    ("Gadget X", "GadgetX"),
    ("Gadget-X", "GadgetX"),
    ("Widget B", "WidgetB"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="inventory.db")
    parser.add_argument("--force", action="store_true", help="Drop and recreate tables")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    cursor = conn.cursor()

    if args.force:
        cursor.executescript(
            "DROP TABLE IF EXISTS inventory; "
            "DROP TABLE IF EXISTS item_aliases; "
            "DROP TABLE IF EXISTS processed_invoices;"
        )

    cursor.executescript(SCHEMA)
    cursor.executemany(
        "INSERT OR IGNORE INTO inventory (item, stock, unit_price, category) VALUES (?, ?, ?, ?)",
        INVENTORY_SEED,
    )
    cursor.executemany(
        "INSERT OR IGNORE INTO item_aliases (alias, canonical_item) VALUES (?, ?)",
        ALIAS_SEED,
    )
    conn.commit()

    print(f"Inventory database ready at {args.db_path}")
    for row in cursor.execute("SELECT item, stock, unit_price, category FROM inventory"):
        print(f"  {row[0]:<12} stock={row[1]:<4} unit_price={row[2]:<8} category={row[3]}")
    conn.close()


if __name__ == "__main__":
    main()
