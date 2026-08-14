"""Maps whatever an invoice calls an item ("Widget A", "WidgetA (rush order)")
to the canonical key the inventory database uses. Runs on every extraction
arm's output uniformly so validation logic never has to know about spelling
variance — see item_aliases in setup_inventory_db.py for the seeded cases.
"""

from __future__ import annotations

import re
import sqlite3

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _fold(name: str) -> str:
    return _NON_ALNUM.sub("", name.lower())


def normalize_item_name(raw_name: str, conn: sqlite3.Connection) -> str:
    name = raw_name.strip()

    row = conn.execute("SELECT item FROM inventory WHERE item = ?", (name,)).fetchone()
    if row:
        return row[0]

    row = conn.execute(
        "SELECT canonical_item FROM item_aliases WHERE lower(alias) = lower(?)", (name,)
    ).fetchone()
    if row:
        return row[0]

    # Fallback: fold to alphanumeric-only and compare against inventory items,
    # so unseen spelling variants ("Gadget-X", "widget a") still resolve
    # without needing an alias row for every permutation.
    folded = _fold(name)
    for (item,) in conn.execute("SELECT item FROM inventory"):
        if _fold(item) == folded:
            return item

    return name  # genuinely unknown — validation will flag it
