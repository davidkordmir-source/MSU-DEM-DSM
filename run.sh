#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
  echo 'No local Python environment. Install the development dependencies once, or use the Windows portable ZIP.' >&2
  exit 1
fi
exec .venv/bin/python -I run_offline.py "$@"
