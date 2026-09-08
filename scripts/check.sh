#!/usr/bin/env bash
# One-button verification. From a clean clone or an existing checkout:
#
#   scripts/check.sh          (or: make check)
#
# Does, in order:
#   1. creates .venv with Python 3.11 if missing
#   2. installs the package and dev dependencies if missing
#   3. runs the full gate: ruff, mypy --strict, tests with coverage floor,
#      readme-check
#   4. initializes the database and prints ceiling doctor
#   5. if CEILING_CONTACT_EMAIL is set and DEMO_DOMAINS is non-empty, runs a
#      robots-verdict dry run against those domains (fetches robots.txt only)
#
# Nothing here scans real stores. A real scan is always an explicit,
# separate command.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "==> creating virtualenv (.venv) with python3.11"
  python3.11 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! command -v ceiling >/dev/null 2>&1 || ! python -c "import responses" 2>/dev/null; then
  echo "==> installing package and dev dependencies"
  pip install --quiet -e ".[dev]"
fi

echo "==> make test (ruff, mypy --strict, pytest with coverage floor, readme-check)"
make test

echo
echo "==> ceiling doctor"
if [ -n "${CEILING_CONTACT_EMAIL:-}" ]; then
  ceiling init >/dev/null
fi
ceiling doctor

if [ -n "${CEILING_CONTACT_EMAIL:-}" ] && [ -n "${DEMO_DOMAINS:-}" ]; then
  echo
  echo "==> robots dry run against: ${DEMO_DOMAINS} (fetches robots.txt only)"
  tmp_csv="$(mktemp)"
  {
    echo "domain"
    tr ',' '\n' <<<"$DEMO_DOMAINS"
  } > "$tmp_csv"
  ceiling scan run --domains "$tmp_csv" --dry-run
  rm -f "$tmp_csv"
elif [ -z "${CEILING_CONTACT_EMAIL:-}" ]; then
  echo
  echo "note: CEILING_CONTACT_EMAIL is not set, so init and the network dry run"
  echo "were skipped. To include them:"
  echo "  CEILING_CONTACT_EMAIL=you@yourfirm.com DEMO_DOMAINS=allbirds.com,gymshark.com scripts/check.sh"
fi

echo
echo "==> all checks passed"
