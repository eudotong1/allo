import { api, esc, post, signInUrl } from "/common.js";

const form = document.getElementById("form");
const status = document.getElementById("status");
let code = (new URLSearchParams(location.search).get("code") || "").toUpperCase();

function say(text, bad = false) {
  status.textContent = text;
  status.className = "status" + (bad ? " bad" : "");
  status.hidden = false;
}

function done(text, bad) {
  form.hidden = true;
  say(text, bad);
}

async function check() {
  if (!/^[A-Z]{4}-[A-Z]{4}$/.test(code)) {
    document.getElementById("codefield").hidden = false;
    document.getElementById("code").hidden = true;
    return;
  }
  document.getElementById("code").textContent = code;
  const r = await api(`/v1/device/${code}`);
  if (r.status === 404) return done("That code isn't known. Run allo-router connect again for a new one.", true);
  if (r.body?.status === "expired") return done("That code has expired. Run allo-router connect again for a new one.", true);
  if (r.body?.status && r.body.status !== "pending") return done("That code was already used.", true);
}

(async () => {
  const me = await api("/v1/me");
  if (me.status === 401) { location.href = signInUrl(location.pathname + location.search); return; }
  document.getElementById("who").innerHTML = `Signed in with GitHub as <b>${esc(me.body.login)}</b>`;
  await check();
})();

form.onsubmit = async (e) => {
  e.preventDefault();
  if (!document.getElementById("codefield").hidden) {
    code = form.code.value.trim().toUpperCase();
    document.getElementById("codefield").hidden = true;
    document.getElementById("code").hidden = false;
    await check();
    if (form.hidden) return;
  }
  const r = await post("/v1/device/approve", { user_code: code, name: form.name.value });
  if (r.status === 200) done(`Approved "${r.body.computer}". Your terminal finishes connecting within a few seconds; then see your dashboard.`);
  else say(r.body?.error === "name must be 1 to 40 characters" ? "Use a name of 1 to 40 characters, without < or >." : "Couldn't approve: " + (r.body?.error || r.status), true);
};

document.getElementById("deny").onclick = async () => {
  await post("/v1/device/deny", { user_code: code });
  done("Declined. That computer won't upload anything.");
};
