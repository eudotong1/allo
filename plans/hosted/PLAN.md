# Allo Router hosted dashboard: plan

Updated: 10/15/2026. Status: Phases 2 to 4 built and tested locally (see [README.md](README.md) to run it). Nothing deployed, no accounts created. Where this plan and the code differ, the code wins: the module is `allo_upload.py` (not `allo_connect.py`), job fields are `id, at, pool, plan_type, provider, status, skip, miss, est_saved_usd` with `schema` and `sent_at` on top (`hosted/worker/src/schema.js`), job ids are always hashed and pools are always `pool-N`, and the web app is served by the same Worker as static assets rather than a separate Pages project. Added since: free for the first 100 users then a waitlist, and live updates (15 s polling with ETag/304).
Mockup: [mockup.html](mockup.html), [mockup.png](mockup.png). Prices are from memory, not checked online: [VERIFY] before spending.

## What it is
An opt-in page at `app.allorouter.com` showing what the router did across your computers: which plan took each job, what ran, what missed, and the estimated usage saved. You run `allo-router connect` to opt in. The local `allo-router dashboard` stays as is, and it stays the only place that shows readings, job names and output.

## Stack
- **Cloudflare Workers** for the API (`/v1/*`), **D1** for storage, **Pages** for the static web app. One Worker with static assets would also work and saves a project. Either way it costs nothing at this size.
- **Sign-in: GitHub OAuth first.** It's free, there's no email deliverability to run, and the users are developers. Add magic-link email (Cloudflare Email Service or Resend) only if non-GitHub users ask for it.
- **CLI side:** a new stdlib-only module, `allo_upload.py` (`urllib.request`, no dependencies). It is the only module allowed to open a connection.

## Architecture
```
runs.jsonl ─> allo_upload.build_payload (pure, allowlist) ─> POST /v1/jobs  (Bearer upload token)
                                                                │ Worker: strict schema check, rate limit
browser ─> GitHub OAuth ─> session cookie ─> GET /v1/me/*  ─────┴─> D1
```
D1 tables: `users`, `sessions` (hashed id), `installs` (one per connected folder: name, `token_hash`, `last_seen_at`, `revoked_at`), `jobs` (keyed on `install_id, job, at`, so a re-upload is a no-op), `daily` (rollups, kept after job rows expire). A Cron Trigger deletes job rows older than 90 days.

## Privacy model
The payload is built from an allowlist: named fields copied into a new object, never the run record minus some fields. The Worker rejects any unknown key (400), so a client bug can't widen what gets stored.

```jsonc
POST /v1/jobs
{
  "schema": 1,
  "client": "allo-router/0.5.0",
  "jobs": [{
    "job":    "14",            // task id if it matches ^[0-9]{1,9}$; otherwise "h:" + first 12 hex of
                               //   HMAC-SHA256(local salt, task id). The salt never leaves the computer.
    "at":     "2026-10-14T03:00Z",  // start, rounded to the minute (needed to draw the week)
    "plan":   "claude_work",   // pool id if it matches ^[a-z0-9_-]{1,32}$, else "pool-1", "pool-2"...
    "kind":   "subscription",  // subscription | credit_usd | other
    "status": "ok",            // ok | failed | timeout | error | skipped | dry-run
    "skip":   null,            // skip_kind enum from allo_exec, or null
    "est_saved_usd": 4.12,     // allo_report estimate vs the api baseline, 2 dp, null if unknown
    "miss":   []               // zero or more of: failed, timeout, error, unverified-reading,
                               //   unassigned, needs-reading, bad-run-block, missing-cwd, other
  }]
}
```
**Never sent:** task names, prompts, commands, code, stdout/stderr, file or folder paths, `reason` text, readings, token counts, reported cost, plan hashes, hostname, username, API keys. `at` and `kind` go beyond the five fields in the brief. Without `at` there's no timeline, and without `kind` the saved figure can't be explained. Cut either if you'd rather not send it.
The dashboard can't show "% left" because readings aren't sent. That's deliberate. Sending readings could be a separate opt-in later.

Proof, as tests: a canary task (`name: "CANARY-SECRET"`, cwd, a reason, output) where the serialized payload must not contain the canary; the payload key set equals the schema exactly; `conftest.py` keeps blocking sockets, and only `allo_upload`'s tests stub the transport. `allo-router connect --show` prints the exact next payload and sends nothing. Every upload is appended to a local `connect.log` (what was sent, when).

## Connect, disconnect and tokens
1. `allo-router connect` asks for a name for this computer (default "Computer 1", never the hostname), then starts a device flow: `POST /v1/device` returns a user code. The CLI opens `app.allorouter.com/connect?code=WXYZ-1234`.
2. You sign in with GitHub, check the code and the computer name, and approve. The CLI polls and receives an **upload token** (`allo_up_` plus 32 random bytes).
3. The token is stored in `~/.config/allo-router/connect.json`, mode 0600, outside the router folder, so it can't be committed with your config. The server stores only its SHA-256.
4. Scope: append job rows for that one computer. It can't read anything. A stolen token can add junk rows, nothing more. Tokens unused for 90 days expire.
5. Uploads happen at the end of `run --execute`, for new lines since a local cursor. Max 500 rows per request, 60 requests per hour per token. A failed upload never fails or delays the run; it retries next time. `allo-router connect --push` uploads now.
6. `allo-router disconnect` revokes the token, deletes that computer's rows on the server (`--keep-history` to keep them), then deletes the local file. The web page has the same Disconnect, plus "Delete my account".

Name: keep "upload" for this. `allo-router sync` already means reading local usage files, and the site promises sync opens no connection.

## Phases
| Phase | What | Time |
|---|---|---|
| 1 | This plan and the mockup | done |
| 2 | `allo_connect.py`: payload builder, `connect --show`, canary tests. Ships in 0.5 and sends nothing yet | 1-2 days |
| 3 | Worker + D1 + GitHub OAuth + device flow on `*.workers.dev`; dogfood with the `allo-dogfood` folder and 5 testers | ~1 week |
| 4 | Web dashboard from the mockup, disconnect/delete, privacy page, `app.allorouter.com` | ~1 week |
| 5 | Open beta, 90-day retention cron, magic link if asked | ongoing |

## Monthly cost (USD) [VERIFY]
Assumptions: ~20 jobs and 2 uploads per user per day, ~5 page views, ~250 bytes per stored row, 90-day retention.

| | Requests/day | D1 rows written/day | Storage | Cost/month |
|---|---|---|---|---|
| Free tier (to ~1k users) | ~7k | ~20k | <1 GB | **$0** (Workers 100k req/day, D1 100k writes/day, 5 GB free) |
| 1k users | ~7k | ~20k | ~0.5 GB | **$0**, or $5 Workers Paid for headroom and no daily caps |
| 10k users | ~70k | ~200k (over the free daily cap) | ~4.5 GB | **~$5** Workers Paid (10M req and 50M row writes included). +$20 if magic-link email is on |

The domain is already held. GitHub OAuth is free.

## Risks
1. **It breaks a public promise.** The site and roadmap say there's no outbound network code and no step "quietly adds network access". Fix: off by default, one module, a test that the other five modules import no network code, and update the landing copy, README and architecture in the same release.
2. **Leaks through names you chose.** Task ids and pool ids are free text (`acme-redlines`). The regex gate plus HMAC above covers it; `--show` makes it checkable.
3. **DNS.** allorouter.com is on GitHub Pages. A Worker custom domain needs the zone on Cloudflare (free; GitHub Pages keeps working by CNAME). Check where DNS is today [VERIFY].
4. **Estimates read as facts once they're in the cloud.** Label "estimate" next to every saved figure and link the method, as the report does.
5. **D1 size limit** (10 GB per database [VERIFY]). Retention and rollups keep 10k users at ~4.5 GB. Shard by user past that.
6. **Ownership.** Decide which Cloudflare account and GitHub org own the OAuth app before Phase 3. Moving them later breaks every token.
7. **Publishing.** `docs/` is the public site, so this plan and the mockup now live in `plans/hosted/`. Merging this branch publishes the landing page's hosted-dashboard section and counter; merge once the server is live.
