"""The page `allo-router dashboard` serves. One self-contained HTML file: it fetches /api/state and draws it.
Kept in a module (not a data file) so it ships with a plain `pip install`.

Layout (direction D of docs/mockups): a ledger in numbered sections. 01 Now (what is running, a countdown to the
next scheduled run with its pre-run checks, what needs you), 02 Plans (amount left, runway to reset, with the route
diagram as a fold-out), 03 Jobs (queued and ran today), 04 This week."""

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Allo Router</title>
<link rel="preload" href="/fonts/archivo-latin-var.woff2" as="font" type="font/woff2" crossorigin>
<style>
  @font-face { font-family: "Archivo"; src: url(/fonts/archivo-latin-var.woff2) format("woff2"); font-weight: 100 900; font-stretch: 62% 125%; font-display: swap; }
  @font-face { font-family: "JetBrains Mono"; src: url(/fonts/jetbrains-mono-latin-var.woff2) format("woff2"); font-weight: 100 800; font-display: swap; }
  :root {
    --paper: #F2EFE7; --ink: #16140F; --muted: #6B665B; --rule: #D6D0C2; --well: #E4DFD2;
    --codex: #2446D6; --claude: #D08C06; --credit: #8E2A74; --other: #2F6F73; --none: #A8A194;
    --red: #B3200F;
    --sans: "Archivo", "Helvetica Neue", ui-sans-serif, system-ui, sans-serif;
    --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--paper); color: var(--ink); }
  body { font: 400 15px/1.5 var(--sans); font-variant-numeric: tabular-nums lining-nums; }
  a { color: inherit; }
  a:focus-visible { outline: 3px solid var(--ink); outline-offset: 2px; }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 0 48px 56px; }
  .mono { font-family: var(--mono); font-size: 12.5px; }
  .dim { color: var(--muted); }
  .fig { font-weight: 300; letter-spacing: -0.02em; }
  .bad { color: var(--red); font-weight: 600; }
  .err { color: var(--red); font-weight: 600; }
  .empty { padding: 4px 0; color: var(--muted); }

  .mast { display: grid; grid-template-columns: 1fr auto 1fr; align-items: end; gap: 0 16px; padding: 26px 0 10px; border-bottom: 3px double var(--ink); }
  .mast .mark { font-stretch: 125%; font-variation-settings: "wdth" 125; font-weight: 800; font-size: 22px; letter-spacing: -0.01em; }
  .mast .date { text-align: center; font-size: 13px; letter-spacing: .04em; white-space: nowrap; }
  .mast .right { text-align: right; font-size: 13px; color: var(--muted); overflow-wrap: anywhere; }
  .mast .right b { color: var(--red); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; font-size: 11.5px; margin-left: 10px; white-space: nowrap; }

  .sec { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 24px; padding: 22px 0 26px; border-bottom: 1px solid var(--ink); }
  .sec > * { min-width: 0; }
  .sec > h2 { margin: 0; font-size: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }
  .sec > h2 span { display: block; font-family: var(--mono); font-weight: 400; color: var(--muted); letter-spacing: 0; margin-bottom: 4px; }
  .sec > h2 .since { font-family: var(--sans); text-transform: none; letter-spacing: 0; font-weight: 400; margin: 4px 0 0; }

  /* 01 Now: running, next run, needs you */
  .now { display: grid; grid-template-columns: 1.5fr 1fr 1fr; }
  .now > div { min-width: 0; padding: 0 28px; border-left: 1px solid var(--rule); }
  .now > div:first-child { padding-left: 0; border-left: 0; }
  .label { margin: 0 0 8px; font-size: 12px; color: var(--muted); }
  .now h1 { margin: 0; font-size: 38px; line-height: 1.02; font-weight: 300; letter-spacing: -0.02em; overflow-wrap: anywhere; }
  .now h1 b { font-weight: 700; }
  .now p { margin: 10px 0 0; }
  .now .label { margin: 0 0 8px; }
  .now .cmd { font-size: 13px; color: var(--muted); }
  .warn h3 { margin: 0; font-size: 20px; line-height: 1.25; font-weight: 600; color: var(--red); }
  .warn h3.ok { color: var(--ink); }
  .warn h3 + p { margin-top: 6px; }
  .warn .more { margin-top: 18px; }
  .warn .issue { margin: 12px 0 0; padding-top: 10px; border-top: 1px solid var(--rule); color: var(--red); font-weight: 600; font-size: 14px; }

  /* Next run: countdown and pre-run checks */
  .count { margin: 0; font-size: 72px; line-height: .9; font-stretch: 62%; font-variation-settings: "wdth" 62; font-weight: 760; letter-spacing: -0.01em; white-space: nowrap; }
  .count small { font-size: 20px; font-weight: 400; color: var(--muted); margin-left: 6px; font-stretch: 100%; font-variation-settings: "wdth" 100; letter-spacing: 0; }
  .checks { list-style: none; margin: 14px 0 0; padding: 0; border-top: 1px solid var(--ink); }
  .checks li { display: grid; grid-template-columns: 16px 1fr; padding: 6px 0; border-bottom: 1px solid var(--rule); font-size: 13.5px; }
  .checks li::before { content: ""; width: 7px; height: 7px; background: var(--ink); margin-top: 7px; }
  .checks li.no::before { background: var(--red); }
  .checks li.no { color: var(--red); }

  /* 02 Plans: the ledger */
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 12px; font-weight: 500; color: var(--muted); padding: 0 16px 8px 0; border-bottom: 1px solid var(--ink); white-space: nowrap; }
  td { padding: 14px 16px 14px 0; border-bottom: 1px solid var(--rule); vertical-align: middle; }
  tr:last-child td { border-bottom: 0; }
  .r { text-align: right; }
  .key { display: inline-block; width: 9px; height: 9px; margin-right: 10px; }
  .plan { font-weight: 600; white-space: nowrap; }
  .left { font-size: 34px; line-height: 1; text-align: right; white-space: nowrap; }
  .left small { font-size: 13px; color: var(--muted); letter-spacing: 0; margin-left: 2px; }
  .runway { width: 40%; }
  .bar { position: relative; height: 10px; background: var(--well); }
  .bar i { position: absolute; inset: 0 auto 0 0; }
  .bar.line { height: 2px; margin: 4px 0; }
  .bar.none { background: repeating-linear-gradient(90deg, var(--well) 0 6px, transparent 6px 10px); }
  .bar-note { margin-top: 6px; font-size: 12.5px; color: var(--muted); }
  .bar-note.bad { color: var(--red); }
  .resets { white-space: nowrap; }
  .resets b { display: block; font-weight: 600; }
  .ids { font-family: var(--mono); font-size: 12.5px; white-space: nowrap; }
  .ids .run { background: var(--ink); color: var(--paper); padding: 1px 4px; }

  /* 02 Plans, fold-out: the route diagram. Closed by default so the ledger stays the answer. */
  .diagram { margin-top: 14px; border-top: 1px solid var(--rule); }
  .diagram summary { cursor: pointer; padding: 10px 0 0; font-size: 13px; font-weight: 600; list-style: none; }
  .diagram summary::-webkit-details-marker { display: none; }
  .diagram summary::before { content: "+"; display: inline-block; width: 16px; font-family: var(--mono); }
  .diagram[open] summary::before { content: "\2212"; }
  .diagram summary b { font-weight: 600; }
  .diagram .when-open, .diagram[open] .when-closed { display: none; }
  .diagram[open] .when-open { display: inline; }
  .diagram summary span { font-weight: 400; color: var(--muted); margin-left: 6px; }
  .diagram summary:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
  .diagram .scroll { overflow-x: auto; padding-top: 10px; }
  .diagram .scroll > svg { display: block; width: 100%; min-width: 760px; height: auto; }
  .diagram text { font-family: var(--sans); }
  .d-nm { font-size: 13.5px; font-weight: 600; fill: var(--ink); }
  .d-v { font-size: 13.5px; fill: var(--muted); }
  .d-ax { font-size: 11.5px; fill: var(--muted); }
  .d-sub { font-size: 11.5px; fill: var(--muted); }
  .d-rs { font-size: 11.5px; font-weight: 700; fill: var(--red); }
  .d-chip { font: 700 10.5px var(--mono); text-anchor: middle; }
  .d-legend { display: flex; flex-wrap: wrap; gap: 4px 22px; padding: 6px 0 0; font-size: 12px; color: var(--muted); }
  .d-legend svg { vertical-align: middle; margin-right: 6px; }
  .d-stuck { margin: 6px 0 0; font-size: 12.5px; color: var(--red); }

  /* 03 Jobs: queued and ran, side by side */
  .jobs { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }
  .jobs > div { min-width: 0; }
  .jobs h3 { margin: 0 0 6px; font-size: 13px; font-weight: 600; }
  .jobs h3 .dim { font-weight: 400; }
  .jobs td { padding: 9px 12px 9px 0; vertical-align: baseline; }
  .jobs td.id { font-family: var(--mono); font-size: 12.5px; color: var(--muted); width: 36px; }
  .jobs td.p { white-space: nowrap; color: var(--muted); font-size: 13px; }
  .jobs td.s { white-space: nowrap; text-align: right; font-size: 13px; }
  .note { display: block; font-size: 12.5px; color: var(--muted); }

  /* 04 Week */
  .week { display: grid; grid-template-columns: repeat(4, 1fr) 1.6fr; border-top: 1px solid var(--rule); }
  .week > div { padding: 12px 20px 4px; border-left: 1px solid var(--rule); }
  .week > div:first-child { border-left: 0; padding-left: 0; }
  .week .fig { display: block; font-size: 52px; line-height: 1; }
  .week .alert .fig { color: var(--red); }
  .days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; align-items: end; height: 92px; margin-top: 6px; }
  .days div { display: flex; flex-direction: column; justify-content: flex-end; height: 100%; }
  .days i { display: block; background: var(--ink); }
  .days span { font-size: 11.5px; color: var(--muted); text-align: center; border-top: 1px solid var(--ink); margin-top: 3px; padding-top: 2px; }
  .days .today span { color: var(--ink); font-weight: 700; }
  .days .future span { border-top-color: var(--rule); }
  .saved { display: grid; grid-template-columns: auto 1fr; gap: 28px; align-items: baseline; margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--rule); }
  .saved .fig { font-size: 52px; line-height: 1; }
  .saved p { margin: 0; color: var(--muted); max-width: 52em; }
  .saved p b { color: var(--ink); }

  footer { padding-top: 12px; color: var(--muted); font-size: 12.5px; }

  @media (max-width: 960px) {
    .sec { grid-template-columns: 1fr; gap: 12px; }
    .now, .jobs { grid-template-columns: 1fr; gap: 20px; }
    .now > div { padding: 0; border: 0; }
    .week { grid-template-columns: repeat(2, 1fr); }
    .week > div:nth-child(3) { border-left: 0; padding-left: 0; }
    .week > div:last-child { grid-column: 1 / -1; border-left: 0; padding-left: 0; }
  }
  @media (max-width: 640px) {
    .wrap { padding: 0 16px 32px; }
    .mast { grid-template-columns: 1fr auto; } .mast .date { display: none; } .mast .mark { white-space: nowrap; } .mast .right .mono, .mast .right .sync { display: none; }
    .now h1 { font-size: 32px; }
    .jobs td.p { display: none; }
    .saved { grid-template-columns: 1fr; gap: 8px; }
    .ledger thead { display: none; }
    .ledger tr { display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; padding: 12px 0; border-bottom: 1px solid var(--rule); }
    .ledger tr:last-child { border-bottom: 0; }
    .ledger td { border: 0; padding: 0; width: auto; }
    .ledger td.runway { grid-column: 1 / -1; }
  }
</style>
</head>
<body>
<div class="wrap" id="app"><p class="empty">Loading the router's state…</p></div>
<script>
"use strict";
const COLORS = { codex: ["#2446D6", "#16308F"], claude: ["#D08C06", "#9A6500"], credit: ["#8E2A74", "#5E1B4C"], other: ["#2F6F73", "#1E4A4D"] };
const TEXT = { codex: "#2446D6", claude: "#8A5A00", credit: "#8E2A74", other: "#2F6F73" };
const WORDS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const D = s => s ? new Date(s) : null;
const pad = n => String(n).padStart(2, "0");
const hhmm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const dayName = d => d.toLocaleDateString("en-US", { weekday: "short" });
const mdy = d => `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const cap = t => t ? t[0].toUpperCase() + t.slice(1) : t;
function sameDay(a, b) { return a.toDateString() === b.toDateString(); }
function when(d, now) { return sameDay(d, now) ? hhmm(d) : `${dayName(d)} ${hhmm(d)}`; }
function span(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${s} s`;
  return `${s} s`;
}
// "4:48": hours and minutes to go, for the countdown
function hm(sec) { const m = Math.max(0, Math.floor(sec / 60)); return `${Math.floor(m / 60)}:${pad(m % 60)}`; }
// "in 28 h", "in 3 d 13 h", "in 16 d"
function inSpan(ms) {
  const h = ms / 3600000;
  if (h < 1) return `in ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `in ${Math.round(h)} h`;
  const H = Math.round(h), d = Math.floor(H / 24), r = H % 24;
  return h < 168 && r ? `in ${d} d ${r} h` : `in ${d} d`;
}
function resetText(d, now) { return d - now < 7 * 86400000 ? `${dayName(d)} ${hhmm(d)}` : `${dayName(d)} ${mdy(d)}`; }
function ago(d, now) {
  const m = Math.round((now - d) / 60000);
  if (m < 1) return "just now";
  if (m < 90) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}
function clock(sec) { const m = Math.floor(sec / 60), s = Math.round(sec % 60); return `${m}:${pad(s)}`; }
function money(x) { return x == null ? "n/a" : (x < 0 ? "-" : "") + "$" + Math.abs(x).toFixed(Math.abs(x) >= 0.1 || x === 0 ? 2 : 4); }
function tokens(n) { return n == null ? "" : n.toLocaleString("en-US"); }
const pace = p => p.kind !== "credit_usd" && !p.flags.length && p.left != null && p.hours_to_reset > 0 ? p.left / p.hours_to_reset : null;
const paceText = x => x.toFixed(x < 1 ? 2 : 1);

function palette(pools) {
  const seen = {}, out = {};
  for (const p of pools) {
    const i = seen[p.family] = (seen[p.family] || 0) + 1;
    out[p.id] = { line: COLORS[p.family][Math.min(i - 1, 1)], text: TEXT[p.family], label: p.label, pool: p };
  }
  return out;
}

const RESULT = { ok: "Done", failed: "Failed", timeout: "Timed out", error: "Error", skipped: "Skipped", "dry-run": "Dry run" };

// Masthead: name, date and time, folder and last sync
function mast(s, now) {
  const synced = s.pools.map(p => p.last_sync && D(p.last_sync.taken_at)).filter(Boolean).sort((a, b) => b - a)[0];
  const date = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const sync = synced ? `<span class="sync" title="Synced ${hhmm(synced)}. To read again: allo-router sync"> · synced ${ago(synced, now)}</span>`
                      : `<span class="sync" title="Readings come from allo-router sync or set"> · not synced</span>`;
  return `<header class="mast"><span class="mark">Allo Router</span><span class="date">${esc(date)} · ${hhmm(now)}${s.tz ? " " + esc(s.tz) : ""}</span>
    <span class="right"><span class="mono">${esc(s.config_dir)}</span>${sync}${s.example ? "<b>Example data</b>" : ""}</span></header>`;
}

function sec(n, title, body, extra) {
  return `<section class="sec"><h2><span>${n}</span>${title}${extra || ""}</h2><div>${body}</div></section>`;
}

function decision(q, pal) {
  if (!q || !q.pool) return "";
  const p = pal[q.pool]; if (!p) return "";
  const name = `<b style="color:${p.text}">${esc(p.label)}</b>`;
  if (p.pool.kind === "credit_usd") return `${name} because this job needs the API, and API jobs only run on credit while it fits above the reserve.`;
  if (q.note && q.note.includes("UNKNOWN")) return `${name}: its headroom is unknown, so confirm before trusting it.`;
  return `${name} had the most of its limit left (${esc(q.note)}) of the plans allowed this job's data.`;
}

// 01 Now, first column: what is running, or the headline when nothing is
function running(s, now, pal) {
  const run = s.running;
  if (s.level === "running" && run) {
    const p = pal[run.pool] || { text: "inherit", label: run.pool };
    const pool = s.pools.find(x => x.id === run.pool);
    const q = s.queue.find(x => String(x.task_id) === String(run.task_id));
    const why = q && q.pool ? decision(q, pal) : pool && pool.left != null ? `${esc(p.label)} read ${esc(pool.left_text)} left before it started.` : "";
    return `<p class="label">Running for ${span(run.seconds)}${run.started_at ? ` · started ${hhmm(D(run.started_at))}` : ""}</p>
      <h1><b>#${esc(run.task_id)}</b> on <b style="color:${p.text}">${esc(p.label)}</b>${run.name ? `: ${esc(run.name)}` : ""}</h1>
      ${why ? `<p class="dim">${why}</p>` : ""}`;
  }
  const last = (s.history || {}).last;
  let out = `<p class="label">Nothing running</p><h1>${esc(s.headline)}</h1>`;
  if (last) {
    const p = last.pool ? pal[last.pool] : null;
    out += `<p class="dim">Last job #${esc(last.task_id)}${last.name ? `, ${esc(last.name)}` : ""}: ${esc((RESULT[last.status] || last.status || "").toLowerCase())}${p ? ` on ${esc(p.label)}` : ""}, ${when(D(last.at), now)}.</p>`;
  }
  return out;
}

function check(ok, text) { return `<li class="${ok ? "" : "no"}"><span>${text}</span></li>`; }
function readingsCheck(s) {
  if (!s.pools.length) return "";
  const usable = s.pools.filter(p => !p.flags.length).length;
  return check(usable === s.pools.length, `${usable} of ${s.pools.length} plans have a usable reading`);
}

// 01 Now, second column: countdown to the next scheduled run and its pre-run checks
function nextRun(s, now) {
  const sch = s.schedule, plan = s.plan;
  const runnable = s.queue.filter(q => !q.skip).length;
  const preview = `<p class="cmd">${plural(runnable, "job")} would run if you previewed now. To review: <span class="mono">allo-router run</span></p>`;
  if (!sch || !sch.next_run) {
    return `<p class="label">Next scheduled run</p><p class="count">None</p>
      <p>Nothing starts on its own. Jobs run when you run a reviewed plan: <span class="mono">allo-router run --plan plan.json --execute</span></p>
      ${preview}<ul class="checks" aria-label="Checks">${readingsCheck(s)}</ul>`;
  }
  const n = D(sch.next_run);
  let out = `<p class="label">Next scheduled run · ${hhmm(n)} ${dayName(n)}</p><p class="count">${hm((n - now) / 1000)}<small>h:min to go</small></p>`;
  const checks = [];
  if (!plan) {
    out += `<p class="bad">No saved plan. The run will refuse.</p>`;
  } else if (plan.error) {
    out += `<p class="bad">plan.json cannot be used: ${esc(plan.error)}</p>`;
  } else {
    let t = `${plural(plan.runnable, "job")}, reviewed ${when(D(plan.reviewed_at), now)}.`;
    if (plan.refreshed) t += ` Re-checked on fresh readings ${when(D(plan.created_at), now)}.`;
    if (plan.ready_for_next_run) {
      t += sch.refreshes ? " Re-checked on fresh readings just before it starts; it runs only if every job still lands where you reviewed it."
                         : ` Good until ${when(D(plan.expires_at), now)}. The run refuses if a reading or job changes first.`;
    }
    out += `<p>${t}</p>`;
    if (!plan.ready_for_next_run) {
      out += `<p class="bad">${sch.refreshes ? "The review will be too old by then. Review a new plan before the run."
                                             : `It expires ${when(D(plan.expires_at), now)}, before the run, so the run will refuse it.`}</p>`;
    }
    if (plan.check) checks.push(check(plan.check.ok, plan.check.ok ? "Plan still matches the readings" : `Plan no longer matches: ${esc(plan.check.text)}`));
  }
  if (!plan || plan.error || runnable !== plan.runnable) out += preview;
  const a = sch.agents.find(x => x.label === sch.runner);
  if (a && a.status) {
    const st = a.status;
    const txt = st.loaded === false ? "not loaded, so it will not run" : st.loaded == null ? (st.note || "status unknown")
      : `loaded · ${st.runs === "0" ? "has not run yet" : `ran ${st.runs} time${st.runs === "1" ? "" : "s"}${st.last_exit != null ? `, last exit ${st.last_exit}` : ""}`}`;
    checks.push(check(st.loaded !== false, `launchd ${esc(a.label)} ${esc(txt)}`));
  }
  checks.push(readingsCheck(s));
  return out + `<ul class="checks" aria-label="Pre-run checks">${checks.join("")}</ul>`;
}

// 01 Now, third column: what needs a person. A plan without a usable reading gets its own line and the fix.
function needsYou(s) {
  const flagged = s.pools.filter(p => p.flags.length);
  const other = s.issues.filter(i => !flagged.some(p => i.startsWith(p.label + ": ")));
  let out = `<p class="label">Needs you</p>`;
  const fix = `It is left out of every plan until you run <span class="mono">allo-router sync</span> or <span class="mono">set</span>.`;
  flagged.forEach((p, i) => {
    const head = p.left == null ? `${esc(p.label)} has no reading.` : `${esc(p.label)}: ${esc(p.flags.join(", ").toLowerCase())} reading.`;
    out += `<h3${i ? ' class="more"' : ""}>${head}</h3><p>${fix}</p>`;
  });
  for (const i of other) out += `<p class="issue">${esc(i)}</p>`;
  for (const e of s.errors) out += `<p class="issue err">${esc(e)}</p>`;
  if (!flagged.length && !other.length && !s.errors.length) {
    out += `<h3 class="ok">Nothing.</h3><p class="dim">No warnings from the plans, the schedule or the last job.</p>`;
  }
  return out;
}

function now01(s, now, pal) {
  return `<div class="now"><div>${running(s, now, pal)}</div><div>${nextRun(s, now)}</div><div class="warn">${needsYou(s)}</div></div>`;
}

// 02 Plans: a ledger row per plan
function onPlan(s, id) {
  const run = s.running && s.running.alive && s.running.pool === id ? s.running : null;
  const ids = s.queue.filter(q => !q.skip && q.pool === id && !(run && String(q.task_id) === String(run.task_id))).map(q => q.task_id);
  return { run, ids };
}
function ledger(s, now, pal) {
  if (!s.pools.length) return `<p class="empty">No plans yet. Add them to pools.json with <span class="mono">allo-router init</span>.</p>`;
  const paced = s.pools.map(pace).filter(x => x != null);
  const tight = paced.length > 1 ? Math.max(...paced) : null;
  const rows = s.pools.map(p => {
    const c = pal[p.id], flagged = p.flags.length > 0, credit = p.kind === "credit_usd";
    let left;
    if (p.left == null) left = "?";
    else if (credit) left = esc(p.left_text);
    else left = `${esc(String(p.left_text).replace(/%$/, ""))}<small>%</small>`;
    let bar, note;
    if (flagged) {
      bar = `<div class="bar none"></div>`;
      note = `<div class="bar-note bad">${p.left == null ? "No reading" : esc(cap(p.flags.join(", ").toLowerCase()))}. Not routed.</div>`;
    } else if (credit) {
      const read = p.updated_at ? `Last read ${ago(D(p.updated_at), now)}, ${p.sync || (p.source || "").startsWith("sync") ? "by sync" : "by hand"}.` : "Never read.";
      bar = `<div class="bar line" style="background:${c.line}"></div>`;
      note = `<div class="bar-note">Dollars with no weekly cap, so no bar. ${read}</div>`;
    } else {
      const x = pace(p);
      bar = `<div class="bar"><i style="width:${Math.max(0, Math.min(100, p.left || 0))}%;background:${c.line}"></i></div>`;
      note = x != null ? `<div class="bar-note">Use ${paceText(x)} pts an hour to finish it by reset.${x === tight ? ` Tightest of the ${WORDS[paced.length] || paced.length}.` : ""}</div>` : "";
    }
    const r = D(p.resets_at);
    const resets = !r ? `<b>No reset time</b>` : r <= now ? `<b class="bad">Reset passed</b><span class="dim">read again</span>`
                      : `<b>${resetText(r, now)}</b><span class="dim">${inSpan(r - now)}</span>`;
    const { run, ids } = onPlan(s, p.id);
    const shown = ids.slice(0, 8).map(esc).join(" ") + (ids.length > 8 ? ` +${ids.length - 8}` : "");
    const jobs = run || ids.length ? `${run ? `<span class="run">${esc(run.task_id)}</span> ` : ""}${shown}` : `<span class="dim">none</span>`;
    return `<tr><td class="plan${flagged ? " dim" : ""}"><span class="key" style="background:${flagged ? "var(--none)" : c.line}"></span>${esc(p.label)}</td>
      <td class="left fig${flagged ? " dim" : ""}">${left}</td><td class="runway">${bar}${note}</td>
      <td class="resets">${resets}</td><td class="ids">${jobs}</td></tr>`;
  }).join("");
  return `<table class="ledger"><thead><tr><th scope="col">Plan</th><th scope="col" class="r">Left</th><th scope="col">Runway to reset</th><th scope="col">Resets</th><th scope="col">Jobs on it</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// 02 Plans, fold-out: each plan as a track running to its reset, with its jobs as numbered chips.
let diagramOpen = false;
try { diagramOpen = localStorage.getItem("allo.diagram") === "open"; } catch (e) {}

function diagram(s, now, pal) {
  const pools = s.pools;
  if (!pools.length) return "";
  const W = 1020, NOW = 170, END = 975, TOP = 50, PITCH = 60;
  const yb = TOP + (pools.length - 1) * PITCH + 32, H = yb + 26;
  // The axis spans the subscriptions' resets (up to a week); credit that resets later gets an arrow instead.
  const hours = pools.filter(p => p.kind !== "credit_usd").map(p => p.hours_to_reset).filter(h => h != null && h > 0 && h <= 168);
  const horizon = Math.max(48, ...hours) * 1.06;
  const pxh = (END - NOW) / horizon;
  const xAt = d => NOW + (d - now) / 3600000 * pxh;
  let g = `<g stroke="var(--rule)">`, ax = `<g class="d-ax">`;
  const mid = new Date(now); mid.setHours(24, 0, 0, 0);
  for (let t = mid; xAt(t) <= END; t = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1)) {
    const x = xAt(t).toFixed(1);
    g += `<line x1="${x}" y1="22" x2="${x}" y2="${yb}"/>`;
    ax += `<text x="${(+x + 5).toFixed(1)}" y="${yb + 20}">${dayName(t)} ${mdy(t)}</text>`;
  }
  g += `</g>${ax}</g>`;
  g += `<line x1="${NOW}" y1="14" x2="${NOW}" y2="${yb + 2}" stroke="var(--ink)" stroke-width="1.5"/>
        <text x="${NOW}" y="10" class="d-ax" text-anchor="middle" style="fill:var(--ink);font-weight:700">Now</text>`;
  const sch = s.schedule, nxt = sch && sch.next_run ? D(sch.next_run) : null;
  let xNext = null;
  if (nxt && xAt(nxt) < END) {
    xNext = xAt(nxt);
    g += `<line x1="${xNext.toFixed(1)}" y1="22" x2="${xNext.toFixed(1)}" y2="${yb + 2}" stroke="var(--ink)" stroke-dasharray="2 3"/>
          <text x="${(xNext + 4).toFixed(1)}" y="32" class="d-ax">${hhmm(nxt)} run</text>`;
  }
  const x0 = (xNext != null ? xNext : NOW + 30) + 8;
  pools.forEach((p, i) => {
    const y = TOP + i * PITCH, c = pal[p.id], flagged = p.flags.length > 0;
    const stroke = flagged ? "var(--none)" : c.line, dash = flagged ? ' stroke-dasharray="9 7"' : "";
    const name = p.label.length > 20 ? p.label.slice(0, 19) + "…" : p.label;
    g += `<text x="0" y="${y + 5}"><title>${esc(p.label)}</title><tspan class="d-nm"${flagged ? ' style="fill:var(--muted)"' : ""}>${esc(name)}</tspan><tspan class="d-v" dx="6">${esc(p.left_text)}</tspan></text>`;
    const r = D(p.resets_at);
    const passed = r && r <= now, beyond = r && !passed && xAt(r) > END;
    let limit = END;
    if (!r || passed) {
      const xe = passed ? NOW + 24 : END;
      g += `<line x1="${NOW}" y1="${y}" x2="${xe}" y2="${y}" stroke="${stroke}" stroke-width="7"${dash}/>`;
      g += `<text x="${passed ? xe + 10 : END}" y="${passed ? y + 4 : y - 15}" class="d-rs"${passed ? "" : ' text-anchor="end"'}>${passed ? "reset passed, read again" : "no reset time"}</text>`;
      if (passed) limit = NOW + 24;
    } else if (beyond) {
      g += `<line x1="${NOW}" y1="${y}" x2="${END + 21}" y2="${y}" stroke="${stroke}" stroke-width="7"${dash}/>
            <path d="M${END + 19} ${y - 9} l13 9 l-13 9 z" fill="${stroke}"/>
            <text x="${END - 15}" y="${y - 15}" class="d-rs" text-anchor="end">resets ${mdy(r)}</text>`;
    } else {
      const xr = Math.max(NOW + 20, xAt(r)), rs = `resets ${dayName(r)} ${hhmm(r)}`;
      const fits = xr + 15 + rs.length * 6.6 < W;
      g += `<line x1="${NOW}" y1="${y}" x2="${xr.toFixed(1)}" y2="${y}" stroke="${stroke}" stroke-width="7"${dash}/>
            <path d="M${xr.toFixed(1)} ${y - 11} v22 M${xr.toFixed(1)} ${y} h9" stroke="var(--red)" stroke-width="2.5" fill="none"/>
            <text x="${(fits ? xr + 15 : xr - 8).toFixed(1)}" y="${fits ? y + 4 : y - 15}" class="d-rs"${fits ? "" : ' text-anchor="end"'}>${rs}</text>`;
      limit = xr;
    }
    const x = pace(p);
    if (flagged) g += `<text x="${x0}" y="${y + 28}" class="d-sub">Not routed until it has a reading</text>`;
    else if (x != null) g += `<text x="${x0}" y="${y + 28}" class="d-sub">${paceText(x)} pts an hour uses it all by reset</text>`;
    // jobs: running chip on Now, queued chips in run order after the next-run line (not to time scale)
    const { run, ids } = onPlan(s, p.id);
    if (run) g += chip(NOW - 19, y, 38, run.task_id, run.name, true);
    let cx = x0, hidden = 0;
    for (const id of ids) {
      const w = Math.max(32, 12 + String(id).length * 7.5);
      if (cx + w > limit - 4) { hidden++; continue; }
      const q = s.queue.find(q => String(q.task_id) === String(id));
      g += chip(cx, y, w, id, q && q.name, false);
      cx += w + 6;
    }
    if (hidden) g += `<text x="${cx + 2}" y="${y + 4}" class="d-ax">+${hidden} more</text>`;
  });
  const desc = pools.map(p => `${p.label} ${p.left == null ? "no reading" : p.left_text + " left"}, ${p.resets_at ? "resets " + resetText(D(p.resets_at), now) : "no reset time"}`).join(". ");
  const stuck = s.queue.filter(q => !q.pool);
  return `<details class="diagram" id="diagram"${diagramOpen ? " open" : ""}>
    <summary><b class="when-closed">Show</b><b class="when-open">Hide</b> route diagram<span>each plan as a track running to its reset</span></summary>
    <div class="scroll"><svg viewBox="0 0 ${W + 12} ${H}" role="img" aria-label="${esc(`Plans drawn as tracks to their reset times. ${desc}.`)}">${g}</svg></div>
    ${stuck.length ? `<p class="d-stuck">No plan fits ${stuck.map(q => "#" + esc(q.task_id)).join(", ")}; see 03 Jobs.</p>` : ""}
    <div class="d-legend">
      <span><svg width="20" height="12"><rect width="20" height="12" fill="#16140F"/></svg>Running</span>
      <span><svg width="20" height="12"><rect x="1" y="1" width="18" height="10" fill="none" stroke="#16140F" stroke-width="1.5"/></svg>Queued, in run order (not to time scale)</span>
      <span><svg width="12" height="14"><path d="M3 1 v12 M3 7 h7" stroke="#B3200F" stroke-width="2.5"/></svg>Reset</span>
      <span><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke="#A8A194" stroke-width="5" stroke-dasharray="7 5"/></svg>No usable reading</span>
    </div></details>`;
}
function chip(x, y, w, id, name, running) {
  const tip = name ? `${esc(String(name))} (#${esc(id)})` : `#${esc(id)}`;
  return `<g><title>${tip}</title><rect x="${x.toFixed(1)}" y="${y - 10}" width="${w}" height="20" fill="${running ? "var(--ink)" : "var(--paper)"}"${running ? "" : ' stroke="var(--ink)" stroke-width="1.5"'}/>` +
    `<text x="${(x + w / 2).toFixed(1)}" y="${y + 4}" class="d-chip" fill="${running ? "var(--paper)" : "var(--ink)"}">${esc(id)}</text></g>`;
}

// 03 Jobs: what would run next, in run order, and what already ran
function jobs(s, now, pal) {
  const h = s.history || {};
  const sch = s.schedule, nxt = sch && sch.next_run ? D(sch.next_run) : null;
  const run = s.running && s.running.alive ? s.running : null;
  const planName = id => id && pal[id] ? esc(pal[id].label) : "None";
  const queued = s.queue.filter(q => !q.skip && q.pool && !(run && String(q.task_id) === String(run.task_id)))
    .map(q => `<tr><td class="id">${esc(q.task_id)}</td><td>${esc(q.name)}</td><td class="p">${planName(q.pool)}</td></tr>`);
  for (const q of s.queue.filter(q => !q.pool)) {
    queued.push(`<tr><td class="id">${esc(q.task_id)}</td><td>${esc(q.name)}<span class="note">${esc(q.note || "No plan may take this job's data")}</span></td><td class="p">None</td><td class="s bad">No plan fits</td></tr>`);
  }
  const qHead = `<h3>${nxt ? `Queued for ${hhmm(nxt)}` : "Would run next"} <span class="dim">· in run order</span></h3>`;
  const left = qHead + (queued.length ? `<table><tbody>${queued.join("")}</tbody></table>`
    : `<p class="empty">Nothing queued. Add jobs to tasks.json, then preview with <span class="mono">allo-router run</span>.</p>`);
  let title = "Ran today", sub = "newest first", list = h.today || [];
  if (!list.length && (h.recent || []).length) { title = "Recent jobs"; sub = "nothing ran today"; list = h.recent; }
  const ran = list.map(r => {
    const p = r.pool ? pal[r.pool] : null, credit = p && p.pool.kind === "credit_usd";
    const mis = r.misroute && r.misroute.length;
    let res = RESULT[r.status] || esc(r.status), cls = "";
    if (r.status === "ok") {
      if (credit && r.cost_usd != null) res += ` · ${money(r.cost_usd)}`;
      else if (r.plan_used_pts != null) res += ` · ${r.plan_used_pts} pts`;
    } else if (["failed", "timeout", "error"].includes(r.status) || mis) cls = "bad";
    else cls = "dim";
    let note = "";
    if (r.status === "skipped" && r.reason) note = cap(r.reason);
    else if (r.status === "timeout") note = `Stopped at ${Math.round((r.seconds || 0) / 60)} min; counted as a misroute`;
    else if (mis) note = `Misroute: ${r.misroute.join("; ")}`;
    const tip = [r.seconds != null ? `Took ${clock(r.seconds)}` : "", r.output_tokens != null ? `${tokens(r.output_tokens)} tokens out` : "",
                 r.plan_used_why || ""].filter(Boolean).join(" · ");
    return `<tr${tip ? ` title="${esc(tip)}"` : ""}><td class="id">${esc(r.task_id)}</td><td>${esc(r.name)}${r.recurring ? ` <span class="dim">· ${esc(r.recurring)}</span>` : ""}${note ? `<span class="note">${esc(note)}</span>` : ""}</td>
      <td class="p">${planName(r.pool)}</td><td class="s ${cls}">${res}</td></tr>`;
  });
  const right = `<h3>${title} <span class="dim">· ${sub}</span></h3>` + (ran.length ? `<table><tbody>${ran.join("")}</tbody></table>`
    : `<p class="empty">Nothing has run yet. Jobs are logged by <span class="mono">allo-router run --plan plan.json --execute</span>.</p>`);
  return `<div class="jobs"><div>${left}</div><div>${right}</div></div>`;
}

// 04 This week: counts, jobs per day, and the estimate of what the plans saved
function week(s, now, pal) {
  const h = s.history, w = h && h.logged ? h.week : null;
  if (!w) return `<p class="empty">Nothing has run here yet. Jobs are logged by <span class="mono">allo-router run --plan plan.json --execute</span>.</p>`;
  const totals = w.days.map(d => Object.values(d.by_pool).reduce((a, b) => a + b, 0));
  const top = Math.max(1, ...totals);
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const days = w.days.map((d, i) => `<div class="${d.date === today ? "today" : ""}${d.future ? " future" : ""}" title="${esc(d.label)}: ${plural(totals[i], "job")}"><i style="height:${Math.round(totals[i] / top * 72)}px"></i><span>${esc(d.label[0])}</span></div>`).join("");
  const stats = `<div class="week">
      <div><span class="fig">${w.routed}</span><span class="dim">routed</span></div><div><span class="fig">${w.ok}</span><span class="dim">done</span></div>
      <div><span class="fig">${w.skipped}</span><span class="dim">skipped</span></div>
      <div class="${w.misroutes ? "alert" : ""}" title="A misroute is a job that failed or timed out, ran on an unverified reading, or was skipped because no plan could take it."><span class="fig">${w.misroutes}</span><span class="dim">misroutes</span></div>
      <div><span class="dim" style="font-size:12px">Jobs per day</span><div class="days" aria-label="Jobs per day: ${esc(w.days.map((d, i) => `${d.label} ${totals[i]}`).join(", "))}">${days}</div></div>
    </div>`;
  const used = w.measured.map(m => {
    const p = pal[m.pool] || { label: m.pool, pool: {} };
    if (p.pool.kind === "credit_usd") return `${esc(p.label)} ${money(m.cost_usd)}${m.cost_n < m.jobs ? ` on ${m.cost_n} of ${m.jobs} jobs` : ""}`;
    if (!m.measured) return `${esc(p.label)} n/a`;
    return `${esc(p.label)} ${+m.pts.toFixed(2)} pts${m.measured < m.jobs ? ` on ${m.measured} of ${m.jobs} jobs` : ""}`;
  });
  const sv = h.saved;
  const saved = sv && sv.week_usd != null ? `<div class="saved"><span class="fig">${money(sv.week_usd)}</span>
      <p><b>Saved this week, estimated.</b> Each job that ran, priced at the API list price, minus what it cost on the plan it ran on (nothing extra on a subscription). ${plural(sv.week_priced, "job")} priced.${used.length ? ` Measured plan use: ${used.join(", ")}.` : ""} <span class="mono">allo-router report</span> shows the method.</p></div>`
    : used.length ? `<div class="saved"><span></span><p>Measured plan use: ${used.join(", ")}.</p></div>` : "";
  return stats + saved;
}

function render(s) {
  const now = D(s.now), pal = palette(s.pools);
  document.title = (s.example ? "Example data: " : "") + "Allo Router, " + s.headline;
  const since = s.history && s.history.week ? new Date(s.history.week.since + "T00:00:00") : null;
  document.getElementById("app").innerHTML = mast(s, now)
    + sec("01", "Now", now01(s, now, pal))
    + sec("02", "Plans", ledger(s, now, pal) + diagram(s, now, pal))
    + sec("03", "Jobs", jobs(s, now, pal))
    + sec("04", "This week", week(s, now, pal), since ? `<span class="since dim">since ${dayName(since)} ${mdy(since)}</span>` : "")
    + `<footer>Read-only. Reads pools.json, tasks.json, plan.json and runs.jsonl in ${esc(s.config_dir)}, and asks launchd whether its agents are loaded. Never syncs, never starts a job. Refreshes every 15 seconds. allo-router ${esc(s.version)}.${s.example ? " Example data: every plan, job and number on this page is made up. Run <span class=\"mono\">allo-router dashboard</span> without <span class=\"mono\">--example</span> to see your own." : ""}</footer>`;
  const d = document.getElementById("diagram");
  if (d) d.addEventListener("toggle", () => {
    diagramOpen = d.open;
    try { localStorage.setItem("allo.diagram", d.open ? "open" : "closed"); } catch (e) {}
  });
}

async function load() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    render(await r.json());
  } catch (e) {
    const app = document.getElementById("app");
    if (!app.querySelector("header")) app.innerHTML = `<p class="err">Could not reach the dashboard server: ${esc(e.message)}. Is <span class="mono">allo-router dashboard</span> still running?</p>`;
  }
}
load();
setInterval(load, 15000);
</script>
</body>
</html>
"""
