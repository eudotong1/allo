import { showSpots } from "/common.js";

const login = new URLSearchParams(location.search).get("login");
if (login && /^[A-Za-z0-9-]{1,39}$/.test(login)) {
  document.getElementById("title").textContent = `You're on the waitlist, ${login}`;
}
showSpots(document.getElementById("spots"));
