# Allo Router

Plan which AI plan should take which job before its limit resets. Site: https://allorouter.com

You pay for several AI plans. Each has its own weekly limit and its own reset time, so some capacity goes unused while another plan runs dry. Allo Router reads what each plan has left and when it resets, and proposes which plan should take each of your jobs. It plans; you start the jobs.

![The local dashboard with example data: each plan's line running to its reset, with the queued jobs on it.](docs/dashboard.png)
*Example data. Every plan, job and number in this screenshot is made up.*

## Quickstart
Needs Python 3.9 or newer. No dependencies. Not on PyPI yet, so install from a clone:

    git clone https://github.com/eudotong1/allo.git
    cd allo
    pip install .                      # or skip this and use python3 /path/to/allo.py in place of allo-router

Then, in a folder for your own config:

    mkdir my-router && cd my-router
    allo-router init                   # writes pools.json and tasks.json
    allo-router                        # prints the plan
    allo-router set chatgpt pct=55     # record a fresh reading by hand
    allo-router sync                   # or read it from local Claude Code / Codex files
    allo-router report --period week   # summary of runs.jsonl
    allo-router dashboard              # read-only local page of the above

The starter files describe four example plans with made-up readings. Replace them with yours before trusting anything. `allo-router --help` lists every command.

## What you put in
- `pools.json`: one entry per plan. Subscriptions carry `remaining_pct`, API credit carries `remaining_usd`. Each has `resets_at` (UTC) and the data `tiers` it may touch.
- `tasks.json`: each job has a `tier`, a rough `est_usd`, whether it `needs` the `api`, a `priority`, and optionally `"recurring": "daily"` or `"weekly"`.

Never put API keys or other secrets in either file.

## How it decides
- Plans are ranked by urgency: what is left divided by hours until reset.
- API jobs go to the credit pool while dollars fit above a reserve (default 10%). An API job never falls back to a subscription: if the credit pool is missing, unread, out of budget or not allowed the job's tier, it stays UNASSIGNED.
- Other jobs go to the subscription with the most known headroom (default at least 20%). Unknown readings are shown as unknown, never guessed.
- Data tiers are ordered, least sensitive first. A job only goes to a pool that lists its tier. `subscription_max_tier` caps subscription plans at a lower tier.
- Readings older than 24 hours are flagged STALE.
- Bad input stops the planner with a message instead of a guess: a timestamp without a zone, a negative `est_usd`, a tier missing from `tiers`, an unknown `set` key or a non-number for `pct=`/`usd=`. `init` writes nothing if either file already exists.

The planner never logs in to a plan, never starts a job and makes no network calls.

## Sync: readings from your own computer
A pool opts in with a `sync` block:

    "sync": {"source": "claude", "window": "week", "auto": true}

`allo-router sync` reads every such pool (`--dry-run` to only print, `--pool ID` for one). With `"auto": true` the pool is synced before a plan is printed; `--no-sync` skips it.

| source | % of plan limit and reset time | tokens |
|---|---|---|
| `claude` | Claude Code's cache in `~/.claude.json`, or the Claude Usage menu-bar app's `~/.claude/.statusline-usage-cache`, whichever is fresher | `~/.claude/projects/**/*.jsonl` |
| `codex` | the `rate_limits` Codex logs in `~/.codex/sessions` (5-hour and weekly windows) | the same session files |

Each reading is labelled FULL (a limit % and a reset time), PARTIAL (tokens only; `remaining_pct` is left alone, tokens are never turned into a percentage) or UNAVAILABLE (with the reason). These are undocumented local file formats: when one changes, sync says UNAVAILABLE rather than giving a wrong number, and you type the reading in with `set`.

## Report and dashboard
`allo-router report` turns `runs.jsonl` into a markdown summary on stdout (`--period day|week`, `--since`, `--until`, `--csv FILE`). Usage saved is labelled as an ESTIMATE and the method is printed with it.

`allo-router dashboard` serves a read-only page on http://127.0.0.1:8765 and opens it in your browser (`--port`, `--no-open`, `--json`, `--example` for made-up data). It listens on 127.0.0.1 only, answers GET only and loads nothing from anywhere else. Anything else on your computer can reach that port while it runs, and the page shows your job names and commands.

## Experimental: `run`
There is an experimental `allo-router run` that hands a reviewed plan to CLIs you already have, such as `claude -p` or `codex exec`. It isn't ready for general use. Every starter pool uses the `dry-run` backend, so nothing starts unless you edit `pools.json` to opt a pool in. If you try it: `allo-router run` previews and saves `plan.json`, and `allo-router run --plan plan.json --execute` runs only that saved plan, refusing if readings or config changed since. `allo-router run --help` has the details, and [docs/architecture.md](docs/architecture.md) covers backends.

## What it does not do
- It does not contact any provider for usage. `sync` reads only files on your computer.
- It does not check whether a provider's terms allow a given use of your plan. That is yours to read.
- Cost estimates are your guesses. The planner does not measure them.
- No scheduler. See [docs/roadmap.md](docs/roadmap.md).

## More
- [CHANGELOG.md](CHANGELOG.md): what changed in each version. `allo-router --version` prints it.
- [docs/architecture.md](docs/architecture.md): how the pieces fit together.
- [examples/](examples/): the starter config and a custom backend.

## Tests
    pip install pytest
    python3 -m pytest tests

Tests never use the network: a fixture blocks sockets.
