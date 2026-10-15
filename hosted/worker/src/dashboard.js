// What the web page shows, built from the stored job rows. Pure functions, so they test without a database.

// Skips that are the router working as configured, not a routed job: they are left out of every count.
const NOT_ROUTED = ["already-ran", "manual", "declined"];
const DAY = 86400000;

export function rangeFor(key, now) {
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const monday = today - ((now.getUTCDay() + 6) % 7) * DAY;
  if (key === "last") return { key, start: new Date(monday - 7 * DAY), end: new Date(monday), label: "Last week" };
  if (key === "30d") return { key, start: new Date(now.getTime() - 30 * DAY), end: now, label: "30 days" };
  return { key: "week", start: new Date(monday), end: new Date(monday + 7 * DAY), label: "This week" };
}

// Job rows store times as "YYYY-MM-DDTHH:MMZ"; this is the same format, for range queries on text.
export const minuteKey = (d) => d.toISOString().slice(0, 16) + "Z";

// Rows that are not real work: the "Say hi" test-fixture job that leaked from a local test run.
export function isJunk(r) {
  if (r.title === "Say hi") return true;
  return false;
}

export function summarize(rows, installs, now = new Date()) {
  const STALE_MS = 3 * 60 * 1000; // a running row with no heartbeat for 3 minutes is treated as gone
  const routed = rows.filter((r) => !isJunk(r) && !(r.status === "skipped" && NOT_ROUTED.includes(r.skip)));
  const many = installs.length > 1;
  const names = Object.fromEntries(installs.map((i) => [i.id, i.name]));
  const lines = new Map();
  const jobs = [];
  const running = [];
  const totals = { jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0, running: 0 };
  let n = 0;
  routed.forEach((r) => {
    const miss = typeof r.miss === "string" ? JSON.parse(r.miss) : (r.miss || []);
    const key = r.pool ? `${r.install_id}:${r.pool}` : "none";
    if (!lines.has(key)) {
      lines.set(key, {
        key, pool: r.pool, provider: r.provider, plan_type: r.plan_type,
        computer: r.pool && many ? names[r.install_id] || null : null,
        jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0, running: 0,
      });
    }
    const line = lines.get(key);
    if (r.provider && (line.provider === "other" || !line.provider)) line.provider = r.provider;
    else if (r.provider && r.provider.startsWith("claude_") && line.provider === "claude") line.provider = r.provider;
    else if (r.provider === "grok") line.provider = "grok";
    else if (r.provider === "codex" && line.provider === "other") line.provider = "codex";
    if (r.plan_type === "orchestrator") line.plan_type = "orchestrator";

    const beat = r.heartbeat_at || r.at;
    const beatMs = beat ? Date.parse(String(beat).replace(/Z$/, ":00Z")) : 0;
    const isRunning = r.status === "running" && beatMs && (now.getTime() - beatMs) < STALE_MS;
    if (r.status === "running" && !isRunning) return; // stale running row: hide

    n += 1;
    const ran = r.status === "ok";
    const missed = Array.isArray(miss) && miss.length > 0;
    if (!isRunning) {
      for (const t of [line, totals]) {
        t.jobs += 1;
        if (ran) t.ran += 1;
        if (missed) t.missed += 1;
        if (typeof r.est_saved_usd === "number") { t.saved += r.est_saved_usd; t.priced += 1; }
      }
    } else {
      line.running += 1;
      totals.running += 1;
    }
    const row = {
      n, at: r.at, line: key, status: isRunning ? "running" : r.status, miss,
      computer: names[r.install_id] || null,
      title: r.title || null, model: r.model || null, effort: r.effort || null,
      heartbeat_at: r.heartbeat_at || null, started_at: r.at,
      seconds: isRunning && beatMs ? Math.max(0, Math.round((now.getTime() - Date.parse(String(r.at).replace(/Z$/, ":00Z"))) / 1000)) : null,
    };
    jobs.push(row);
    if (isRunning) running.push(row);
  });
  const round = (x) => Math.round(x * 100) / 100;
  const ordered = [...lines.values()]
    .map((l) => ({ ...l, saved: round(l.saved) }))
    .sort((a, b) => (a.key === "none") - (b.key === "none") || (b.jobs + b.running) - (a.jobs + a.running) || a.key.localeCompare(b.key));
  totals.saved = round(totals.saved);
  totals.runs = totals.jobs;
  // Collapse repeat runs of the same task on the same plan into one task with a run count.
  const groups = new Map();
  for (const j of jobs) {
    const k = j.title ? `${j.line}|${j.title}` : `${j.line}|#${j.n}`; // untitled jobs are never merged
    const g = groups.get(k);
    if (!g) { groups.set(k, { ...j, runs: 1, ok: j.status === "ok" ? 1 : 0, failed: j.status === "ok" || j.status === "running" ? 0 : 1, first_at: j.at }); continue; }
    g.runs += 1;
    if (j.status === "ok") g.ok += 1; else if (j.status !== "running") g.failed += 1;
    if (j.at < g.first_at) g.first_at = j.at;
    if (j.at >= g.at || j.status === "running") Object.assign(g, { at: j.at, status: j.status === "running" ? "running" : (g.status === "running" ? "running" : j.status), seconds: j.seconds ?? g.seconds, started_at: j.started_at, heartbeat_at: j.heartbeat_at || g.heartbeat_at, model: j.model || g.model, effort: j.effort || g.effort });
    if (j.miss.length) g.miss = j.miss;
  }
  const tasks = [...groups.values()].sort((a, b) => a.at.localeCompare(b.at));
  tasks.forEach((t, i) => { t.n = i + 1; });
  const orch = new Set(ordered.filter((l) => l.plan_type === "orchestrator").map((l) => l.key));
  for (const l of ordered) { l.runs = l.jobs; const mine = tasks.filter((t) => t.line === l.key && t.status !== "running"); l.jobs = mine.length; l.ran = mine.filter((t) => t.ok > 0).length; }
  // Orchestrator (Grok Bot) rows mirror the work it hands to another plan, so they are not counted twice.
  const real = tasks.filter((t) => !orch.has(t.line));
  totals.jobs = real.filter((t) => t.status !== "running").length;
  totals.ran = real.filter((t) => t.ok > 0).length;
  totals.runs = ordered.filter((l) => !orch.has(l.key)).reduce((a, l) => a + l.runs, 0);
  return { totals, lines: ordered, jobs: tasks, running };
}
