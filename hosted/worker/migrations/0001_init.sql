-- Allo Router hosted dashboard. Times are ISO UTC strings, so they sort as text.

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  github_id INTEGER NOT NULL UNIQUE,
  login TEXT NOT NULL,
  created_at TEXT NOT NULL,
  data_version INTEGER NOT NULL DEFAULT 0   -- bumped on every change to this user's data; the dashboard ETag
);

-- Sign-ins after the free spots are taken. No account is created for them.
CREATE TABLE waitlist (
  github_id INTEGER PRIMARY KEY,
  login TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE sessions (
  id_hash TEXT PRIMARY KEY,                 -- SHA-256 of the cookie value; the value itself is never stored
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

-- One per connected computer. The upload token is stored only as its SHA-256.
CREATE TABLE installs (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  token_hash TEXT UNIQUE,
  created_at TEXT NOT NULL,
  last_upload_at TEXT,
  revoked_at TEXT,
  window_start TEXT,
  window_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX installs_user ON installs(user_id);

-- Device approval: the CLI holds device_code (stored hashed), the browser sees user_code.
CREATE TABLE device_codes (
  device_hash TEXT PRIMARY KEY,
  user_code TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_poll_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | denied | claimed
  user_id INTEGER,
  install_id INTEGER
);

-- Exactly the allowlisted upload fields (src/schema.js), plus who sent them.
CREATE TABLE jobs (
  install_id INTEGER NOT NULL REFERENCES installs(id) ON DELETE CASCADE,
  id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  pool TEXT,
  plan_type TEXT,
  provider TEXT,
  status TEXT NOT NULL,
  skip TEXT,
  miss TEXT NOT NULL,                       -- JSON list of enums
  est_saved_usd REAL,
  PRIMARY KEY (install_id, id)
);
CREATE INDEX jobs_user_at ON jobs(user_id, at);
