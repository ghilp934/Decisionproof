#!/usr/bin/env bash
# Clean ZIP packaging script for Decisionproof
# Uses git archive to package tracked files only (no __pycache__, *.pyc, etc.)

set -Eeuo pipefail

# Output name: decisionproof_src_<shortsha>_<yyyymmdd>.zip
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

SHA="$(git rev-parse --short HEAD)"
DATE="$(date +%Y%m%d)"
OUT="${1:-decisionproof_src_${SHA}_${DATE}.zip}"

# Ensure we don't accidentally package CRLF
# (optional extra guard; RC gate already checks)
if git grep -Il $'\r' -- . >/dev/null 2>&1; then
  echo "ERROR: CRLF detected. Fix line endings before packaging."
  git grep -Il $'\r' -- .
  exit 1
fi

# Create clean ZIP from tracked files only
git archive --format=zip --prefix=decisionproof/ -o "$OUT" HEAD

echo "OK: created clean archive -> $OUT"
