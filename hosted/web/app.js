// The dashboard: what the router did across your computers. Polls every 15 s while the tab is visible, with
// If-None-Match, so an unchanged page costs one tiny read.
import { api, DAYS, esc, hhmm, mdy, parseMinute, post, signInUrl, signOut } from "/common.js";

const POLL_MS = 60000; // D1 free tier: keep idle tabs cheap
const POLL_LIVE_MS = 15000;
const COLORS = {
  codex: "var(--codex)", chatgpt: "var(--codex)",
  claude: "var(--claude)", claude_personal: "var(--claude)", claude_team: "#C4A35A",
  grok: "#1C1C1C", api: "var(--credit)", gemini: "var(--gemini)", other: "var(--other)",
};
const EXTRA = ["#0F7C8C", "#6A4C93", "#B5651D", "#2E7D32", "#8C2F39"];
const PROVIDER = {
  claude: "Claude", claude_personal: "Claude Personal", claude_team: "Claude Team",
  codex: "ChatGPT", chatgpt: "ChatGPT", grok: "Grok Bot", gemini: "Gemini", api: "API credit", other: "Other",
};
// "gpt-6.1-sol" -> "GPT-6.1 Sol" for display; ids stay as sent.
const modelName = (m) => (m ? m.replace(/^gpt-/i, "GPT-").replace(/-([a-z])([a-z]*)$/, (_, a, b) => " " + a.toUpperCase() + b) : m);
const MODEL_COLOR = {
  "gpt-6.1-sol": "#1f4fd8",
  opus: "#6A4C93", sonnet: "#0F7C8C", haiku: "#2E7D32",
  "gpt-5": "var(--codex)", "gpt-5-codex": "var(--codex)", o3: "#B5651D", "o4-mini": "#8C2F39", other: "var(--other)",
};
const EFFORT_COLOR = { low: "#A9A9A2", medium: "#6B8F71", high: "#0F7C8C", very_high: "#6A4C93", xhigh: "#8C2F39" };
let chipMode = "name"; // name | model | effort

const WHY = {
  failed: "Failed", timeout: "Timed out", error: "Could not start", "unverified-reading": "Ran on an unverified reading",
  unassigned: "No plan fits this job", "needs-reading": "Its plan had no reading", "bad-run-block": "Its plan's run block is invalid",
  "missing-cwd": "Its working folder is missing", other: "Skipped for another reason",
};
const RANGES = [["week", "This week"], ["last", "Last week"], ["30d", "30 days"]];

const range = new URLSearchParams(location.search).get("range") || "week";
let etag = null;
let data = null;
let checkedAt = null;

const money = (x) => (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;


// ---------- plan limits: reset times and what is left ----------
// Times in the viewer's own zone, with a short label: "Fri 6:30 PM ET".
const zoneShort = (d) => {
  const z = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" }).formatToParts(d).find((p) => p.type === "timeZoneName")?.value || "";
  return z.replace(/^([A-Z])[SD]T$/, "$1T");
};
const localWhen = (d, withDay = true) => {
  const t = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${withDay ? d.toLocaleDateString("en-US", { weekday: "short" }) + " " : ""}${t} ${zoneShort(d)}`;
};
const localDate = (d) => `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })}, ${localWhen(d, false)}`;
const WINDOW_MS = { "5h": 5 * 3600000, week: 7 * 86400000 };
// Every reset of this plan between a and b (ms). A known window repeats the reset; without one it is shown once.
function resetsBetween(limit, a, b) {
  if (!limit || !limit.resets_at) return [];
  const r0 = parseMinute(limit.resets_at).getTime();
  const step = WINDOW_MS[limit.window];
  if (!step) return r0 >= a && r0 <= b ? [r0] : [];
  const out = [];
  let t = r0 - Math.ceil((r0 - a) / step) * step;
  for (; t <= b && out.length < 60; t += step) if (t >= a) out.push(t);
  return out;
}
function nextReset(limit, now) {
  const r = resetsBetween(limit, now.getTime(), now.getTime() + 400 * 86400000);
  return r.length ? new Date(r[0]) : null;
}
// The reading is from before the plan's most recent reset, so "left" is out of date.
function staleReading(limit, now) {
  if (!limit || !limit.as_of) return false;
  const asOf = parseMinute(limit.as_of).getTime();
  return resetsBetween(limit, asOf + 60000, now.getTime()).length > 0;
}
function leftText(limit) {
  if (!limit) return null;
  if (typeof limit.left_pct === "number") return `${Math.round(limit.left_pct)}% left`;
  if (typeof limit.left_usd === "number") return `${money(limit.left_usd)} left`;
  return null;
}
function sourceText(limit) {
  if (!limit || !limit.as_of) return "";
  const at = localDate(parseMinute(limit.as_of));
  return limit.how === "auto" ? `Read automatically ${at}` : `Entered by hand, as of ${at}`;
}

function planLabel(provider, pool, computer) {
  const p = provider || "other";
  const n = pool ? pool.replace("pool-", "") : "";
  const named = PROVIDER[p] || "Other";
  // Specific providers are the display name; generic Claude/Other keep pool-N for disambiguation.
  const base = (p === "claude" || p === "other" || p === "gemini")
    ? `${named}, pool ${n}`
    : named; // ChatGPT / Claude Personal / Claude Team / Grok Bot stand alone
  return base + (computer ? ` on ${computer}` : "");
}

function styleLines(lines) {
  const used = {};
  let extra = 0;
  return lines.map((l) => {
    if (!l.pool) return { ...l, color: "var(--none)", name: "No plan took it", short: "no plan" };
    if (l.direct) return { ...l, color: COLORS.grok, name: "Grok Bot \u00b7 direct (not routed)", short: "Grok Bot, direct (not routed)" };
    const p = l.provider || "other";
    const colorKey = p === "codex" ? "chatgpt" : p;
    const color = used[colorKey] ? EXTRA[extra++ % EXTRA.length] : COLORS[colorKey] || COLORS.other;
    used[colorKey] = true;
    return {
      ...l, color,
      name: planLabel(p, l.pool, l.computer),
      short: planLabel(p, l.pool, null),
    };
  });
}

function chipLabel(j) {
  if (chipMode === "model" && j.model) return modelName(j.model);
  if (chipMode === "effort" && j.effort) return j.effort.replace("_", " ");
  if (j.title) {
    const t = j.title.length > 18 ? j.title.slice(0, 16) + "…" : j.title;
    return j.runs > 1 ? `${t} ×${j.runs}` : t;
  }
  return `#${j.n}`;
}

function chipFill(j, lineColor) {
  if (chipMode === "model" && j.model) return MODEL_COLOR[j.model] || lineColor;
  if (chipMode === "effort" && j.effort) return EFFORT_COLOR[j.effort] || lineColor;
  return null; // default ink / missed styling
}

function chipToggle(d) {
  const modes = [["name", "Name"], ["model", "Model"], ["effort", "Effort"]];
  const buttons = modes.map(([id, label]) =>
    `<button type="button" class="chip-mode${chipMode === id ? " on" : ""}" data-chip-mode="${id}" aria-pressed="${chipMode === id}">${label}</button>`
  ).join("");
  const hasMeta = (d.jobs || []).some((j) => j.model || j.effort || j.title);
  if (!hasMeta) return "";
  return `<div class="chip-modes" role="group" aria-label="Colour chips by">${buttons}<a class="jump" href="#jobs">See every job ↓</a></div>`;
}

function fmtElapsed(sec) {
  if (sec == null) return "";
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60), r = s % 60;
  return m > 0 ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`;
}

// Where a job ran, with the hand-off when Grok Bot sent it: "Grok Bot → ChatGPT".
function via(j, l) {
  return j.dispatched_by && PROVIDER[j.dispatched_by] && !l.direct ? `${PROVIDER[j.dispatched_by]} \u2192 ${l.short}` : l.short;
}

function agoText(minute, now) {
  const m = Math.max(0, Math.round((now.getTime() - parseMinute(minute).getTime()) / 60000));
  return m < 1 ? "just now" : `${m} min ago`;
}

// Running now, plus what finished in the last 30 minutes, so short jobs never flash by unseen.
function runningStrip(d, lines) {
  const live = d.running || [];
  const recent = d.recent || [];
  if (!live.length && !recent.length) return "";
  const now = new Date();
  const lineOf = Object.fromEntries(lines.map((l) => [l.key, l]));
  const blank = { color: "var(--ink)", short: "plan", name: "plan" };
  const items = live.map((j) => {
    const l = lineOf[j.line] || blank;
    return `<li style="--c:${l.color}"><span class="pulse" aria-hidden="true"></span>` +
      `<b>${esc(j.title || `#${j.n}`)}</b><span class="on">${esc(via(j, l))}</span>` +
      `<span class="clock" data-started="${esc(j.started_at || j.at)}">${fmtElapsed(j.seconds)}</span></li>`;
  }).join("");
  const done = recent.map((j) => {
    const l = lineOf[j.line] || blank;
    const bad = j.status !== "ok";
    return `<li class="done" style="--c:${l.color}"><span class="dot" aria-hidden="true"></span>` +
      `<b>${esc(j.title || "Untitled run")}</b><span class="on">${esc(via(j, l))}</span>` +
      `<span class="clock${bad ? " bad" : ""}">${bad ? esc(j.status) + " \u00b7 " : ""}${esc(agoText(j.ended_at, now))}</span></li>`;
  }).join("");
  const head = live.length
    ? (live.length === 1 ? "One job is in flight." : live.length + " jobs are in flight.") + " Updates every 15 seconds."
    : "Nothing is running right now.";
  return `<section class="running" aria-live="polite"><div class="running-head"><h2 class="cond">${live.length ? "Running now" : "Live"}</h2>` +
    `<p class="intro">${head}</p></div>` +
    (items ? `<ul class="running-list">${items}</ul>` : "") +
    (done ? `<h3 class="running-sub">Finished in the last 30 min</h3><ul class="running-list recent">${done}</ul>` : "") +
    `</section>`;
}

// Suggested next: the planner's runnable jobs on each computer (titles and suggested plan only). Each has its own
// "Send to Grok Bot" button: one click asks that computer to run that one job; nothing runs by itself.
const sending = new Set();
function queueSection(d) {
  const q = d.queue || [];
  const head = `<div class="running-head"><h2 class="cond">Suggested next</h2><p class="intro">What allo-router would run next, and on which plan. Nothing runs until you press Send; your computer picks it up within about 20 seconds and runs it there.</p></div>`;
  if (!q.length) {
    return `<section class="queue">${head}<p class="queue-empty">Nothing queued. When <code>tasks.json</code> on a connected computer has jobs that haven\u2019t run yet, they show up here.</p></section>`;
  }
  const items = q.map((j) => {
    const key = `${j.install_id}|${j.id}`;
    const plan = planLabel(j.provider || "other", j.pool, null);
    const where = j.computer || "your computer";
    let state = "", btn = "";
    if (sending.has(key)) state = `<span class="qs">Sending\u2026</span>`;
    else if (j.request === "pending") state = `<span class="qs wait">Waiting for ${esc(where)}</span>`;
    else if (j.request === "taken") state = `<span class="qs run"><span class="pulse" aria-hidden="true"></span>Running on ${esc(where)}</span>`;
    else {
      if (j.request === "failed") state = `<span class="qs bad">Last run failed</span>`;
      if (j.request === "done") state = `<span class="qs ok">Ran</span>`;
      btn = `<button type="button" class="send" data-send="${esc(key)}">${j.request === "failed" ? "Try again" : "Send to Grok Bot"}</button>`;
    }
    const sw = COLORS[j.provider === "codex" ? "chatgpt" : j.provider] || COLORS.other;
    return `<li><span class="sw" style="background:${sw}"></span><span class="qt"><b>${esc(j.title || "Untitled job")}</b>` +
      `<span class="on">${esc(plan)}${d.computers.length > 1 ? ` \u00b7 ${esc(where)}` : ""}${j.priority != null ? ` \u00b7 priority ${j.priority}` : ""}</span></span>` +
      `<span class="qa">${state}${btn}</span></li>`;
  }).join("");
  return `<section class="queue">${head}<ul class="queue-list">${items}</ul></section>`;
}

function wireQueue() {
  for (const b of document.querySelectorAll("[data-send]")) {
    b.onclick = async () => {
      const key = b.getAttribute("data-send");
      if (sending.has(key)) return; // one request per click, and a double click is still one
      const [install, id] = key.split("|");
      sending.add(key);
      render();
      const r = await post("/v1/me/queue/run", { install_id: Number(install), id });
      sending.delete(key);
      if (r.status === 200 && data) {
        const j = (data.queue || []).find((x) => `${x.install_id}|${x.id}` === key);
        if (j) j.request = r.body.status;
      }
      etag = null;
      render();
    };
  }
}

// Grok Bot's row: its own work, and separately what it sent to other plans. Never a copy of their numbers.
function sentText(l) {
  if (!l.direct || !l.sent || !l.sent.tasks) return "";
  const to = l.sent.to.map((p) => PROVIDER[p] || p).join(", ") || "other plans";
  return `sent ${plural(l.sent.tasks, "task")} (${plural(l.sent.runs, "run")}) to ${to}`;
}


function nav(d) {
  const periods = RANGES.map(([k, label]) => `<a href="/?range=${k}" class="${k === d.range.key ? "on" : ""}">${label}</a>`).join("");
  const live = checkedAt
    ? `Live, checked ${hhmm(checkedAt)}:${String(checkedAt.getUTCSeconds()).padStart(2, "0")} UTC`
    : "";
  return `<div class="band"><div class="wrap"><nav>
    <a class="mark" href="/">Allo Router</a>
    <span class="periods">${periods}</span>
    <span class="who"><span class="live" id="live">${live}</span><span>GitHub: <b>${esc(d.login)}</b></span><button class="link" id="signout">Sign out</button></span>
  </nav></div></div>`;
}


function header(d, now) {
  const t = d.totals;
  const plans = d.lines.filter((l) => l.pool).length;
  const start = new Date(d.range.start);
  const scope = {
    week: `Week of ${mdy(start)}, Monday to now`,
    last: `Week of ${mdy(start)}, Monday to Sunday`,
    "30d": `The 30 days to ${mdy(now)}`,
  }[d.range.key];
  const whenSaved = { week: "this week", last: "last week", "30d": "in the last 30 days" }[d.range.key];
  const comps = d.computers.filter((c) => c.last_upload_at).sort((a, b) => b.last_upload_at.localeCompare(a.last_upload_at));
  const uploads = comps.length
    ? (() => {
        const lastC = comps[0], last = new Date(lastC.last_upload_at);
        const day = mdy(last) === mdy(now) ? "today" : mdy(last);
        let out = `Last upload ${hhmm(last)} from ${esc(lastC.name)}, ${day} UTC.`;
        if (comps.length > 1) { const a = new Date(comps[1].last_upload_at); out += ` ${esc(comps[1].name)} last uploaded at ${hhmm(a)}.`; }
        return out;
      })()
    : "No upload yet.";
  let aside;
  if (t.priced) {
    aside = `<div class="big cond">${money(t.saved)}</div>
      <p><b>API value (est.) ${whenSaved}.</b> What the work would have cost at public API list prices, from real reported token usage or cost. ${t.priced} of ${t.runs ?? t.jobs} runs had usage; the rest show n/a.</p>
      <p class="m">${uploads}</p>`;
  } else {
    const big = comps.length ? hhmm(new Date(comps[0].last_upload_at)) : "--:--";
    aside = `<div class="big cond">${big}</div>
      <p><b>${comps.length ? `Last upload from ${esc(comps[0].name)}.` : "No upload yet."}</b></p>
      <p class="m">API value n/a: no run reported real token usage or cost yet.</p>`;
  }
  return `<header class="head">
    <div>
      <p class="scope">${scope}, from ${plural(d.computers.length, "computer")}</p>
      <h1 class="cond">${plural(t.jobs, "task")} allocated<br>across ${plural(plans, "plan")}</h1>
      <p class="sub"><b>Allo</b>cate usage across your plans based on how much of each limit remains.</p>
      <div class="stats">
        <div><b class="cond">${t.jobs}</b>tasks</div><div><b class="cond">${t.runs ?? t.jobs}</b>runs</div>
        <div><b class="cond">${t.ran}</b>ran</div>
        <div class="${t.missed ? "miss" : ""}"><b class="cond">${t.missed}</b>missed</div>
      </div>
    </div>
    <aside class="side">${aside}</aside>
  </header>`;
}

function limitBlock(l) {
  if (!l.pool) return "";
  const now = new Date();
  const lim = l.limit;
  const nr = nextReset(lim, now);
  const left = leftText(lim);
  if (!lim || (!left && !nr)) {
    if (l.direct) return `<span class="lim lim-none">Grok Bot's own usage isn't tracked yet, so no % left.</span>`;
    return `<span class="lim lim-none">% left not shared yet. Turn on with <code>allo-router connect --share-readings on</code>.</span>`;
  }
  const stale = staleReading(lim, now);
  const pct = typeof lim.left_pct === "number" ? Math.max(0, Math.min(100, lim.left_pct)) : null;
  const tag = lim.how === "auto" ? `<span class="tag auto">Auto</span>` : (lim.how === "manual" ? `<span class="tag manual">Manual</span>` : "");
  return `<span class="lim">
    ${pct !== null ? `<span class="lim-bar${stale ? " stale" : ""}"><span style="width:${pct}%;background:${l.color}"></span></span>` : ""}
    <span class="lim-row">${left ? `<b>${esc(left)}</b>` : ""}${left && nr ? " · " : ""}${nr ? `resets ${esc(localWhen(nr))}` : ""}</span>
    <span class="lim-src">${tag}${esc(sourceText(lim))}${stale ? ` <em>Older than the last reset; re-read it.</em>` : ""}</span>
  </span>`;
}

// The allocation strip: a stacked bar of each plan's share of jobs, with per-plan totals. Real data only.
function allocation(d, lines) {
  const total = d.totals.jobs || 1;
  const bar = lines.map((l) => {
    const pct = l.share ?? (l.jobs / total) * 100;
    return `<span class="${l.pool ? "" : "alloc-none"}" style="width:${pct.toFixed(2)}%;background:${l.color}" title="${esc(l.name)}: ${l.jobs} of ${d.totals.jobs}"></span>`;
  }).join("");
  const keys = lines.map((l) => {
    const pct = l.share ?? Math.round((l.jobs / total) * 100);
    const meta = l.direct
      ? `${plural(l.jobs, "own job")} \u00b7 ${pct}%` + (sentText(l) ? ` \u00b7 ${sentText(l)}` : "")
      : l.pool
      ? `${plural(l.jobs, "job")} \u00b7 ${pct}%` + (l.priced ? ` \u00b7 ${money(l.saved)} API value` : (l.jobs ? " \u00b7 API value n/a (no cost reported)" : ""))
      : `${plural(l.jobs, "job")} \u00b7 ${pct}% \u00b7 missed, no plan fit`;
    return `<div><span class="sw" style="background:${l.color}"></span><b${l.pool ? "" : ' class="no"'}>${esc(l.name)}</b><span class="meta">${meta}</span>${limitBlock(l)}</div>`;
  }).join("");
  return `<section class="alloc">
    <div class="alloc-head"><h2 class="cond">Allocation</h2><p class="intro">Share of the jobs each plan took. Each job goes to the plan with the most of its limit left that may take it.</p></div>
    <div class="bar">${bar}</div>
    <div class="alloc-keys">${keys}</div>
  </section>`;
}

function ticks(d, X0, X1) {
  const start = new Date(d.range.start).getTime(), end = new Date(d.range.end).getTime();
  const x = (t) => X0 + ((t - start) / (end - start)) * (X1 - X0);
  const out = [];
  if (d.range.key === "30d") {
    const first = Math.ceil(start / 86400000) * 86400000;
    for (let t = first, i = 0; t <= end; t += 86400000, i++) {
      if (i % 5 === 0) out.push({ x: x(t), label: mdy(new Date(t)).slice(0, 5) });
    }
  } else {
    for (let i = 0; i < 7; i++) {
      const t = new Date(start + i * 86400000);
      out.push({ x: x(t.getTime()), label: `${DAYS[t.getUTCDay()]} ${mdy(t).slice(0, 5)}` });
    }
  }
  return { out, x, start, end };
}

function map(d, lines, now) {
  const X0 = 260, X1 = 1150, GAP = 90, Y0 = 80;
  const ys = lines.map((_, i) => Y0 + i * GAP);
  const last = ys[ys.length - 1];
  const hubY = (ys[0] + last) / 2;
  const H = last + 70;
  const { out: days, x, start, end } = ticks(d, X0, X1);
  const nowT = Math.min(Math.max(now.getTime(), start), end);
  const nowX = x(nowT);
  const live = now.getTime() < end;
  const lineEnd = live ? nowX : X1;
  const byLine = {};
  for (const j of d.jobs) (byLine[j.line] ||= []).push(j);

  let s = `<svg viewBox="0 0 1312 ${H}" role="img" aria-label="${esc(`${d.totals.jobs} jobs routed across ${lines.length} lines, ${d.totals.missed} missed`)}">`;
  s += `<g stroke="var(--rule)" stroke-width="1">` + days.map((t) => `<path d="M${t.x.toFixed(1)} 34 V${last + 34}"/>`).join("") + `<path d="M${X1} 34 V${last + 34}"/></g>`;
  s += `<g class="t-day">` + days.map((t) => `<text x="${(t.x + 6).toFixed(1)}" y="${last + 56}">${t.label}</text>`).join("") + `</g>`;
  s += `<g fill="none" stroke-linejoin="round" stroke-width="12"><path d="M0 ${hubY} H150" stroke="var(--ink)" stroke-width="10"/>`;
  lines.forEach((l, i) => {
    const dash = l.pool ? "" : ` stroke-dasharray="12 8"`;
    s += `<path d="M150 ${hubY} C200 ${hubY} 200 ${ys[i]} 250 ${ys[i]} H260" stroke="${l.color}"${dash}/>`;
  });
  s += `</g><g fill="none" stroke-width="14">`;
  lines.forEach((l, i) => {
    s += `<path d="M258 ${ys[i]} H${lineEnd.toFixed(1)}" stroke="${l.color}"${l.pool ? "" : ` stroke-dasharray="12 8"`}/>`;
  });
  s += `</g>`;
  if (live) s += `<g fill="none" stroke="var(--rule)" stroke-width="4">` + ys.map((y) => `<path d="M${nowX.toFixed(1)} ${y} H${X1}"/>`).join("") + `</g>`;
  s += `<circle cx="150" cy="${hubY}" r="17" fill="var(--paper)" stroke="var(--ink)" stroke-width="5"/>`;
  s += `<text x="168" y="${hubY + 47}" text-anchor="end" class="t-in" style="fill:var(--ink);font-weight:700">allo-router</text>`;
  s += `<text x="0" y="${hubY - 17}" class="t-in">${esc(`${plural(d.computers.length, "computer")}, ${plural(d.totals.jobs, "job")}`)}</text>`;
  if (live) {
    s += `<path d="M${nowX.toFixed(1)} 22 V${last + 34}" stroke="var(--ink)" stroke-width="1.5"/>`;
    const lab = `Now, ${DAYS[now.getUTCDay()]} ${hhmm(now)}`;
    s += `<text x="${(nowX + (nowX > X1 - 140 ? -6 : 6)).toFixed(1)}" y="20" text-anchor="${nowX > X1 - 140 ? "end" : "start"}" style="font-size:13.5px;font-weight:700;fill:var(--ink)">${lab}</text>`;
  }
  s += `<text x="1308" y="20" text-anchor="end" class="t-day" style="font-weight:700;fill:var(--ink)">Share of jobs</text>`;
  lines.forEach((l, i) => {
    const y = ys[i];
    const red = l.pool ? "" : ` style="fill:var(--reset-text)"`;
    const counts = l.direct
      ? `${plural(l.jobs, "own task")}${sentText(l) ? ` · ${sentText(l)}` : ""}`
      : `${plural(l.jobs, "task")}${l.runs > l.jobs ? ` · ${plural(l.runs, "run")}` : ""}${l.missed && l.pool ? `, ${l.missed} missed` : ""}`;
    const lt = leftText(l.limit);
    s += `<text x="262" y="${y - 24}" class="t-name"${red}>${esc(l.name)}<tspan class="t-num" dx="12"${red}>${counts}</tspan>${lt ? `<tspan class="t-left" dx="12">${esc(lt)}${l.limit.how === "manual" ? " (manual)" : ""}</tspan>` : ""}</text>`;
    const share = l.share ?? Math.round((l.jobs / (d.totals.jobs || 1)) * 100);
    s += `<text x="1308" y="${y + 6}" text-anchor="end" class="t-share"${red}>${share}%</text>`;
    const cap = l.direct ? "dispatcher, no API value" : l.pool ? (l.priced ? `${money(l.saved)} API value (est.)` : "API value n/a") : "missed, no plan fit";
    s += `<text x="1308" y="${y + 26}" text-anchor="end" class="t-est">${cap}</text>`;
    let prevEnd = X0;
    let hidden = 0;
    for (const j of byLine[l.key] || []) {
      const label = chipLabel(j);
      const w = Math.max(34, Math.min(120, 10 + label.length * 7.2));
      const cx = Math.max(x(parseMinute(j.at).getTime()), prevEnd + 4);
      if (cx + w > X1 - 4) { hidden++; continue; }
      prevEnd = cx + w;
      const missed = j.miss && j.miss.length > 0;
      const isRun = j.status === "running";
      const fill = isRun ? (l.color || "var(--ink)") : chipFill(j, l.color);
      const tip = [`#${j.n}`, j.title, j.dispatched_by ? `sent by ${PROVIDER[j.dispatched_by] || j.dispatched_by}` : null, j.runs > 1 ? `${j.runs} runs` : null, j.model, j.effort ? `effort ${j.effort.replace("_", " ")}` : null, j.status,
        isRun && j.seconds != null ? fmtElapsed(j.seconds) : null,
        missed ? j.miss.map((m) => WHY[m] || m).join(", ") : null].filter(Boolean).join(" · ");
      const fillAttr = (!missed && fill) ? ` style="fill:${fill}"` : "";
      s += `<g class="${missed ? "car-x" : (isRun ? "car-run" : "car")}"><title>${esc(tip)}</title>` +
        `<rect x="${cx.toFixed(1)}" y="${y - 9}" width="${w}" height="18"${fillAttr}/><text x="${(cx + w / 2).toFixed(1)}" y="${y + 4}">${esc(label)}</text></g>`;
    }
    const marks = l.limit && l.limit.window === "5h" ? [nextReset(l.limit, now)].filter((d) => d && d.getTime() <= end).map((d) => d.getTime()) : resetsBetween(l.limit, start, end);
    for (const t of marks) {
      const rx = x(t);
      const past = t < now.getTime();
      s += `<g class="reset${past ? " past" : ""}"><title>${esc(`${l.short} resets ${localDate(new Date(t))}`)}</title>` +
        `<path class="halo" d="M${rx.toFixed(1)} ${y - 14} V${y + 12}"/><path d="M${rx.toFixed(1)} ${y - 14} V${y + 12}"/><circle cx="${rx.toFixed(1)}" cy="${y + 17}" r="5"/>` +
        `<text x="${(rx + (rx > X1 - 150 ? -10 : 10)).toFixed(1)}" y="${y + 22}" text-anchor="${rx > X1 - 150 ? "end" : "start"}">${esc(`${past ? "reset" : "resets"} ${localWhen(new Date(t))}`)}</text></g>`;
    }
    if (hidden) s += `<text x="${X1 - 4}" y="${y + 26}" text-anchor="end" class="more"><a href="#jobs">+${hidden} more, see all jobs below ↓</a></text>`;
  });
  s += `</svg>`;
  const stripe = (ls) => { const c = ls.filter((l) => l.pool).map((l) => l.color); const w = 38 / (c.length || 1); return `<svg width="38" height="12" aria-hidden="true">${c.map((col, i) => `<rect x="${(i * w).toFixed(1)}" y="3" width="${w.toFixed(1)}" height="6" fill="${col}"/>`).join("")}</svg>`; };
  const scale = d.range.key === "30d" ? "period" : "week";
  return `<div class="map">${s}</div>
  <div class="legend" aria-label="How to read the map">
    <span class="lg-title">How to read it</span>
    <span class="lg">${stripe(lines)}<span><b>Each line is a plan.</b> Its color matches the plan above.</span></span>
    <span class="lg"><svg width="38" height="18" aria-hidden="true"><rect width="38" height="18" rx="2" fill="var(--ink)"/><text x="19" y="13" text-anchor="middle" style="fill:var(--paper);font-size:10px;font-weight:700">Docs</text></svg><span><b>Each box is a task</b> that plan did.</span></span>
    <span class="lg"><svg width="38" height="18" aria-hidden="true"><rect x="1" y="1" width="36" height="16" rx="2" fill="var(--paper)" stroke="var(--reset-text)" stroke-width="1.5" stroke-dasharray="4 3"/></svg><span><b>Dashed red</b> = missed, no plan had room.</span></span>
    <span class="lg"><svg width="38" height="22" aria-hidden="true"><rect y="8" width="38" height="6" fill="var(--rule)"/><path d="M19 1 V21" stroke="var(--ink)" stroke-width="2"/><circle cx="19" cy="11" r="5" fill="var(--paper)" stroke="var(--ink)" stroke-width="2.5"/></svg><span><b>Ring</b> = the plan's limit resets here.</span></span>
    ${live ? `<span class="lg"><svg width="38" height="12" aria-hidden="true"><rect y="4" width="38" height="4" fill="var(--rule)"/></svg><span><b>Gray</b> = rest of the ${scale}.</span></span>` : ""}
    <span class="lg-note">Days are to scale (day lines at midnight UTC); reset labels are in your time zone, ${esc(zoneShort(new Date()))}. Resets sit at their exact time; a box starts at its job's time and only shifts right to avoid overlapping.</span>
  </div>`;
}

function tables(d, lines) {
  const t = d.totals;
  const rows = lines.map((l) => `<div role="row"><span class="seg" style="background:${l.color}"></span><span>${esc(l.short)}${l.computer ? ` <span class="est">${esc(l.computer)}</span>` : ""}</span>` +
    `<span class="r">${l.jobs}</span><span class="r">${l.ran}</span><span class="r${l.missed ? " no" : ""}">${l.missed}</span>` +
    `<span class="r">${l.pool && l.priced ? money(l.saved) : "n/a"}</span></div>`).join("");
  const lineOf = Object.fromEntries(lines.map((l) => [l.key, l]));
  const misses = d.jobs.filter((j) => j.miss.length).map((j) => {
    const l = lineOf[j.line];
    const at = parseMinute(j.at);
    return `<div role="row"><span class="id">#${j.n}</span><span>${esc(j.title || ("#" + j.n))}</span><span>${DAYS[at.getUTCDay()]} ${hhmm(at)}</span><span class="seg" style="background:${l.color}"></span>` +
      `<span>${esc(l.short)}</span><span class="why">${j.miss.map((m) => WHY[m] || m).join("; ")}</span></div>`;
  }).join("");
  const period = { week: "this week", last: "last week", "30d": "the last 30 days" }[d.range.key];
  return `<section class="part">
    <div>
      <h2>Where jobs went</h2>
      <p class="intro">Totals for ${period} by plan. API value (est.) is what the work would have cost at public API list prices, computed only from token usage or cost the tool itself reported; anything else shows n/a. Every task counts once, under the plan that did it. Grok Bot\u2019s own work, done without routing, has its own row. Plans show as ChatGPT, Claude Personal or Claude Team when the upload can tell them apart; otherwise as pool-N.</p>
    </div>
    <div>
      <div class="tbl" role="table" aria-label="Jobs by plan">
        <div class="h" role="row"><span></span><span>Plan</span><span class="r">Jobs</span><span class="r">Ran</span><span class="r">Missed</span><span class="r">API value (est.)</span></div>
        ${rows}
        <div role="row" class="tot"><span></span><span>Total</span><span class="r">${t.jobs}</span><span class="r">${t.ran}</span><span class="r${t.missed ? " no" : ""}">${t.missed}</span><span class="r">${t.priced ? money(t.saved) : "n/a"}</span></div>
      </div>
      ${misses ? `<h3>Misses</h3><div class="misses" role="table" aria-label="Missed jobs">${misses}</div>` : `<h3>No misses</h3>`}
    </div>
  </section>`;
}

function computers(d) {
  const rows = d.computers.map((c) => {
    const up = c.last_upload_at ? (() => { const a = new Date(c.last_upload_at); return `Last upload ${mdy(a)} at ${hhmm(a)}.`; })() : "No upload yet.";
    return `<div><dt>${esc(c.name)}</dt><dd>${c.connected_at ? `Connected ${mdy(new Date(c.connected_at))}.` : ""} ${up} ${plural(c.jobs, "job")} in this period.</dd>` +
      `<button class="link act" data-disconnect="${c.id}" data-name="${esc(c.name)}">Disconnect</button></div>`;
  }).join("");
  return `<section class="part">
    <div>
      <h2>Computers</h2>
      <p class="intro">Each one was connected with <code>allo-router connect</code> and has its own upload token. A token can add jobs, but it can't read anything.</p>
    </div>
    <dl class="rows">${rows}</dl>
  </section>`;
}

function empty() {
  return `<section class="empty">
    <h2>Connect a computer</h2>
    <ol>
      <li>Update Allo Router to 0.5 or later on the computer that runs your jobs.</li>
      <li>In your router folder, run <code>allo-router connect --show</code> to see exactly what would be sent. Nothing is sent yet.</li>
      <li>Run <code>allo-router connect</code> and approve the code it shows on this site. Jobs appear here as they finish.</li>
    </ol>
  </section>`;
}

const about = `<section class="part">
    <div>
      <h2>What this page gets</h2>
      <p class="intro">The same list is on the <a href="/privacy">privacy page</a> and in <code>allo-router connect --show</code>, which prints the next upload without sending it.</p>
    </div>
    <dl class="rows">
      <div class="two"><dt>Sent</dt><dd>A hashed job id, when it started (to the minute), the plan it went to as pool-1, pool-2, its type and provider (ChatGPT / Claude Personal / Claude Team when known), a short job title, model and effort when set on the plan, whether it ran, why it missed and the API value (est.).</dd></div>
      <div class="two"><dt>Never sent</dt><dd>Prompts, code, commands, output, file names or paths, plan labels you typed, where a reading came from, or keys. Plan readings (% or $ left, reset time, when read, auto or manual) only if you turn them on with <code>allo-router connect --share-readings on</code>.</dd></div>
      <div class="two"><dt>Stop</dt><dd><code>allo-router disconnect</code> revokes the token and deletes that computer's history here. Your local dashboard keeps working either way.</dd></div>
    </dl>
  </section>`;

// ---------- Jobs: pools -> tasks -> runs, collapsed by default ----------
const openKeys = new Set();     // survives the polling re-render
const shown = new Map();        // key -> how many rows to show
const PAGE = 50;
const dur = (a, b) => {
  if (!a || !b) return "n/a";
  const s = Math.max(0, (parseMinute(b) - parseMinute(a)) / 1000);
  return s < 60 ? "<1m" : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
};
const when = (s) => { const d = parseMinute(s); return `${mdy(d).slice(0, 5)} ${hhmm(d)}`; };
const usdOr = (x) => (typeof x === "number" ? (x < 0.1 ? "$" + x.toFixed(4) : money(x)) : "n/a");
function more(key, total) {
  const n = shown.get(key) || PAGE;
  return total > n ? `<button type="button" class="more-btn" data-more="${esc(key)}">Show ${Math.min(PAGE, total - n)} more (${total - n} left)</button>` : "";
}
function jobsSection(d, lines) {
  const byLine = {};
  for (const t of d.jobs) (byLine[t.line] ||= []).push(t);
  const pools = lines.map((l) => {
    const tasks = (byLine[l.key] || []).slice().sort((a, b) => b.runs - a.runs || b.at.localeCompare(a.at));
    const pk = `p:${l.key}`;
    const nT = shown.get(pk) || PAGE;
    const rows = tasks.slice(0, nT).map((t) => {
      const tk = `t:${l.key}:${t.title || "#" + t.n}`;
      const nR = shown.get(tk) || PAGE;
      const runs = openKeys.has(tk) ? t.list.slice(0, nR).map((r) =>
        `<div class="run" role="row"><span>${when(r.at)}</span><span class="st st-${esc(r.status)}">${esc(r.status)}</span><span>${r.status === "running" ? "running" : dur(r.at, r.end)}</span><span class="r">${usdOr(r.usd)}</span></div>`).join("") + more(tk, t.list.length) : "";
      return `<details class="task" data-k="${esc(tk)}"${openKeys.has(tk) ? " open" : ""}><summary>` +
        `<span class="tname">${esc(t.title || "Untitled #" + t.n)}</span><span class="r">${t.runs}</span>` +
        `<span>${esc(modelName(t.model) || "n/a")}</span><span>${esc((t.effort || "n/a").replace("_", " "))}</span>` +
        `<span class="r">${usdOr(t.usd_total)}</span><span>${when(t.first_at)}</span><span>${when(t.at)}</span></summary>` +
        `<div class="runs"><div class="run h" role="row"><span>Started (UTC)</span><span>Status</span><span>Duration</span><span class="r">API value (est.)</span></div>${runs}</div></details>`;
    }).join("");
    const head = `<div class="task-h"><span>Task</span><span class="r">Runs</span><span>Model</span><span>Effort</span><span class="r">API value</span><span>First run</span><span>Last run</span></div>`;
    return `<details class="pool" data-k="${esc(pk)}"${openKeys.has(pk) ? " open" : ""}><summary><span class="sw" style="background:${l.color}"></span>` +
      `<b>${esc(l.name)}</b><span class="meta">${plural(tasks.length, "task")} · ${plural(l.runs || 0, "run")}</span><span class="chev" aria-hidden="true">▸</span></summary>` +
      (openKeys.has(pk) ? head + rows + more(pk, tasks.length) : "") + `</details>`;
  }).join("");
  return `<section class="jobs" id="jobs"><div class="jobs-head"><h2 class="cond">Jobs</h2>` +
    `<p class="intro">Every task by plan. Click a plan to see its tasks, and a task to see each run.</p></div>${pools}</section>`;
}
function wireJobs() {
  for (const el of document.querySelectorAll("details[data-k]")) {
    el.addEventListener("toggle", () => {
      const k = el.getAttribute("data-k");
      const was = openKeys.has(k);
      if (el.open) openKeys.add(k); else openKeys.delete(k);
      if (was !== el.open) render();
    });
  }
  for (const b of document.querySelectorAll("[data-more]")) {
    b.onclick = () => { const k = b.getAttribute("data-more"); shown.set(k, (shown.get(k) || PAGE) + PAGE); render(); };
  }
}

function render() {
  if (!data) return;
  const now = new Date();
  const lines = styleLines(data.lines);
  const body = data.computers.length === 0
    ? empty()
    : queueSection(data) + (lines.length || (data.running && data.running.length) ? runningStrip(data, lines) + allocation(data, lines) + chipToggle(data) + map(data, lines, now) + jobsSection(data, lines) : `<section class="empty"><h2>No jobs in this period</h2><p class="intro">Jobs show up here after <code>run --execute</code> finishes them on a connected computer.</p></section>`) +
      tables(data, lines) + computers(data);
  const note = `<p class="note"><b>Only job numbers, plans, outcomes and estimates reach this page.</b> Job names and everything else stay on your computer; open <code>allo-router dashboard</code> there to see them. Times are UTC.</p>`;
  document.getElementById("app").innerHTML = `${nav(data)}<div class="wrap">${note}${header(data, now)}${body}${about}
  <footer>
    <span class="mark ext">Allo Router</span>
    <span class="spots" id="spots" hidden></span>
    <a href="/privacy">Privacy</a>
    <button class="link" id="delete">Delete my account</button>
  </footer></div>`;
  const sp = data.spots;
  const el = document.getElementById("spots");
  el.innerHTML = sp.left > 0 ? `Free for the first ${sp.limit} users: <b>${sp.left} of ${sp.limit}</b> free spots left` : `All ${sp.limit} free spots are taken`;
  el.hidden = false;
  document.getElementById("signout").onclick = signOut;
  wireJobs();
  wireQueue();
  for (const b of document.querySelectorAll("[data-chip-mode]")) {
    b.onclick = () => { chipMode = b.getAttribute("data-chip-mode"); render(); };
  }
  tickClocks();
  document.getElementById("delete").onclick = async () => {
    if (!confirm("Delete your account? Every computer's token stops working and all your history here is deleted.")) return;
    await post("/v1/me/delete");
    location.href = "/signin";
  };
  for (const b of document.querySelectorAll("[data-disconnect]")) {
    b.onclick = async () => {
      if (!confirm(`Disconnect ${b.dataset.name}? Its token stops working and its history here is deleted.`)) return;
      await post(`/v1/me/installs/${b.dataset.disconnect}/disconnect`);
      etag = null;
      load();
    };
  }
}

function tickClocks() {
  for (const el of document.querySelectorAll(".clock[data-started]")) {
    const start = Date.parse(String(el.getAttribute("data-started")).replace(/Z$/, ":00Z"));
    if (!Number.isFinite(start)) continue;
    el.textContent = fmtElapsed(Math.round((Date.now() - start) / 1000));
  }
}

async function load() {
  const r = await api(`/v1/me/dashboard?range=${encodeURIComponent(range)}`, { headers: etag ? { "If-None-Match": etag } : {} }).catch(() => null);
  if (!r) return;
  if (r.status === 401) { location.href = signInUrl("/"); return; }
  checkedAt = new Date();
  if (r.status === 200 && r.body) {
    etag = r.headers.get("ETag");
    data = r.body;
  }
  render();  // a 304 still re-renders, so "now" moves along
  const live = data && data.running && data.running.length;
  const waiting = data && (data.queue || []).some((q) => q.request === "pending" || q.request === "taken");
  if (window.__alloPoll) clearInterval(window.__alloPoll);
  window.__alloPoll = setInterval(() => { if (!document.hidden) load(); }, live || waiting ? POLL_LIVE_MS : POLL_MS);
  if (window.__alloClock) clearInterval(window.__alloClock);
  if (live) window.__alloClock = setInterval(tickClocks, 1000);
}

document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
load();
