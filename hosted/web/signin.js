import { api, safeNext, showSpots } from "/common.js";

const next = safeNext(new URLSearchParams(location.search).get("next") || "/");
const big = document.getElementById("spots-big");

(async () => {
  const me = await api("/v1/me").catch(() => null);
  if (me?.status === 200) { location.href = next; return; }
  const { body: cfg } = await api("/v1/config").catch(() => ({ body: null }));
  const gh = document.getElementById("github");
  gh.href = `/auth/login?next=${encodeURIComponent(next)}`;
  if (cfg?.github) gh.hidden = false;
  if (cfg?.dev_login) {
    const form = document.getElementById("dev");
    form.hidden = false;
    form.onsubmit = (e) => {
      e.preventDefault();
      location.href = `/auth/login?dev_login=${encodeURIComponent(form.login.value)}&next=${encodeURIComponent(next)}`;
    };
  }
  if (!cfg?.github && !cfg?.dev_login) document.getElementById("nosignin").hidden = false;
  await showSpots(big);
  if (!big.hidden) big.classList.add("count");
})();
