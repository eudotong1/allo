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

export function summarize(rows, installs) {
  const routed = rows.filter((r) => !(r.status === "skipped" && NOT_ROUTED.includes(r.skip)));
  const many = installs.length > 1;
  const names = Object.fromEntries(installs.map((i) => [i.id, i.name]));
  const lines = new Map();
  const jobs = [];
  const totals = { jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0 };
  routed.forEach((r, i) => {
    const miss = typeof r.miss === "string" ? JSON.parse(r.miss) : r.miss;
    const key = r.pool ? `${r.install_id}:${r.pool}` : "none";
    if (!lines.has(key)) {
      lines.set(key, {
        key, pool: r.pool, provider: r.provider, plan_type: r.plan_type,
        computer: r.pool && many ? names[r.install_id] || null : null,
        jobs: 0, ran: 0, missed: 0, saved: 0, priced: 0,
      });
    }
    const line = lines.get(key);
    const ran = r.status === "ok";
    const missed = miss.length > 0;
    for (const t of [line, totals]) {
      t.jobs += 1;
      if (ran) t.ran += 1;
      if (missed) t.missed += 1;
      if (typeof r.est_saved_usd === "number") { t.saved += r.est_saved_usd; t.priced += 1; }
    }
    jobs.push({ n: i + 1, at: r.at, line: key, status: r.status, miss, computer: names[r.install_id] || null });
  });
  const round = (x) => Math.round(x * 100) / 100;
  const ordered = [...lines.values()]
    .map((l) => ({ ...l, saved: round(l.saved) }))
    .sort((a, b) => (a.key === "none") - (b.key === "none") || b.jobs - a.jobs || a.key.localeCompare(b.key));
  totals.saved = round(totals.saved);
  return { totals, lines: ordered, jobs };
}
