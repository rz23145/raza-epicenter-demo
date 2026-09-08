#!/usr/bin/env bash
# Full pipeline, in dependency order. Fails loudly on any step: no step may
# swallow an error. Requires: CEILING_CONTACT_EMAIL set, label CSVs populated,
# and a built panel (see the Bootstrap section of the README).
#
# LIMIT caps the number of panel stores scanned (default 50).
set -euo pipefail

LIMIT="${LIMIT:-50}"
PANEL_VERSION="${PANEL_VERSION:-v1}"

if [ -z "${CEILING_CONTACT_EMAIL:-}" ]; then
  echo "ERROR: CEILING_CONTACT_EMAIL is not set. The pipeline refuses to fetch without it." >&2
  exit 1
fi

# Rough runtime estimate: ~5 pages per store (robots, home, collection,
# product, products.json) at >= 1s per request plus inter-host gaps.
python3 - "$LIMIT" <<'EOF'
import sys
limit = int(sys.argv[1])
seconds = limit * (5 * 1.0 + 2.0)
print(f"Estimated scan time for {limit} stores: about {seconds/60:.0f} minutes (rate limits are floors).")
EOF

if [ ! -f "data/labels/panel.csv" ] || ! grep -q ",${PANEL_VERSION}$" data/labels/panel.csv; then
  echo "ERROR: panel version ${PANEL_VERSION} not found in data/labels/panel.csv." >&2
  echo "Bootstrap first: populate label CSVs, then:" >&2
  echo "  ceiling labels ingest" >&2
  echo "  ceiling stores add --file data/labels/non_plus_negatives.csv" >&2
  echo "  ceiling scan run --domains data/labels/non_plus_negatives.csv" >&2
  echo "  ceiling panel build --size 750 --version ${PANEL_VERSION}" >&2
  exit 1
fi

ceiling init
ceiling labels ingest
ceiling scan run --panel "$PANEL_VERSION" --limit "$LIMIT"
ceiling index compute --version "$PANEL_VERSION"
ceiling index plot --version "$PANEL_VERSION"
ceiling validate crosssection
ceiling export samples

echo "run_all complete. Exports are in data/exports/."
