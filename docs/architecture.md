# Architecture

Allo Router is six stdlib-only Python modules (plus `allo_dash_page.py`, the dashboard's HTML) and two JSON files you own. One of them, `allo_upload.py`, is the opt-in upload to the hosted dashboard; it is off by default and is the only module that opens a connection.

    ~/.claude, ~/.codex ──> allo_sync.py: sync ──> readings in pools.json                       (allo-router sync)
    pools.json ─┐                         ┌─> printed plan                                   (allo-router)
                ├─> allo.py: build_plan ──┤
    tasks.json ─┘                         └─> allo_exec.py: select ──> run_jobs (describe only)  (allo-router run)
                                                                │
                                                          save_plan ──> plan.json
                                                                            │
                     load_plan ──> jobs_from_plan ──> run_jobs ──> backend.run   (allo-router run --plan plan.json --execute)
                                                                       │   allo_sync: reading before / after each job
                                                         runs.jsonl + runs/*.log
                                                                       │
                                        allo_report.py: cmd_report ──> markdown on stdout, --csv FILE   (allo-router report)

    pools.json, tasks.json, plan.json, runs.jsonl, runs/current.json, ~/Library/LaunchAgents
        ──> allo_dash.py: build_state ──> JSON ──> page on 127.0.0.1           (allo-router dashboard)

    runs.jsonl ──> allo_upload.py: build_payload (allowlist) ──> POST app.allorouter.com/v1/jobs   (only after allo-router connect)

## allo.py: the planner
Pure functions over the two files. It never imports `subprocess`, `socket` or anything that reaches out; a test checks this.

- `load(here)` reads `pools.json` and `tasks.json`.
- `pool_rows(pools, now)` adds what is left, hours to reset, urgency and flags (`NO READING`, `NO RESET TIME`, `STALE`, `RESET PASSED, re-read`) to each pool, most urgent first.
- `propose(tasks, rows, tiers, options)` assigns tasks in priority order and returns `[(task, pool_id or None, note, eligible_pool_ids)]` plus the credit left above the reserve. `note` is display text such as `90% left` or `headroom UNKNOWN, confirm first`.
- `build_plan(cfg, tasks, now)` wraps the two. The printout (`render`) and the executor both call it, so what you read is what runs.
- `cmd_set` and `cmd_init` are the only functions that write, and only to `pools.json` / `tasks.json`.

`main` loads `allo_exec` only for the `run` subcommand and `allo_report` only for `report`, so printing a plan never loads the code that starts processes. Before printing a plan, and before `run` previews one (never with `--plan`), it calls `allo_sync.auto_sync` for pools with `"sync": {..., "auto": true}`; `--no-sync` skips that. `recurring_period(task, now)` gives a recurring task's period: the UTC date for `daily`, the ISO week (`2026-W42`) for `weekly`, `None` otherwise.

## allo_sync.py: readings from local files
Reads only; opens no connection. Each source returns candidate readings `{source, as_of, windows: {"5h"|"week": {used_pct, resets_at}}, resolution_pts}`:

- `claude_code_cache`: `cachedUsageUtilization` in `~/.claude.json` (`fetchedAtMs`, `five_hour`, `seven_day`).
- `usage_app_cache`: the Claude Usage app's `~/.claude/.statusline-usage-cache` (`TIMESTAMP`, `UTILIZATION`, `RESETS_AT`, `WEEKLY_*`). Off with `"usage_app": false`.
- `codex_limits`: the newest `token_count` event with `rate_limits` for `limit_id` (default `codex`) in the newest session files; `window_minutes` 300 is `5h`, 10080 is `week`; `resets_at` (epoch) or the older `resets_in_seconds`.
- `claude_tokens` / `codex_tokens`: token sums since the window started (reset minus window length, or now minus it when there is no reset). Claude lines are de-duplicated by (message id, request id); Codex tokens are increases in each session file's running total. Files last written before the window are skipped.

`_finish` takes the freshest candidate that has the pool's `window`. FULL if that window's `resets_at` is still ahead; otherwise the reading is from a window that reset and is not used. No limit but tokens: PARTIAL. Nothing: UNAVAILABLE. `apply` writes FULL readings into the pool (`remaining_pct`, `resets_at`, `updated_at` = `as_of`, `source` = `sync: ...`) unless the pool's `updated_at` is as new or newer, and always records `last_sync`.

`job_reading`, `reading_after` and `measure` are what the executor uses around each job; see below.

## allo_exec.py: the executor
`cmd_run` parses the `run` flags. Without `--plan` it builds the plan with the current time, turns it into jobs, prints them and saves the plan file. With `--plan` it loads a saved plan, checks it against the files as they are now, and (under `--execute`) runs it. `--execute` without `--plan` is refused (exit 2).

### From plan to jobs (`select`)
Each assignment becomes a job dict: `task`, `pool`, `note`, `prompt` (task `prompt`, else its `name`), `cwd` (task `cwd`, else the run block's, relative to the config folder; with neither, the config folder itself), `here` (the config folder), `backend` and `skip`. The first rule that matches sets `skip`:

1. No pool fits: `unassigned`.
2. The pool has any flag and `--allow-unverified` is off: needs a fresh reading.
3. `runs.jsonl` has an `ok` entry for the same task id and name and `--rerun` is off: already ran. The prompt is deliberately not part of this key, so editing a finished job's prompt does not run it again; a test locks this in.
4. The pool has no `run` block: start it yourself.
5. The run block is invalid: the backend's own error.

`--only` drops tasks before any of this.

### Saved plans (`save_plan`, `load_plan`, `jobs_from_plan`)
A preview writes `plan.json` (or `--save-plan FILE`):

    {"format": "allo-router-plan/1", "allo_router": "<version>", "created_at": "2026-10-14T09:00:00Z",
     "config_dir": "/abs/path", "options": {"only": null, "allow_unverified": false, "rerun": false},
     "config_hash": "sha256:...", "readings_fingerprint": "sha256:...",
     "jobs": [{"task_id": "3", "task_name": "...", "pool": "chatgpt", "prompt": "...", "cwd": "/abs/path",
               "command": "<backend.describe(job)>", "skip": null}, ...],
     "hash": "sha256:..."}

`readings_fingerprint` hashes each pool's `remaining_pct`, `remaining_usd`, `resets_at`, `updated_at`, `source` and `last_sync`, the fields `allo-router set` and `allo-router sync` write. `config_hash` hashes everything else in `pools.json` plus `tasks.json`. `hash` covers every other field, as canonical JSON (sorted keys, no spaces).

`load_plan` refuses an unreadable file, another format, or a hash mismatch. `jobs_from_plan` then refuses, in order: a plan more than 5 minutes in the future or older than `--max-plan-age` (default 60 minutes); another config folder; a readings change; a config change. Finally it re-runs `select` with the plan's saved options and the current time and walks the saved jobs:

1. Saved as skipped: stays skipped with the saved reason, whatever `select` says now. Only reviewed jobs run.
2. Ran ok under this plan's hash (per `runs.jsonl`): skipped, so one plan runs each job at most once, even one saved with `--rerun`.
3. Ran ok since, under another plan, and the plan was not saved with `--rerun`: skipped.
4. `select` would now skip it (for example the reading went STALE): the whole plan is refused.
5. Any field of `job_record` differs (pool, prompt, folder, command): the whole plan is refused.

Every refusal happens before the first job starts.

### Running (`run_jobs`)
Every job is printed with its target and `backend.describe(job)`, the exact command it would run. In a dry run that is all. Under `--execute`, for each job without `skip`:

1. Skip it if its `cwd` does not exist.
2. Ask (`confirm`) unless `--yes`. With no terminal and no `--yes`, `cmd_run` refuses before loading anything (exit 2).
3. Call `backend.run(job)`. An exception from a plugged-in backend becomes status `error`; later jobs still run.
4. For a pool with a `sync` block (and not the dry-run backend), `allo_sync.job_reading` is taken just before step 3 and `allo_sync.reading_after` just after it, polling up to `settle_s` for a reading whose `as_of` is after the job ended. `measure(before, after, start, end)` gives per-window `used_pts` (after minus before) and sets `measurable` only for the pool's window when both readings are FULL, the reset times match (within 2 minutes) and the after reading postdates the job; otherwise `why`. These readings are never written to `pools.json`.
5. `record` appends one JSON line to `runs.jsonl` and, unless the status is `dry-run`, writes the command, status, stdout and stderr to `runs/<time>-task<id>.log`.

A job skipped under `--execute`, whether by `select`, the saved plan, a missing folder or your "no", is recorded too, as status `skipped` with no log file. A preview records nothing.

Jobs run one at a time in plan order. Exit code: 0 when nothing failed, 1 when any job ended `failed`, `timeout` or `error`, 2 when it refused to start (no plan, a plan that no longer matches, or no terminal without `--yes`).

### runs.jsonl
One line per job reached or skipped under `--execute`:

    {"at": "2026-10-14T09:00:00Z", "task_id": 3, "task_name": "Refactor the pricing page", "pool": "chatgpt",
     "backend": "ShellBackend", "status": "ok", "returncode": 0, "seconds": 41.2, "output": "runs/20261014T090000Z-task3.log",
     "plan": "sha256:...", "tier": "public", "est_usd": 0, "pool_kind": "subscription", "pool_flags": [],
     "usage": {"cost_usd": 0.0123, "input_tokens": 1200, "output_tokens": 300}}
    {"at": "...", "task_id": 4, ..., "backend": null, "status": "skipped", "returncode": null, "seconds": null,
     "output": null, "reason": "unassigned: no pool fits this job", "skip_kind": "unassigned", ...}

- `pool_flags` are the pool's reading flags when the job was planned (`STALE`, `NO READING`, ...); non-empty only for a job run under `--allow-unverified`.
- `usage` is present only when known: a backend may return it in its result, otherwise `allo_report.usage_from_output` takes the last JSON object in stdout carrying `total_cost_usd` or a `usage` block. Nothing is estimated at this stage.
- `period` and `recurring`: present for a recurring task. The rerun guard keys on (task id, task name, period); one-off tasks have no period.
- `readings` (`before`, `after`) and `plan_usage` (`window`, `measurable`, `used_pts` or `why`, `windows`, `note`): present for jobs on a pool with a `sync` block.
- `skip_kind` is one of `unassigned`, `needs-reading`, `already-ran`, `manual` (no run block), `bad-run-block`, `missing-cwd`, `declined` or `other` (`allo_exec.skip_kind`).

Only `ok` lines count as done for the rerun guard. A `dry-run` backend line records that the job was reached, not that it ran. Lines written by 0.2.0 have only the first ten fields; the report shows the rest as not recorded. Logs and plan files hold your prompts and model output; `runs.jsonl`, `runs/` and `plan.json` are gitignored.

### Refreshing a reviewed plan (`refresh_jobs`, `refresh`)
`run --refresh FILE` is for a scheduled run some hours after a review. It loads the plan (hash checked), refuses if the review is older than `--max-review-age` (from `reviewed_at`, which a refreshed plan carries over from the plan a person reviewed, so refreshing a refresh does not extend it), if the plan was saved for another folder, or if `config_hash` differs, meaning anything in `pools.json` but the readings, or `tasks.json`, changed. It then calls `select` with the plan's own options on the readings as they are now and compares each job with the saved one: a job saved as skipped stays skipped; a runnable job that has since finished ok becomes skipped; a runnable job that would now be skipped for any other reason, or whose `job_record` differs, refuses the refresh. On success it saves a new plan with `reviewed_at` and `reviewed_plan` (the hash of the reviewed plan), which `--execute` then checks like any other. `allo.main` auto-syncs before `--refresh`, as before a preview.

### The running marker
While a non-dry-run job runs, `run_jobs` writes `runs/current.json` (task id and name, pool, start, process id, plan hash) and removes it when the job ends, whatever the outcome. The dashboard treats a marker whose process is gone as a run that did not finish cleanly.

## allo_dash.py: the dashboard
`build_state(here, now)` returns one dict, built only by reading: `pool_rows` for the readings and flags, `select` for where each job would go now (the same code a preview uses, without saving), `load_plan` and `jobs_from_plan` for whether the saved plan still matches, `load_entries`, `Prices` and `build` from the report for today's jobs and the week, `runs/current.json` for the running job, and `find_agents` for launchd agents whose arguments run `allo-router` on this folder (`plistlib`; a `/bin/sh -c` wrapper is read as one string). For each agent it computes the next `StartCalendarInterval` time, asks `launchctl print` whether it is loaded, and reads the last outcome line of its log. `health` turns that into the headline and a list of issues, for example a saved plan that will be too old by the next run. `example_state` writes a made-up folder to a temporary directory and runs the same `build_state` on it, then marks it `example`.

`serve` binds `ThreadingHTTPServer` to `127.0.0.1` only. `respond(method, path, host, port, ...)` is a pure function, so tests cover it without a socket: it refuses any `Host` other than `127.0.0.1:PORT` or `localhost:PORT` (403, against DNS rebinding), anything but GET or HEAD (405), and serves `/`, `/api/state` and `/fonts/NAME.woff2` from `docs/fonts` when that folder exists (a checkout or editable install). The page is in `allo_dash_page.py` so it ships with a plain install; it falls back to system fonts without `docs/fonts`.

## allo_upload.py: the opt-in upload
Off until `allo-router connect` has approved this computer (a device code shown in the terminal and approved in the browser). The upload token, a random salt and the pool numbering are kept in `~/.config/allo-router/connect.json` (mode 0600), outside the router folder; the server stores only the token's SHA-256.

`build_payload` copies named fields into a new object, one job at a time: `id` (`h:` plus 16 hex of an HMAC of the task id with the local salt), `at` (rounded to the minute), `pool` (`pool-N`, never the id you typed), `plan_type`, `provider`, `status`, `skip` and `miss` (enums) and `est_saved_usd` (the report's estimate). `check_payload` checks the result against the same allowlist before anything is sent, and the server (`hosted/worker/src/schema.js`) rejects any payload with another key or value. A test keeps the two lists equal. `connect --show` prints the next payload and sends nothing.

`AfterJobs` is what `run --execute` calls after each line it writes to `runs.jsonl`: it uploads new lines at most once every 15 seconds and once at the end, and never raises. A local cursor tracks what was sent; a failed upload leaves the lines for the next one. `disconnect` revokes the token on the server (deleting that computer's rows unless `--keep-history`) and forgets it locally even if the server is unreachable.

## allo_report.py: the report
`cmd_report` reads `runs.jsonl` (unreadable lines are counted and skipped), filters by `--since`/`--until`, groups by `--period`, and prints markdown: a summary per period, the MEASURED plan use from `plan_usage` (per pool and per job, with CLI-reported tokens), the usage-saved ESTIMATE with its method, misroutes, and one row per job. The two never mix: the estimate does not read `plan_usage`. `--csv FILE` writes the same rows with every derived column. It reads `pools.json` for pool kinds and `tasks.json` for `est_usd` of old entries when they exist, and needs neither. It imports neither `allo_exec` nor anything that starts processes; a test checks this.

- `misroutes(entry)`: failed, timeout or error; ran with `pool_flags`; or skipped with a `skip_kind` other than `already-ran`, `manual` or `declined`.
- `Prices`: a factor per pool (override, else 0.0 for `subscription`, 1.0 otherwise) and the baseline factor (`api` 1.0, `max`, or a pool's).
- `cost_basis(entry)`: reported `cost_usd`, else the entry's `est_usd`, else the task's current `est_usd`, else none.

## Backends
A backend is a class registered under a name and chosen by a pool's `run.backend`.

    class MyBackend:
        def __init__(self, run_block): ...      # validate; raise allo_exec.BackendError to reject
        def describe(self, job): ...            # one line for the dry run; must not start anything
        def run(self, job): ...                 # -> {"status", "returncode", "stdout", "stderr"}

    allo_exec.register_backend("mine", MyBackend)

`status` is one of `ok`, `failed`, `timeout`, `error` or `dry-run`. `examples/custom_backend.py` is a working example that writes each job to an outbox folder.

Built in:

| backend   | does |
|-----------|------|
| `shell`   | Resolves `command[0]` on PATH (shown as `NOT FOUND` in a dry run if missing), fills placeholders in one pass so text inside a prompt is never substituted again, refuses a filled argument that starts with `-` unless a `--` item comes before it, then runs the argv list with `subprocess.run` (no shell), your environment plus `env`, and `timeout_s` (default 1800). Exit 0 is `ok`, anything else `failed`. |
| `dry-run` | Starts nothing, even under `--execute`. Accepts the `shell` keys and, given a `command`, validates it and shows what `shell` would run. Every starter pool uses it. |

## What is deliberately missing
- The executor never edits `pools.json` or `tasks.json`. Usage readings come only from files the provider's own software writes; where none gives a limit, the reading is PARTIAL or UNAVAILABLE, never estimated.
- No outbound network code outside `allo_upload.py`, which does nothing until you run `allo-router connect`, and then sends only the allowlisted summary numbers above. A test checks the other modules import no network code. The local dashboard only listens, on 127.0.0.1. Any other network use happens inside the CLI you configure, under that CLI's own login.
- No secrets in config. The CLI you call should already be authenticated.

## Tests
`tests/test_allo.py` covers the planner and the CLI. `tests/test_exec.py` covers the executor with local `python -c` commands standing in for real CLIs. `tests/test_report.py` covers the report and what the executor logs for it, again with `python -c` stand-ins that print `claude`- and `codex`-style usage JSON. `tests/test_sync.py` covers sync, auto-sync, readings around jobs, the measured report section and recurring jobs, with fake `~/.claude` and `~/.codex` folders (HOME points at an empty temp folder, so the real ones are never read). `tests/test_refresh.py` covers `run --refresh` and the running marker. `tests/test_dash.py` covers the dashboard's state, launchd discovery and schedule maths, that it writes nothing and never syncs, the example data, and the request handler. `tests/test_upload.py` covers the upload: the payload holds only allowlisted keys, a canary task name, path and output never appear in it, no reading or "% left" field, the allowlist matches the server's, the device flow, the 15-second debounce, and that nothing is sent before `connect`; its transport is a stub. `tests/conftest.py` blocks socket connections and DNS for every test.
