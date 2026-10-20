// The dashboard: what the router did across your computers. Polls every 15 s while the tab is visible, with
// If-None-Match, so an unchanged page costs one tiny read.
// Layout (direction D): numbered sections. 01 Now (running, countdown to the next plan reset, needs you),
// 02 Suggested next, 03 Plans (ledger, with the route diagram as a fold-out), 04 Jobs, 05 the period's totals,
// 06 Computers, 07 what this page gets.
import { api, DAYS, esc, hhmm, mdy, parseMinute, post, signInUrl, signOut } from "/common.js";

const POLL_MS = 60000; // D1 free tier: keep idle tabs cheap
const POLL_LIVE_MS = 15000;
const COLORS = {
  codex: "var(--codex)", chatgpt: "var(--codex)",
  claude: "var(--claude)", claude_personal: "var(--claude)", claude_team: "#C4A35A",
  grok: "#1C1C1C", api: "var(--credit)", gemini: "var(--gemini)", other: "var(--other)",
};
// Plan colours too light to read as text on the paper get a darker twin for words.
const TEXT_COLOR = { "var(--claude)": "var(--claude-text)", "#C4A35A": "#7A6020" };
const textColor = (c) => TEXT_COLOR[c] || c;
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
const PERIOD = { week: "this week", last: "last week", "30d": "in the last 30 days" };
const WORDS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

const range = new URLSearchParams(location.search).get("range") || "week";
let etag = null;
let data = null;
let checkedAt = null;

const money = (x) => (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const pad = (n) => String(n).padStart(2, "0");
// "4:48": hours and minutes to go, for the countdown
const hm = (ms) => { const m = Math.max(0, Math.floor(ms / 60000)); return `${Math.floor(m / 60)}:${pad(m % 60)}`; };
// "in 28 h", "in 3 d 13 h", "in 16 d"
function inSpan(ms) {
  const h = ms / 3600000;
  if (h < 1) return `in ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `in ${Math.round(h)} h`;
  const H = Math.round(h), d = Math.floor(H / 24), r = H % 24;
  return h < 168 && r ? `in ${d} d ${r} h` : `in ${d} d`;
}


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
    if (l.direct) return { ...l, color: COLORS.grok, name: "Grok Bot · direct (not routed)", short: "Grok Bot, direct (not routed)" };
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
  return j.dispatched_by && PROVIDER[j.dispatched_by] && !l.direct ? `${PROVIDER[j.dispatched_by]} → ${l.short}` : l.short;
}

function agoText(minute, now) {
  const m = Math.max(0, Math.round((now.getTime() - parseMinute(minute).getTime()) / 60000));
  return m < 1 ? "just now" : `${m} min ago`;
}

function sec(n, title, body, sub = "", id = "") {
  return `<section class="sec"${id ? ` id="${id}"` : ""}><h2><span>${n}</span>${title}${sub ? `<em class="since">${sub}</em>` : ""}</h2><div>${body}</div></section>`;
}

// ---------- 01 Now ----------
// First column: running now, plus what finished in the last 30 minutes, so short jobs never flash by unseen.
function nowRunning(d, lines, now) {
  const live = d.running || [];
  const recent = d.recent || [];
  const lineOf = Object.fromEntries(lines.map((l) => [l.key, l]));
  const blank = { color: "var(--ink)", short: "plan", name: "plan" };
  let out;
  if (live.length) {
    const j = live[0], l = lineOf[j.line] || blank;
    const meta = [j.computer && d.computers.length > 1 ? j.computer : "", modelName(j.model), j.effort ? `effort ${j.effort.replace("_", " ")}` : ""].filter(Boolean).join(" · ");
    out = `<p class="label" aria-live="polite">Running for <span class="clock" data-started="${esc(j.started_at || j.at)}">${fmtElapsed(j.seconds)}</span> · updates every 15 seconds</p>
      <h1><b>${esc(j.title || `#${j.n}`)}</b> on <b style="color:${textColor(l.color)}">${esc(via(j, l))}</b></h1>
      ${meta ? `<p class="dim">${esc(meta)}</p>` : ""}`;
    if (live.length > 1) {
      out += `<ul class="also"><li class="h">Also running</li>` + live.slice(1).map((r) => {
        const rl = lineOf[r.line] || blank;
        return `<li><span class="pulse" style="--c:${rl.color}" aria-hidden="true"></span><b>${esc(r.title || `#${r.n}`)}</b> <span class="dim">${esc(via(r, rl))}</span><span class="clock" data-started="${esc(r.started_at || r.at)}">${fmtElapsed(r.seconds)}</span></li>`;
      }).join("") + `</ul>`;
    }
  } else {
    const t = d.totals;
    const plans = d.lines.filter((l) => l.pool).length;
    out = `<p class="label">Nothing running right now</p>
      <h1><b>${plural(t.jobs, "task")}</b> allocated across ${plural(plans, "plan")} ${PERIOD[d.range.key]}.</h1>
      <p class="dim"><b>Allo</b>cate usage across your plans based on how much of each limit remains. Each job goes to the plan with the most of its limit left that may take it.</p>`;
  }
  if (recent.length) {
    out += `<ul class="also"><li class="h">Finished in the last 30 min</li>` + recent.map((j) => {
      const l = lineOf[j.line] || blank, bad = j.status !== "ok";
      return `<li><span class="dot" style="--c:${l.color}" aria-hidden="true"></span><b>${esc(j.title || "Untitled run")}</b> <span class="dim">${esc(via(j, l))}</span>` +
        `<span class="when${bad ? " bad" : ""}">${bad ? esc(j.status) + " · " : ""}${esc(agoText(j.ended_at, now))}</span></li>`;
    }).join("") + `</ul>`;
  }
  return out;
}

function check(ok, text) { return `<li class="${ok ? "" : "no"}"><span>${text}</span></li>`; }
const planLines = (lines) => lines.filter((l) => l.pool && !l.direct);
const lastUpload = (d) => d.computers.filter((c) => c.last_upload_at).sort((a, b) => b.last_upload_at.localeCompare(a.last_upload_at))[0];

// Second column: countdown to the soonest plan reset, and whether the readings behind the plan are usable.
function nowNext(d, lines, now) {
  const plans = planLines(lines);
  const soon = plans.map((l) => ({ l, at: nextReset(l.limit, now) })).filter((x) => x.at).sort((a, b) => a.at - b.at)[0];
  let out;
  if (soon) {
    const lt = leftText(soon.l.limit);
    const said = staleReading(soon.l.limit, now) ? `${esc(soon.l.short)}’s last reading (${esc(lt || "no % left")}) is from before its last reset.`
      : lt ? `${esc(soon.l.short)} has ${esc(lt)} until then.` : `${esc(soon.l.short)} resets then.`;
    out = `<p class="label">Next plan reset · ${esc(soon.l.short)} · ${esc(localWhen(soon.at))}</p>
      <p class="count">${hm(soon.at - now)}<small>h:min to go</small></p>
      <p>${said} The suggested plan for each job goes to whichever plan has the most of its limit left.</p>`;
  } else {
    out = `<p class="label">Next plan reset</p><p class="count">--:--</p>
      <p>% left not shared yet. Turn on with <code>allo-router connect --share-readings on</code>.</p>`;
  }
  const checks = [];
  if (plans.length) {
    const read = plans.filter((l) => leftText(l.limit)).length;
    checks.push(check(read === plans.length, `${read} of ${plans.length} plans share a reading`));
    const stale = plans.filter((l) => staleReading(l.limit, now)).length;
    if (read) checks.push(check(!stale, stale ? `${plural(stale, "reading")} older than the last reset` : "Every reading is newer than its plan’s last reset"));
  }
  const up = lastUpload(d);
  checks.push(up ? check(true, `Last upload ${hhmm(new Date(up.last_upload_at))} UTC from ${esc(up.name)}${mdy(new Date(up.last_upload_at)) === mdy(now) ? "" : `, ${mdy(new Date(up.last_upload_at))}`}`) : check(false, "No upload yet"));
  return out + `<ul class="checks" aria-label="Checks">${checks.join("")}</ul>`;
}

// Third column: what needs a person, each with what to do about it.
function nowNeeds(d, lines, now) {
  const items = [];
  for (const j of d.queue || []) {
    const r = (j.routes || []).find((x) => x.pool === j.route);
    if (j.request === "refused") items.push([`${esc(j.title || "A job")} was refused on ${esc(r ? routeName(r) : "that plan")}.`, `${esc(cap(REASON[j.reason] || REASON.other))}. Pick another plan under Suggested next.`]);
    else if (j.request === "failed") items.push([`${esc(j.title || "A job")} failed on ${esc(r ? routeName(r) : "that plan")}.`, `See it on ${esc(j.computer || "your computer")} in <code>allo-router dashboard</code>.`]);
  }
  if (d.totals.missed) items.push([`${plural(d.totals.missed, "task")} missed ${PERIOD[d.range.key]}.`, `No plan had room, or the plan could not run it. <a href="#misses">See the misses</a>.`]);
  const plans = planLines(lines);
  for (const l of plans.filter((x) => staleReading(x.limit, now))) {
    items.push([`${esc(l.name)}’s reading is older than its last reset.`, `Re-read it on ${esc(l.computer || "that computer")}: <code>allo-router sync</code>, or <code>set</code> for a plan read by hand.`]);
  }
  const unshared = plans.filter((l) => !leftText(l.limit) && !nextReset(l.limit, now));
  if (unshared.length) items.push([`% left not shared for ${esc(unshared.map((l) => l.name).join(", "))}.`, `Turn on with <code>allo-router connect --share-readings on</code>.`]);
  for (const c of d.computers.filter((x) => !x.last_upload_at)) items.push([`${esc(c.name)} has not uploaded yet.`, `Run <code>allo-router live</code> or a job there.`]);
  let out = `<p class="label">Needs you</p>`;
  if (!items.length) return out + `<h3 class="ok">Nothing.</h3><p class="dim">No refusals, misses or stale readings ${PERIOD[d.range.key]}.</p>`;
  items.forEach(([h, p], i) => { out += `<h3${i ? ' class="more"' : ""}>${h}</h3><p>${p}</p>`; });
  return out;
}
const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);

// ---------- 02 Suggested next ----------
// The planner's runnable jobs on each computer (titles and plans only). Each job has one chip per plan that may
// take it; the planner's pick is highlighted. A click asks that computer, through the same request path, to run that
// one job on that plan. Nothing runs by itself.
const sending = new Set();
const REASON = {
  "stale-reading": "needs a fresh reading", "no-run-setup": "that plan has no run setup", "already-ran": "it already ran",
  "not-eligible": "that plan may not take this job", "unknown-route": "that plan isn't set up on the computer",
  "unknown-job": "the computer no longer has this job", other: "the computer refused it",
};
const routeColor = (p) => COLORS[p === "codex" ? "chatgpt" : p] || COLORS.other;
const routeName = (r) => planLabel(r.provider || "other", r.pool, null);

function queueSection(d) {
  const q = d.queue || [];
  const head = `<p class="intro">What allo-router would run next. Pick a plan to send the job to Grok Bot; the highlighted one is allo-router’s pick. Your computer picks it up within about 20 seconds and runs it there.</p>`;
  if (!q.length) {
    return `<div class="queue">${head}<p class="queue-empty">Nothing queued. When <code>tasks.json</code> on a connected computer has jobs that haven’t run yet, they show up here.</p></div>`;
  }
  const items = q.map((j) => {
    const key = `${j.install_id}|${j.id}`;
    const where = j.computer || "your computer";
    const routes = j.routes || [];
    const chosen = routes.find((r) => r.pool === j.route);
    const busy = sending.has(key) || j.request === "pending" || j.request === "taken";
    let state = "";
    if (sending.has(key)) state = `<span class="qs">Sending…</span>`;
    else if (j.request === "pending") state = `<span class="qs wait">Waiting for ${esc(where)} · ${esc(chosen ? routeName(chosen) : "")}</span>`;
    else if (j.request === "taken") state = `<span class="qs run"><span class="pulse" aria-hidden="true"></span>Running on ${esc(chosen ? routeName(chosen) : where)}</span>`;
    else if (j.request === "refused") state = `<span class="qs bad">Refused on ${esc(chosen ? routeName(chosen) : "that plan")}: ${esc(REASON[j.reason] || REASON.other)}</span>`;
    else if (j.request === "failed") state = `<span class="qs bad">Last run on ${esc(chosen ? routeName(chosen) : "that plan")} failed</span>`;
    else if (j.request === "done") state = `<span class="qs ok">Ran on ${esc(chosen ? routeName(chosen) : "a plan")}</span>`;
    const chips = busy ? "" : `<span class="routes" role="group" aria-label="Send ${esc(j.title || "job")} to">` + routes.map((r) => {
      const sug = r.pool === j.pool;
      return `<button type="button" class="route${sug ? " sug" : ""}" style="--c:${routeColor(r.provider)}" data-send="${esc(key)}" data-route="${esc(r.pool)}"` +
        ` title="Run on ${esc(routeName(r))}${sug ? " (allo-router’s pick)" : ""}"><span class="dot" aria-hidden="true"></span>${esc(routeName(r))}${sug ? `<span class="pick">pick</span>` : ""}</button>`;
    }).join("") + `</span>`;
    return `<li><span class="qt"><b>${esc(j.title || "Untitled job")}</b>` +
      `<span class="on">${d.computers.length > 1 ? `${esc(where)} · ` : ""}${j.priority != null ? `priority ${j.priority}` : ""}</span>${state}</span>` +
      chips + `</li>`;
  }).join("");
  return `<div class="queue">${head}<ul class="queue-list">${items}</ul></div>`;
}

function wireQueue() {
  for (const b of document.querySelectorAll("[data-send]")) {
    b.onclick = async () => {
      const key = b.getAttribute("data-send");
      const route = b.getAttribute("data-route");
      if (sending.has(key)) return; // one request per job; a double click is still one
      const [install, id] = key.split("|");
      sending.add(key);
      render();
      const r = await post("/v1/me/queue/run", { install_id: Number(install), id, route });
      sending.delete(key);
      if (r.status === 200 && data) {
        const j = (data.queue || []).find((x) => `${x.install_id}|${x.id}` === key);
        if (j) { j.request = r.body.status; j.route = r.body.route; }
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


// ---------- masthead ----------
function mast(d) {
  const periods = RANGES.map(([k, label]) => `<a href="/?range=${k}" class="${k === d.range.key ? "on" : ""}"${k === d.range.key ? ' aria-current="page"' : ""}>${label}</a>`).join("");
  const live = checkedAt
    ? `Live, checked ${hhmm(checkedAt)}:${String(checkedAt.getUTCSeconds()).padStart(2, "0")} UTC`
    : "";
  return `<header class="mast">
    <a class="mark" href="/">Allo Router</a>
    <nav class="periods" aria-label="Period">${periods}</nav>
    <span class="right"><span class="live" id="live">${live}</span><span class="who">GitHub: <b>${esc(d.login)}</b></span><button class="link" id="signout">Sign out</button></span>
  </header>`;
}

// ---------- 03 Plans: the ledger ----------
function ledger(d, lines, now) {
  const rows = lines.filter((l) => l.pool);
  if (!rows.length) return `<p class="empty">No plans in this period yet.</p>`;
  const total = d.totals.jobs || 1;
  const body = rows.map((l) => {
    const lim = l.limit, nr = nextReset(lim, now), stale = staleReading(lim, now);
    const pct = lim && typeof lim.left_pct === "number" ? Math.max(0, Math.min(100, lim.left_pct)) : null;
    const usd = lim && typeof lim.left_usd === "number" ? lim.left_usd : null;
    const dim = stale || (pct === null && usd === null);
    const left = pct !== null ? `${Math.round(lim.left_pct)}<small>%</small>` : usd !== null ? esc(money(usd)) : l.direct ? `<span class="na">n/a</span>` : "?";
    let bar, note;
    if (pct !== null) {
      bar = `<div class="bar${stale ? " stale" : ""}"><i style="width:${pct}%;background:${l.color}"></i></div>`;
    } else if (usd !== null) {
      bar = `<div class="bar line" style="background:${l.color}"></div>`;
    } else {
      bar = `<div class="bar none"></div>`;
    }
    if (l.direct) note = `<div class="bar-note">Grok Bot's own usage isn't tracked yet, so no % left.</div>`;
    else if (!lim || (!leftText(lim) && !nr)) note = `<div class="bar-note">% left not shared yet. Turn on with <code>allo-router connect --share-readings on</code>.</div>`;
    else {
      const tag = lim.how === "auto" ? `<span class="tag auto">Auto</span>` : (lim.how === "manual" ? `<span class="tag manual">Manual</span>` : "");
      note = `<div class="bar-note">${tag}${esc(sourceText(lim))}${usd !== null && pct === null ? ". Dollars with no weekly cap, so no bar" : ""}${stale ? ` <em>Older than the last reset; re-read it.</em>` : ""}</div>`;
    }
    const resets = nr ? `<b>${esc(localWhen(nr))}</b><span class="dim">${inSpan(nr - now)}</span>` : `<span class="dim">no reset time</span>`;
    const share = l.share ?? Math.round((l.jobs / total) * 100);
    const value = l.direct ? (sentText(l) || "dispatcher, no API value") : l.priced ? `${money(l.saved)} API value` : (l.jobs ? "API value n/a" : "");
    const jobs = `<b>${plural(l.jobs, l.direct ? "own task" : "task")}</b> · ${share}%${l.runs > l.jobs ? `<span class="dim"> · ${plural(l.runs, "run")}</span>` : ""}${value ? `<span class="sub">${esc(value)}</span>` : ""}`;
    return `<tr><td class="plan${dim ? " dim" : ""}"><span class="key" style="background:${l.color}"></span>${esc(l.short)}${l.computer ? `<span class="sub">${esc(l.computer)}</span>` : ""}</td>
      <td class="left fig${dim ? " dim" : ""}">${left}</td><td class="runway">${bar}${note}</td>
      <td class="resets">${resets}</td><td class="jobs-on">${jobs}</td></tr>`;
  }).join("");
  return `<table class="ledger"><thead><tr><th scope="col">Plan</th><th scope="col" class="r">Left</th><th scope="col">Runway to reset</th><th scope="col">Resets</th><th scope="col">Jobs ${PERIOD[d.range.key]}</th></tr></thead><tbody>${body}</tbody></table>`;
}

// ---------- 03 Plans, fold-out: the route diagram ----------
let diagramOpen = false;
try { diagramOpen = localStorage.getItem("allo.diagram") === "open"; } catch {}

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
  const W = 1020, X0 = 196, X1 = 1004, GAP = 64, Y0 = 52;
  const ys = lines.map((_, i) => Y0 + i * GAP);
  const last = ys[ys.length - 1];
  const yb = last + 36, H = yb + 24;
  const { out: days, x, start, end } = ticks(d, X0, X1);
  const nowT = Math.min(Math.max(now.getTime(), start), end);
  const nowX = x(nowT);
  const live = now.getTime() < end;
  const lineEnd = live ? nowX : X1;
  const byLine = {};
  for (const j of d.jobs) (byLine[j.line] ||= []).push(j);

  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(`${d.totals.jobs} jobs routed across ${lines.length} lines, ${d.totals.missed} missed`)}">`;
  s += `<g stroke="var(--rule)">` + days.map((t) => `<line x1="${t.x.toFixed(1)}" y1="22" x2="${t.x.toFixed(1)}" y2="${yb}"/>`).join("") + `<line x1="${X1}" y1="22" x2="${X1}" y2="${yb}"/></g>`;
  s += `<g class="d-ax">` + days.map((t) => `<text x="${(t.x + 5).toFixed(1)}" y="${yb + 18}">${t.label}</text>`).join("") + `</g>`;
  if (live) {
    s += `<line x1="${nowX.toFixed(1)}" y1="14" x2="${nowX.toFixed(1)}" y2="${yb + 2}" stroke="var(--ink)" stroke-width="1.5"/>`;
    const near = nowX > X1 - 90;
    s += `<text x="${(nowX + (near ? -4 : 0)).toFixed(1)}" y="10" text-anchor="${near ? "end" : "middle"}" class="d-ax" style="fill:var(--ink);font-weight:700">Now, ${DAYS[now.getUTCDay()]} ${hhmm(now)}</text>`;
  }
  // Two tracks with the same plan name (one per computer) get the computer added, as in the ledger.
  const seen = {};
  for (const l of lines) seen[l.short] = (seen[l.short] || 0) + 1;
  lines.forEach((l, i) => {
    const y = ys[i];
    const full = seen[l.short] > 1 && l.computer ? `${l.short}, ${l.computer}` : l.short;
    const name = full.length > 22 ? full.slice(0, 21) + "…" : full;
    const lt = leftText(l.limit);
    s += `<text x="0" y="${y + 5}"><title>${esc(l.name)}</title><tspan class="d-nm"${l.pool ? "" : ' style="fill:var(--red)"'}>${esc(name)}</tspan>${lt ? `<tspan class="d-v" dx="6">${esc(lt.replace(/ left$/, ""))}${l.limit.how === "manual" ? " (manual)" : ""}</tspan>` : ""}</text>`;
    s += `<line x1="${X0}" y1="${y}" x2="${lineEnd.toFixed(1)}" y2="${y}" stroke="${l.color}" stroke-width="7"${l.pool ? "" : ' stroke-dasharray="9 7"'}/>`;
    if (live) s += `<line x1="${nowX.toFixed(1)}" y1="${y}" x2="${X1}" y2="${y}" stroke="var(--rule)" stroke-width="3"/>`;
    // Resets first, so a task box drawn over one stays readable; the mark's ends and its label still show.
    const marks = l.limit && l.limit.window === "5h" ? [nextReset(l.limit, now)].filter((r) => r && r.getTime() <= end).map((r) => r.getTime()) : resetsBetween(l.limit, start, end);
    for (const t of marks) {
      const rx = x(t);
      const past = t < now.getTime();
      const flip = rx > X1 - 150;
      s += `<g class="reset${past ? " past" : ""}"><title>${esc(`${l.short} resets ${localDate(new Date(t))}`)}</title>` +
        `<path class="halo" d="M${rx.toFixed(1)} ${y - 12} v24"/><path d="M${rx.toFixed(1)} ${y - 12} v24 M${rx.toFixed(1)} ${y} h9"/>` +
        `<text x="${(rx + (flip ? -6 : 6)).toFixed(1)}" y="${y + 26}" text-anchor="${flip ? "end" : "start"}">${esc(`${past ? "reset" : "resets"} ${localWhen(new Date(t))}`)}</text></g>`;
    }
    let prevEnd = X0;
    let hidden = 0;
    for (const j of byLine[l.key] || []) {
      const label = chipLabel(j);
      const w = Math.max(30, Math.min(116, 10 + label.length * 6.4));
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
        `<rect x="${cx.toFixed(1)}" y="${y - 9}" width="${w.toFixed(1)}" height="18"${fillAttr}/><text x="${(cx + w / 2).toFixed(1)}" y="${y + 4}">${esc(label)}</text></g>`;
    }
    if (hidden) s += `<text x="${X1}" y="${y - 14}" text-anchor="end" class="more"><a href="#jobs">+${hidden} more, see all jobs below ↓</a></text>`;
  });
  s += `</svg>`;
  const stripe = (ls) => { const c = ls.filter((l) => l.pool).map((l) => l.color); const w = 26 / (c.length || 1); return `<svg width="26" height="8" aria-hidden="true">${c.map((col, i) => `<rect x="${(i * w).toFixed(1)}" y="1" width="${w.toFixed(1)}" height="6" fill="${col}"/>`).join("")}</svg>`; };
  const scale = d.range.key === "30d" ? "period" : "week";
  return `${chipToggle(d)}<div class="scroll">${s}</div>
  <div class="d-legend" aria-label="How to read the diagram">
    <span>${stripe(lines)}Each track is a plan, coloured as in the ledger</span>
    <span><svg width="20" height="12" aria-hidden="true"><rect width="20" height="12" fill="#16140F"/></svg>A task that plan did</span>
    <span><svg width="20" height="12" aria-hidden="true"><rect x="1" y="1" width="18" height="10" fill="none" stroke="#B3200F" stroke-width="1.5" stroke-dasharray="3 2"/></svg>Missed, no plan had room</span>
    <span><svg width="12" height="14" aria-hidden="true"><path d="M3 1 v12 M3 7 h7" stroke="#B3200F" stroke-width="2.5"/></svg>The plan’s limit resets</span>
    ${live ? `<span><svg width="26" height="8" aria-hidden="true"><line x1="0" y1="4" x2="26" y2="4" stroke="#D6D0C2" stroke-width="3"/></svg>Rest of the ${scale}</span>` : ""}
    <span class="note">Days are to scale (day lines at midnight UTC); reset labels are in your time zone, ${esc(zoneShort(new Date()))}. Resets sit at their exact time; a box starts at its job's time and only shifts right to avoid overlapping.</span>
  </div>`;
}

function plansSection(d, lines, now) {
  const diagram = lines.length ? `<details class="diagram" id="diagram"${diagramOpen ? " open" : ""}>
    <summary><b class="when-closed">Show</b><b class="when-open">Hide</b> route diagram<span>each plan as a track across the ${d.range.key === "30d" ? "30 days" : "week"}, with its tasks and resets</span></summary>
    ${diagramOpen ? map(d, lines, now) : ""}</details>` : "";
  return ledger(d, lines, now) + diagram;
}

// ---------- 04 Jobs ----------
function tables(d, lines) {
  const t = d.totals;
  const rows = lines.map((l) => `<div role="row"><span class="seg" style="background:${l.color}"></span><span>${esc(l.short)}${l.computer ? ` <span class="est">${esc(l.computer)}</span>` : ""}</span>` +
    `<span class="r">${l.jobs}</span><span class="r">${l.ran}</span><span class="r${l.missed ? " no" : ""}">${l.missed}</span>` +
    `<span class="r">${l.pool && l.priced ? money(l.saved) : "n/a"}</span></div>`).join("");
  const lineOf = Object.fromEntries(lines.map((l) => [l.key, l]));
  const misses = d.jobs.filter((j) => j.miss.length).map((j) => {
    const l = lineOf[j.line];
    const at = parseMinute(j.at);
    return `<div role="row"><span class="id">#${j.n}</span><span>${esc(j.title || ("#" + j.n))}<span class="why">${j.miss.map((m) => WHY[m] || m).join("; ")}</span></span><span class="at">${DAYS[at.getUTCDay()]} ${hhmm(at)}</span>` +
      `<span class="p"><span class="seg" style="background:${l.color}"></span>${esc(l.short)}</span></div>`;
  }).join("");
  const period = { week: "this week", last: "last week", "30d": "the last 30 days" }[d.range.key];
  return `<div class="jobs">
    <div>
      <h3>Where jobs went <span class="dim">· by plan</span></h3>
      <div class="tbl" role="table" aria-label="Jobs by plan">
        <div class="h" role="row"><span></span><span>Plan</span><span class="r">Jobs</span><span class="r">Ran</span><span class="r">Missed</span><span class="r">API value (est.)</span></div>
        ${rows}
        <div role="row" class="tot"><span></span><span>Total</span><span class="r">${t.jobs}</span><span class="r">${t.ran}</span><span class="r${t.missed ? " no" : ""}">${t.missed}</span><span class="r">${t.priced ? money(t.saved) : "n/a"}</span></div>
      </div>
      <p class="fine">Totals for ${period} by plan. API value (est.) is what the work would have cost at public API list prices, computed only from token usage or cost the tool itself reported; anything else shows n/a. Every task counts once, under the plan that did it. Grok Bot’s own work, done without routing, has its own row. Plans show as ChatGPT, Claude Personal or Claude Team when the upload can tell them apart; otherwise as pool-N.</p>
    </div>
    <div id="misses">
      <h3>${misses ? `Misses <span class="dim">· oldest first</span>` : "No misses"}</h3>
      ${misses ? `<div class="misses" role="table" aria-label="Missed jobs">${misses}</div>` : `<p class="fine">Every task ${period} found a plan with room.</p>`}
    </div>
  </div>`;
}

// Every task: pools -> tasks -> runs, collapsed by default
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
      `<b>${esc(l.name)}</b><span class="meta">${plural(tasks.length, "task")} · ${plural(l.runs || 0, "run")}</span><span class="chev" aria-hidden="true"></span></summary>` +
      (openKeys.has(pk) ? `<div class="task-wrap">${head + rows + more(pk, tasks.length)}</div>` : "") + `</details>`;
  }).join("");
  return `<div class="every" id="jobs"><h3>Every task <span class="dim">· by plan. Click a plan to see its tasks, and a task to see each run.</span></h3>${pools}</div>`;
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
  const dg = document.getElementById("diagram");
  if (dg) dg.addEventListener("toggle", () => {
    const was = diagramOpen;
    diagramOpen = dg.open;
    try { localStorage.setItem("allo.diagram", dg.open ? "open" : "closed"); } catch {}
    if (was !== dg.open) render();
  });
}

// ---------- 05 The period: counts, runs per day, API value ----------
function period(d, now) {
  const t = d.totals;
  const start = new Date(d.range.start).getTime();
  const nDays = Math.max(1, Math.round((new Date(d.range.end).getTime() - start) / 86400000));
  const per = Array(nDays).fill(0);
  for (const j of d.jobs) for (const r of j.list || []) {
    const i = Math.floor((parseMinute(r.at).getTime() - start) / 86400000);
    if (i >= 0 && i < nDays) per[i] += 1;
  }
  const top = Math.max(1, ...per);
  const todayI = Math.floor((now.getTime() - start) / 86400000);
  const bars = per.map((n, i) => {
    const day = new Date(start + i * 86400000);
    const lab = d.range.key === "30d" ? (i % 5 === 0 ? mdy(day).slice(3, 5) : "") : DAYS[day.getUTCDay()][0];
    return `<div class="${i === todayI ? "today" : ""}${start + i * 86400000 > now.getTime() ? " future" : ""}" title="${DAYS[day.getUTCDay()]} ${mdy(day).slice(0, 5)}: ${plural(n, "run")}"><i style="height:${Math.round((n / top) * 72)}px"></i><span>${lab}</span></div>`;
  }).join("");
  const stats = `<div class="week">
    <div><span class="fig">${t.jobs}</span><span class="dim">tasks</span></div><div><span class="fig">${t.runs ?? t.jobs}</span><span class="dim">runs</span></div>
    <div><span class="fig">${t.ran}</span><span class="dim">ran</span></div><div class="${t.missed ? "alert" : ""}"><span class="fig">${t.missed}</span><span class="dim">missed</span></div>
    <div><span class="dim" style="font-size:12px">Runs per day, UTC</span><div class="days" style="grid-template-columns:repeat(${nDays}, 1fr)" aria-label="${esc(`Runs per day: ${per.join(", ")}`)}">${bars}</div></div>
  </div>`;
  const comps = d.computers.filter((c) => c.last_upload_at).sort((a, b) => b.last_upload_at.localeCompare(a.last_upload_at));
  let uploads = "No upload yet.";
  if (comps.length) {
    const lastC = comps[0], last = new Date(lastC.last_upload_at);
    const day = mdy(last) === mdy(now) ? "today" : mdy(last);
    uploads = `Last upload ${hhmm(last)} from ${esc(lastC.name)}, ${day} UTC.`;
    if (comps.length > 1) { const a = new Date(comps[1].last_upload_at); uploads += ` ${esc(comps[1].name)} last uploaded at ${hhmm(a)}.`; }
  }
  const saved = t.priced
    ? `<div class="saved"><span class="fig">${money(t.saved)}</span><p><b>API value (est.) ${PERIOD[d.range.key]}.</b> What the work would have cost at public API list prices, from real reported token usage or cost. ${t.priced} of ${t.runs ?? t.jobs} runs had usage; the rest show n/a. ${uploads}</p></div>`
    : `<div class="saved"><span class="fig">n/a</span><p><b>API value n/a:</b> no run reported real token usage or cost yet. ${uploads}</p></div>`;
  return stats + saved;
}

// ---------- 06 Computers, 07 What this page gets ----------
function computers(d) {
  const rows = d.computers.map((c) => {
    const up = c.last_upload_at ? (() => { const a = new Date(c.last_upload_at); return `Last upload ${mdy(a)} at ${hhmm(a)}.`; })() : "No upload yet.";
    return `<div><dt>${esc(c.name)}</dt><dd>${c.connected_at ? `Connected ${mdy(new Date(c.connected_at))}.` : ""} ${up} ${plural(c.jobs, "job")} in this period.</dd>` +
      `<button class="link act" data-disconnect="${c.id}" data-name="${esc(c.name)}">Disconnect</button></div>`;
  }).join("");
  return `<p class="intro">Each one was connected with <code>allo-router connect</code> and has its own upload token. A token can add jobs, but it can't read anything.</p>
    <dl class="rows">${rows}</dl>`;
}

function empty() {
  return `<div class="connect">
    <h1>Connect a computer</h1>
    <ol>
      <li>Update Allo Router to 0.5 or later on the computer that runs your jobs.</li>
      <li>In your router folder, run <code>allo-router connect --show</code> to see exactly what would be sent. Nothing is sent yet.</li>
      <li>Run <code>allo-router connect</code> and approve the code it shows on this site. Jobs appear here as they finish.</li>
    </ol>
  </div>`;
}

const about = `<p class="intro">The same list is on the <a href="/privacy">privacy page</a> and in <code>allo-router connect --show</code>, which prints the next upload without sending it.</p>
    <dl class="rows">
      <div class="two"><dt>Sent</dt><dd>A hashed job id, when it started (to the minute), the plan it went to as pool-1, pool-2, its type and provider (ChatGPT / Claude Personal / Claude Team when known), a short job title, model and effort when set on the plan, whether it ran, why it missed and the API value (est.).</dd></div>
      <div class="two"><dt>Never sent</dt><dd>Prompts, code, commands, output, file names or paths, plan labels you typed, where a reading came from, or keys. Plan readings (% or $ left, reset time, when read, auto or manual) only if you turn them on with <code>allo-router connect --share-readings on</code>.</dd></div>
      <div class="two"><dt>Stop</dt><dd><code>allo-router disconnect</code> revokes the token and deletes that computer's history here. Your local dashboard keeps working either way.</dd></div>
    </dl>`;

function render() {
  if (!data) return;
  const now = new Date();
  const lines = styleLines(data.lines);
  const start = new Date(data.range.start);
  const scope = {
    week: `Week of ${mdy(start)}, Monday to now`,
    last: `Week of ${mdy(start)}, Monday to Sunday`,
    "30d": `The 30 days to ${mdy(now)}`,
  }[data.range.key];
  const title = { week: "This week", last: "Last week", "30d": "30 days" }[data.range.key];
  let n = 0;
  const num = () => String(++n).padStart(2, "0");
  let body;
  if (data.computers.length === 0) {
    body = sec(num(), "Start", empty());
  } else {
    const hasJobs = lines.length || (data.running && data.running.length);
    body = sec(num(), "Now", `<div class="now"><div>${nowRunning(data, lines, now)}</div><div>${nowNext(data, lines, now)}</div><div class="warn">${nowNeeds(data, lines, now)}</div></div>`)
      + sec(num(), "Suggested next", queueSection(data))
      + sec(num(), "Plans", plansSection(data, lines, now))
      + sec(num(), "Jobs", hasJobs ? tables(data, lines) + jobsSection(data, lines)
        : `<p class="empty">No jobs in this period. Jobs show up here after <code>run --execute</code> finishes them on a connected computer.</p>`)
      + sec(num(), title, period(data, now), `${scope}, from ${plural(data.computers.length, "computer")}`)
      + sec(num(), "Computers", computers(data));
  }
  body += sec(num(), "This page", about, "what it gets");
  document.getElementById("app").innerHTML = `<div class="wrap">${mast(data)}${body}
  <footer>
    <p class="note"><b>Only job numbers, plans, outcomes and estimates reach this page.</b> Job names and everything else stay on your computer; open <code>allo-router dashboard</code> there to see them. Times are UTC.</p>
    <p class="links"><span class="mark">Allo Router</span><span class="spots" id="spots" hidden></span><a href="/privacy">Privacy</a><button class="link" id="delete">Delete my account</button></p>
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
