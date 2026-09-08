-- 002_company_boards: job board slugs discovered from panel store pages.

CREATE TABLE company_boards (
  board_id INTEGER PRIMARY KEY,
  board TEXT NOT NULL,                  -- greenhouse | lever | ashby
  company_slug TEXT NOT NULL,
  store_id INTEGER REFERENCES stores(store_id),
  discovered_from_url TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(board, company_slug)
);
