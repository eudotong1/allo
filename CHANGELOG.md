# Changelog

All notable changes to Allo Router. Dates are MM/DD/YYYY.

## Unreleased

### Added
- **`allo-router report`.** Reads `runs.jsonl` and prints a markdown summary: jobs logged, routed, ran, ok, failed, skipped and dry-run, run time and CLI-reported usage, per `--period day|week` or for a `--since`/`--until` range (UTC). Lists each job's pool, backend, status, duration, usage and whether its pool's reading was fresh, and lists misroutes (failed or timed out, ran on a flagged reading, or skipped because no pool fit, the reading was stale, the run block was bad or the folder was missing). `--csv FILE` writes one row per job. Read-only; stdlib only.
- **Estimated usage saved**, labelled ESTIMATE with its method in every report: each job's cost basis (CLI-reported cost, else `est_usd`) times the difference between a baseline's price factor and that of the pool it ran on. `--baseline api` (default), `max` or a pool id; `--price POOL=FACTOR` overrides the defaults (subscription 0.0, credit 1.0).

### Changed
- **`runs.jsonl` records more.** Each line now also has `tier`, `est_usd`, `pool_kind`, `pool_flags` (the pool's reading flags when it ran) and, when the CLI printed it as JSON (`claude -p --output-format json`, `codex exec --json`) or a backend returned it, `usage`. Under `--execute`, skipped jobs are logged as status `skipped` with `reason` and `skip_kind`; they never count as finished. A preview still logs nothing.
- Backends may return `usage` in their result dict.

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
