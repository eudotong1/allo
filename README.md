# Allo Router

The agent router for maximizing usage. Site: https://allorouter.com

You pay for several AI plans. Each has its own weekly limit and its own reset time. Some capacity goes unused while another plan runs dry. This tool tells you which plan should take which job before the next reset, and can then start those jobs for you.

It has two parts:
- The **planner** reads two JSON files you keep and prints a proposal. It never logs in to a plan, never starts a job, never sends anything and makes no network calls.
- The **executor** (`allo-router run`) takes that proposal and starts each job through the CLI you configured for its plan, such as `claude -p` or `codex exec`. It is a dry run unless you pass `--execute`, and it asks before each job.

## Quickstart
Needs Python 3.9 or newer. No dependencies. Install with `pip install .` from this folder (not on PyPI yet), or run `python3 allo.py` directly.

    mkdir my-router && cd my-router
    allo-router init                   # writes pools.json and tasks.json
    allo-router                        # prints the plan
    allo-router set chatgpt pct=55     # record a fresh reading
    allo-router run                    # dry run: shows the exact command each job would run
    allo-router run --execute          # runs them, asking before each one

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
- API jobs go to the credit pool while dollars fit above a reserve (default 10%).
- Other jobs go to the subscription with the most known headroom (default at least 20%). Unknown readings are shown as unknown and never guessed.
- Data tiers are ordered, least sensitive first. A job can only go to a pool that lists its tier. Subscription plans can be capped at a lower tier with `subscription_max_tier`. Change the tier names in `pools.json`.
- Readings older than 24 hours are flagged STALE.

## What `run` will not do
- Start anything without `--execute`. Without a terminal to ask you, it also needs `--yes`.
- Run a job on a pool that has no `run` block. Those jobs stay yours to start.
- Run on a pool flagged STALE, NO READING, NO RESET TIME or RESET PASSED. Record a fresh reading with `allo-router set`, or pass `--allow-unverified`.
- Run a job that already finished ok (per `runs.jsonl`) unless you pass `--rerun`.
- Pass a prompt that starts with `-` where the CLI would read it as an option. Put `"--"` before `{prompt}` in `command` if your CLI supports it.
- Update your readings. A finished job does not say how much of a plan it used, so `pools.json` and `tasks.json` are never edited. Each job's output goes to `runs/` and a one-line record to `runs.jsonl`.

Other flags: `--only 1,3` limits the run to those task ids. `allo-router run --help` lists them all.

## What it does not do
- It does not read your usage from any provider. You type the numbers in. Providers do not expose consumer-plan usage in a stable way, so any automatic reader would break without warning.
- It does not check whether a provider's terms allow a given use of your plan, including running a CLI non-interactively. That is yours to read.
- Cost estimates are your guesses. The planner does not measure them.
- No dashboard and no scheduling yet. See [docs/roadmap.md](docs/roadmap.md).

## More
- [docs/architecture.md](docs/architecture.md): how the planner and executor fit together, and how to write a backend.
- [docs/roadmap.md](docs/roadmap.md): what is next.
- [examples/](examples/): the starter config and a custom backend.

## Tests
    pip install pytest
    python3 -m pytest tests

Tests never use the network: a fixture blocks sockets, and the shell backend is exercised with small local Python commands.
