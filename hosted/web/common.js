// Shared by every page. No dependencies, no build step.

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) },
  });
  let body = null;
  if (r.status !== 204 && r.status !== 304) body = await r.json().catch(() => null);
  return { status: r.status, body, headers: r.headers };
}

export const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });

// "X of 100 free spots left", from the real count. Hidden if it can't be fetched.
export async function showSpots(el) {
  if (!el) return;
  try {
    const { status, body } = await api("/v1/spots");
    if (status !== 200 || typeof body?.left !== "number") throw new Error();
    el.innerHTML = body.left > 0
      ? `<b>${body.left} of ${body.limit}</b> free spots left`
      : `All ${body.limit} free spots are taken; new sign-ins join the waitlist`;
    el.hidden = false;
  } catch {
    el.hidden = true;
  }
}

export function signInUrl(next) {
  return `/signin?next=${encodeURIComponent(next || location.pathname + location.search)}`;
}

export function safeNext(next) {
  return typeof next === "string" && /^\/(?!\/)[A-Za-z0-9/_?=&.\-]*$/.test(next) ? next : "/";
}

export async function signOut() {
  await post("/auth/logout");
  location.href = "/signin";
}

// Times are UTC everywhere, like the CLI and its reports.
const pad = (n) => String(n).padStart(2, "0");
export const hhmm = (d) => `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
export const mdy = (d) => `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())}/${d.getUTCFullYear()}`;
export const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const parseMinute = (s) => new Date(s.length === 17 ? s.replace("Z", ":00Z") : s);
