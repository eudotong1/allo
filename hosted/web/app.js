// The dashboard: what the router did across your computers. Polls every 15 s while the tab is visible, with
// If-None-Match, so an unchanged page costs one tiny read.
import { api, DAYS, esc, hhmm, mdy, parseMinute, post, signInUrl, signOut } from "/common.js";

const POLL_MS = 15000;
const COLORS = {
  codex: "var(--codex)", chatgpt: "var(--codex)",
  claude: "var(--claude)", claude_personal: "var(--claude)", claude_team: "#C4A35A",
  api: "var(--credit)", gemini: "var(--gemini)", other: "var(--other)",
};
const EXTRA = ["#0F7C8C", "#6A4C93", "#B5651D", "#2E7D32", "#8C2F39"];
const PROVIDER = {
  claude: "Claude", claude_personal: "Claude Personal", claude_team: "Claude Team",
  codex: "ChatGPT", chatgpt: "ChatGPT", gemini: "Gemini", api: "API credit", other: "Other",
};
const MODEL_COLOR = {
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

function planLabel(provider, pool, computer) {
  const p = provider || "other";
  const n = pool ? pool.replace("pool-", "") : "";
  const named = PROVIDER[p] || "Other";
  // Specific providers are the display name; generic Claude/Other keep pool-N for disambiguation.
  const base = (p === "claude" || p === "other" || p === "gemini")
    ? `${named}, pool ${n}`
    : named;
  return base + (computer ? ` on ${computer}` : "");
}

function styleLines(lines) {
  const used = {};
  let extra = 0;
  return lines.map((l) => {
    if (!l.pool) return { ...l, color: "var(--none)", name: "No plan took it", short: "no plan" };
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
  if (chipMode === "model" && j.model) return j.model;
  if (chipMode === "effort" && j.effort) return j.effort.replace("_", " ");
  if (j.title) {
    const t = j.title.length > 18 ? j.title.slice(0, 16) + "…" : j.title;
    return t;
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
  return `<div class="chip-modes" role="group" aria-label="Colour chips by">${buttons}</div>`;
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
      <p><b>Estimated saving ${whenSaved}.</b> Every job priced at API rates, minus its price on the plan that ran it.</p>
      <p class="m">${uploads}</p>`;
  } else {
    const big = comps.length ? hhmm(new Date(comps[0].last_upload_at)) : "--:--";
    aside = `<div class="big cond">${big}</div>
      <p><b>${comps.length ? `Last upload from ${esc(comps[0].name)}.` : "No upload yet."}</b></p>
      <p class="m">No saving estimate yet: none of these jobs had a price.</p>`;
  }
  return `<header class="head">
    <div>
      <p class="scope">${scope}, from ${plural(d.computers.length, "computer")}</p>
      <h1 class="cond">${plural(t.jobs, "job")} allocated<br>across ${plural(plans, "plan")}</h1>
      <p class="sub"><b>Allo</b>cate usage across your plans based on how much of each limit remains.</p>
      <div class="stats">
        <div><b class="cond">${t.jobs}</b>jobs</div>
        <div><b class="cond">${t.ran}</b>ran</div>
        <div class="${t.missed ? "miss" : ""}"><b class="cond">${t.missed}</b>missed</div>
      </div>
    </div>
    <aside class="side">${aside}</aside>
  </header>`;
}

// The allocation strip: a stacked bar of each plan's share of jobs, with per-plan totals. Real data only.
function allocation(d, lines) {
  const total = d.totals.jobs || 1;
  const bar = lines.map((l) => {
    const pct = (l.jobs / total) * 100;
    return `<span class="${l.pool ? "" : "alloc-none"}" style="width:${pct.toFixed(2)}%;background:${l.color}" title="${esc(l.name)}: ${l.jobs} of ${d.totals.jobs}"></span>`;
  }).join("");
  const keys = lines.map((l) => {
    const pct = Math.round((l.jobs / total) * 100);
    const meta = l.pool
      ? `${plural(l.jobs, "job")} \u00b7 ${pct}%` + (l.priced ? ` \u00b7 ${money(l.saved)} saved` : (l.plan_type === "credit_usd" ? " \u00b7 API price" : ""))
      : `${plural(l.jobs, "job")} \u00b7 ${pct}% \u00b7 missed, no plan fit`;
    return `<div><span class="sw" style="background:${l.color}"></span><b${l.pool ? "" : ' class="no"'}>${esc(l.name)}</b><span class="meta">${meta}</span></div>`;
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
    const counts = `${plural(l.jobs, "job")}${l.missed && l.pool ? `, ${l.missed} missed` : ""}`;
    s += `<text x="262" y="${y - 24}" class="t-name"${red}>${esc(l.name)}<tspan class="t-num" dx="12"${red}>${counts}</tspan></text>`;
    const share = Math.round((l.jobs / (d.totals.jobs || 1)) * 100);
    s += `<text x="1308" y="${y + 6}" text-anchor="end" class="t-share"${red}>${share}%</text>`;
    const cap = l.pool ? (l.priced ? `${money(l.saved)} saved` : (l.plan_type === "credit_usd" ? "API price" : "no estimate")) : "missed, no plan fit";
    s += `<text x="1308" y="${y + 26}" text-anchor="end" class="t-est">${cap}</text>`;
    let prevEnd = X0;
    let hidden = 0;
    for (const j of byLine[l.key] || []) {
      const label = chipLabel(j);
      const w = Math.max(34, Math.min(120, 10 + label.length * 7.2));
      const cx = Math.max(x(parseMinute(j.at).getTime()), prevEnd + 4);
      if (cx + w > X1 - 4) { hidden++; continue; }
      prevEnd = cx + w;
      const missed = j.miss.length > 0;
      const fill = chipFill(j, l.color);
      const tip = [`#${j.n}`, j.title, j.model, j.effort ? `effort ${j.effort.replace("_", " ")}` : null, j.status,
        missed ? j.miss.map((m) => WHY[m] || m).join(", ") : null].filter(Boolean).join(" · ");
      const fillAttr = (!missed && fill) ? ` style="fill:${fill}"` : "";
      s += `<g class="${missed ? "car-x" : "car"}"><title>${esc(tip)}</title>` +
        `<rect x="${cx.toFixed(1)}" y="${y - 9}" width="${w}" height="18"${fillAttr}/><text x="${(cx + w / 2).toFixed(1)}" y="${y + 4}">${esc(label)}</text></g>`;
    }
    if (hidden) s += `<text x="${X1 - 4}" y="${y + 26}" text-anchor="end" class="more">+${hidden} more, see the table</text>`;
  });
  s += `</svg>`;
  const sample = lines.find((l) => l.pool)?.color || "var(--codex)";
  return `<div class="map">${s}</div>
  <div class="legend" aria-hidden="true">
    <span><svg width="34" height="18"><rect x="1" y="1" width="32" height="16" fill="var(--ink)"/></svg>Ran</span>
    <span><svg width="34" height="18"><rect x="1" y="1" width="32" height="16" fill="var(--paper)" stroke="var(--reset-text)" stroke-width="1.5" stroke-dasharray="4 3"/></svg>Missed</span>
    <span><svg width="28" height="10"><rect width="28" height="10" fill="${sample}"/></svg>The plan that took it</span>
    ${live ? `<span><svg width="28" height="10"><rect y="3" width="28" height="4" fill="var(--rule)"/></svg>Rest of the ${d.range.key === "30d" ? "period" : "week"}</span>` : ""}
    <span>Jobs that start close together sit in queue order, not to time scale.</span>
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
      <p class="intro">Totals for ${period} by plan. The saving is an estimate: each job priced at API rates, minus its price on the plan that ran it. A subscription job counts as $0. Plans show as ChatGPT, Claude Personal or Claude Team when the upload can tell them apart; otherwise as pool-N.</p>
    </div>
    <div>
      <div class="tbl" role="table" aria-label="Jobs by plan">
        <div class="h" role="row"><span></span><span>Plan</span><span class="r">Jobs</span><span class="r">Ran</span><span class="r">Missed</span><span class="r">Saved, estimate</span></div>
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
    return `<div><dt>${esc(c.name)}</dt><dd>Connected ${mdy(new Date(c.connected_at))}. ${up} ${plural(c.jobs, "job")} in this period.</dd>` +
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
      <div class="two"><dt>Sent</dt><dd>A hashed job id, when it started (to the minute), the plan it went to as pool-1, pool-2, its type and provider (ChatGPT / Claude Personal / Claude Team when known), a short job title, model and effort when set on the plan, whether it ran, why it missed and the estimated saving.</dd></div>
      <div class="two"><dt>Never sent</dt><dd>Prompts, code, commands, output, file names or paths, plan labels you typed, usage readings or "% left", or keys.</dd></div>
      <div class="two"><dt>Stop</dt><dd><code>allo-router disconnect</code> revokes the token and deletes that computer's history here. Your local dashboard keeps working either way.</dd></div>
    </dl>
  </section>`;

function render() {
  if (!data) return;
  const now = new Date();
  const lines = styleLines(data.lines);
  const body = data.computers.length === 0
    ? empty()
    : (lines.length ? allocation(data, lines) + chipToggle(data) + map(data, lines, now) : `<section class="empty"><h2>No jobs in this period</h2><p class="intro">Jobs show up here after <code>run --execute</code> finishes them on a connected computer.</p></section>`) +
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
  for (const b of document.querySelectorAll("[data-chip-mode]")) {
    b.onclick = () => { chipMode = b.getAttribute("data-chip-mode"); render(); };
  }
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
}

setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
load();
