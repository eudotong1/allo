// Allo Router hosted dashboard API (app.allorouter.com). One Worker, one D1 database, static pages in ../web.
//
//   GET  /v1/spots                     public: how many of the free spots are left (counted from real users)
//   GET  /v1/config                    which sign-in is available (GitHub, or the local dev login)
//   GET  /auth/login?next=/path        sign in with GitHub (or, in local dev only, ?dev_login=name)
//   GET  /auth/callback                GitHub OAuth callback
//   POST /auth/logout
//   POST /v1/device                    CLI: start approving this computer; returns a device_code and user_code
//   POST /v1/device/token              CLI: poll with device_code until approved; returns the upload token once
//   GET  /v1/device/:user_code         browser (signed in): what is being approved
//   POST /v1/device/approve | deny     browser (signed in): {user_code, name}
//   POST /v1/jobs                      CLI (Bearer upload token): job rows, strictly on the allowlist
//   POST /v1/disconnect                CLI (Bearer upload token): {keep_history}
//   GET  /v1/me                        browser: who is signed in
//   GET  /v1/me/dashboard?range=week|last|30d   browser: the page's data; ETag / 304 for cheap polling
//   POST /v1/me/installs/:id/disconnect          browser: revoke a computer and delete its rows
//   POST /v1/me/delete                 browser: delete the account and everything in it
//
// A daily cron deletes job rows older than 90 days, expired device codes and sessions, and tokens unused for 90 days.

import { MAX_ROWS, validateDisconnect, validatePayload } from "./schema.js";
import { minuteKey, rangeFor, summarize } from "./dashboard.js";

const DAY = 86400000;
const SESSION_DAYS = 30;
const DEVICE_MINUTES = 15;
const DEVICE_INTERVAL_S = 5;
const RETENTION_DAYS = 90;
const UPLOADS_PER_HOUR = 300;
const MAX_BODY = 300 * 1024;
const USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ";  // no vowels, so a code never spells a word
const SESSION_COOKIE = "allo_session";
const OAUTH_COOKIE = "allo_oauth";

// ---------- helpers ----------

const iso = (d = new Date()) => d.toISOString();

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
  });
}

const error = (status, code, detail) => json(detail ? { error: code, detail } : { error: code }, status);

function randomHex(bytes) {
  const a = crypto.getRandomValues(new Uint8Array(bytes));
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function userCode() {
  const a = crypto.getRandomValues(new Uint8Array(8));
  const s = [...a].map((b) => USER_CODE_ALPHABET[b % USER_CODE_ALPHABET.length]).join("");
  return `${s.slice(0, 4)}-${s.slice(4)}`;
}

function cookies(request) {
  const out = {};
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function cookie(name, value, url, maxAge) {
  const secure = url.protocol === "https:" ? "; Secure" : "";
  return `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${secure}`;
}

async function readJson(request) {
  const len = Number(request.headers.get("Content-Length") || 0);
  if (len > MAX_BODY) return { tooLarge: true };
  const text = await request.text();
  if (text.length > MAX_BODY) return { tooLarge: true };
  try {
    return { body: JSON.parse(text) };
  } catch {
    return { bad: true };
  }
}

// Browser POSTs must come from this site. SameSite=Lax already keeps the cookie off cross-site POSTs.
function sameOrigin(request, url) {
  return request.headers.get("Origin") === url.origin;
}

// Only a local path, so ?next= can't send someone to another site after sign-in.
function safeNext(next) {
  return typeof next === "string" && /^\/(?!\/)[A-Za-z0-9/_?=&.\-]*$/.test(next) ? next : "/";
}

export function freeSpots(env) {
  const n = parseInt(env.FREE_SPOTS || "100", 10);
  return Number.isFinite(n) && n >= 0 ? n : 100;
}

const devLoginOn = (env, url) =>
  env.DEV_LOGIN === "true" && !env.GITHUB_CLIENT_ID && ["127.0.0.1", "localhost"].includes(url.hostname);

// ---------- users, spots and the waitlist ----------

export async function spots(env) {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM users").first();
  const limit = freeSpots(env);
  return { limit, taken: row.n, left: Math.max(0, limit - row.n) };
}

// Sign a GitHub identity in. An existing user always gets in. A new one gets a free spot if one is left, in a
// single statement, so two sign-ins at once can't both take the last spot. Otherwise they join the waitlist.
export async function signIn(env, ghId, login) {
  const now = iso();
  const existing = await env.DB.prepare("SELECT id FROM users WHERE github_id = ?").bind(ghId).first();
  if (existing) {
    await env.DB.prepare("UPDATE users SET login = ? WHERE id = ?").bind(login, existing.id).run();
    return { userId: existing.id };
  }
  const ins = await env.DB.prepare(
    "INSERT OR IGNORE INTO users (github_id, login, created_at) SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM users) < ?",
  ).bind(ghId, login, now, freeSpots(env)).run();
  if (ins.meta.changes === 1) {
    await env.DB.prepare("DELETE FROM waitlist WHERE github_id = ?").bind(ghId).run();
    const row = await env.DB.prepare("SELECT id FROM users WHERE github_id = ?").bind(ghId).first();
    return { userId: row.id };
  }
  await env.DB.prepare("INSERT OR IGNORE INTO waitlist (github_id, login, created_at) VALUES (?, ?, ?)")
    .bind(ghId, login, now).run();
  return { waitlisted: true };
}

async function startSession(env, userId, url, next) {
  const value = randomHex(32);
  const now = new Date();
  await env.DB.prepare("INSERT INTO sessions (id_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256(value), userId, iso(now), iso(new Date(now.getTime() + SESSION_DAYS * DAY))).run();
  return new Response(null, {
    status: 302,
    headers: { Location: safeNext(next), "Set-Cookie": cookie(SESSION_COOKIE, value, url, SESSION_DAYS * 86400) },
  });
}

async function finishSignIn(env, url, ghId, login, next) {
  const r = await signIn(env, ghId, login);
  if (r.waitlisted) {
    return new Response(null, { status: 302, headers: { Location: `/waitlist?login=${encodeURIComponent(login)}` } });
  }
  return startSession(env, r.userId, url, next);
}

// The signed-in user, with data_version for the ETag, in one read.
async function sessionUser(env, request) {
  const value = cookies(request)[SESSION_COOKIE];
  if (!value) return null;
  return env.DB.prepare(
    "SELECT u.id, u.login, u.created_at, u.data_version FROM sessions s JOIN users u ON u.id = s.user_id " +
    "WHERE s.id_hash = ? AND s.expires_at > ?",
  ).bind(await sha256(value), iso()).first();
}

async function tokenInstall(env, request) {
  const m = /^Bearer (allo_up_[0-9a-f]{64})$/.exec(request.headers.get("Authorization") || "");
  if (!m) return null;
  const inst = await env.DB.prepare(
    "SELECT id, user_id, name, created_at, last_upload_at, window_start, window_count FROM installs " +
    "WHERE token_hash = ? AND revoked_at IS NULL",
  ).bind(await sha256(m[1])).first();
  if (!inst) return null;
  const lastUsed = new Date(inst.last_upload_at || inst.created_at).getTime();
  return Date.now() - lastUsed > RETENTION_DAYS * DAY ? null : inst;
}

const bump = (env, userId) => env.DB.prepare("UPDATE users SET data_version = data_version + 1 WHERE id = ?").bind(userId);

async function revokeInstall(env, inst, keepHistory) {
  const stmts = [
    env.DB.prepare("UPDATE installs SET revoked_at = ?, token_hash = NULL WHERE id = ?").bind(iso(), inst.id),
    bump(env, inst.user_id),
  ];
  stmts.unshift(env.DB.prepare("DELETE FROM plans WHERE install_id = ?").bind(inst.id));
  if (!keepHistory) stmts.unshift(env.DB.prepare("DELETE FROM jobs WHERE install_id = ?").bind(inst.id));
  await env.DB.batch(stmts);
}

// ---------- routes ----------

async function login(request, env, url) {
  const next = safeNext(url.searchParams.get("next") || "/");
  if (env.GITHUB_CLIENT_ID && env.GITHUB_CLIENT_SECRET) {
    const state = randomHex(16);
    const gh = new URL("https://github.com/login/oauth/authorize");
    gh.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
    gh.searchParams.set("redirect_uri", `${url.origin}/auth/callback`);
    gh.searchParams.set("state", state);
    gh.searchParams.set("scope", "");  // public profile only: id and login
    return new Response(null, {
      status: 302,
      headers: { Location: gh.toString(), "Set-Cookie": cookie(OAUTH_COOKIE, `${state}|${next}`, url, 600) },
    });
  }
  if (devLoginOn(env, url)) {
    const name = (url.searchParams.get("dev_login") || "").trim();
    if (!/^[A-Za-z0-9-]{1,39}$/.test(name)) return error(400, "dev_login must look like a GitHub login");
    // A stable fake GitHub id per name, far above real ids.
    const ghId = 9e12 + parseInt((await sha256(name.toLowerCase())).slice(0, 10), 16);
    return finishSignIn(env, url, ghId, name, next);
  }
  return error(503, "sign_in_not_configured", "set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET");
}

async function callback(request, env, url) {
  const saved = cookies(request)[OAUTH_COOKIE] || "";
  const [state, next] = [saved.slice(0, saved.indexOf("|")), saved.slice(saved.indexOf("|") + 1)];
  if (!state || url.searchParams.get("state") !== state || !url.searchParams.get("code")) {
    return error(400, "bad_oauth_state");
  }
  const tok = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "User-Agent": "allo-router-app" },
    body: JSON.stringify({
      client_id: env.GITHUB_CLIENT_ID, client_secret: env.GITHUB_CLIENT_SECRET,
      code: url.searchParams.get("code"), redirect_uri: `${url.origin}/auth/callback`,
    }),
  }).then((r) => r.json()).catch(() => ({}));
  if (!tok.access_token) return error(400, "github_sign_in_failed");
  const gh = await fetch("https://api.github.com/user", {
    headers: { Authorization: `Bearer ${tok.access_token}`, Accept: "application/vnd.github+json", "User-Agent": "allo-router-app" },
  }).then((r) => r.json()).catch(() => ({}));
  if (!Number.isInteger(gh.id) || typeof gh.login !== "string") return error(400, "github_sign_in_failed");
  const res = await finishSignIn(env, url, gh.id, gh.login, next);
  res.headers.append("Set-Cookie", cookie(OAUTH_COOKIE, "", url, 0));
  return res;
}

async function deviceStart(env, url) {
  const deviceCode = randomHex(32);
  const now = new Date();
  let code;
  for (let i = 0; i < 5; i++) {
    code = userCode();
    const r = await env.DB.prepare(
      "INSERT OR IGNORE INTO device_codes (device_hash, user_code, created_at, expires_at) VALUES (?, ?, ?, ?)",
    ).bind(await sha256(deviceCode), code, iso(now), iso(new Date(now.getTime() + DEVICE_MINUTES * 60000))).run();
    if (r.meta.changes === 1) break;
    code = null;
  }
  if (!code) return error(503, "try_again");
  return json({
    device_code: deviceCode,
    user_code: code,
    verification_uri: `${url.origin}/connect`,
    verification_uri_complete: `${url.origin}/connect?code=${code}`,
    interval: DEVICE_INTERVAL_S,
    expires_in: DEVICE_MINUTES * 60,
  });
}

async function deviceToken(request, env) {
  const { body, tooLarge, bad } = await readJson(request);
  if (tooLarge || bad || typeof body?.device_code !== "string") return error(400, "invalid_request");
  const hash = await sha256(body.device_code);
  const d = await env.DB.prepare("SELECT * FROM device_codes WHERE device_hash = ?").bind(hash).first();
  if (!d) return error(400, "invalid_grant");
  const now = new Date();
  if (d.status === "denied") return error(400, "access_denied");
  if (d.status === "claimed") return error(400, "invalid_grant");
  if (new Date(d.expires_at) < now) return error(400, "expired_token");
  if (d.status === "pending") {
    const tooSoon = d.last_poll_at && now - new Date(d.last_poll_at) < (DEVICE_INTERVAL_S - 1) * 1000;
    await env.DB.prepare("UPDATE device_codes SET last_poll_at = ? WHERE device_hash = ?").bind(iso(now), hash).run();
    return error(400, tooSoon ? "slow_down" : "authorization_pending");
  }
  // Approved: mint the token now and hand it over exactly once. Only its hash is stored.
  const token = `allo_up_${randomHex(32)}`;
  const claim = await env.DB.prepare("UPDATE device_codes SET status = 'claimed' WHERE device_hash = ? AND status = 'approved'")
    .bind(hash).run();
  if (claim.meta.changes !== 1) return error(400, "invalid_grant");
  await env.DB.batch([
    env.DB.prepare("UPDATE installs SET token_hash = ? WHERE id = ?").bind(await sha256(token), d.install_id),
    bump(env, d.user_id),
  ]);
  const inst = await env.DB.prepare("SELECT name FROM installs WHERE id = ?").bind(d.install_id).first();
  return json({ token, computer: inst.name });
}

async function deviceLookup(env, user, code) {
  const d = await env.DB.prepare("SELECT user_code, status, expires_at, user_id FROM device_codes WHERE user_code = ?")
    .bind(code.toUpperCase()).first();
  if (!d || (d.user_id && d.user_id !== user.id)) return error(404, "unknown_code");
  const expired = new Date(d.expires_at) < new Date() && d.status === "pending";
  return json({ user_code: d.user_code, status: expired ? "expired" : d.status, expires_at: d.expires_at });
}

async function deviceDecide(request, env, user, approve) {
  const { body } = await readJson(request);
  const code = typeof body?.user_code === "string" ? body.user_code.toUpperCase() : "";
  const d = await env.DB.prepare("SELECT device_hash, status, expires_at FROM device_codes WHERE user_code = ?").bind(code).first();
  if (!d) return error(404, "unknown_code");
  if (d.status !== "pending" || new Date(d.expires_at) < new Date()) return error(409, "code_already_used_or_expired");
  if (!approve) {
    await env.DB.prepare("UPDATE device_codes SET status = 'denied', user_id = ? WHERE device_hash = ?").bind(user.id, d.device_hash).run();
    return json({ ok: true, status: "denied" });
  }
  // Names are never user-typed: each computer is computer-1, computer-2, ... per user, in approval order.
  const top = await env.DB.prepare(
    "SELECT MAX(CAST(SUBSTR(name, 10) AS INTEGER)) AS n FROM installs WHERE user_id = ? AND name LIKE 'computer-%'",
  ).bind(user.id).first();
  const name = `computer-${(top?.n || 0) + 1}`;
  const inst = await env.DB.prepare("INSERT INTO installs (user_id, name, created_at) VALUES (?, ?, ?) RETURNING id")
    .bind(user.id, name, iso()).first();
  const upd = await env.DB.prepare(
    "UPDATE device_codes SET status = 'approved', user_id = ?, install_id = ? WHERE device_hash = ? AND status = 'pending'",
  ).bind(user.id, inst.id, d.device_hash).run();
  if (upd.meta.changes !== 1) {
    await env.DB.prepare("DELETE FROM installs WHERE id = ?").bind(inst.id).run();
    return error(409, "code_already_used_or_expired");
  }
  return json({ ok: true, status: "approved", computer: name });
}

async function uploadJobs(request, env) {
  const inst = await tokenInstall(env, request);
  if (!inst) return error(401, "bad_token");
  const { body, tooLarge, bad } = await readJson(request);
  if (tooLarge) return error(413, "too_large");
  if (bad) return error(400, "rejected", "not JSON");
  const why = validatePayload(body);
  if (why) return error(400, "rejected", why);
  // Rate limit per computer: UPLOADS_PER_HOUR in a rolling hour window.
  const now = new Date();
  const fresh = !inst.window_start || now - new Date(inst.window_start) > 3600000;
  if (!fresh && inst.window_count >= UPLOADS_PER_HOUR) return error(429, "rate_limited");
  const stmts = body.jobs.map((j) =>
    env.DB.prepare(
      "INSERT INTO jobs (install_id, id, user_id, at, pool, plan_type, provider, status, skip, miss, est_saved_usd, title, model, effort, heartbeat_at) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(install_id, id) DO UPDATE SET pool=excluded.pool, plan_type=excluded.plan_type, " +
      "provider=excluded.provider, status=excluded.status, skip=excluded.skip, miss=excluded.miss, " +
      "est_saved_usd=excluded.est_saved_usd, title=excluded.title, model=excluded.model, effort=excluded.effort, " +
      "heartbeat_at=excluded.heartbeat_at",
    ).bind(inst.id, j.id, inst.user_id, j.at, j.pool, j.plan_type, j.provider, j.status, j.skip, JSON.stringify(j.miss),
           j.est_saved_usd, j.title, j.model, j.effort, j.heartbeat_at),
  );
  for (const r of body.plans || []) {
    stmts.push(env.DB.prepare(
      "INSERT INTO plans (install_id, pool, user_id, plan_type, provider, left_pct, left_usd, resets_at, window, as_of, how) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(install_id, pool) DO UPDATE SET plan_type=excluded.plan_type, " +
      "provider=excluded.provider, left_pct=excluded.left_pct, left_usd=excluded.left_usd, resets_at=excluded.resets_at, " +
      "window=excluded.window, as_of=excluded.as_of, how=excluded.how",
    ).bind(inst.id, r.pool, inst.user_id, r.plan_type, r.provider, r.left_pct, r.left_usd, r.resets_at, r.window, r.as_of, r.how));
  }
  stmts.push(
    env.DB.prepare("UPDATE installs SET last_upload_at = ?, window_start = ?, window_count = ? WHERE id = ?")
      .bind(iso(now), fresh ? iso(now) : inst.window_start, fresh ? 1 : inst.window_count + 1, inst.id),
    bump(env, inst.user_id),
  );
  const res = await env.DB.batch(stmts);
  const stored = res.slice(0, body.jobs.length).reduce((n, r) => n + (r.meta.changes || 0), 0);
  return json({ received: body.jobs.length, stored });
}

async function cliDisconnect(request, env) {
  const inst = await tokenInstall(env, request);
  if (!inst) return error(401, "bad_token");
  const { body } = await readJson(request);
  const why = validateDisconnect(body);
  if (why) return error(400, "rejected", why);
  await revokeInstall(env, inst, body.keep_history);
  return json({ ok: true });
}

async function dashboard(request, env, user, url) {
  const range = rangeFor(url.searchParams.get("range") || "week", new Date());
  const etag = `W/"${user.data_version}-${range.key}-${minuteKey(range.start).slice(0, 10)}"`;
  if (request.headers.get("If-None-Match") === etag) {
    return new Response(null, { status: 304, headers: { ETag: etag, "Cache-Control": "private, no-cache" } });
  }
  const [installs, rows, spotInfo, plans] = await Promise.all([
    env.DB.prepare("SELECT id, name, created_at, last_upload_at FROM installs WHERE user_id = ? AND revoked_at IS NULL AND token_hash IS NOT NULL ORDER BY created_at")
      .bind(user.id).all().then((r) => r.results),
    env.DB.prepare("SELECT install_id, id, at, pool, plan_type, provider, status, skip, miss, est_saved_usd, title, model, effort, heartbeat_at FROM jobs " +
      "WHERE user_id = ? AND at >= ? AND at < ? ORDER BY at, install_id, id LIMIT 5000")
      .bind(user.id, minuteKey(range.start), minuteKey(range.end)).all().then((r) => r.results),
    spots(env),
    env.DB.prepare("SELECT install_id, pool, plan_type, provider, left_pct, left_usd, resets_at, window, as_of, how FROM plans WHERE user_id = ?")
      .bind(user.id).all().then((r) => r.results),
  ]);
  const s = summarize(rows, installs, new Date(), plans);
  const perInstall = {};
  for (const j of s.jobs) perInstall[j.computer] = (perInstall[j.computer] || 0) + 1;
  const computers = installs.map((i) => ({
    id: i.id, name: i.name, connected_at: i.created_at, last_upload_at: i.last_upload_at, jobs: perInstall[i.name] || 0,
  }));
  return json(
    {
      login: user.login,
      spots: spotInfo,
      range: { key: range.key, label: range.label, start: iso(range.start), end: iso(range.end) },
      ...s,
      computers,
    },
    200,
    { ETag: etag, "Cache-Control": "private, no-cache" },
  );
}

async function deleteAccount(env, user, url) {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM jobs WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM plans WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM installs WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM device_codes WHERE user_id = ?").bind(user.id),
    env.DB.prepare("DELETE FROM users WHERE id = ?").bind(user.id),
  ]);
  return json({ ok: true }, 200, { "Set-Cookie": cookie(SESSION_COOKIE, "", url, 0) });
}

export async function cleanup(env, now = new Date()) {
  const old = iso(new Date(now.getTime() - RETENTION_DAYS * DAY));
  await env.DB.batch([
    env.DB.prepare("DELETE FROM jobs WHERE at < ?").bind(minuteKey(new Date(old))),
    env.DB.prepare("DELETE FROM device_codes WHERE expires_at < ?").bind(iso(new Date(now.getTime() - DAY))),
    env.DB.prepare("DELETE FROM sessions WHERE expires_at < ?").bind(iso(now)),
    env.DB.prepare("UPDATE installs SET token_hash = NULL, revoked_at = ? WHERE revoked_at IS NULL AND COALESCE(last_upload_at, created_at) < ?")
      .bind(iso(now), old),
  ]);
}

async function route(request, env) {
  const url = new URL(request.url);
  const { pathname: p } = url;
  const m = request.method;

  if (p === "/v1/spots" && m === "GET") {
    return json(await spots(env), 200, { "Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=30" });
  }
  if (p === "/v1/spots" && m === "OPTIONS") {
    return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET" } });
  }
  if (p === "/v1/config" && m === "GET") {
    return json({ github: Boolean(env.GITHUB_CLIENT_ID && env.GITHUB_CLIENT_SECRET), dev_login: devLoginOn(env, url) });
  }
  if (p === "/auth/login" && m === "GET") return login(request, env, url);
  if (p === "/auth/callback" && m === "GET") return callback(request, env, url);
  if (p === "/v1/device" && m === "POST") return deviceStart(env, url);
  if (p === "/v1/device/token" && m === "POST") return deviceToken(request, env);
  if (p === "/v1/jobs" && m === "POST") return uploadJobs(request, env);
  if (p === "/v1/disconnect" && m === "POST") return cliDisconnect(request, env);

  // Everything below needs a browser session.
  if (m === "POST" && !sameOrigin(request, url)) return error(403, "bad_origin");
  const user = await sessionUser(env, request);
  if (p === "/auth/logout" && m === "POST") {
    const value = cookies(request)[SESSION_COOKIE];
    if (value) await env.DB.prepare("DELETE FROM sessions WHERE id_hash = ?").bind(await sha256(value)).run();
    return json({ ok: true }, 200, { "Set-Cookie": cookie(SESSION_COOKIE, "", url, 0) });
  }
  if (!user) return error(401, "sign_in");
  if (p === "/v1/me" && m === "GET") return json({ login: user.login, created_at: user.created_at });
  if (p === "/v1/me/dashboard" && m === "GET") return dashboard(request, env, user, url);
  if (p === "/v1/me/delete" && m === "POST") return deleteAccount(env, user, url);
  let r;
  if ((r = /^\/v1\/device\/([A-Za-z]{4}-[A-Za-z]{4})$/.exec(p)) && m === "GET") return deviceLookup(env, user, r[1]);
  if (p === "/v1/device/approve" && m === "POST") return deviceDecide(request, env, user, true);
  if (p === "/v1/device/deny" && m === "POST") return deviceDecide(request, env, user, false);
  if ((r = /^\/v1\/me\/installs\/(\d+)\/disconnect$/.exec(p)) && m === "POST") {
    const inst = await env.DB.prepare("SELECT id, user_id FROM installs WHERE id = ? AND user_id = ?").bind(Number(r[1]), user.id).first();
    if (!inst) return error(404, "unknown_computer");
    await revokeInstall(env, inst, false);
    return json({ ok: true });
  }
  return error(404, "not_found");
}

export default {
  async fetch(request, env, ctx) {
    try {
      return await route(request, env);
    } catch (e) {
      console.error(e);
      return error(500, "server_error");
    }
  },
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(cleanup(env));
  },
};
