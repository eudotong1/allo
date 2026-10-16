-- Each plan's latest reading per computer: what is left, when it resets, and whether it was read automatically
-- (allo-router sync) or typed in (allo-router set). Replaced on every upload that carries it.
CREATE TABLE plans (
  install_id INTEGER NOT NULL REFERENCES installs(id) ON DELETE CASCADE,
  pool TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_type TEXT,
  provider TEXT,
  left_pct REAL,
  left_usd REAL,
  resets_at TEXT,
  window TEXT,
  as_of TEXT,
  how TEXT,
  PRIMARY KEY (install_id, pool)
);
CREATE INDEX plans_user ON plans(user_id);
