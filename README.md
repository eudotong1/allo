# Allo Router

The agent router for maximizing usage. Site: https://allorouter.com

You pay for several AI plans. Each has its own weekly limit and its own reset time. Some capacity goes unused while another plan runs dry. This tool tells you which plan should take which job before the next reset, and can then start those jobs for you.

It has two parts:
- The **planner** reads two JSON files you keep and prints a proposal. It never logs in to a plan, never starts a job, never sends anything and makes no network calls.
- The **executor** (`allo-router run`) previews that proposal and saves it as a plan file. `allo-router run --plan plan.json --execute` then runs exactly that reviewed plan through the CLI you configured for each pool, such as `claude -p` or `codex exec`, asking before each job. If anything changed since you reviewed it, it refuses and starts nothing.

## Quickstart
Needs Python 3.9 or newer. No dependencies. Install with `pip install .` from this folder (not on PyPI yet), or run `python3 allo.py` directly.

    mkdir my-router && cd my-router
    allo-router init                   # writes pools.json and tasks.json
    allo-router                        # prints the plan
    allo-router set chatgpt pct=55     # record a fresh reading
    allo-router run                    # preview: the exact command each job would run; saves plan.json
    allo-router run --plan plan.json --execute   # runs exactly that plan, asking before each job

Without installing, replace `allo-router` with `python3 /path/to/allo.py`.

The starter files describe four example plans with made-up readings. Replace them with yours before trusting anything.

### Turning on a real backend
Every starter pool uses the `dry-run` backend, so a first `--execute` cannot start a real agent: it logs each job and starts nothing. Each starter pool also carries the command it would run, and the dry run prints it:

    #3  Refactor the pricing page  -> chatgpt (90% left)
        dry-run backend: nothing starts, even with --execute. With "backend": "shell" it would run: codex exec '...'

To opt a pool in, check that command against the CLI you have installed (a dry run marks a missing one `NOT FOUND`), then change that pool's `"backend": "dry-run"` to `"backend": "shell"` in `pools.json`:

    "run": {"backend": "shell", "command": ["codex", "exec", "{prompt}"]}

Opt in one pool at a time. Pools you leave on `dry-run` stay planned and logged but never start anything.

## What you put in
- `pools.json`: one entry per plan. Subscriptions carry `remaining_pct`. API credit carries `remaining_usd`. Each has `resets_at` (UTC) and the data `tiers` it may touch. A pool that should be runnable gets a `run` block (below).
- `tasks.json`: each job has a `tier`, a rough `est_usd`, whether it `needs` the `api`, a `priority`, and optionally the `prompt` to send (defaults to its `name`) and a `cwd` to run in.

A `run` block names a backend:

    "run": {"backend": "shell", "command": ["claude", "-p", "{prompt}"], "timeout_s": 1800}
    "run": {"backend": "dry-run"}

`command` is a list, not a shell string: it is run directly, never through a shell, so nothing in a prompt can start another command. Items may use `{prompt}`, `{task_id}`, `{task_name}` and `{tier}`. Leave `{prompt}` out and the prompt is sent on stdin instead. `env` adds environment variables, `cwd` sets the working folder, relative to the config folder. Without one, jobs run in the config folder itself, not wherever you launched `allo-router`. Never put API keys or other secrets in `pools.json`; the CLI should already be logged in. The `dry-run` backend never starts anything, which is useful for a pool you want planned but not yet wired up. It accepts the same `command`, `env` and `cwd` as `shell` and only shows them, so switching to `shell` is a one-word change.

## How it decides
- Pools are ranked by urgency: what is left divided by hours until reset.
- API jobs go to the credit pool while dollars fit above a reserve (default 10%). An API job never falls back to a subscription: if the credit pool is missing, unread, out of budget or not allowed the job's tier, it stays UNASSIGNED.
- Other jobs go to the subscription with the most known headroom (default at least 20%). Unknown readings are shown as unknown and never guessed.
- Data tiers are ordered, least sensitive first. A job can only go to a pool that lists its tier. Subscription plans can be capped at a lower tier with `subscription_max_tier`. Change the tier names in `pools.json`.
- Readings older than 24 hours are flagged STALE.
- Bad input stops the planner with a message instead of a guess: a timestamp without a zone (`2026-10-16T20:00:00` instead of `...Z`), a negative `est_usd`, a task or pool tier missing from `tiers`, an unknown `set` key (`set chatgpt percent=55`) or a non-number for `pct=`/`usd=`. `init` writes nothing if either file already exists, and a missing `pools.json` or `tasks.json` points you to `init` or `--dir`.

## Preview, review, run
`run` never plans and executes in one step. The preview is what you approve, and `--execute` only accepts a saved preview:

1. `allo-router run` prints every job, its pool and the exact command, starts nothing, and saves the plan to `plan.json` in the config folder (`--save-plan FILE` to put it elsewhere). It prints the command to run it next.
2. Read it. `allo-router run --plan plan.json` re-checks a saved plan against your files without starting anything.
3. `allo-router run --plan plan.json --execute` runs the jobs that were runnable in that plan, in its order, and nothing else.

The plan file records when it was made, a fingerprint of the readings (`remaining_pct`, `remaining_usd`, `resets_at`, `updated_at`, `source` of every pool), a hash of the rest of `pools.json` and `tasks.json`, each job's pool, prompt, folder and command, and a hash of all of that. `--execute` refuses, with exit code 2 and before starting any job, when:
- the plan file was edited after it was saved (its hash no longer matches),
- it is older than `--max-plan-age` minutes (default 60) or dated in the future,
- it was saved for another config folder,
- readings changed since (for example you ran `allo-router set`), or anything else in `pools.json` or `tasks.json` changed,
- a reviewed job would now come out differently: another pool, prompt, folder or command, or a skip such as a reading that has since gone STALE.

Fix the cause and preview again. A job that finished ok after the plan was saved, or under the same plan, is skipped rather than run twice. `--only`, `--allow-unverified` and `--rerun` shape the plan, so they go on the preview, not on `--plan`.

The hash catches accidental edits and drift. It is not a signature: anyone who can write the plan file can also write `pools.json`. Plan files hold your prompts; `plan.json` is gitignored.

## What `run` will not do
- Start anything without `--execute`, and execute anything but a saved plan that still matches (above). Without a terminal to ask you, it also needs `--yes`.
- Run a job on a pool that has no `run` block. Those jobs stay yours to start.
- Run on a pool flagged STALE, NO READING, NO RESET TIME or RESET PASSED. Record a fresh reading with `allo-router set`, or preview with `--allow-unverified`.
- Run a job that already finished ok (per `runs.jsonl`) unless you preview with `--rerun`. A job counts as finished by its task `id` and `name`, not its prompt, so rewording a finished job's `prompt` does not run it again. To run it with the new prompt, preview with `--rerun --only <id>`, or add it as a new task.
- Pass a prompt that starts with `-` where the CLI would read it as an option. Put `"--"` before `{prompt}` in `command` if your CLI supports it.
- Update your readings. A finished job does not say how much of a plan it used, so `pools.json` and `tasks.json` are never edited. Each job's output goes to `runs/` and a one-line record to `runs.jsonl`.

Other flags: `--only 1,3` limits the preview to those task ids. `allo-router run --help` lists them all.

## What it does not do
- It does not read your usage from any provider. You type the numbers in. Providers do not expose consumer-plan usage in a stable way, so any automatic reader would break without warning.
- It does not check whether a provider's terms allow a given use of your plan, including running a CLI non-interactively. That is yours to read.
- Cost estimates are your guesses. The planner does not measure them.
- No dashboard and no scheduling yet. See [docs/roadmap.md](docs/roadmap.md).

## More
- [CHANGELOG.md](CHANGELOG.md): what changed in each version. This is 0.2.0; `allo-router --version` prints it.
- [docs/architecture.md](docs/architecture.md): how the planner and executor fit together, and how to write a backend.
- [docs/roadmap.md](docs/roadmap.md): what is next.
- [examples/](examples/): the starter config and a custom backend.

## Tests
    pip install pytest
    python3 -m pytest tests

or, with uv and no install: `uvx --python 3.12 pytest -q tests`.

Tests never use the network: a fixture blocks sockets, and the shell backend is exercised with small local Python commands.
