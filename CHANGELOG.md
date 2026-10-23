# Changelog

## Unreleased

### Docs, install and CI
- README, allorouter.com, and the hosted sign-in, connect and dashboard pages now say what uploads actually include: a short job title (first line of the task name, at most 48 characters) plus model and effort. Before this, they said job names were never sent. `allo_upload.py` and `privacy.html` already had it right. No code change.
- Quickstart: one-line install from GitHub (`pip install git+https://github.com/eudotong1/allo`), and a note on the two things a fresh `init` shows on purpose (the empty personal-plan reading; `sync` with no `sync` block).
- README: MIT license badge.

### Hosted dashboard: Grok Bot pool + live running jobs
- **Grok Bot** appears as an orchestrator plan (provider `grok`, plan type `orchestrator`): job count only, no invented %. Fed from real Chief `run.log` dispatch events.
- **Live heartbeats** from `com.chief.codex-*` launchd lanes and `runs/current.json`. Status `running` with `heartbeat_at`; the page polls every 3s, shows a Running now strip, pulsing chips, and elapsed timers.
- New CLI: `allo-router live` / `allo-router live --watch` (see `allo_live.py`).


### Hosted dashboard: plan names and distinguishable jobs
- Providers now resolve to **ChatGPT** (codex), **Claude Personal** (personal config dir), and **Claude Team** (default Claude), so the hosted page no longer shows "Other, pool 3" / "Claude, pool 1".
- Uploads may include a short job `title` (from the task name, max 48 chars), plus `model` and `effort` parsed from the pool's run command. Timeline chips show the title (full text on hover); a Name / Model / Effort toggle colours chips accordingly.
- Re-uploads upsert rows, so a corrected provider or title can replace an earlier "other" reading for the same hashed job id.
- Local dashboard cars show the job short name (not only `#1`), and Eman's live `pools.json` labels are Claude Personal / Claude Team / ChatGPT.

All notable changes to Allo Router. Dates are MM/DD/YYYY.

## 0.5.0 (10/14/2026)

### Added
- **`allo-router connect`: an opt-in upload to the hosted dashboard at app.allorouter.com. Off by default.** Nothing is sent until you run `connect` and approve the computer once in the browser (a device code, like signing in a TV). The upload token is kept in `~/.config/allo-router/connect.json` (mode 0600), outside the router folder; the server stores only its SHA-256.
- **What an upload carries, per job:** a hashed id (HMAC with a salt that never leaves the computer), the start time rounded to the minute, the pool as `pool-1`, `pool-2`... (the ids and labels you typed never leave), the plan type, the provider (`claude` / `codex` / `gemini` / `api` / `other`), the outcome, why it missed (an enum) and the estimated saving in USD. Never task names, prompts, commands, code, output, file names or paths, readings or "% left", token counts, hostnames or keys. The payload is built field by field from an allowlist and checked again before sending; the server rejects any other field.
- **`allo-router connect --show`** prints exactly the JSON the next upload would send, and sends nothing, connected or not.
- **`allo-router upload`** sends new `runs.jsonl` lines now. Once connected, `run --execute` uploads after jobs finish, at most once every 15 seconds and once at the end; a failed upload never fails the run and the lines go with the next one. Every upload is logged in `connect.log` next to the token.
- **`allo-router disconnect`** revokes the token, deletes the computer's history on the server (`--keep-history` keeps it) and forgets the connection locally, even if the server can't be reached.
- `allo_upload.py` is the only module with network code. A test checks the others have none (the local dashboard's loopback check of its own page is the one, listed, exception).

## 0.4.0 (10/14/2026)

### Added
- **`allo-router dashboard`.** A read-only page on 127.0.0.1 built from the files the router keeps: the running job and why its plan was picked, each plan's reading and reset on one time axis with the queued jobs on it, the saved plan and whether the next scheduled run will accept it, launchd agents for the folder (via `launchctl print`; never loads or unloads) and their last logged outcome, today's jobs, and this week's counts, measured plan use and estimated saving. It opens in your browser (`--no-open` to only serve it). GET only, loopback only, `Host` checked; no login and no account, and nothing it shows leaves your computer. `--json` prints the data; `--example` serves labelled example data through the real planner. Started again while it is already running, it opens the running one instead of failing.
- **`allo-router dashboard --install-launcher`** adds an "Allo Dashboard" you can double-click for that config folder. macOS: `~/Applications/Allo Dashboard.command`, plus `Allo Dashboard.app` (built with `osacompile`) so Spotlight and the Dock find it. Linux: `~/.local/share/applications/allo-dashboard.desktop`. It runs in a terminal window; closing the window stops the dashboard.
- **`allo-router run --refresh FILE`** for scheduled runs. Re-checks a reviewed plan on fresh readings and saves it again (`--save-plan` or the same file) only if the config apart from readings and the tasks are as reviewed, the review is within `--max-review-age` minutes (default 960, counted from the review across refreshes), and every runnable job comes out with the same pool, prompt, folder and command. Saves nothing and exits 2 otherwise. Refreshed plans carry `reviewed_at` and `reviewed_plan`.
- `examples/launchd/`: a launchd job that runs refresh, then execute, in one job.
- The executor writes `runs/current.json` (task, pool, start, process id) while a job runs, and removes it when the job ends. Not for the dry-run backend.
- Site: redesigned landing page; Archivo and JetBrains Mono (SIL OFL) are served from `docs/fonts`.

## 0.3.0 (10/14/2026)

### Added
- **`allo-router sync`: readings from files already on your computer.** A pool opts in with `"sync": {"source": "claude" | "codex", "window": "week" | "5h"}`. Claude: % of the plan limit and reset time from Claude Code's own cache (`~/.claude.json`, `cachedUsageUtilization`) or the Claude Usage menu-bar app's cache (`~/.claude/.statusline-usage-cache`), whichever is fresher (`"usage_app": false` ignores the app); tokens from `~/.claude/projects/**/*.jsonl`, de-duplicated per response. Codex: % and reset from the `rate_limits` Codex logs with every turn in `~/.codex/sessions`, tokens from the same files. No network, read-only on every source.
- **Readings are FULL, PARTIAL or UNAVAILABLE, never invented.** FULL (a limit % and a reset time for a window that has not reset) is written to the pool, with `updated_at` set to when the source took it, so an old source still shows STALE. PARTIAL (tokens, no limit) and UNAVAILABLE leave `remaining_pct` alone. A reading you typed in that is newer than the source's is kept. Every sync is recorded in the pool's `last_sync`, and the plan printout shows it.
- **Auto-sync.** Pools with `"sync": {..., "auto": true}` are synced before `allo-router` prints a plan and before `allo-router run` previews one. Never before `run --plan`, so a reviewed plan still matches. `--no-sync` turns it off for one command.
- **Plan used, measured.** Under `--execute`, a job on a pool with a sync block gets a usage reading just before and just after it (`settle_s` waits for the source to refresh). `runs.jsonl` records both readings and `plan_usage`: the percentage points of each window's limit the job used, only when both readings are FULL, cover the same window and the after reading postdates the job. Otherwise it says why not. It is account-wide, so other use of the plan in between is included, and labelled so. Readings around a job never go into `pools.json`.
- **Report: "Plan used (MEASURED)"** section, per pool and per job: window, before and after %, points used, CLI-reported tokens, and why a job was not measurable. Kept apart from the API-price ESTIMATE, which is unchanged. CSV gains `plan_window`, `plan_before_pct`, `plan_after_pct`, `plan_used_pts`, `plan_measurable`, `plan_not_measurable_why` and `recurring_period`.
- **Recurring jobs.** A task with `"recurring": "daily"` or `"weekly"` runs once per UTC day or ISO week: the finished-job check keys on task id, name and that period. One-off tasks keep the id-and-name key.
- **`allo-router report`** (first released here). Reads `runs.jsonl` and prints a markdown summary: jobs logged, routed, ran, ok, failed, skipped and dry-run, run time and CLI-reported usage, per `--period day|week` or for a `--since`/`--until` range (UTC). Lists each job's pool, backend, status, duration, usage and whether its pool's reading was fresh, and lists misroutes (failed or timed out, ran on a flagged reading, or skipped because no pool fit, the reading was stale, the run block was bad or the folder was missing). `--csv FILE` writes one row per job. Read-only; stdlib only.
- **Estimated usage saved**, labelled ESTIMATE with its method in every report: each job's cost basis (CLI-reported cost, else `est_usd`) times the difference between a baseline's price factor and that of the pool it ran on. `--baseline api` (default), `max` or a pool id; `--price POOL=FACTOR` overrides the defaults (subscription 0.0, credit 1.0).

### Changed
- `readings_fingerprint` in a saved plan also covers `last_sync`, so syncing after a preview refuses that plan, the same as `set`.
- Subcommands' own `--help` (`run`, `report`, `sync`) is no longer swallowed by the top-level usage.
- **`runs.jsonl` records more.** Each line now also has `tier`, `est_usd`, `pool_kind`, `pool_flags` (the pool's reading flags when it ran) and, when the CLI printed it as JSON (`claude -p --output-format json`, `codex exec --json`) or a backend returned it, `usage`. Under `--execute`, skipped jobs are logged as status `skipped` with `reason` and `skip_kind`; they never count as finished. A preview still logs nothing.
- Backends may return `usage` in their result dict.
- New `runs.jsonl` fields: `period` and `recurring` for recurring tasks; `readings` (`before`, `after`) and `plan_usage` for jobs on pools with a sync block.

## 0.2.0 (10/14/2026)

### Added
- **Executor.** `allo-router run` turns the plan into jobs and runs them through a pool's backend: `shell` (an argv list, never a shell), `dry-run`, or your own via `register_backend`. It asks before each job unless `--yes`, writes `runs.jsonl` and per-job output to `runs/`, and never edits `pools.json` or `tasks.json`.
- **Run exactly the reviewed plan.** `allo-router run` previews and saves the plan to `plan.json` (`--save-plan FILE` to choose), with its creation time, a readings fingerprint, a config hash, every job's pool, prompt, folder and command, and a hash over all of it. `allo-router run --plan FILE --execute` runs only that plan's runnable jobs. It refuses (exit 2, nothing started) if the file was edited, is older than `--max-plan-age` minutes (default 60), was saved for another folder, the readings or config changed, or a reviewed job would now come out differently. `run --plan FILE` without `--execute` re-checks a plan and starts nothing.
- `runs.jsonl` entries record the plan hash; a plan runs each job at most once.
- The `dry-run` backend accepts the `shell` keys and shows the command it would run.
- `allo-router --version`.
- Documented and tested: a job counts as finished by task id and name, so editing a finished job's prompt does not run it again (`--rerun` does).

### Safety defaults
- **`--execute` only runs a saved plan.** `allo-router run --execute` without `--plan` is refused; `--only`, `--allow-unverified` and `--rerun` apply when previewing and cannot be combined with `--plan`.
- **The starter config puts every pool on the `dry-run` backend**, so a first `--execute` on a new install cannot start a real agent. Each starter pool still shows the command it would run; change its `"backend"` to `"shell"` to opt in.

### Changed
- **API jobs only run on API credit.** A job that `needs` the `api` stays UNASSIGNED when the credit pool is missing, unread, out of budget or not allowed the job's tier, instead of falling back to a subscription.
- **The planner rejects bad input instead of guessing.** Timestamps must carry a zone (`Z` or an offset); a negative `est_usd` or a tier not listed in `tiers` (on a task or a pool) stops the plan with a message. `allo-router set` refuses unknown keys, non-numeric `pct=`/`usd=` and bad `reset=` before touching `pools.json`. `init` checks both files before writing either. A missing or unreadable `pools.json`/`tasks.json` gives a one-line error pointing to `init` or `--dir`, and `--help` prints usage.

### If you used the pre-release executor on the 0.1 line
- `run --execute` now needs a preview first: `allo-router run`, then `allo-router run --plan plan.json --execute`.
- Your existing `pools.json` is not touched; pools you already set to `shell` keep using it.

## 0.1.0 (10/10/2026)
- First release: the read-only planner. Ranks plans by what is left before reset, assigns jobs by data tier, credit reserve and headroom, flags stale or missing readings, and `allo-router set` / `init`.
