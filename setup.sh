#!/usr/bin/env bash
# Build a self-contained venv for the parsing scripts.
# The upstream repo assumes `uv`; these scripts only need two packages, so a
# plain venv avoids that dependency entirely.
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet requests python-dateutil
./.venv/bin/python -c "import requests, dateutil; print('deps ok')"

echo "venv ready at $(pwd)/.venv"