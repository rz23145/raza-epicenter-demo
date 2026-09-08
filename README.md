# Shop Ceiling Index

A public-data pipeline that measures "ceiling strain" among self-serve
Shopify merchants: observable traces of stores pressing against the limits of
the Advanced plan before a possible upgrade to Shopify Plus. Built for a
buy-side research workflow. Everything here is reproducible from a clean
clone plus the operator's label files.

## Status: pipeline complete, no data collected

This repository contains working, tested code and empty data files. **No scan
has been run against real stores, the label sets are empty, and zero
signatures are verified.** Every number this pipeline will ever report traces
to a file in `data/exports/`; there are no such files yet, so this README
claims no results. The `make readme-check` target enforces that discipline
mechanically.

<!-- results:begin -->
No results yet. This section is populated only from files in data/exports/
after real scans, and every number in it must appear in an export file or
readme-check fails the build.
<!-- results:end -->

## The thesis

Shopify does not publish who is on which plan. But merchants outgrowing the
Advanced plan leave public traces: third-party apps that substitute for
Plus-native capabilities (B2B/wholesale, multi-store sync, launch
scheduling, complex discount logic, SSO), fast catalog growth, large review
counts, expansion-store link structures, and hiring posts mentioning a
replatform. This pipeline turns those traces into:

1. A transparent, additive per-store **ceiling score** (readable weights in
   `config/settings.yaml`, no fitted model).
2. A monthly **panel index**: the share of a fixed self-serve panel showing
   ceiling strain, and the share converting to Plus between scans.
3. A **Wayback backtest**: for confirmed migrators, did workarounds appear
   before the migration month more often than in matched non-migrators?

## What a Plus fingerprint does and does not prove

No public marker is a plan lookup. Each fingerprint in
`config/plus_fingerprints.yaml` is circumstantial:

- `multipass_login`: Multipass is documented as Plus-only, but the marker is
  a string in HTML, and documentation can lag reality.
- `shopify_plus_text`: literal "Shopify Plus" text has many false positives
  (agency badges, blog posts).
- `expansion_store_hreflang`: also produced by non-Plus merchants running two
  separate plans.
- The rest currently have **no patterns** and exist so the discovery work is
  visible.

Consequences baked into the code: fingerprints never enter the ceiling score
(a test fails if a fingerprint key appears in `feature_json`), and conversion
detection uses **only verified fingerprints**. Since zero fingerprints are
verified today, the conversion series is empty by construction until the
human owner completes verification. That is intentional, not a bug.

## Crawling policy

- Identifying user agent: `CeilingIndexResearchBot/0.1 (+mailto:...)`. The
  contact email must be set (`CEILING_CONTACT_EMAIL`); nothing fetches
  without it.
- `robots.txt` parsed per host; disallowed paths are never fetched and every
  skip is recorded. Unreachable robots.txt is treated as allowed and
  recorded as `robots_unreachable`.
- At most 1 request/second/host, 2 seconds between hosts, exponential
  backoff with full jitter on 429/503, hard stop per host after 3
  consecutive 429s. These are floors: config validation rejects faster
  values.
- Only public, unauthenticated pages. No headless browsers, no JavaScript
  execution, no login walls, no paywalls, no personal data. Product data is
  stored as aggregates only.
- Every fetch archives raw bytes under `data/raw/{host}/{date}/{sha256}.html`
  with a JSON sidecar, and writes a `fetches` row: any number in any table
  traces back to bytes on disk.

## Setup

Python 3.11 required.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
make install
export CEILING_CONTACT_EMAIL="you@yourfirm.com"
ceiling init
ceiling doctor
make test
```

`ceiling doctor` prints dependency versions, config hash, signature
verification counts, database status, and the open items only a human can
resolve.

## Bootstrap: from clean clone to a first index

1. **Populate labels** (`data/labels/`): `plus_positives.csv`,
   `non_plus_negatives.csv`, `confirmed_migrators.csv`. Every row needs a
   `source_url` a reviewer can open. Helpers:
   `ceiling labels candidates-from-page --url ... --out ...` extracts Shopify
   storefronts from a public directory page for hand review.
2. `ceiling labels ingest` (validates everything, rejects whole files on any
   error).
3. Seed the store universe and scan it:
   `ceiling stores add --file data/labels/non_plus_negatives.csv`, then
   `ceiling scan run --domains <csv>` (start with `--dry-run` to see robots
   verdicts before fetching anything).
4. `ceiling labels check-fingerprints --set non_plus_negatives`: negatives
   showing Plus fingerprints get flagged for removal.
5. `ceiling panel build --size 750 --version v1` (frozen once written).
6. `LIMIT=50 scripts/run_all.sh` for the standard monthly run, or the
   individual commands it wraps.

## Commands

| Command | What it does |
| --- | --- |
| `ceiling init` / `ceiling doctor` | setup and diagnostics |
| `ceiling stores add/detect` | universe management, is_shopify checks |
| `ceiling labels ingest/candidates-from-page/check-fingerprints` | label building |
| `ceiling scan run --domains CSV \| --panel v1 [--dry-run]` | per-store pipeline |
| `ceiling panel build/scan` | panel construction (frozen) and rescans |
| `ceiling index compute/plot` | index series and chart |
| `ceiling wayback backtest` | pre/post migration comparison |
| `ceiling hiring discover/count` | job board cross-check |
| `ceiling validate crosssection/falsepos/falsepos-summary/reconcile` | validation |
| `ceiling export samples` | small committed samples of exports |

## The score

Additive weights over workaround capabilities (B2B 3.0, multistore 3.0,
discount 1.5, SSO 1.5, launch 1.0, intl 1.0 requiring >= 2 intl apps),
high growth (2.0), large catalog (1.0), review scale (1.0 at 1k, 2.0 at 5k).
Contributions from unverified signatures are halved and labeled in the
stored components. Null inputs contribute zero and are labeled `null_input`,
never imputed. Feature keys are documented in `src/ceiling/features/build.py`.

## The index

For each panel scan: `share_ceiling` (share of scanned self-serve panel
stores at or above threshold 3.0), `share_workaround_any`,
`share_high_growth`, `share_converted_since_prior` (verified fingerprints
only), and a monthly hiring post count. Denominators exclude robots skips
and dead stores; stores that go dark are attrition, never conversion.

## Data dictionary (short)

- `stores`, `scans`, `fetches`, `assets`: universe, run provenance (git sha +
  config sha per scan), fetch log, extracted asset URLs.
- `app_matches` / `fingerprint_matches`: which signature matched on what,
  with the matching pattern and its verification status.
- `catalog_snapshots`: aggregates from `/products.json` (counts, created-at
  windows, median price; currency is NULL because products.json does not
  expose one).
- `review_snapshots`: per-vendor review counts with source URL and method.
- `features`: `feature_json`, `ceiling_score`, per-component breakdown JSON.
- `labels`, `wayback_*`, `hiring_posts`, `company_boards`, `index_values`.

## Known failure modes (what breaks this)

- **Signature rot**: apps rename script hosts; the dictionary needs periodic
  re-verification. All 23 app signatures and 6 fingerprints currently ship
  `verified: false` with TODO_VERIFY markers; several ship empty patterns
  because no defensible pattern was found from public knowledge alone.
- **The workaround inversion**: upgraded stores drop their workarounds, so
  cross-sectional AUC against Plus positives can legitimately sit below 0.5
  for workaround features. The generated crosssection report discusses this;
  the Wayback backtest is the real longitudinal test.
- **products.json availability**: many stores disallow or disable it; those
  stores get NULL catalog features, not zeros.
- **Wayback coverage**: uneven for small stores; the backtest reports
  snapshot coverage as a first-class number.
- **Hiring coverage**: only stores linking Greenhouse/Lever/Ashby boards are
  visible, a small fraction of any storefront panel. The count is a weak
  aggregate cross-check, not a per-store signal.
- **Plan-gating drift**: Shopify has repeatedly moved features down-tier
  (Functions, Markets). Fingerprint evidence URLs must be re-confirmed.

## Next three months (operator's plan)

1. Verify signatures: read both evidence URLs per entry, flip `verified`,
   discover patterns for the empty entries against live installs.
2. Build labels to target sizes (300+ positives, 300+ negatives, 40+
   confirmed migrators with dated sources).
3. First panel scan, then monthly rescans; the index needs two scans before
   the conversion series exists.
4. Run the Wayback backtest and the false-positive hand review; only then
   decide whether the signal carries a memo.

## Provenance and honesty rules

Numbers in this README must exist in `data/exports/` (`make readme-check`).
Missing data stays NULL. Idempotent reruns. Config hash and git sha recorded
on every scan. The `disclosures.csv` reconciliation requires operator-entered
figures with page references; the code refuses to run on remembered numbers.
