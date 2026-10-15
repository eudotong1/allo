-- Live heartbeats and orchestrator (Grok) support.
ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT;
