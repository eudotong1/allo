# Allo

The agent router for maximizing usage.

You pay for several AI plans. Each has its own weekly limit and its own reset time. Some capacity goes unused while another plan runs dry. This tool tells you which plan should take which job before the next reset.

It is a planner. It reads two JSON files you keep, prints a proposal, and stops. It never logs in to a plan, never starts a job, never sends anything and makes no network calls. You read the proposal and start each job yourself.

## Quickstart
Needs Python 3.9 or newer. No dependencies. Install with `pip install .` from this folder (not on PyPI yet), or run `python3 allo.py` directly.

    mkdir my-router && cd my-router
    python3 allo init     # writes pools.json and tasks.json
    python3 allo          # prints the plan
    python3 allo set chatgpt pct=55    # record a fresh reading

## What you put in
- `pools.json`: one entry per plan. Subscriptions carry `remaining_pct`. API credit carries `remaining_usd`. Each has `resets_at` (UTC) and the data `tiers` it may touch.
- `tasks.json`: each job has a `tier`, a rough `est_usd`, whether it `needs` the `api`, and a `priority`.

## How it decides
- Pools are ranked by urgency: what is left divided by hours until reset.
- API jobs go to the credit pool while dollars fit above a reserve (default 10%).
- Other jobs go to the subscription with the most known headroom (default at least 20%). Unknown readings are shown as unknown and never guessed.
- Data tiers are ordered, least sensitive first. A job can only go to a pool that lists its tier. Subscription plans can be capped at a lower tier with `subscription_max_tier`. Change the tier names in `pools.json`.
- Readings older than 24 hours are flagged STALE.

## What it does not do
- It does not read your usage from any provider. You type the numbers in. Providers do not expose consumer-plan usage in a stable way, so any automatic reader would break without warning.
- It does not check whether a provider's terms allow a given use of your plan. That is yours to read.
- Cost estimates are your guesses. The planner does not measure them.
- No dashboard and no scheduling yet.

## Tests
    python3 -m pytest tests
