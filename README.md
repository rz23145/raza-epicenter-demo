# Shop Ceiling Index

A public-data pipeline that measures "ceiling strain" among self-serve
Shopify merchants: observable traces of stores pressing against the limits of
the Advanced plan before a possible upgrade to Shopify Plus. Built for a
buy-side research workflow. Everything here is reproducible from a clean
clone plus the operator's label files.

## Results

<!-- results:begin -->
First full run 2026-09-09. Every number below appears in a file under
data/exports/ (copies of the key exports are committed in
data/exports/samples/), enforced by make readme-check.

Index, panel v1 (40 small self-serve stores drawn from the negative label
set; a strain-representative panel is future work):

- share_ceiling: 0.175
- share_workaround_any: 0.025
- share_high_growth: 0.375
- conversion series: empty by design until a second, later scan exists.
- hiring board coverage on the panel: 0 stores, as the charter predicted
  for small merchants.

Cross-sectional validation (36 scanned positives vs 40 negatives, base rate
0.474, from crosssection_report.md):

- AUC of ceiling_score: 0.494, bootstrap 95% CI [0.368, 0.613]. No
  discriminative power on this cross-section, and the report explains why
  that is expected: stores that already upgraded replaced their workarounds
  with native features, so current-Plus vs small-negative is dominated by
  size (product_count alone: 0.658). The cross-section validates the volume
  proxies, not the workaround signal.
- Precision at top decile: 0.571.

Wayback backtest (12 migrators, 20 matched controls, snapshot coverage
0.463, from wayback_backtest_summary.md):

- Migrator workaround prevalence before migration: 0.100 at -12 months,
  0.125 at -6 months.
- Control prevalence: 0.000 at every offset.
- Directionally consistent with the thesis, far too small to carry a claim:
  8 to 10 snapshots per offset.

Reconciliation against disclosures (reconcile_report.md): an
Advanced-to-Plus upgrade at list is 1901 USD/month of subscription revenue.
Plus share of MRR moved 35 to 34 percent year-over-year (2025Q2 to 2026Q2),
so the naive attribution of share change to upgrades gives a negative
number: any memo must model mix shift, not just upgrade counts.
<!-- results:end -->

## Status

As of 2026-09-09: **14 of 23 app signatures and 1 of 6 Plus fingerprints
(Multipass) are verified** against live storefront HTML, with the store
checked and the matched snippet recorded in each YAML entry's notes. The
label sets hold 41 Plus positives, 40 non-Plus negatives, and 12 confirmed
migrators, each row backed by a source URL (targets are 150/150/20, so all
three sets are below target and say so here). `disclosures.csv` is populated
from Shopify's actual Q2 2026 and Q2 2025 releases and current pricing page.
The 9 unverified signatures are unverified for documented reasons (backend
apps with no storefront trace, apps invisible to anonymous visitors, one app
that appears not to exist); they contribute at half weight.

Material fact discovered during verification: Shopify Scripts ceased
executing on 2026-06-30 and public Functions-based discount apps now run on
every plan, so the discount_logic capability is a weaker Plus-strain signal
than this charter originally assumed. The migrator set includes 4 rows that
are "on Plus by" upper bounds rather than exact upgrade dates; each is
flagged in its quote field.

Every number in the Results section traces to a file in `data/exports/`. The
`make readme-check` target enforces that discipline mechanically and fails
if the Results section is empty or contains an untraceable number.

## Data sample

`ceiling export samples` copies every export CSV and markdown file, plus a
dated copy of the SQLite database, into `data/exports/samples/`, which is
committed. Contents from the 2026-09-09 run:

- `crosssection_scores.csv` (76 rows): per-store ceiling score, label, and
  volume features for every scanned labeled store in the cross-section.
- `falsepos_review.csv` (30 rows): the top-scoring negatives with score
  components and matched apps, hand-reviewed (reviewer_category and
  reviewer_note filled in).
- `index_timeseries.csv` (1 row): the panel v1 index point for the
  2026-09-09 scan (share_ceiling, share_workaround_any, share_high_growth).
- `wayback_backtest.csv` (160 rows): one row per migrator/control per
  month-offset with snapshot availability and workaround detection.
- `crosssection_report.md` (59 lines): AUC, bootstrap CI, precision at top
  decile, and the interpretation of why the cross-section is size-dominated.
- `falsepos_summary.md` (16 lines): reviewer category counts and shares of
  the flagged set from the hand review.
- `reconcile_report.md` (16 lines): pipeline output reconciled against
  Shopify's disclosed MRR mix and list prices, including the panel bound
  under an operator-assumed 2,000,000-merchant self-serve base.
- `wayback_backtest_summary.md` (13 lines): migrator vs control workaround
  prevalence at each month offset before migration.
- `ceiling_2026-09-09.db`: the full SQLite database from the September 9
  scan — 17 tables covering 81 stores and 1197 archived fetches, with
  provenance (URL, timestamp, SHA-256, body path) on every fetch.

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

- `multipass_login` (verified 2026-09-09): shopify.dev states "Your store
  must be on a Shopify Plus plan" and the marker was observed live on
  brooklinen.com. Caveat: Multipass now requires legacy customer accounts,
  so absence proves nothing.
- `shopify_plus_text`: literal "Shopify Plus" text has many false positives
  (agency badges, blog posts).
- `expansion_store_hreflang`: also produced by non-Plus merchants running two
  separate plans.
- The rest currently have **no patterns** and exist so the discovery work is
  visible.

Consequences baked into the code: fingerprints never enter the ceiling score
(a test fails if a fingerprint key appears in `feature_json`), and conversion
detection uses **only verified fingerprints**. Multipass is the single
verified fingerprint today, so the conversion series detects only
Multipass-visible conversions until more fingerprints are verified.

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

1. Verify the remaining 9 app signatures and 5 fingerprints where possible:
   most need either a confirmed live install (Kaktus, HulkApps VolumeBoost,
   BUCKS, miniOrange) or have no anonymous storefront trace by design
   (Syncio, Sync Power, Wholesale Hub) and should stay documented gaps.
2. Grow labels from current 41/40/12 toward target sizes (150+ positives,
   150+ negatives, 20+ confirmed migrators with dated sources). Prefer
   migrators with exact months; 4 of the current 12 are upper bounds.
3. Build the panel, then monthly rescans; the index needs two scans before
   the conversion series exists.
4. Run the Wayback backtest and the false-positive hand review; only then
   decide whether the signal carries a memo. Note from disclosures: Plus
   share of MRR went 35% (2025Q2) to 34% (2026Q2), so the memo must address
   mix shift, not just upgrades.

## Provenance and honesty rules

Numbers in this README must exist in `data/exports/` (`make readme-check`).
Missing data stays NULL. Idempotent reruns. Config hash and git sha recorded
on every scan. The `disclosures.csv` reconciliation requires operator-entered
figures with page references; the code refuses to run on remembered numbers.
