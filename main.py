"""CLI entrypoint for the invoice processing pipeline.

Usage:
    python main.py --invoice_path=data/invoices/invoice_1001.txt
    python main.py --all
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from src import invoice_folder
from src.config import load_settings
from src.observability.console import console, render_error, render_invoice_result, render_summary_table
from src.observability.logger import write_run_log
from src.orchestration.graph import build_graph


def process_invoice(graph, file_path: str, db_path: str) -> dict:
    state = {
        "file_path": file_path,
        "db_path": db_path,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "invoice": None,
        "validation": None,
        "decision": None,
        "payment": None,
    }
    return graph.invoke(state)


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Automated invoice processing pipeline")
    parser.add_argument("--invoice_path", help="Path to a single invoice file")
    parser.add_argument("--all", action="store_true", help="Process every invoice in the main invoice folder")
    parser.add_argument(
        "--invoices-dir",
        default=invoice_folder.get_main_folder(),
        help="Directory to scan with --all (defaults to the current main invoice folder — "
        "same one the web UI's Batch tab uses; see src/invoice_folder.py)",
    )
    parser.add_argument("--db-path", default=settings.inventory_db_path)
    parser.add_argument("--log-dir", default=settings.log_dir)
    parser.add_argument("--quiet", action="store_true", help="Suppress per-invoice panels in --all mode")
    args = parser.parse_args()

    if not args.invoice_path and not args.all:
        parser.error("Provide --invoice_path=<file> or --all")

    if not os.path.exists(args.db_path):
        parser.error(f"Inventory database not found at {args.db_path!r} — run setup_inventory_db.py first.")

    if settings.llm_api_key:
        console.print(f"[bold cyan]LLM provider:[/bold cyan] {settings.llm_provider} (model: {settings.llm_model})")
    else:
        console.print(
            f"[bold yellow]LLM provider: {settings.llm_provider} — no API key configured.[/bold yellow] "
            f"JSON/CSV/XML invoices will still parse and validate; free-text/PDF ingestion and every "
            f"approval decision require a key. Set {settings.llm_provider.upper()}_API_KEY (or the "
            f"matching env var for another provider) in .env."
        )
    console.print()

    graph = build_graph()

    if args.invoice_path:
        try:
            result = process_invoice(graph, args.invoice_path, args.db_path)
        except Exception as e:
            render_error(args.invoice_path, e)
            raise SystemExit(1)
        render_invoice_result(result)
        log_path = write_run_log(result, args.log_dir)
        console.print(f"[dim]Run log: {log_path}[/dim]")
        return

    files = invoice_folder.list_files(args.invoices_dir)
    results = []
    for file_path in files:
        try:
            result = process_invoice(graph, file_path, args.db_path)
        except Exception as e:
            render_error(file_path, e)
            continue
        if not args.quiet:
            render_invoice_result(result)
        write_run_log(result, args.log_dir)
        results.append(result)

    console.print()
    render_summary_table(results)
    console.print(f"[dim]{len(results)}/{len(files)} invoices processed. Logs written to {args.log_dir}/[/dim]")


if __name__ == "__main__":
    main()
