# Hosted dashboard: run it locally

Updated: 10/15/2026. Status: built and tested locally on branch `hosted-dashboard`. Nothing deployed, no Cloudflare account, GitHub OAuth app or DNS change made. Plan: [PLAN.md](PLAN.md). Mockup: [mockup.html](mockup.html).

## What is where
| Path | What |
|---|---|
| `allo_upload.py` | CLI side: `connect`, `connect --show`, `upload`, `disconnect`, and the after-job upload in `run --execute`. Off by default. Tests: `tests/test_upload.py` |
| `hosted/worker/` | Cloudflare Worker + D1: API, device approval, GitHub OAuth, free spots and waitlist, retention cron. Tests: `hosted/worker/test/` (vitest in the Workers runtime, local D1) |
| `hosted/worker/src/schema.js` | The upload allowlist. Any other key or value is rejected with 400 and nothing is stored |
| `hosted/web/` | Static pages served by the same Worker: dashboard (`/`), sign-in, approve a computer (`/connect`), waitlist, privacy |
| `docs/index.html` | Public landing page: the "X of 100 free spots left" counter fetches `app.allorouter.com/v1/spots` (on localhost, `127.0.0.1:8787`) and stays hidden if that fails |

## Run it
Needs Node 20+ and Python 3.9+. No Cloudflare login: `wrangler dev` runs the Worker and D1 on your computer.

```sh
cd hosted/worker
npm install
npm run dev            # applies migrations to a local D1 under .wrangler/, serves http://127.0.0.1:8787
```

With no `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` set, the sign-in page offers a **local development sign-in** (type any GitHub-style login). It only works when the request is to `localhost` or `127.0.0.1`, `DEV_LOGIN=true` was passed (`npm run dev` does), and GitHub is not configured. To try real GitHub sign-in locally, create an OAuth app with callback `http://127.0.0.1:8787/auth/callback` and put both values in `hosted/worker/.dev.vars` (git-ignored).

Connect a made-up computer from another terminal. `ALLO_ROUTER_CONFIG_DIR` keeps the token out of your real `~/.config`:

```sh
python3 hosted/worker/scripts/demo_folder.py /tmp/allo-demo        # made-up jobs from this week
export ALLO_ROUTER_CONFIG_DIR=/tmp/allo-demo-cfg ALLO_ROUTER_SERVER=http://127.0.0.1:8787
python3 allo.py --dir /tmp/allo-demo connect --show                 # what would be sent; sends nothing
python3 allo.py --dir /tmp/allo-demo connect                        # opens the approval page; sign in, approve
python3 allo.py --dir /tmp/allo-demo upload
python3 allo.py --dir /tmp/allo-demo disconnect                     # revokes and deletes this computer's rows
```

Open http://127.0.0.1:8787. The page polls `/v1/me/dashboard` every 15 seconds while the tab is visible, sending `If-None-Match`; an unchanged dashboard is a 304 with no body. `run --execute` uploads after jobs, at most once every 15 seconds and once at the end, so a routed job shows up within about 30 seconds.

To see the landing page counter, serve `docs/` on localhost (`python3 -m http.server 8000 -d docs`) while `npm run dev` is running.

## Tests
```sh
python3 -m pytest tests          # or: uvx --python 3.12 pytest -q tests
cd hosted/worker && npm test     # Worker + D1, no network
```

End to end, with screenshots (needs `npm run dev` running, and Chrome or Playwright's Chromium):
```sh
ALLO_SITE=http://127.0.0.1:8000/ uv run --no-project --with playwright python hosted/worker/scripts/local_e2e.py OUT_DIR
```
It connects two made-up computers through the real device flow, uploads, checks `connect --show` leaks no names or paths, routes one more job with `run --execute`, and waits for the open dashboard to pick it up without a reload.

## Free spots and the waitlist
`GET /v1/spots` (public, CORS `*`, cached 30 s) returns `{limit, taken, left}`, where `taken` is `COUNT(*)` of real user rows. `FREE_SPOTS` in `wrangler.toml` sets the limit (100). A new sign-in takes a spot in one conditional `INSERT`, so two sign-ins can't both take the last one. After that, a new GitHub identity is stored in `waitlist` (id, login, time) with no account and sent to `/waitlist`. Existing users always get back in. Deleting an account frees its spot.

## Going live (not done; needs your accounts)
1. **Cloudflare account** that will own this (decide company vs personal before step 3: moving later invalidates every token). `wrangler login`, then `wrangler d1 create allo` and put the returned id in `wrangler.toml` in place of the zeros, then `wrangler d1 migrations apply allo --remote`.
2. **GitHub OAuth app** (github.com/settings/developers, or under an org): homepage `https://app.allorouter.com`, callback `https://app.allorouter.com/auth/callback`. Then `wrangler secret put GITHUB_CLIENT_ID` and `wrangler secret put GITHUB_CLIENT_SECRET`. Never put them in `wrangler.toml`.
3. `wrangler deploy` (one Worker serves both the API and `hosted/web`; no separate Pages project is needed).
4. **DNS for app.allorouter.com.** A Worker custom domain needs the `allorouter.com` zone on Cloudflare [VERIFY where DNS is today]. Moving the zone is free; keep the apex and `www` records pointing at GitHub Pages so allorouter.com keeps working, then add `app.allorouter.com` as a Custom Domain on the Worker (Cloudflare creates the record and certificate). If the zone stays elsewhere, use a route on a Cloudflare-proxied CNAME instead.
5. Merge to `main` only when the server is live: the landing page counter hides itself until `/v1/spots` answers, but the copy announces the hosted dashboard.

**Cost [VERIFY current prices before relying on these].** At 100 free users (about 20 jobs and 2 uploads each a day, polling only while a tab is open) this sits inside the Workers Free plan (100k requests a day) and D1's free limits (5M rows read a day, 100k rows written a day, 5 GB): **$0 a month**. An open dashboard tab polls 4 times a minute, about 5,800 requests a day if left open all day, and mostly 304s that read one row. The Workers Paid plan ($5 a month) removes the daily caps if tabs left open push past 100k requests. GitHub OAuth is free. The domain is already held.
