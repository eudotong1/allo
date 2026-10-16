// The upload allowlist. allo_upload.py keeps the same lists, and tests/test_upload.py fails if they drift.
// The Worker rejects (400) any payload with a key or value that is not on these lists, so a client bug or a
// modified client cannot widen what gets stored.

export const SCHEMA = 1;
export const MAX_ROWS = 500;
export const TOP_KEYS = ["schema", "sent_at", "jobs"];
export const OPTIONAL_TOP_KEYS = ["plans", "queue", "results"];
export const QUEUE_KEYS = ["id", "title", "pool", "provider", "priority"];
export const RESULT_KEYS = ["id", "status"];
export const RESULT_STATUSES = ["done", "failed"];
export const MAX_QUEUE = 50;
export const PLAN_KEYS = ["pool", "plan_type", "provider", "left_pct", "left_usd", "resets_at", "window", "as_of", "how"];
export const WINDOW_NAMES = ["5h", "week"];
export const HOWS = ["auto", "manual"];
export const JOB_KEYS = ["id", "at", "pool", "plan_type", "provider", "status", "skip", "miss", "est_saved_usd", "title", "model", "effort", "heartbeat_at"];
export const STATUSES = ["ok", "failed", "timeout", "error", "skipped", "dry-run", "running"];
export const SKIPS = ["unassigned", "needs-reading", "already-ran", "bad-run-block", "missing-cwd", "declined", "manual", "other"];
export const MISSES = ["failed", "timeout", "error", "unverified-reading", "unassigned", "needs-reading", "bad-run-block", "missing-cwd", "other"];
export const PLAN_TYPES = ["subscription", "credit_usd", "orchestrator", "other"];
export const PROVIDERS = ["claude", "claude_personal", "claude_team", "codex", "grok", "gemini", "api", "other"];
export const MODELS = ["opus", "sonnet", "haiku", "gpt-5", "gpt-5-codex", "o3", "o4-mini", "gpt-6.1-sol", "gpt-6-sol", "gpt-5.3-codex", "other"];
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

// Short job titles only: letters, digits, space and - _ . / : #, 1-48 characters.
export function validTitle(t) {
  if (t === null) return true;
  return typeof t === "string" && t.length >= 1 && t.length <= 48 && !/[<>]/.test(t) &&
    [...t].every((c) => c === " " || c === "-" || c === "_" || c === "." || c === "/" || c === ":" || c === "#" || (c >= "0" && c <= "9") || (c >= "A" && c <= "Z") || (c >= "a" && c <= "z"));
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
  top.extra = top.extra.filter((k) => !OPTIONAL_TOP_KEYS.includes(k));
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
    if (j.title !== null && !validTitle(j.title)) return `jobs[${i}].title is not allowed`;
    if (j.model !== null && !MODELS.includes(j.model)) return `jobs[${i}].model is not allowed`;
    if (j.effort !== null && !EFFORTS.includes(j.effort)) return `jobs[${i}].effort is not allowed`;
    if (j.heartbeat_at !== null && !validMinute(j.heartbeat_at)) return `jobs[${i}].heartbeat_at must be a UTC time rounded to the minute`;
  }
  if (p.plans !== undefined) {
    if (!Array.isArray(p.plans) || p.plans.length > 50) return "plans must be a list of at most 50";
    const num = (v, hi) => v === null || (typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= hi);
    for (let i = 0; i < p.plans.length; i++) {
      const r = p.plans[i];
      if (!isObject(r)) return `plans[${i}] must be an object`;
      const k = sameKeys(r, PLAN_KEYS);
      if (k.extra.length || k.missing.length) return `plans[${i}]: fields must be exactly ${PLAN_KEYS.join(", ")}`;
      if (typeof r.pool !== "string" || !POOL_REF.test(r.pool)) return `plans[${i}].pool must be pool-N`;
      if (!PLAN_TYPES.includes(r.plan_type)) return `plans[${i}].plan_type is not allowed`;
      if (!PROVIDERS.includes(r.provider)) return `plans[${i}].provider is not allowed`;
      if (!num(r.left_pct, 100)) return `plans[${i}].left_pct must be 0-100 or null`;
      if (!num(r.left_usd, 1e7)) return `plans[${i}].left_usd must be a number or null`;
      if (r.resets_at !== null && !validMinute(r.resets_at)) return `plans[${i}].resets_at must be a UTC minute or null`;
      if (r.as_of !== null && !validMinute(r.as_of)) return `plans[${i}].as_of must be a UTC minute or null`;
      if (r.window !== null && !WINDOW_NAMES.includes(r.window)) return `plans[${i}].window is not allowed`;
      if (r.how !== null && !HOWS.includes(r.how)) return `plans[${i}].how is not allowed`;
    }
  }
  if (p.queue !== undefined) {
    if (!Array.isArray(p.queue) || p.queue.length > MAX_QUEUE) return `queue must be a list of at most ${MAX_QUEUE}`;
    for (let i = 0; i < p.queue.length; i++) {
      const q = p.queue[i];
      if (!isObject(q)) return `queue[${i}] must be an object`;
      const k = sameKeys(q, QUEUE_KEYS);
      if (k.extra.length || k.missing.length) return `queue[${i}]: fields must be exactly ${QUEUE_KEYS.join(", ")}`;
      if (typeof q.id !== "string" || !JOB_ID.test(q.id)) return `queue[${i}].id must be a hashed id`;
      if (!validTitle(q.title)) return `queue[${i}].title is not allowed`;
      if (q.pool !== null && (typeof q.pool !== "string" || !POOL_REF.test(q.pool))) return `queue[${i}].pool must be pool-N or null`;
      if (q.provider !== null && !PROVIDERS.includes(q.provider)) return `queue[${i}].provider is not allowed`;
      if (q.priority !== null && !(Number.isInteger(q.priority) && q.priority >= 0 && q.priority <= 999)) return `queue[${i}].priority must be 0-999 or null`;
    }
  }
  if (p.results !== undefined) {
    if (!Array.isArray(p.results) || p.results.length > 20) return "results must be a list of at most 20";
    for (let i = 0; i < p.results.length; i++) {
      const r = p.results[i];
      if (!isObject(r)) return `results[${i}] must be an object`;
      const k = sameKeys(r, RESULT_KEYS);
      if (k.extra.length || k.missing.length) return `results[${i}]: fields must be exactly ${RESULT_KEYS.join(", ")}`;
      if (typeof r.id !== "string" || !JOB_ID.test(r.id)) return `results[${i}].id must be a hashed id`;
      if (!RESULT_STATUSES.includes(r.status)) return `results[${i}].status is not allowed`;
    }
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
