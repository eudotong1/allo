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

export function summarize(rows, installs, now = new Date(), plans = []) {
  const STALE_MS = 3 * 60 * 1000; // a running row with no heartbeat for 3 minutes is treated as gone
  const kept = rows.filter((r) => !isJunk(r) && !(r.status === "skipped" && NOT_ROUTED.includes(r.skip)));
  // A job counts once, on the plan that ran it. Orchestrator rows (Grok Bot handing a job to another plan) are
  // not jobs of their own: they become "dispatched by" metadata on the matching job and are left out of every count.
  const isOrch = (r) => r.plan_type === "orchestrator";
  const bare = (t) => String(t || "").replace(/^Dispatch(\s+|$)/, "");
  const dispatch = new Map();
  for (const r of kept) if (isOrch(r)) dispatch.set(`${r.install_id}|${r.at}|${bare(r.title)}`, r.provider || "orchestrator");
  // Grok Bot's own work: any Grok row with no matching job on another plan (it did the work itself, not routed).
  const routedKeys = new Set(kept.filter((r) => !isOrch(r)).map((r) => `${r.install_id}|${r.at}|${bare(r.title)}`));
  const isDirect = (r) => isOrch(r) && !routedKeys.has(`${r.install_id}|${r.at}|${bare(r.title)}`);
  const hasGrok = kept.some((r) => r.provider === "grok");
  const routed = kept.filter((r) => !isOrch(r) || isDirect(r));
  const many = installs.length > 1;
  const names = Object.fromEntries(installs.map((i) => [i.id, i.name]));
  const lines = new Map();
  const jobs = [];
  const running = [];
  const totals = { jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0, running: 0 };
  let n = 0;
  routed.forEach((r) => {
    const miss = typeof r.miss === "string" ? JSON.parse(r.miss) : (r.miss || []);
    const key = r.provider === "grok" ? `${r.install_id}:grok-direct` : (r.pool ? `${r.install_id}:${r.pool}` : "none");
    if (!lines.has(key)) {
      lines.set(key, {
        key, pool: r.pool, provider: r.provider, plan_type: r.plan_type,
        computer: r.pool && many ? names[r.install_id] || null : null,
        jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0, running: 0, direct: r.provider === "grok",
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
        // API value (est.): only real, positive costs; orchestrator rows mirror other plans' work and are left out.
        if (typeof r.est_saved_usd === "number" && r.est_saved_usd > 0 && r.plan_type !== "orchestrator") { t.saved += r.est_saved_usd; t.priced += 1; }
      }
    } else {
      line.running += 1;
      totals.running += 1;
    }
    const row = {
      n, at: r.at, line: key, status: isRunning ? "running" : r.status, miss,
      computer: names[r.install_id] || null,
      title: (isOrch(r) ? bare(r.title) : r.title) || null, dispatched_by: dispatch.get(`${r.install_id}|${r.at}|${bare(r.title)}`) || null, model: r.model || null, effort: r.effort || null,
      heartbeat_at: r.heartbeat_at || null, started_at: r.at,
      usd: (typeof r.est_saved_usd === "number" && r.est_saved_usd > 0 && r.plan_type !== "orchestrator") ? r.est_saved_usd : null,
      seconds: isRunning && beatMs ? Math.max(0, Math.round((now.getTime() - Date.parse(String(r.at).replace(/Z$/, ":00Z"))) / 1000)) : null,
    };
    jobs.push(row);
    if (isRunning) running.push(row);
  });
  // Keep a Grok Bot row visible (even at 0) whenever Grok Bot shows up at all, so direct work is never hidden.
  if (hasGrok && ![...lines.values()].some((l) => l.direct)) {
    const g = kept.find((r) => r.provider === "grok");
    lines.set(`${g.install_id}:grok-direct`, { key: `${g.install_id}:grok-direct`, pool: g.pool || "grok", provider: "grok", plan_type: "orchestrator", computer: null, jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0, running: 0, direct: true });
  }
  const round = (x) => Math.round(x * 100) / 100;
  const ordered = [...lines.values()]
    .map((l) => ({ ...l, saved: round(l.saved) }))
    .sort((a, b) => (a.key === "none") - (b.key === "none") || (b.jobs + b.running) - (a.jobs + a.running) || a.key.localeCompare(b.key));
  totals.saved = round(totals.saved);
  totals.runs = totals.jobs;
  // Collapse repeat runs of the same task on the same plan into one task with a run count.
  const groups = new Map();
  for (const j of jobs) {
    const k = `${j.line}|${j.title || ""}`; // untitled runs on a plan share one "Untitled runs" group
    const g = groups.get(k);
    const one = { at: j.at, end: j.status === "running" ? null : j.heartbeat_at, status: j.status, usd: j.usd };
    if (!g) { groups.set(k, { ...j, list: [one], usd_total: j.usd || 0, priced: j.usd ? 1 : 0, runs: 1, ok: j.status === "ok" ? 1 : 0, failed: j.status === "ok" || j.status === "running" ? 0 : 1, first_at: j.at }); continue; }
    g.runs += 1;
    g.list.push(one);
    if (j.usd) { g.usd_total += j.usd; g.priced += 1; }
    if (j.status === "ok") g.ok += 1; else if (j.status !== "running") g.failed += 1;
    if (j.at < g.first_at) g.first_at = j.at;
    if (j.at >= g.at || j.status === "running") Object.assign(g, { at: j.at, status: j.status === "running" ? "running" : (g.status === "running" ? "running" : j.status), seconds: j.seconds ?? g.seconds, started_at: j.started_at, heartbeat_at: j.heartbeat_at || g.heartbeat_at, model: j.model || g.model, effort: j.effort || g.effort });
    if (j.miss.length) g.miss = j.miss;
    if (j.dispatched_by) g.dispatched_by = j.dispatched_by;
  }
  const tasks = [...groups.values()].sort((a, b) => a.at.localeCompare(b.at));
  tasks.forEach((t, i) => { t.n = i + 1; if (!t.title && t.runs > 1) t.title = "Untitled runs"; t.usd_total = t.priced ? Math.round(t.usd_total * 100) / 100 : null; t.list.sort((a, b) => b.at.localeCompare(a.at)); });
  for (const l of ordered) { l.runs = l.jobs; const mine = tasks.filter((t) => t.line === l.key && t.status !== "running"); l.jobs = mine.length; l.ran = mine.filter((t) => t.ok > 0).length; }
  totals.jobs = tasks.filter((t) => t.status !== "running").length;
  totals.ran = tasks.filter((t) => t.ok > 0).length;
  totals.runs = ordered.reduce((a, l) => a + l.runs, 0);
  // Whole-number shares that always add up to 100 (largest remainder).
  const tot = ordered.reduce((a, l) => a + l.jobs, 0);
  if (tot) {
    const raw = ordered.map((l) => (l.jobs / tot) * 100);
    ordered.forEach((l, i) => { l.share = Math.floor(raw[i]); });
    let left = 100 - ordered.reduce((a, l) => a + l.share, 0);
    [...ordered.keys()].sort((a, b) => (raw[b] % 1) - (raw[a] % 1)).forEach((i) => { if (left > 0 && ordered[i].jobs) { ordered[i].share += 1; left -= 1; } });
  } else ordered.forEach((l) => { l.share = 0; });
  // Each plan's latest reading (left, reset time, auto or manual), matched to its line by computer and pool.
  for (const l of ordered) {
    const inst = String(l.key).split(":")[0];
    const r = plans.find((p) => String(p.install_id) === inst && p.pool === l.pool);
    l.limit = r ? { left_pct: r.left_pct ?? null, left_usd: r.left_usd ?? null, resets_at: r.resets_at || null, window: r.window || null, as_of: r.as_of || null, how: r.how || null } : null;
  }
  return { totals, lines: ordered, jobs: tasks, running };
}
