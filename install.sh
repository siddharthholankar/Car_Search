#!/usr/bin/env bash
# One-time setup on macOS/Linux: creates .venv, installs car-search, writes config.toml.
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.11 or newer is needed: https://www.python.org/downloads/ (or: brew install python)"
    exit 1
fi

echo "Creating virtual environment..."
[ -d .venv ] || "$PY" -m venv .venv
echo "Installing car-search (this takes a minute or two)..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[dashboard]"
[ -f config.toml ] || cp config.example.toml config.toml

cat <<MSG

Done. Next:
  1. Edit config.toml with your zip, cars, max price and miles.
  2. In Chrome or Edge open chrome://extensions, turn on Developer mode,
     click "Load unpacked" and choose: $(pwd)/extension
  3. Run ./start.sh, then browse car listings.
MSG
