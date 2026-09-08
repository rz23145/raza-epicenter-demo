-- 001_init: full schema for the ceiling index pipeline.
-- Timestamps are TEXT in ISO 8601 UTC. Every table has created_at.
-- Foreign key enforcement is enabled per connection (PRAGMA foreign_keys = ON).

CREATE TABLE stores (
  store_id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,          -- normalized: lowercase, no scheme, no www, no trailing slash
  first_seen_at TEXT NOT NULL,
  is_shopify INTEGER,                   -- 1, 0, or NULL if undetermined
  is_shopify_checked_at TEXT,
  myshopify_domain TEXT,                -- if discoverable from HTML, else NULL
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE scans (
  scan_id INTEGER PRIMARY KEY,
  scan_date TEXT NOT NULL,              -- yyyy-mm-dd
  panel_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  git_sha TEXT NOT NULL,
  config_sha TEXT NOT NULL,             -- sha256 of settings.yaml + app_signatures.yaml + plus_fingerprints.yaml
  created_at TEXT NOT NULL,
  UNIQUE(scan_date, panel_version)
);

CREATE TABLE fetches (
  fetch_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  url TEXT NOT NULL,
  page_role TEXT NOT NULL,              -- home | collection | product | products_json | robots | review_api | other
  fetched_at TEXT NOT NULL,
  http_status INTEGER,
  robots_allowed INTEGER NOT NULL,      -- 1 or 0; if 0, http_status is NULL and nothing was fetched
  skip_reason TEXT,                     -- robots_disallow | rate_limit_stop | timeout | connection_error | not_shopify | NULL
  body_sha256 TEXT,
  body_path TEXT,
  content_type TEXT,
  elapsed_ms INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE assets (
  asset_id INTEGER PRIMARY KEY,
  fetch_id INTEGER NOT NULL REFERENCES fetches(fetch_id),
  asset_type TEXT NOT NULL,             -- script_src | link_href | inline_script_marker | dom_marker | meta
  value TEXT NOT NULL,                  -- the src, href, or marker string
  host TEXT,                            -- parsed host for src/href
  created_at TEXT NOT NULL
);

CREATE TABLE app_matches (
  match_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  app_key TEXT NOT NULL,                -- key from app_signatures.yaml
  capability TEXT NOT NULL,             -- from the signature entry
  matched_on TEXT NOT NULL,             -- the asset value that matched
  pattern TEXT NOT NULL,                -- the pattern that matched
  signature_verified INTEGER NOT NULL,  -- copied from yaml at scan time
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, store_id, app_key)
);

CREATE TABLE fingerprint_matches (
  fp_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  fingerprint_key TEXT NOT NULL,
  matched_on TEXT NOT NULL,
  pattern TEXT NOT NULL,
  fingerprint_verified INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, store_id, fingerprint_key)
);

CREATE TABLE catalog_snapshots (
  snapshot_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  products_json_available INTEGER NOT NULL,
  product_count INTEGER,
  pages_fetched INTEGER,
  truncated INTEGER,                    -- 1 if we hit max_pages before exhausting
  earliest_created_at TEXT,
  latest_created_at TEXT,
  products_created_last_90d INTEGER,
  products_created_last_365d INTEGER,
  products_created_prior_365d INTEGER,  -- days 366 to 730 before scan date
  variant_count INTEGER,
  vendor_count INTEGER,                 -- distinct vendor field values
  available_share REAL,                 -- share of variants with available=true
  median_price REAL,
  currency TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, store_id)
);

CREATE TABLE review_snapshots (
  review_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  vendor TEXT NOT NULL,                 -- judgeme | yotpo | loox | okendo | stamped | shopify_native | none
  review_count INTEGER,
  source_url TEXT,
  method TEXT NOT NULL,                 -- html_widget | json_endpoint_referenced_in_page | not_available
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, store_id, vendor)
);

CREATE TABLE features (
  feature_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  feature_json TEXT NOT NULL,           -- the full feature vector, keys documented in features/build.py
  ceiling_score REAL,
  ceiling_components_json TEXT,         -- per-component contributions for auditability
  plus_fingerprint_any INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id, store_id)
);

CREATE TABLE labels (
  label_id INTEGER PRIMARY KEY,
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  label_set TEXT NOT NULL,              -- plus_positive | non_plus_negative | confirmed_migrator
  label_value TEXT,                     -- for migrators: yyyy-mm
  source_url TEXT NOT NULL,
  source_type TEXT NOT NULL,            -- shopify_showcase | agency_portfolio | agency_case_study | press_release | directory | manual
  quote TEXT,
  added_by TEXT NOT NULL,
  added_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(store_id, label_set)
);

CREATE TABLE wayback_snapshots (
  wb_id INTEGER PRIMARY KEY,
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  target_month TEXT NOT NULL,           -- yyyy-mm we asked for
  offset_months INTEGER NOT NULL,       -- relative to migration_month, e.g. -18, -12, -6, -3, 3
  actual_timestamp TEXT,                -- CDX timestamp returned, NULL if none within tolerance
  archive_url TEXT,
  http_status INTEGER,
  body_sha256 TEXT,
  body_path TEXT,
  fetched_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(store_id, offset_months)
);

CREATE TABLE wayback_app_matches (
  wb_match_id INTEGER PRIMARY KEY,
  wb_id INTEGER NOT NULL REFERENCES wayback_snapshots(wb_id),
  app_key TEXT NOT NULL,
  capability TEXT NOT NULL,
  matched_on TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(wb_id, app_key)
);

CREATE TABLE wayback_fingerprint_matches (
  wb_fp_id INTEGER PRIMARY KEY,
  wb_id INTEGER NOT NULL REFERENCES wayback_snapshots(wb_id),
  fingerprint_key TEXT NOT NULL,
  matched_on TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(wb_id, fingerprint_key)
);

CREATE TABLE hiring_posts (
  post_id INTEGER PRIMARY KEY,
  board TEXT NOT NULL,                  -- greenhouse | lever | ashby
  company_slug TEXT NOT NULL,
  store_id INTEGER REFERENCES stores(store_id),   -- NULL if not mapped to a panel store
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  posted_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  matched_terms_json TEXT NOT NULL,     -- which of the Plus-related terms matched
  source_url TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(board, company_slug, external_id)
);

CREATE TABLE index_values (
  index_id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(scan_id),
  panel_version TEXT NOT NULL,
  panel_n INTEGER NOT NULL,
  panel_n_scanned INTEGER NOT NULL,     -- excludes robots skips and failures
  share_ceiling REAL,                   -- share of scanned self-serve panel with ceiling_score >= threshold
  share_workaround_any REAL,
  share_high_growth REAL,
  share_converted_since_prior REAL,     -- stores with no Plus fingerprint at prior scan and any at this scan
  n_converted INTEGER,
  n_prior_selfserve INTEGER,
  hiring_plus_posts_month INTEGER,
  computed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scan_id)
);
