-- Suggested next: the planner's runnable jobs per computer, titles and suggested plan only (never prompts or
-- commands). Replaced whenever the computer's queue changes.
CREATE TABLE queue (
  install_id INTEGER NOT NULL REFERENCES installs(id) ON DELETE CASCADE,
  id TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT,
  pool TEXT,
  provider TEXT,
  priority INTEGER,
  PRIMARY KEY (install_id, id)
);
CREATE INDEX queue_user ON queue(user_id);

-- "Send to Grok Bot": one row per queued job the signed-in user asked to run. The computer collects pending rows
-- on its normal poll and runs them locally; the primary key makes a double click one request.
CREATE TABLE run_requests (
  install_id INTEGER NOT NULL REFERENCES installs(id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL,            -- pending | taken | done | failed
  requested_at TEXT NOT NULL,
  taken_at TEXT,
  done_at TEXT,
  PRIMARY KEY (install_id, job_id)
);
CREATE INDEX run_requests_pending ON run_requests(install_id, status);
CREATE INDEX run_requests_user ON run_requests(user_id);
