#!/usr/bin/env bash
# run.sh — Launch CleanMint using the project venv
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/../venv"

if [ ! -f "$VENV/bin/python" ]; then
    echo "Venv not found. Run from the Cleanmint project root:"
    echo "  python3 -m venv venv && venv/bin/pip install -r cleanmint/requirements.txt"
    exit 1
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/main.py" "$@"
