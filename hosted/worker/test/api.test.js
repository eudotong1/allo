import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker, { cleanup, signIn } from "../src/index.js";
import { rangeFor, summarize } from "../src/dashboard.js";

const ORIGIN = "http://127.0.0.1:8787";
const DEV = { ...env, DEV_LOGIN: "true" };

function call(path, { method = "GET", body, cookie, token, origin = ORIGIN, headers = {}, e = DEV } = {}) {
  const h = { ...headers };
  if (body !== undefined) h["Content-Type"] = "application/json";
  if (cookie) h.Cookie = cookie;
  if (token) h.Authorization = `Bearer ${token}`;
  if (method === "POST" && origin) h.Origin = origin;
  const req = new Request(ORIGIN + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  return worker.fetch(req, e, {});
}

async function devSignIn(name, e = DEV) {
  const r = await call(`/auth/login?dev_login=${name}&next=/`, { e });
  const set = r.headers.get("Set-Cookie") || "";
  return { r, cookie: set.startsWith("allo_session=") ? set.split(";")[0] : null };
}

async function connectComputer(cookie, name = "Studio Mac") {
  const dev = await (await call("/v1/device", { method: "POST", body: {}, origin: null })).json();
  const ok = await call("/v1/device/approve", { method: "POST", cookie, body: { user_code: dev.user_code, name } });
  expect(ok.status).toBe(200);
  const t = await (await call("/v1/device/token", { method: "POST", body: { device_code: dev.device_code }, origin: null })).json();
  expect(t.token).toMatch(/^allo_up_[0-9a-f]{64}$/);
  return t.token;
}

function job(over = {}) {
  return {
    id: "h:0123456789abcdef", at: "2026-10-13T03:00Z", pool: "pool-1", plan_type: "subscription", provider: "codex",
    status: "ok", skip: null, miss: [], est_saved_usd: 4.12, title: "Sample job", model: "gpt-5-codex", effort: "high", heartbeat_at: null, ...over,
  };
}
const payload = (jobs) => ({ schema: 1, sent_at: "2026-10-13T03:05Z", jobs });
const count = async (table) => (await env.DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first()).n;

afterEach(() => vi.restoreAllMocks());

describe("free spots and the waitlist", () => {
  it("counts real signed-up users, publicly", async () => {
    let s = await (await call("/v1/spots")).json();
    expect(s).toEqual({ limit: 100, taken: 0, left: 100 });
    await devSignIn("alice");
    await devSignIn("bob");
    await devSignIn("alice");  // signing in again doesn't take another spot
    const r = await call("/v1/spots");
    expect(r.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(await r.json()).toEqual({ limit: 100, taken: 2, left: 98 });
  });

  it("puts sign-in 101 on the waitlist, and lets existing users back in", async () => {
    for (let i = 0; i < 100; i++) expect((await signIn(env, 1000 + i, `user${i}`)).userId).toBeTruthy();
    expect(await (await call("/v1/spots")).json()).toEqual({ limit: 100, taken: 100, left: 0 });

    const { r, cookie } = await devSignIn("latecomer");
    expect(r.status).toBe(302);
    expect(r.headers.get("Location")).toBe("/waitlist?login=latecomer");
    expect(cookie).toBeNull();
    expect(await count("users")).toBe(100);
    expect(await env.DB.prepare("SELECT login FROM waitlist").first()).toEqual({ login: "latecomer" });
    await devSignIn("latecomer");
    expect(await count("waitlist")).toBe(1);

    expect((await signIn(env, 1000, "user0")).userId).toBeTruthy();  // an existing user is never cut off
    expect(await count("users")).toBe(100);
  });

  it("frees a spot when an account is deleted", async () => {
    const e = { ...DEV, FREE_SPOTS: "1" };
    const a = await devSignIn("first", e);
    expect((await devSignIn("second", e)).cookie).toBeNull();
    const del = await call("/v1/me/delete", { method: "POST", cookie: a.cookie, e });
    expect(del.status).toBe(200);
    expect((await (await call("/v1/spots", { e })).json()).left).toBe(1);
    expect((await devSignIn("second", e)).cookie).toBeTruthy();
    expect(await count("waitlist")).toBe(0);
  });

  it("never lets simultaneous sign-ins take more spots than exist", async () => {
    const e = { ...env, FREE_SPOTS: "3" };
    const res = await Promise.all(Array.from({ length: 10 }, (_, i) => signIn(e, 5000 + i, `racer${i}`)));
    expect(res.filter((x) => x.userId).length).toBe(3);
    expect(await count("users")).toBe(3);
    expect(await count("waitlist")).toBe(7);
  });
});

describe("upload allowlist", () => {
  it("stores an allowlisted payload and upserts on re-upload", async () => {
    const { cookie } = await devSignIn("alice");
    const token = await connectComputer(cookie);
    const r = await call("/v1/jobs", { method: "POST", token, body: payload([job()]), origin: null });
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ received: 1, stored: 1 });
    const again = await (await call("/v1/jobs", { method: "POST", token,
      body: payload([job({ provider: "claude_personal", title: "Industry pages", model: "opus", effort: "high" })]),
      origin: null })).json();
    expect(again.received).toBe(1);
    expect(again.stored).toBeGreaterThanOrEqual(1);  // upsert updates the row
    expect(await count("jobs")).toBe(1);
    const row = await env.DB.prepare("SELECT provider, title, model, effort FROM jobs").first();
    expect(row).toEqual({ provider: "claude_personal", title: "Industry pages", model: "opus", effort: "high" });
  });

  const bad = {
    "an unknown top-level field": (p) => ({ ...p, hostname: "studio" }),
    "a task name": (p) => ({ ...p, jobs: [{ ...p.jobs[0], task_name: "Review contract" }] }),
    "a prompt": (p) => ({ ...p, jobs: [{ ...p.jobs[0], prompt: "secret" }] }),
    "a % left reading": (p) => ({ ...p, jobs: [{ ...p.jobs[0], remaining_pct: 40 }] }),
    "a file path": (p) => ({ ...p, jobs: [{ ...p.jobs[0], output: "runs/x.log" }] }),
    "a typed pool id": (p) => ({ ...p, jobs: [job({ pool: "acme-redlines" })] }),
    "seconds in a time": (p) => ({ ...p, jobs: [job({ at: "2026-10-13T03:00:27Z" })] }),
    "an unhashed job id": (p) => ({ ...p, jobs: [job({ id: "Review contract" })] }),
    "a free-text miss reason": (p) => ({ ...p, jobs: [job({ miss: ["timed out on /Users/me"] })] }),
    "an unknown provider": (p) => ({ ...p, jobs: [job({ provider: "my-claude-plan" })] }),
    "a missing field": (p) => { const j = job(); delete j.miss; return { ...p, jobs: [j] }; },
    "a string saving": (p) => ({ ...p, jobs: [job({ est_saved_usd: "4.12" })] }),
    "a wrong schema": (p) => ({ ...p, schema: 2 }),
    "too many rows": (p) => ({ ...p, jobs: Array.from({ length: 501 }, (_, i) => job({ id: `h:${i.toString(16).padStart(16, "0")}` })) }),
  };
  for (const [what, mutate] of Object.entries(bad)) {
    it(`rejects ${what} and stores nothing`, async () => {
      const { cookie } = await devSignIn("alice");
      const token = await connectComputer(cookie);
      const r = await call("/v1/jobs", { method: "POST", token, body: mutate(payload([job()])), origin: null });
      expect(r.status).toBe(400);
      const body = await r.json();
      expect(body.error).toBe("rejected");
      expect(JSON.stringify(body)).not.toContain("Review contract");  // reasons never echo values back
      expect(await count("jobs")).toBe(0);
    });
  }

  it("refuses a missing, wrong or revoked token", async () => {
    expect((await call("/v1/jobs", { method: "POST", body: payload([job()]), origin: null })).status).toBe(401);
    expect((await call("/v1/jobs", { method: "POST", token: "allo_up_" + "0".repeat(64), body: payload([job()]), origin: null })).status).toBe(401);
    const { cookie } = await devSignIn("alice");
    const token = await connectComputer(cookie);
    expect((await call("/v1/disconnect", { method: "POST", token, body: { keep_history: false }, origin: null })).status).toBe(200);
    expect((await call("/v1/jobs", { method: "POST", token, body: payload([job()]), origin: null })).status).toBe(401);
  });
});

describe("device approval", () => {
  it("approves a computer once and hands the token over once", async () => {
    const { cookie } = await devSignIn("alice");
    const dev = await (await call("/v1/device", { method: "POST", body: {}, origin: null })).json();
    expect(dev.user_code).toMatch(/^[B-DF-HJ-NP-TV-XZ]{4}-[B-DF-HJ-NP-TV-XZ]{4}$/);
    expect(dev.verification_uri_complete).toBe(`${ORIGIN}/connect?code=${dev.user_code}`);
    const poll = () => call("/v1/device/token", { method: "POST", body: { device_code: dev.device_code }, origin: null });
    expect((await (await poll()).json()).error).toBe("authorization_pending");
    expect((await (await poll()).json()).error).toBe("slow_down");

    expect((await call(`/v1/device/${dev.user_code}`)).status).toBe(401);  // needs sign-in
    const look = await (await call(`/v1/device/${dev.user_code}`, { cookie })).json();
    expect(look.status).toBe("pending");
    // a cross-site POST is refused even with the cookie
    expect((await call("/v1/device/approve", { method: "POST", cookie, origin: "https://evil.example", body: { user_code: dev.user_code, name: "x" } })).status).toBe(403);
    expect((await call("/v1/device/approve", { method: "POST", cookie, body: { user_code: dev.user_code, name: "Work laptop" } })).status).toBe(200);

    const t = await (await poll()).json();
    expect(t.computer).toBe("computer-1");
    expect(t.token).toMatch(/^allo_up_/);
    expect((await (await poll()).json()).error).toBe("invalid_grant");
    const stored = await env.DB.prepare("SELECT token_hash FROM installs").first();
    expect(stored.token_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(stored.token_hash).not.toContain(t.token.slice(8));
  });

  it("reports a declined or expired code", async () => {
    const { cookie } = await devSignIn("alice");
    const a = await (await call("/v1/device", { method: "POST", body: {}, origin: null })).json();
    await call("/v1/device/deny", { method: "POST", cookie, body: { user_code: a.user_code } });
    expect((await (await call("/v1/device/token", { method: "POST", body: { device_code: a.device_code }, origin: null })).json()).error).toBe("access_denied");
    const b = await (await call("/v1/device", { method: "POST", body: {}, origin: null })).json();
    await env.DB.prepare("UPDATE device_codes SET expires_at = '2000-01-01T00:00:00.000Z' WHERE user_code = ?").bind(b.user_code).run();
    expect((await (await call("/v1/device/token", { method: "POST", body: { device_code: b.device_code }, origin: null })).json()).error).toBe("expired_token");
    expect((await call("/v1/device/approve", { method: "POST", cookie, body: { user_code: b.user_code, name: "x" } })).status).toBe(409);
  });
});

describe("dashboard", () => {
  it("needs a session", async () => {
    expect((await call("/v1/me/dashboard")).status).toBe(401);
  });

  it("summarizes uploads, and answers 304 until something changes", async () => {
    const { cookie } = await devSignIn("alice");
    const token = await connectComputer(cookie, "Studio Mac");
    const now = new Date();
    const at = new Date(Math.max(rangeFor("week", now).start.getTime(), now.getTime() - 3600000)).toISOString().slice(0, 16) + "Z";
    const jobs = [
      job({ id: "h:0000000000000001", at }),
      job({ id: "h:0000000000000002", at, status: "timeout", miss: ["timeout"], est_saved_usd: 1 }),
      job({ id: "h:0000000000000003", at, pool: "pool-2", plan_type: "credit_usd", provider: "api", est_saved_usd: 0 }),
      job({ id: "h:0000000000000004", at, pool: null, plan_type: null, provider: null, status: "skipped", skip: "unassigned", miss: ["unassigned"], est_saved_usd: null }),
      job({ id: "h:0000000000000005", at, status: "skipped", skip: "already-ran", est_saved_usd: null }),
    ];
    await call("/v1/jobs", { method: "POST", token, body: payload(jobs), origin: null });

    const r = await call("/v1/me/dashboard?range=week", { cookie });
    expect(r.status).toBe(200);
    const d = await r.json();
    expect(d.login).toBe("alice");
    expect(d.totals).toEqual({ jobs: 4, ran: 2, missed: 2, saved: 5.12, priced: 3, running: 0 });
    expect(d.lines.map((l) => [l.pool, l.jobs, l.missed])).toEqual([["pool-1", 2, 1], ["pool-2", 1, 0], [null, 1, 1]]);
    expect(d.computers).toMatchObject([{ name: "computer-1", jobs: 4 }]);
    expect(d.spots.left).toBe(99);

    const etag = r.headers.get("ETag");
    const again = await call("/v1/me/dashboard?range=week", { cookie, headers: { "If-None-Match": etag } });
    expect(again.status).toBe(304);
    await call("/v1/jobs", { method: "POST", token, body: payload([job({ id: "h:0000000000000009", at })]), origin: null });
    const changed = await call("/v1/me/dashboard?range=week", { cookie, headers: { "If-None-Match": etag } });
    expect(changed.status).toBe(200);
    expect((await changed.json()).totals.jobs).toBe(5);
  });

  it("shows each user only their own data", async () => {
    const a = await devSignIn("alice");
    const b = await devSignIn("bob");
    const token = await connectComputer(a.cookie);
    const at = new Date().toISOString().slice(0, 16) + "Z";
    await call("/v1/jobs", { method: "POST", token, body: payload([job({ at })]), origin: null });
    const d = await (await call("/v1/me/dashboard", { cookie: b.cookie })).json();
    expect(d.totals.jobs).toBe(0);
    expect(d.computers).toEqual([]);
    const inst = await env.DB.prepare("SELECT id FROM installs").first();
    expect((await call(`/v1/me/installs/${inst.id}/disconnect`, { method: "POST", cookie: b.cookie, body: {} })).status).toBe(404);
  });
});

describe("disconnect", () => {
  async function setup() {
    const { cookie } = await devSignIn("alice");
    const token = await connectComputer(cookie);
    await call("/v1/jobs", { method: "POST", token, body: payload([job()]), origin: null });
    return { cookie, token };
  }

  it("from the CLI revokes and deletes history", async () => {
    const { token } = await setup();
    expect((await call("/v1/disconnect", { method: "POST", token, body: { keep_history: false }, origin: null })).status).toBe(200);
    expect(await count("jobs")).toBe(0);
  });

  it("from the CLI can keep history, and accepts nothing but keep_history", async () => {
    const { token } = await setup();
    expect((await call("/v1/disconnect", { method: "POST", token, body: { keep_history: true, why: "x" }, origin: null })).status).toBe(400);
    expect((await call("/v1/disconnect", { method: "POST", token, body: { keep_history: true }, origin: null })).status).toBe(200);
    expect(await count("jobs")).toBe(1);
  });

  it("from the web page revokes and deletes history", async () => {
    const { cookie, token } = await setup();
    const inst = await env.DB.prepare("SELECT id FROM installs").first();
    expect((await call(`/v1/me/installs/${inst.id}/disconnect`, { method: "POST", cookie, body: {} })).status).toBe(200);
    expect(await count("jobs")).toBe(0);
    expect((await call("/v1/jobs", { method: "POST", token, body: payload([job()]), origin: null })).status).toBe(401);
  });
});

describe("sign-in", () => {
  it("allows the dev login only on localhost, only when asked for and only without GitHub", async () => {
    expect((await devSignIn("alice", env)).r.status).toBe(503);
    expect((await devSignIn("alice", { ...DEV, GITHUB_CLIENT_ID: "id", GITHUB_CLIENT_SECRET: "s" })).r.headers.get("Location")).toMatch(/^https:\/\/github\.com\/login\/oauth\/authorize/);
    const remote = await worker.fetch(new Request("https://app.allorouter.com/auth/login?dev_login=alice"), DEV, {});
    expect(remote.status).toBe(503);
    expect(await (await call("/v1/config")).json()).toEqual({ github: false, dev_login: true });
  });

  it("never redirects off-site after sign-in", async () => {
    for (const next of ["//evil.example", "https://evil.example", "/\\evil"]) {
      const r = await call(`/auth/login?dev_login=alice&next=${encodeURIComponent(next)}`);
      expect(r.headers.get("Location")).toBe("/");
    }
    const ok = await call(`/auth/login?dev_login=alice&next=${encodeURIComponent("/connect?code=BCDF-GHJK")}`);
    expect(ok.headers.get("Location")).toBe("/connect?code=BCDF-GHJK");
  });

  it("signs in with GitHub OAuth", async () => {
    const gh = { ...env, GITHUB_CLIENT_ID: "cid", GITHUB_CLIENT_SECRET: "secret" };
    const start = await call("/auth/login?next=/", { e: gh });
    const state = new URL(start.headers.get("Location")).searchParams.get("state");
    const oauthCookie = start.headers.get("Set-Cookie").split(";")[0];
    expect((await call(`/auth/callback?code=c&state=wrong`, { cookie: oauthCookie, e: gh })).status).toBe(400);

    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      if (String(url).startsWith("https://github.com/login/oauth/access_token")) return Response.json({ access_token: "gho_x" });
      if (String(url) === "https://api.github.com/user") return Response.json({ id: 42, login: "octocat" });
      throw new Error("unexpected fetch " + url);
    });
    const done = await call(`/auth/callback?code=c&state=${state}`, { cookie: oauthCookie, e: gh });
    expect(done.status).toBe(302);
    expect(done.headers.get("Location")).toBe("/");
    expect(await env.DB.prepare("SELECT github_id, login FROM users").first()).toEqual({ github_id: 42, login: "octocat" });
  });
});

describe("retention", () => {
  it("deletes job rows older than 90 days", async () => {
    const { cookie } = await devSignIn("alice");
    const token = await connectComputer(cookie);
    const old = new Date(Date.now() - 91 * 86400000).toISOString().slice(0, 16) + "Z";
    const recent = new Date().toISOString().slice(0, 16) + "Z";
    await call("/v1/jobs", { method: "POST", token, body: payload([job({ id: "h:000000000000000a", at: old }), job({ at: recent })]), origin: null });
    await cleanup(env);
    expect(await count("jobs")).toBe(1);
  });
});

describe("summarize", () => {
  it("numbers jobs in time order and labels lines by computer when there are several", () => {
    const rows = [
      { install_id: 1, id: "a", at: "2026-10-12T03:00Z", pool: "pool-1", plan_type: "subscription", provider: "codex", status: "ok", skip: null, miss: "[]", est_saved_usd: 2 },
      { install_id: 2, id: "b", at: "2026-10-12T04:00Z", pool: "pool-1", plan_type: "subscription", provider: "claude", status: "ok", skip: null, miss: "[]", est_saved_usd: 1.005 },
    ];
    const s = summarize(rows, [{ id: 1, name: "Studio" }, { id: 2, name: "Laptop" }]);
    expect(s.jobs.map((j) => j.n)).toEqual([1, 2]);
    expect(s.lines.map((l) => l.computer)).toEqual(["Studio", "Laptop"]);
  });

  it("names computers computer-1, computer-2 and ignores user-typed names", async () => {
    const { cookie } = await devSignIn("namer");
    const names = [];
    for (const typed of ["Eman's MacBook", "<b>x</b>"]) {
      const dev = await (await call("/v1/device", { method: "POST", body: {}, origin: null })).json();
      const r = await call("/v1/device/approve", { method: "POST", cookie, body: { user_code: dev.user_code, name: typed } });
      expect(r.status).toBe(200);
      names.push((await r.json()).computer);
    }
    expect(names).toEqual(["computer-1", "computer-2"]);
  });
});
