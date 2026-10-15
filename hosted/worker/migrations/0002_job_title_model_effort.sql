-- Short job title, model and effort: optional fields the CLI may send so chips and the table can tell jobs apart.
-- Titles are short display names only (max 48 chars); prompts and paths stay off the wire.
ALTER TABLE jobs ADD COLUMN title TEXT;
ALTER TABLE jobs ADD COLUMN model TEXT;
ALTER TABLE jobs ADD COLUMN effort TEXT;
