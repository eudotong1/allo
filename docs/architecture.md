# Architecture

Allo Router is two stdlib-only Python modules and two JSON files you own.

    pools.json ─┐                         ┌─> printed plan                                   (allo-router)
                ├─> allo.py: build_plan ──┤
    tasks.json ─┘                         └─> allo_exec.py: select ──> run_jobs (describe only)  (allo-router run)
                                                                │
                                                          save_plan ──> plan.json
                                                                            │
                     load_plan ──> jobs_from_plan ──> run_jobs ──> backend.run   (allo-router run --plan plan.json --execute)
                                                                       │
                                                         runs.jsonl + runs/*.log

## allo.py: the planner
Pure functions over the two files. It never imports `subprocess`, `socket` or anything that reaches out; a test checks this.

- `load(here)` reads `pools.json` and `tasks.json`.
- `pool_rows(pools, now)` adds what is left, hours to reset, urgency and flags (`NO READING`, `NO RESET TIME`, `STALE`, `RESET PASSED, re-read`) to each pool, most urgent first.
- `propose(tasks, rows, tiers, options)` assigns tasks in priority order and returns `[(task, pool_id or None, note, eligible_pool_ids)]` plus the credit left above the reserve. `note` is display text such as `90% left` or `headroom UNKNOWN, confirm first`.
- `build_plan(cfg, tasks, now)` wraps the two. The printout (`render`) and the executor both call it, so what you read is what runs.
- `cmd_set` and `cmd_init` are the only functions that write, and only to `pools.json` / `tasks.json`.

`main` loads `allo_exec` only for the `run` subcommand, so printing a plan never loads the code that starts processes.

## allo_exec.py: the executor
`cmd_run` parses the `run` flags. Without `--plan` it builds the plan with the current time, turns it into jobs, prints them and saves the plan file. With `--plan` it loads a saved plan, checks it against the files as they are now, and (under `--execute`) runs it. `--execute` without `--plan` is refused (exit 2).

### From plan to jobs (`select`)
Each assignment becomes a job dict: `task`, `pool`, `note`, `prompt` (task `prompt`, else its `name`), `cwd` (task `cwd`, else the run block's, relative to the config folder; with neither, the config folder itself), `here` (the config folder), `backend` and `skip`. The first rule that matches sets `skip`:

1. No pool fits: `unassigned`.
2. The pool has any flag and `--allow-unverified` is off: needs a fresh reading.
3. `runs.jsonl` has an `ok` entry for the same task id and name and `--rerun` is off: already ran.
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

`readings_fingerprint` hashes each pool's `remaining_pct`, `remaining_usd`, `resets_at`, `updated_at` and `source`, the fields `allo-router set` writes. `config_hash` hashes everything else in `pools.json` plus `tasks.json`. `hash` covers every other field, as canonical JSON (sorted keys, no spaces).

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
4. `record` appends one JSON line to `runs.jsonl` and, unless the status is `dry-run`, writes the command, status, stdout and stderr to `runs/<time>-task<id>.log`.

Jobs run one at a time in plan order. Exit code: 0 when nothing failed, 1 when any job ended `failed`, `timeout` or `error`, 2 when it refused to start (no plan, a plan that no longer matches, or no terminal without `--yes`).

### runs.jsonl
One line per attempted job:

    {"at": "2026-10-14T09:00:00Z", "task_id": 3, "task_name": "Refactor the pricing page", "pool": "chatgpt",
     "backend": "ShellBackend", "status": "ok", "returncode": 0, "seconds": 41.2, "output": "runs/20261014T090000Z-task3.log",
     "plan": "sha256:..."}

Only `ok` lines count as done for the rerun guard. A `dry-run` backend line records that the job was reached, not that it ran. Logs and plan files hold your prompts and model output; `runs.jsonl`, `runs/` and `plan.json` are gitignored.

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
- The executor never edits `pools.json` or `tasks.json`. Usage is not measured, so it is not guessed either.
- No network code anywhere. Any network use happens inside the CLI you configure, under that CLI's own login.
- No secrets in config. The CLI you call should already be authenticated.

## Tests
`tests/test_allo.py` covers the planner and the CLI. `tests/test_exec.py` covers the executor with local `python -c` commands standing in for real CLIs. `tests/conftest.py` blocks socket connections and DNS for every test.
