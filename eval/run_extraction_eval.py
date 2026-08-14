"""Extraction accuracy eval: regex-only vs LLM-only vs the hybrid router,
scored against hand-labeled ground truth for every sample invoice.

This is the evidence behind the ingestion routing decision (see
src/ingestion/agent.py): structured formats get parsed deterministically
because it's free and exact; only free-form text goes to the LLM because
that's the one case where a parser has nothing reliable to key off of.

    python -m eval.run_extraction_eval [--db-path inventory.db] [--out eval_report.md]

Requires a configured LLM provider (Settings panel, or LLM_PROVIDER +
*_API_KEY in .env) — there is no offline mode. The "llm_only" arm makes a
real call per invoice; note its structured-format numbers aren't the point
(a real LLM can read JSON/XML/CSV fine) — the comparison that actually
justifies routing structured formats around the LLM entirely is the
free-text arm plus cost/latency, not an accuracy gap. See SOLUTION.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone

from eval.ground_truth import GROUND_TRUTH
from src.config import load_settings
from src.ingestion.agent import IngestionAgent
from src.ingestion.llm_extractor import extract_with_llm
from src.ingestion.normalize import normalize_item_name
from src.ingestion.parsers import extract_pdf_text, regex_extract
from src.llm_client import LLMClient
from src.schemas import ExtractedInvoice
from src.validation.agent import _try_parse_date

INVOICES_DIR = "data/invoices"


def _load_text(file_path: str) -> str:
    if file_path.endswith(".pdf"):
        return extract_pdf_text(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _normalize_vendor(v: str | None) -> str:
    return "".join(ch for ch in (v or "").lower() if ch.isalnum())


def _vendor_correct(extracted: str | None, expected: str | None) -> bool:
    if expected is None:
        return not extracted
    a, b = _normalize_vendor(extracted), _normalize_vendor(expected)
    if not a:
        return False
    return a == b or a in b or b in a


def _invoice_number_correct(extracted: str | None, expected: str | None) -> bool:
    norm = lambda s: "".join(ch for ch in (s or "").upper() if ch.isalnum())
    return norm(extracted) == norm(expected)


def _due_date_correct(extracted: str | None, expected: str | None) -> bool:
    if expected is None:
        return extracted is None or _try_parse_date(extracted) is None
    parsed_expected = _try_parse_date(expected)
    parsed_extracted = _try_parse_date(extracted) if extracted else None
    return parsed_extracted is not None and parsed_expected is not None and parsed_extracted.date() == parsed_expected.date()


def _total_correct(extracted: float | None, expected: float | None) -> bool:
    if expected is None:
        return extracted is None
    return extracted is not None and abs(extracted - expected) < 0.02


def _line_items_f1(extracted_items, expected_items, conn) -> tuple[float, float, float]:
    def norm(items):
        return Counter((normalize_item_name(item, conn), qty) for item, qty in items)

    extracted_multiset = norm([(li.item, li.quantity) for li in extracted_items])
    expected_multiset = norm(expected_items)

    overlap = sum((extracted_multiset & expected_multiset).values())
    precision = overlap / sum(extracted_multiset.values()) if extracted_multiset else 0.0
    recall = overlap / sum(expected_multiset.values()) if expected_multiset else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def score_extraction(invoice: ExtractedInvoice, expected: dict, conn) -> dict:
    precision, recall, f1 = _line_items_f1(invoice.line_items, expected["line_items"], conn)
    fields = {
        "vendor": _vendor_correct(invoice.vendor, expected["vendor"]),
        "invoice_number": _invoice_number_correct(invoice.invoice_number, expected["invoice_number"]),
        "due_date": _due_date_correct(invoice.due_date, expected["due_date"]),
        "total": _total_correct(invoice.amount, expected["total"]),
    }
    field_accuracy = sum(fields.values()) / len(fields)
    return {"fields": fields, "field_accuracy": field_accuracy, "line_item_f1": f1, "line_item_precision": precision, "line_item_recall": recall}


def run_eval(db_path: str) -> dict:
    LLMClient()  # fail fast with a clear error if unconfigured, before doing any work

    conn = sqlite3.connect(db_path)
    ingestion_agent = IngestionAgent(db_path)

    per_file = []
    for filename, expected in GROUND_TRUTH.items():
        file_path = os.path.join(INVOICES_DIR, filename)
        if not os.path.exists(file_path):
            continue
        text = _load_text(file_path)

        arms = {}

        t0 = time.perf_counter()
        regex_invoice = regex_extract(text)
        arms["regex_only"] = (regex_invoice, time.perf_counter() - t0)

        t0 = time.perf_counter()
        llm_invoice = extract_with_llm(text)
        arms["llm_only"] = (llm_invoice, time.perf_counter() - t0)

        t0 = time.perf_counter()
        hybrid_invoice = ingestion_agent.run(file_path)
        arms["hybrid_router"] = (hybrid_invoice, time.perf_counter() - t0)

        row = {"file": filename, "format": os.path.splitext(filename)[1].lstrip("."), "arms": {}}
        for arm_name, (invoice, elapsed) in arms.items():
            score = score_extraction(invoice, expected, conn)
            score["latency_s"] = round(elapsed, 4)
            row["arms"][arm_name] = score
        per_file.append(row)

    conn.close()
    return {"llm_provider": load_settings().llm_provider, "generated_at": datetime.now(timezone.utc).isoformat(), "files": per_file}


def summarize(results: dict) -> dict:
    arm_names = ["regex_only", "llm_only", "hybrid_router"]
    summary = {arm: {"field_accuracy": [], "line_item_f1": [], "latency_s": []} for arm in arm_names}
    by_format = {arm: {} for arm in arm_names}

    for row in results["files"]:
        fmt = row["format"]
        for arm in arm_names:
            score = row["arms"][arm]
            summary[arm]["field_accuracy"].append(score["field_accuracy"])
            summary[arm]["line_item_f1"].append(score["line_item_f1"])
            summary[arm]["latency_s"].append(score["latency_s"])
            by_format[arm].setdefault(fmt, []).append(score["field_accuracy"])

    def avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    return {
        "overall": {arm: {"field_accuracy": avg(v["field_accuracy"]), "line_item_f1": avg(v["line_item_f1"]), "avg_latency_s": avg(v["latency_s"])} for arm, v in summary.items()},
        "by_format": {arm: {fmt: avg(scores) for fmt, scores in fmts.items()} for arm, fmts in by_format.items()},
    }


def render_markdown(results: dict, summary: dict) -> str:
    lines = [
        "# Extraction Accuracy Eval",
        "",
        f"LLM provider at run time: **{results['llm_provider']}**",
        f"Generated: {results['generated_at']}",
        "",
        "## Overall (field accuracy = mean of vendor/invoice_number/due_date/total correctness)",
        "",
        "| Arm | Field Accuracy | Line-Item F1 | Avg Latency (s) |",
        "|---|---|---|---|",
    ]
    for arm, s in summary["overall"].items():
        lines.append(f"| {arm} | {s['field_accuracy']:.0%} | {s['line_item_f1']:.0%} | {s['avg_latency_s']:.4f} |")

    lines += ["", "## Field accuracy by source format", "", "| Arm | " + " | ".join(sorted({f for fmts in summary["by_format"].values() for f in fmts})) + " |"]
    formats = sorted({f for fmts in summary["by_format"].values() for f in fmts})
    lines.append("|---|" + "|".join(["---"] * len(formats)) + "|")
    for arm, fmts in summary["by_format"].items():
        lines.append(f"| {arm} | " + " | ".join(f"{fmts.get(f, 0):.0%}" for f in formats) + " |")

    lines += ["", "## Per-invoice detail", "", "| File | Arm | Vendor | Inv# | Due Date | Total | Item F1 |", "|---|---|---|---|---|---|---|"]
    for row in results["files"]:
        for arm, score in row["arms"].items():
            f = score["fields"]
            check = lambda b: "✓" if b else "✗"
            lines.append(
                f"| {row['file']} | {arm} | {check(f['vendor'])} | {check(f['invoice_number'])} | "
                f"{check(f['due_date'])} | {check(f['total'])} | {score['line_item_f1']:.0%} |"
            )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=load_settings().inventory_db_path)
    parser.add_argument("--out", default="eval_report.md")
    parser.add_argument("--json-out", default="eval_report.json")
    args = parser.parse_args()

    results = run_eval(args.db_path)
    summary = summarize(results)
    report = render_markdown(results, summary)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump({"results": results, "summary": summary}, f, indent=2, default=str)

    print(report)
    print(f"\nWritten to {args.out} and {args.json_out}")


if __name__ == "__main__":
    main()
