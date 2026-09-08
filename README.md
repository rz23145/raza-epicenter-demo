# shop-ceiling-index

A public-data pipeline that measures ceiling strain among self-serve Shopify
merchants: workaround apps that substitute for Plus-native features, and
catalog and review volume proxies, aggregated over a fixed panel into a
monthly index with a leading component (share showing ceiling signals) and a
confirming component (share converted to Plus since the prior scan).

## Status: Phase 1 of 11, skeleton only

This repository currently contains the package skeleton, validated settings,
the full database schema (migration 001), logging, and two working commands:
`ceiling init` and `ceiling doctor`. No fetching, detection, scoring,
validation, or index computation exists yet. Nothing in this README claims a
result, because no results have been produced.

The full README required by the project charter (thesis, data dictionary,
label construction, validation results, index definition, failure modes) is
written in Phase 11, when the numbers it must cite exist in export files.

## Setup

Requires Python 3.11 (pinned in `.python-version`).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export CEILING_CONTACT_EMAIL="you@example.com"   # required, refuses to run without it
ceiling init
ceiling doctor
make test
```

`ceiling init` creates `data/ceiling.db`, applies migrations, validates
config, and refuses to proceed if the contact email is not set. The contact
email is embedded in the crawler user agent so site operators can reach the
operator of this bot.

`ceiling doctor` prints the Python version, dependency versions, the config
sha, the count of unverified signatures, database and robots cache status,
and the open items that only the human owner can resolve.

`make test` runs `ruff check`, `mypy --strict` on `src/`, and `pytest` with
coverage.

## Crawling policy, enforced in code

- Public data only. No logins, no cookies, no gated APIs, no paid datasets.
- robots.txt is parsed and respected on every host, every time. Disallowed
  paths are never fetched and every skip is recorded with its reason.
- Rate limits are floors: at most 1 request per second per host, 2 seconds
  between hosts, exponential backoff from 5 seconds on 429 or 503, hard stop
  on a host after 3 consecutive 429s. Settings validation rejects any config
  faster than these floors. They are configurable upward only.
- No headless browsers. `requests` only. Data that requires JavaScript to
  render is not collected.
- No fetching of Store Leads, BuiltWith, Similarweb, or any technographic
  vendor.
- No fabricated or filled-in data. Failed fetches are recorded as failures,
  uncomputable features are null.
- Every measurement carries provenance: fetch timestamp, source URL, HTTP
  status, and a SHA-256 of the raw response body, which is archived on disk.

## Layout

See `src/ceiling/` for the package. Subpackages are placeholders labeled with
the phase that fills them. `config/` holds settings and the signature
dictionaries (empty until Phase 3, every future entry starts `verified: false`
until a human confirms its evidence URLs). `data/labels/` holds the
human-built label CSVs, currently header-only.
