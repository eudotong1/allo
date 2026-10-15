// The upload allowlist. allo_upload.py keeps the same lists, and tests/test_upload.py fails if they drift.
// The Worker rejects (400) any payload with a key or value that is not on these lists, so a client bug or a
// modified client cannot widen what gets stored.

export const SCHEMA = 1;
export const MAX_ROWS = 500;
export const TOP_KEYS = ["schema", "sent_at", "jobs"];
export const JOB_KEYS = ["id", "at", "pool", "plan_type", "provider", "status", "skip", "miss", "est_saved_usd", "title", "model", "effort", "heartbeat_at"];
export const STATUSES = ["ok", "failed", "timeout", "error", "skipped", "dry-run", "running"];
export const SKIPS = ["unassigned", "needs-reading", "already-ran", "bad-run-block", "missing-cwd", "declined", "manual", "other"];
export const MISSES = ["failed", "timeout", "error", "unverified-reading", "unassigned", "needs-reading", "bad-run-block", "missing-cwd", "other"];
export const PLAN_TYPES = ["subscription", "credit_usd", "orchestrator", "other"];
export const PROVIDERS = ["claude", "claude_personal", "claude_team", "codex", "grok", "gemini", "api", "other"];
export const MODELS = ["opus", "sonnet", "haiku", "gpt-5", "gpt-5-codex", "o3", "o4-mini", "other"];
export const EFFORTS = ["low", "medium", "high", "very_high", "xhigh"];

const MINUTE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$/;
const JOB_ID = /^h:[0-9a-f]{16}$/;
const POOL_REF = /^pool-[0-9]{1,3}$/;

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function sameKeys(obj, keys) {
  const got = Object.keys(obj);
  const extra = got.filter((k) => !keys.includes(k));
  const missing = keys.filter((k) => !got.includes(k));
  return { extra, missing };
}

function validMinute(s) {
  if (typeof s !== "string" || !MINUTE.test(s)) return false;
  const d = new Date(s.replace("Z", ":00Z"));
  return !Number.isNaN(d.getTime());
}

// Returns null when the payload is exactly on the allowlist, else a short reason. Never echoes values back.
export function validatePayload(p) {
  if (!isObject(p)) return "payload must be an object";
  const top = sameKeys(p, TOP_KEYS);
  if (top.extra.length) return `unknown field(s): ${top.extra.slice(0, 5).map((k) => k.slice(0, 40)).join(", ")}`;
  if (top.missing.length) return `missing field(s): ${top.missing.join(", ")}`;
  if (p.schema !== SCHEMA) return `schema must be ${SCHEMA}`;
  if (!validMinute(p.sent_at)) return "sent_at must be a UTC time rounded to the minute";
  if (!Array.isArray(p.jobs)) return "jobs must be a list";
  if (p.jobs.length > MAX_ROWS) return `at most ${MAX_ROWS} jobs per upload`;
  for (let i = 0; i < p.jobs.length; i++) {
    const j = p.jobs[i];
    if (!isObject(j)) return `jobs[${i}] must be an object`;
    const k = sameKeys(j, JOB_KEYS);
    if (k.extra.length) return `jobs[${i}]: unknown field(s): ${k.extra.slice(0, 5).map((x) => x.slice(0, 40)).join(", ")}`;
    if (k.missing.length) return `jobs[${i}]: missing field(s): ${k.missing.join(", ")}`;
    if (typeof j.id !== "string" || !JOB_ID.test(j.id)) return `jobs[${i}].id must be a hashed id`;
    if (!validMinute(j.at)) return `jobs[${i}].at must be a UTC time rounded to the minute`;
    if (j.pool !== null && (typeof j.pool !== "string" || !POOL_REF.test(j.pool))) return `jobs[${i}].pool must be pool-N or null`;
    if (j.plan_type !== null && !PLAN_TYPES.includes(j.plan_type)) return `jobs[${i}].plan_type is not allowed`;
    if (j.provider !== null && !PROVIDERS.includes(j.provider)) return `jobs[${i}].provider is not allowed`;
    if (!STATUSES.includes(j.status)) return `jobs[${i}].status is not allowed`;
    if (j.skip !== null && !SKIPS.includes(j.skip)) return `jobs[${i}].skip is not allowed`;
    if (!Array.isArray(j.miss) || j.miss.length > MISSES.length || !j.miss.every((m) => MISSES.includes(m)))
      return `jobs[${i}].miss is not allowed`;
    if (j.est_saved_usd !== null && (typeof j.est_saved_usd !== "number" || !Number.isFinite(j.est_saved_usd) || Math.abs(j.est_saved_usd) >= 1e7))
      return `jobs[${i}].est_saved_usd must be a number or null`;
    if (j.title !== null && (typeof j.title !== "string" || j.title.length < 1 || j.title.length > 48 || /[<>]/.test(j.title) || ![...j.title].every((c) => c === " " || c === "-" || c === "_" || c === "." || c === "/" || c === ":" || c === "#" || (c >= "0" && c <= "9") || (c >= "A" && c <= "Z") || (c >= "a" && c <= "z"))))
      return `jobs[${i}].title is not allowed`;
    if (j.model !== null && !MODELS.includes(j.model)) return `jobs[${i}].model is not allowed`;
    if (j.effort !== null && !EFFORTS.includes(j.effort)) return `jobs[${i}].effort is not allowed`;
    if (j.heartbeat_at !== null && !validMinute(j.heartbeat_at)) return `jobs[${i}].heartbeat_at must be a UTC time rounded to the minute`;
  }
  return null;
}

// The disconnect request from the CLI: only {keep_history: boolean}.
export function validateDisconnect(p) {
  if (!isObject(p)) return "body must be an object";
  const k = sameKeys(p, ["keep_history"]);
  if (k.extra.length || k.missing.length || typeof p.keep_history !== "boolean") return "body must be exactly {keep_history: boolean}";
  return null;
}
