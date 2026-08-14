#!/usr/bin/env bash
# One-command setup + launch for the web UI. Mirrors the manual steps in
# SOLUTION.md's "Running it" section — this script doesn't do anything you
# couldn't do by hand, it just does it in one call.
#
# Usage:
#   ./run.sh            # first run: creates .venv, installs deps, sets up
#                        # inventory.db if missing, starts the server
#   ./run.sh --reset     # also wipes and reseeds inventory.db (clears the
#                        # payment ledger — use this between batch runs)

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

if [ "${1:-}" = "--reset" ]; then
  echo "Resetting inventory database..."
  python setup_inventory_db.py --force
elif [ ! -f inventory.db ]; then
  echo "Setting up inventory database..."
  python setup_inventory_db.py
fi

if [ ! -f .env ]; then
  echo "No .env found — that's fine, you can add a provider and API key from the"
  echo "Settings panel once the app is running. (Or: cp .env.example .env)"
fi

echo ""
echo "Starting the server at http://localhost:8000"
echo "Press Ctrl+C to stop."
echo ""
exec python server.py
