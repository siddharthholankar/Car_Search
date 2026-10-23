#!/usr/bin/env bash
# Starts the capture server in the background and opens the dashboard; Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/car-search ] || { echo "Run ./install.sh first."; exit 1; }
.venv/bin/car-search serve &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
.venv/bin/car-search dashboard
