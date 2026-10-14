"""The page `allo-router dashboard` serves. One self-contained HTML file: it fetches /api/state and draws it.
Kept in a module (not a data file) so it ships with a plain `pip install`."""

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
    --paper: #E4E9E3; --ink: #0E1D18; --muted: #4F5E57; --rule: #BCC6BE; --none: #9AA59F;
    --codex: #2446D6; --claude: #E39B0B; --credit: #9A2C7C; --other: #2F6F73;
    --codex-text: #2446D6; --claude-text: #8A5A00; --credit-text: #9A2C7C; --other-text: #2F6F73;
    --stop: #D3261A; --reset-text: #B3200F;
    --sans: "Archivo", "Arial Narrow", ui-sans-serif, system-ui, sans-serif;
    --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--paper); color: var(--ink); }
  body { font: 400 15px/1.45 var(--sans); font-variant-numeric: tabular-nums; }
  a { color: inherit; }
  a:focus-visible { outline: 3px solid var(--ink); outline-offset: 2px; }
  .wrap { max-width: 1440px; margin: 0 auto; padding: 0 56px 40px; }
  .mono { font-family: var(--mono); font-size: 13px; }
  .dim { color: var(--muted); }

  header { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 28px; padding: 18px 0 14px; border-bottom: 1px solid var(--rule); }
  .mark { font-variation-settings: "wdth" 125; font-stretch: 125%; font-weight: 800; font-size: 19px; letter-spacing: -0.01em; }
  .example-tag { font-size: 15px; font-weight: 700; color: var(--reset-text); }
  header .path { color: var(--muted); margin-right: auto; overflow-wrap: anywhere; }
  header .meta { color: var(--muted); }
  header .meta b { color: var(--ink); font-weight: 600; }
  .example-line { margin: 0; padding: 10px 0; border-bottom: 1px solid var(--rule); font-weight: 600; }
  .example-line span { font-weight: 400; color: var(--muted); }

  .band { display: grid; grid-template-columns: minmax(0, 1fr) 460px; column-gap: 56px; }
  .band > * { min-width: 0; }
  .band > .side { border-left: 1px solid var(--rule); padding-left: 40px; }
  .status { padding: 22px 0 18px; }
  .big { margin: 0; font-variation-settings: "wdth" 62; font-stretch: 62%; font-weight: 820; font-size: 64px; line-height: .95; letter-spacing: -0.01em; }
  .sub { margin: 12px 0 0; font-size: 16px; max-width: 46em; }
  .sub .dim { color: var(--muted); }
  .why-line { margin: 8px 0 0; font-size: 15px; color: var(--muted); max-width: 46em; }
  .why-line b { color: var(--ink); font-weight: 600; }
  .issues { margin: 12px 0 0; padding: 0; list-style: none; max-width: 52em; }
  .issues li { padding: 7px 0; border-top: 1px solid var(--rule); font-size: 15px; }
  .issues li:first-child { border-top: 0; padding-top: 2px; }
  .next .clock { font-variation-settings: "wdth" 62; font-stretch: 62%; font-weight: 820; font-size: 48px; line-height: 1; margin: 0; }
  .next .when { margin: 6px 0 0; font-size: 17px; font-weight: 700; }
  .next p { margin: 4px 0 0; font-size: 15px; }
  .next .cmd { margin-top: 10px; color: var(--muted); font-size: 14px; }
  .next .cmd code { font-family: var(--mono); font-size: 13px; color: var(--ink); }
  .ok-text { color: var(--ink); }
  .bad { color: var(--reset-text); font-weight: 600; }

  .yard { border-top: 1px solid var(--rule); padding-top: 8px; overflow-x: auto; }
  .yard > svg { display: block; width: 100%; min-width: 900px; height: auto; }
  .yard > svg text { paint-order: stroke; stroke: var(--paper); stroke-width: 4px; stroke-linejoin: round; }
  .yard > svg .car-q text, .yard > svg .car-s text, .yard > svg .car-x text { stroke: none; }
  .yard text { font-family: var(--sans); }
  .ax { font-size: 13px; fill: var(--muted); }
  .ax-now { font-size: 13px; font-weight: 700; fill: var(--ink); }
  .nm { font-size: 15px; font-weight: 700; fill: var(--ink); }
  .lf { font-size: 15px; font-weight: 600; fill: var(--ink); }
  .pace { font-size: 13.5px; fill: var(--muted); }
  .rs { font-size: 13.5px; font-weight: 700; fill: var(--reset-text); }
  .car-q rect { fill: var(--paper); stroke: var(--ink); stroke-width: 1.5; }
  .car-q text { fill: var(--ink); font-size: 11px; font-weight: 700; text-anchor: middle; }
  .car-s rect { fill: var(--ink); }
  .car-s text { fill: var(--paper); font-size: 11px; font-weight: 700; text-anchor: middle; }
  .car-x rect { fill: var(--paper); stroke: var(--reset-text); stroke-width: 1.5; stroke-dasharray: 3 2; }
  .car-x text { fill: var(--reset-text); font-size: 11px; font-weight: 700; text-anchor: middle; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 26px; margin: 6px 0 0; padding: 0 0 12px; font-size: 13.5px; color: var(--muted); }
  .legend svg { vertical-align: middle; margin-right: 7px; }

  .lower { border-top: 1px solid var(--rule); padding-top: 14px; }
  h2 { margin: 0 0 8px; font-size: 17px; font-weight: 700; }
  h2 .dim { font-weight: 400; font-size: 15px; }
  table { width: 100%; border-collapse: collapse; }
  th { white-space: nowrap; text-align: left; font-weight: 500; color: var(--muted); font-size: 13.5px; padding: 4px 12px 8px 0; border-bottom: 1px solid var(--rule); }
  td { padding: 10px 12px 10px 0; border-bottom: 1px solid var(--rule); vertical-align: baseline; }
  tr:last-child td { border-bottom: 0; }
  td.when, td.num, td.plan, td.res { white-space: nowrap; }
  td.job { min-width: 260px; }
  td.job b { font-weight: 700; margin-right: 6px; }
  .num { text-align: right; }
  .seg { display: inline-block; width: 16px; height: 5px; vertical-align: middle; margin-right: 8px; position: relative; top: -1px; }
  .note { display: block; font-size: 13px; color: var(--muted); white-space: normal; }
  .queued { color: var(--muted); }
  .empty { padding: 12px 0; color: var(--muted); }

  .stats { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
  .stats div { padding: 8px 0 8px 14px; }
  .stats div:first-child { padding-left: 0; }
  .stats div + div { border-left: 1px solid var(--rule); }
  .stats b { display: block; font-variation-settings: "wdth" 62; font-stretch: 62%; font-weight: 820; font-size: 40px; line-height: 1; }
  .stats span { font-size: 13.5px; color: var(--muted); }
  .stats .alert b { color: var(--reset-text); }
  .defn { margin: 6px 0 0; font-size: 13px; color: var(--muted); }
  .units { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; margin: 14px 0 0; align-items: end; }
  .units .day { display: flex; flex-direction: column; align-items: center; gap: 4px; min-height: 64px; justify-content: flex-end; }
  .units .count { font-size: 13px; font-weight: 700; }
  .units .sq { display: flex; flex-wrap: wrap-reverse; width: 50px; gap: 2px; }
  .units .sq i { width: 8px; height: 8px; display: block; }
  .units .lbl { font-size: 12.5px; color: var(--muted); border-top: 1px solid var(--ink); width: 100%; text-align: center; padding-top: 3px; }
  .units .today .lbl { color: var(--ink); font-weight: 700; }
  .units .future .lbl { border-top-color: var(--rule); }
  .used { margin-top: 12px; }
  .used .row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; padding: 6px 0; border-top: 1px solid var(--rule); align-items: baseline; }
  .used .row:first-child { border-top: 0; }
  .used .v { font-weight: 700; text-align: right; }
  .used .n { color: var(--muted); font-size: 13.5px; }
  .est { margin: 10px 0 0; padding-top: 10px; border-top: 1px solid var(--ink); font-size: 14px; color: var(--muted); }
  .est b { color: var(--ink); font-size: 16px; }

  footer { margin-top: 28px; padding-top: 12px; border-top: 1px solid var(--rule); color: var(--muted); font-size: 13.5px; }
  .err { color: var(--reset-text); font-weight: 600; }

  @media (max-width: 1100px) {
    .band { grid-template-columns: minmax(0, 1fr); }
    .band > .side { border-left: 0; padding-left: 0; border-top: 1px solid var(--rule); padding-top: 16px; margin-bottom: 16px; }
  }
  @media (max-width: 700px) {
    .wrap { padding: 0 20px 32px; }
    .big { font-size: 44px; line-height: 1; }
    thead { display: none; }
    tr { display: grid; grid-template-columns: auto 1fr auto; column-gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--rule); }
    td { border: 0; padding: 0; }
    td.job { grid-column: 2; min-width: 0; }
    td.plan, td.num { grid-column: 2; grid-row: auto; text-align: left; font-size: 13px; color: var(--muted); }
    td.res { grid-column: 3; grid-row: 1; }
  }
</style>
</head>
<body>
<div class="wrap" id="app"><p class="empty">Loading the router's state…</p></div>
<script>
"use strict";
const COLORS = { codex: ["#2446D6", "#16308F"], claude: ["#E39B0B", "#B57400"], credit: ["#9A2C7C", "#6E1F59"], other: ["#2F6F73", "#1E4A4D"] };
const TEXT = { codex: "#2446D6", claude: "#8A5A00", credit: "#9A2C7C", other: "#2F6F73" };
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const D = s => s ? new Date(s) : null;
const pad = n => String(n).padStart(2, "0");
const hhmm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const dayName = d => d.toLocaleDateString("en-US", { weekday: "short" });
const mdy = d => `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
function sameDay(a, b) { return a.toDateString() === b.toDateString(); }
function when(d, now) { return sameDay(d, now) ? hhmm(d) : `${dayName(d)} ${hhmm(d)}`; }
function span(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${s} s`;
  return `${s} s`;
}
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

function palette(pools) {
  const seen = {}, out = {};
  for (const p of pools) {
    const i = seen[p.family] = (seen[p.family] || 0) + 1;
    out[p.id] = { line: COLORS[p.family][Math.min(i - 1, 1)], text: TEXT[p.family], label: p.label, pool: p };
  }
  return out;
}

const RESULT = { ok: "Done", failed: "Failed", timeout: "Timed out", error: "Error", skipped: "Skipped", "dry-run": "Dry run" };

function header(s, now, pal) {
  const sch = s.schedule;
  let sched = "No scheduled runs";
  if (sch && sch.next_run) {
    const n = D(sch.next_run), a = sch.agents.find(x => x.label === sch.runner);
    const on = a && a.status && a.status.loaded !== false;
    sched = `Scheduled runs <b>${on ? "on" : "not loaded"}</b>, ${hhmm(n)}${a && a.calendar && a.calendar.Weekday == null ? " daily" : ""}`;
  }
  const synced = s.pools.map(p => p.last_sync && D(p.last_sync.taken_at)).filter(Boolean).sort((a, b) => b - a)[0];
  const sync = synced ? `Synced <b>${hhmm(synced)}</b>, ${ago(synced, now)}. To read again: <span class="mono">allo-router sync</span>`
                      : `Not synced. Readings come from <span class="mono">allo-router sync</span> or <span class="mono">set</span>`;
  return `<header><span class="mark">Allo Router</span>${s.example ? '<span class="example-tag">Example data</span>' : ""}
    <span class="path mono">${esc(s.config_dir)}</span><span class="meta">${sched}</span><span class="meta">${sync}</span></header>
    ${s.example ? '<p class="example-line">Example data. Every plan, job and number on this page is made up. <span>Run <span class="mono">allo-router dashboard</span> without <span class="mono">--example</span> to see your own.</span></p>' : ""}`;
}

function decision(q, pal) {
  if (!q || !q.pool) return "";
  const p = pal[q.pool]; if (!p) return "";
  const name = `<b style="color:${p.text}">${esc(p.label)}</b>`;
  if (p.pool.kind === "credit_usd") return `${name} because this job needs the API, and API jobs only run on credit while it fits above the reserve.`;
  if (q.note && q.note.includes("UNKNOWN")) return `${name}: its headroom is unknown, so confirm before trusting it.`;
  return `${name} had the most of its limit left (${esc(q.note)}) of the plans allowed this job's data.`;
}

function status(s, now, pal) {
  const run = s.running, qById = Object.fromEntries(s.queue.map(q => [String(q.task_id), q]));
  let left = "";
  if (s.level === "running" && run) {
    const p = pal[run.pool] || { text: "inherit", label: run.pool };
    const pool = s.pools.find(x => x.id === run.pool);
    left = `<p class="big">Running #${esc(run.task_id)} on <span style="color:${p.text}">${esc(p.label)}</span></p>
      <p class="sub">${esc(run.name)}. <span class="dim">Started ${hhmm(D(run.started_at))}, ${span(run.seconds)} so far.${pool && pool.left != null ? ` ${esc(p.label)} read ${esc(pool.left_text)} left before it started.` : ""}</span></p>
      ${qById[String(run.task_id)] ? `<p class="why-line">Picked ${decision(qById[String(run.task_id)], pal)}</p>` : ""}`;
  } else if (s.level === "attention") {
    left = `<p class="big">Needs attention</p>`;
  } else {
    left = `<p class="big">${esc(s.headline)}</p>`;
  }
  if (s.issues.length) left += `<ul class="issues">${s.issues.map(i => `<li>${esc(i)}</li>`).join("")}</ul>`;
  if (s.errors.length) left += `<ul class="issues">${s.errors.map(i => `<li class="err">${esc(i)}</li>`).join("")}</ul>`;
  return `<section class="band status"><div>${left}</div><div class="side next">${nextRun(s, now)}</div></section>`;
}

function nextRun(s, now) {
  const sch = s.schedule, plan = s.plan;
  if (!sch || !sch.next_run) {
    return `<p class="clock">No schedule</p><p>Nothing starts on its own. Jobs run when you run a reviewed plan:</p>
      <p class="cmd"><code>allo-router run --plan plan.json --execute</code></p>`;
  }
  const n = D(sch.next_run);
  const runnable = s.queue.filter(q => !q.skip).length;
  let out = `<p class="clock">${hhmm(n)}</p><p class="when">Scheduled run, in ${span((n - now) / 1000)}</p>`;
  if (!plan) {
    out += `<p class="bad">No saved plan. The run will refuse.</p>`;
  } else if (plan.error) {
    out += `<p class="bad">plan.json cannot be used: ${esc(plan.error)}</p>`;
  } else {
    const rv = D(plan.reviewed_at);
    out += `<p>Reviewed ${when(rv, now)}, ${plan.runnable} job${plan.runnable === 1 ? "" : "s"} to run.${plan.refreshed ? ` Re-checked on fresh readings ${when(D(plan.created_at), now)}.` : ""}</p>`;
    if (sch.refreshes) {
      out += `<p class="${plan.ready_for_next_run ? "ok-text" : "bad"}">${plan.ready_for_next_run
        ? "Just before the run, it is re-checked on fresh readings. It runs only if every job still comes out as reviewed."
        : "The review will be too old by then. Review a new plan before the run."}</p>`;
    } else {
      out += `<p class="${plan.ready_for_next_run ? "ok-text" : "bad"}">${plan.ready_for_next_run
        ? `Good until ${when(D(plan.expires_at), now)}. The run refuses if a reading or job changes first.`
        : `It expires ${when(D(plan.expires_at), now)}, before the run, so the run will refuse it.`}</p>`;
    }
  }
  out += `<p class="cmd">${runnable} job${runnable === 1 ? "" : "s"} would run if you previewed now. To review: <code>allo-router run</code></p>`;
  const a = sch.agents.find(x => x.label === sch.runner);
  if (a && a.status) {
    const st = a.status;
    const txt = st.loaded === false ? "not loaded" : st.loaded == null ? (st.note || "status unknown")
      : `loaded, ${st.runs === "0" ? "has not run yet" : `ran ${st.runs} time${st.runs === "1" ? "" : "s"}, last exit ${st.last_exit}`}`;
    out += `<p class="cmd">launchd ${esc(a.label)}: ${esc(txt)}</p>`;
  }
  return out;
}

function yard(s, now, pal) {
  const W = 1328, X0 = 250, NOW = 430, END = 1270;
  const pools = s.pools;
  const rows = pools.length || 1, PITCH = 66, TOP = 92;
  const H = TOP + rows * PITCH + 34;
  // The axis spans the subscriptions' resets (up to a week); credit that resets later gets an arrow instead.
  const hours = pools.filter(p => p.kind !== "credit_usd").map(p => p.hours_to_reset).filter(h => h != null && h <= 168);
  const horizon = Math.max(48, ...hours) * 1.06;
  const pxh = (END - NOW) / horizon;
  const xAt = d => NOW + (d - now) / 3600000 * pxh;
  const yIn = TOP + (rows - 1) * PITCH / 2 + 8;
  let g = "";
  // time axis: midnights and 6-hour ticks along the bottom
  const base = new Date(now); base.setMinutes(0, 0, 0);
  for (let t = new Date(base); xAt(t) <= END + 40; t = new Date(t.getTime() + 3600000)) {
    const x = xAt(t); if (x <= NOW + 2) continue;
    if (t.getHours() === 0) {
      g += `<line x1="${x}" y1="${TOP - 30}" x2="${x}" y2="${H - 20}" stroke="var(--rule)"/>
            <text x="${x + 5}" y="${H - 6}" class="ax">${dayName(t)} ${mdy(t)}</text>`;
    } else if (t.getHours() % 6 === 0) {
      g += `<line x1="${x}" y1="${H - 24}" x2="${x}" y2="${H - 18}" stroke="var(--muted)"/>`;
    }
  }
  g += `<line x1="${NOW}" y1="${TOP - 74}" x2="${NOW}" y2="${H - 20}" stroke="var(--ink)" stroke-width="1.5"/>
        <text x="${NOW + 6}" y="${TOP - 60}" class="ax-now">Now, ${dayName(now)} ${hhmm(now)}</text>`;
  const sch = s.schedule, nxt = sch && sch.next_run ? D(sch.next_run) : null;
  let xNext = null;
  if (nxt && xAt(nxt) < END) {
    xNext = xAt(nxt);
    g += `<line x1="${xNext}" y1="${TOP - 52}" x2="${xNext}" y2="${H - 20}" stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="3 4"/>
          <text x="${xNext + 6}" y="${TOP - 40}" class="ax" style="fill:var(--ink)">${hhmm(nxt)} run</text>`;
  }
  // incoming track, junction, branches
  g += `<path d="M0 ${yIn} H${X0}" stroke="var(--ink)" stroke-width="10" fill="none"/>`;
  const queued = s.queue.filter(q => !q.skip && q.pool);
  const stuck = s.queue.filter(q => !q.pool);
  pools.forEach((p, i) => {
    const y = TOP + i * PITCH + 8, c = pal[p.id], flagged = p.flags.length > 0;
    const stroke = flagged ? "var(--none)" : c.line;
    const dash = flagged ? ' stroke-dasharray="10 7"' : "";
    const dy = y - yIn, bend = Math.min(Math.abs(dy), 60) * Math.sign(dy);
    g += `<path d="M${X0} ${yIn} H${X0 + 14} C${X0 + 60} ${yIn} ${X0 + 60} ${y} ${X0 + 110 + Math.abs(bend) * 0} ${y} H${NOW}" stroke="${stroke}" stroke-width="12" fill="none"${dash}/>`;
    const reset = D(p.resets_at);
    const beyond = !reset || reset - now <= 0 || xAt(reset) > END;
    const xr = beyond ? END + 40 : Math.max(NOW + 20, xAt(reset));
    g += `<path d="M${NOW} ${y} H${xr}" stroke="${stroke}" stroke-width="14" fill="none"${dash}/>`;
    if (!beyond) {
      g += `<rect x="${xr}" y="${y - 20}" width="6" height="40" fill="var(--stop)"/><rect x="${xr}" y="${y - 3}" width="14" height="6" fill="var(--stop)"/>`;
    } else if (reset && reset - now > 0) {
      g += `<path d="M${END + 40} ${y - 10} l12 10 l-12 10 z" fill="${stroke}"/>`;
    }
    const left = p.left == null ? "no reading" : `${p.left_text} left`;
    g += `<text x="${NOW + 10}" y="${y - 14}" class="nm">${esc(p.label)}<tspan class="lf" dx="10">${esc(left)}</tspan></text>`;
    const rs = !reset ? "no reset time" : reset - now <= 0 ? "reset passed, read again" : `resets ${xAt(reset) > END ? mdy(reset) : `${dayName(reset)} ${hhmm(reset)}`}`;
    // Reset label ends at its stop, unless the line is too short to hold it next to the name; then it goes past the stop.
    const nameW = (p.label.length + left.length) * 8.2 + 24, rsW = rs.length * 7.4;
    const tight = !beyond && NOW + 10 + nameW > xr - 8 - rsW;
    const rx = beyond ? END + 52 : tight ? xr + 22 : xr - 8;
    g += `<text x="${rx}" y="${tight ? y + 5 : y - 14}" text-anchor="${tight ? "start" : "end"}" class="rs">${esc(rs)}</text>`;
    if (flagged) {
      g += `<text x="${NOW + 10}" y="${y + 26}" class="pace">${esc(p.flags.join(", "))}. Not routed until you sync or set a reading.</text>`;
    } else if (p.kind !== "credit_usd" && p.hours_to_reset && p.left != null) {
      const pace = p.left / p.hours_to_reset;
      g += `<text x="${tight ? NOW + 10 : rx}" y="${y + 26}" text-anchor="${tight ? "start" : "end"}" class="pace">${pace.toFixed(pace < 1 ? 2 : 1)} pts an hour uses it all by reset</text>`;
    }
    // cars: running solid in the plan colour at Now; queued outlined after the next-run marker, in queue order
    let x = (xNext || NOW + 40) + 8;
    const run = s.running && s.running.alive && s.running.pool === p.id ? s.running : null;
    if (run) g += car(NOW - 36, y, run.task_id, "s", c.line);
    for (const q of queued.filter(q => q.pool === p.id && !(run && String(q.task_id) === String(run.task_id)))) {
      if (x > (beyond ? END : xr) - 40) break;
      g += car(x, y, q.task_id, "q"); x += 38;
    }
  });
  let cx = 14;
  for (const q of stuck.slice(0, 5)) { g += car(cx, yIn, q.task_id, "x"); cx += 38; }
  g += `<circle cx="${X0}" cy="${yIn}" r="13" fill="var(--paper)" stroke="var(--ink)" stroke-width="4"/>
        <text x="0" y="${yIn + 30}" class="ax">tasks.json, ${s.queue.length} job${s.queue.length === 1 ? "" : "s"}${stuck.length ? `, ${stuck.length} no plan fits` : ""}</text>
        <text x="${X0}" y="${yIn + 34}" text-anchor="middle" class="ax" style="fill:var(--ink);font-weight:700">allo-router</text>`;
  const desc = pools.map(p => `${p.label}: ${p.left == null ? "no reading" : p.left_text + " left"}, ${p.resets_at ? "resets " + D(p.resets_at).toLocaleString() : "no reset time"}`).join("; ");
  return `<section class="yard"><svg viewBox="0 0 ${W + 70} ${H}" role="img" aria-labelledby="yt yd"><title id="yt">Plans on a shared time axis, each ending at its reset</title><desc id="yd">${esc(desc)}</desc>${g}</svg>
    <div class="legend">
      <span><svg width="30" height="16"><rect x="1" y="1" width="28" height="14" fill="var(--ink)"/></svg>Running</span>
      <span><svg width="30" height="16"><rect x="1" y="1" width="28" height="14" fill="var(--paper)" stroke="var(--ink)" stroke-width="1.5"/></svg>Would run next, in queue order (not to time scale)</span>
      <span><svg width="30" height="16"><rect x="1" y="1" width="28" height="14" fill="var(--paper)" stroke="var(--reset-text)" stroke-width="1.5" stroke-dasharray="3 2"/></svg>No plan fits</span>
      <span><svg width="16" height="20"><rect x="2" y="0" width="5" height="20" fill="var(--stop)"/><rect x="2" y="8" width="12" height="5" fill="var(--stop)"/></svg>Reset</span>
      <span><svg width="30" height="10"><path d="M0 5 H30" stroke="var(--none)" stroke-width="6" stroke-dasharray="8 5"/></svg>No usable reading</span>
    </div></section>`;
}
function car(x, y, id, kind, fill) {
  const label = `#${esc(id)}`, w = Math.max(30, 8 + label.length * 7);
  return `<g class="car-${kind}"><rect x="${x}" y="${y - 8}" width="${w}" height="16"${fill ? ` style="fill:${fill}"` : ""}/><text x="${x + w / 2}" y="${y + 4}">${label}</text></g>`;
}

function jobRow(r, now, pal, cls) {
  const p = r.pool ? pal[r.pool] : null;
  const plan = p ? `<span class="seg" style="background:${p.line}"></span>${esc(p.label)}` : `<span class="dim">Not assigned</span>`;
  let res = r.result || RESULT[r.status] || esc(r.status);
  const resCls = r.status === "ok" ? "" : ["failed", "timeout", "error"].includes(r.status) || (r.misroute && r.misroute.length) ? "bad" : r.status === "running" ? "ok-text" : "dim";
  let note = "";
  if (r.status === "skipped" && r.reason) note = `<span class="note">${esc(r.reason)}</span>`;
  const used = r.plan_used_pts != null ? `${r.plan_used_pts} pts` : r.status === "running" ? "after it ends" : "";
  return `<tr class="${cls || ""}"><td class="when">${r.whenText}</td><td class="job"><b>#${esc(r.task_id)}</b>${esc(r.name)}${note}</td>
    <td class="plan">${plan}</td><td class="res ${resCls}">${res}</td><td class="num">${r.seconds != null ? clock(r.seconds) : ""}</td>
    <td class="num"${r.plan_used_why ? ` title="${esc(r.plan_used_why)}"` : ""}>${used || '<span class="dim">–</span>'}</td>
    <td class="num">${r.output_tokens != null ? tokens(r.output_tokens) : '<span class="dim">–</span>'}</td></tr>`;
}

function jobs(s, now, pal) {
  const h = s.history || {};
  const sch = s.schedule, nxt = sch && sch.next_run ? D(sch.next_run) : null;
  const rows = [];
  if (s.running && s.running.alive) {
    const r = s.running;
    rows.push(jobRow({ task_id: r.task_id, name: r.name, pool: r.pool, status: "running", result: "Running", seconds: r.seconds, whenText: hhmm(D(r.started_at)) }, now, pal));
  }
  for (const q of s.queue.filter(q => !q.skip)) {
    if (s.running && s.running.alive && String(q.task_id) === String(s.running.task_id)) continue;
    rows.push(jobRow({ task_id: q.task_id, name: q.name, pool: q.pool, status: "queued", result: "Would run", whenText: nxt ? `Next ${hhmm(nxt)}` : "Next" }, now, pal, "queued"));
  }
  for (const q of s.queue.filter(q => !q.pool)) {
    rows.push(jobRow({ task_id: q.task_id, name: q.name, pool: null, status: "skipped", result: "No plan fits", misroute: ["unassigned"], whenText: nxt ? `Next ${hhmm(nxt)}` : "Next" }, now, pal, "queued"));
  }
  let title = "Today's jobs", list = h.today || [];
  if (!list.length && (h.recent || []).length) { title = "Recent jobs"; list = h.recent; }
  for (const r of list) rows.push(jobRow({ ...r, whenText: when(D(r.at), now) }, now, pal));
  const body = rows.length ? `<table><thead><tr><th scope="col">When</th><th scope="col">Job</th><th scope="col">Plan</th><th scope="col">Result</th>
      <th scope="col" class="num">Took</th><th scope="col" class="num">Plan used</th><th scope="col" class="num">Tokens out</th></tr></thead><tbody>${rows.join("")}</tbody></table>`
    : `<p class="empty">No jobs yet. Add them to tasks.json, then preview with <span class="mono">allo-router run</span>.</p>`;
  return `<div><h2>${title}${list === h.recent ? ' <span class="dim">(nothing ran today)</span>' : ""}</h2>${body}</div>`;
}

function week(s, now, pal) {
  const h = s.history;
  if (!h || !h.logged || !h.week) return `<div class="side"><h2>This week</h2><p class="empty">Nothing has run here yet. Jobs are logged by <span class="mono">allo-router run --plan plan.json --execute</span>.</p></div>`;
  const w = h.week, since = new Date(w.since + "T00:00:00");
  const stats = `<div class="stats"><div><b>${w.routed}</b><span>routed</span></div><div><b>${w.ok}</b><span>done</span></div>
      <div><b>${w.skipped}</b><span>skipped</span></div><div class="${w.misroutes ? "alert" : ""}"><b>${w.misroutes}</b><span>misroutes</span></div></div>
      <p class="defn">A misroute is a job that failed or timed out, ran on an unverified reading, or was skipped because no plan could take it.</p>`;
  const units = w.days.map(d => {
    const total = Object.values(d.by_pool).reduce((a, b) => a + b, 0);
    let sq = "";
    for (const [pid, n] of Object.entries(d.by_pool)) for (let i = 0; i < n; i++) sq += `<i style="background:${(pal[pid] || { line: "var(--none)" }).line}"></i>`;
    const isToday = d.date === `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    return `<div class="day ${isToday ? "today" : ""} ${d.future ? "future" : ""}">${total ? `<span class="count">${total}</span>` : ""}<span class="sq">${sq}</span><span class="lbl">${d.label}</span></div>`;
  }).join("");
  const used = w.measured.map(m => {
    const p = pal[m.pool] || { line: "var(--none)", label: m.pool, pool: {} };
    const credit = p.pool.kind === "credit_usd";
    const v = credit ? money(m.cost_usd) : m.measured ? `${+m.pts.toFixed(2)} pts` : "n/a";
    const n = credit ? `reported by the CLI on ${m.cost_n} of ${m.jobs} jobs` : `measured on ${m.measured} of ${m.jobs} jobs`;
    return `<div class="row"><span><span class="seg" style="background:${p.line}"></span>${esc(p.label)}</span><span class="n">${n}</span><span class="v">${v}</span></div>`;
  }).join("");
  const sv = h.saved;
  const est = sv && sv.week_usd != null ? `<p class="est"><b>${money(sv.week_usd)} saved this week, estimated.</b> Each job that ran, priced at the API list price, minus what it cost on the plan it ran on (nothing extra on a subscription). ${sv.week_priced} job${sv.week_priced === 1 ? "" : "s"} priced. <span class="mono">allo-router report</span> shows the method.</p>` : "";
  return `<div class="side"><h2>This week <span class="dim">since ${dayName(since)} ${mdy(since)}</span></h2>${stats}
    <div class="units" aria-label="Jobs per day this week, one square per job, coloured by plan">${units}</div>
    ${used ? `<h2 style="margin-top:16px">Plan used <span class="dim">measured from usage readings</span></h2><div class="used">${used}</div>` : ""}${est}</div>`;
}

function render(s) {
  const now = D(s.now), pal = palette(s.pools);
  document.title = (s.example ? "Example data: " : "") + "Allo Router, " + s.headline;
  document.getElementById("app").innerHTML = header(s, now, pal) + status(s, now, pal) + yard(s, now, pal)
    + `<section class="band lower">${jobs(s, now, pal)}${week(s, now, pal)}</section>`
    + `<footer>Read-only. Reads pools.json, tasks.json, plan.json and runs.jsonl in ${esc(s.config_dir)}, and asks launchd whether its agents are loaded. Never syncs, never starts a job. Refreshes every 15 seconds. allo-router ${esc(s.version)}${s.example ? ". Example data." : ""}</footer>`;
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
