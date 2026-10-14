#!/usr/bin/env python3
"""Allo Router: the agent router for maximizing usage. Plans which AI plan should take which job before limits reset.

Reads pools.json and tasks.json from the directory you point it at (default: current directory).
The planner in this file is read-only. It never calls a network, never sends anything and never starts a job.
Unknown readings are printed as unknown, not guessed. Starting jobs lives in allo_exec.py, behind `run`.

  allo-router                      print the plan
  allo-router set <pool> pct=40    update a reading (also usd=, reset=ISO-UTC, note=)
  allo-router init                 write a sample pools.json and tasks.json (dates relative to now)
  allo-router run                  preview each job and save the plan to plan.json; see allo_exec.py
  allo-router run --plan plan.json --execute   run exactly that reviewed plan through each pool's backend
  allo-router --dir examples       use another folder
  allo-router --version            print the version
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.2.0"
STALE_H = 24

# Starter files written by `allo-router init`. Kept in code so a pip install works without extra data files.
# Every starter pool uses the dry-run backend, so a first `run --execute` cannot start a real agent.
# The command each pool would run is shown in the dry run; switch "backend" to "shell" to opt in.
STARTER_POOLS = {
  "tiers": [
    "public",
    "private"
  ],
  "options": {
    "credit_reserve": 0.1,
    "min_headroom_pct": 20,
    "subscription_max_tier": "public"
  },
  "pools": [
    {
      "id": "claude_work",
      "label": "Claude (work plan)",
      "kind": "subscription",
      "tiers": [
        "public",
        "private"
      ],
      "remaining_pct": 30,
      "resets_at": "2099-01-04T15:00:00Z",
      "updated_at": "2099-01-03T09:00:00Z",
      "run": {
        "backend": "dry-run",
        "command": [
          "claude",
          "-p",
          "{prompt}"
        ]
      }
    },
    {
      "id": "claude_personal",
      "label": "Claude (personal plan)",
      "kind": "subscription",
      "tiers": [
        "public"
      ],
      "remaining_pct": None,
      "resets_at": "2099-01-08T09:00:00Z",
      "updated_at": None,
      "run": {
        "backend": "dry-run",
        "command": [
          "claude",
          "-p",
          "{prompt}"
        ]
      }
    },
    {
      "id": "chatgpt",
      "label": "ChatGPT / Codex",
      "kind": "subscription",
      "tiers": [
        "public"
      ],
      "remaining_pct": 90,
      "resets_at": "2099-01-06T06:00:00Z",
      "updated_at": "2099-01-03T09:00:00Z",
      "run": {
        "backend": "dry-run",
        "command": [
          "codex",
          "exec",
          "{prompt}"
        ]
      }
    },
    {
      "id": "api_credit",
      "label": "API credit",
      "kind": "credit_usd",
      "tiers": [
        "public",
        "private"
      ],
      "remaining_usd": 200,
      "resets_at": "2099-01-10T00:00:00Z",
      "updated_at": "2099-01-03T09:00:00Z",
      "run": {
        "backend": "dry-run"
      }
    }
  ]
}

STARTER_TASKS = {
  "tasks": [
    {
      "id": 1,
      "name": "Weekly newsletter drafts",
      "tier": "public",
      "est_usd": 20,
      "needs": [
        "api"
      ],
      "priority": 1,
      "prompt": "Draft three subject lines and a 200-word intro for this week's newsletter."
    },
    {
      "id": 2,
      "name": "Summarize customer interview notes",
      "tier": "private",
      "est_usd": 15,
      "needs": [
        "api"
      ],
      "priority": 2,
      "prompt": "Summarize notes/interviews.md into the five requests customers made most often."
    },
    {
      "id": 3,
      "name": "Refactor the pricing page",
      "tier": "public",
      "est_usd": 0,
      "needs": [],
      "priority": 3,
      "prompt": "Refactor the pricing page for readability without changing behavior. Show the diff; do not commit."
    },
    {
      "id": 4,
      "name": "Review contract redlines",
      "tier": "private",
      "est_usd": 500,
      "needs": [
        "api"
      ],
      "priority": 4,
      "prompt": "List every change in the contract redlines and flag any that shift liability."
    }
  ]
}


def parse(ts):
    """ISO timestamp with a zone ("Z" or an offset). Times are UTC, so a bare timestamp is rejected, not guessed."""
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"bad timestamp {ts!r}; use ISO format with a zone, e.g. 2026-10-16T20:00:00Z")
    if d.tzinfo is None:
        raise SystemExit(f"timestamp {ts!r} has no zone; add Z for UTC, e.g. {ts}Z")
    return d


def hours(a, b):
    return (a - b).total_seconds() / 3600


def pool_rows(pools, now):
    """One row per pool. Urgency = what is left divided by hours to reset: highest first."""
    rows = []
    for p in pools:
        reset, upd = parse(p.get("resets_at")), parse(p.get("updated_at"))
        flags = []
        left = p.get("remaining_usd") if p["kind"] == "credit_usd" else p.get("remaining_pct")
        if left is None:
            flags.append("NO READING")
        if reset is None:
            flags.append("NO RESET TIME")
        if upd is None or hours(now, upd) > STALE_H:
            flags.append("STALE")
        h = hours(reset, now) if reset else None
        if h is not None and h <= 0:
            flags.append("RESET PASSED, re-read")
            h = None
        urgency = (left / h) if (left is not None and h) else None
        rows.append({"p": p, "left": left, "hours": h, "urgency": urgency, "flags": flags})
    rows.sort(key=lambda r: (r["urgency"] is None, -(r["urgency"] or 0), r["hours"] if r["hours"] is not None else 1e9))
    return rows


def fmt_left(r):
    if r["left"] is None:
        return "?"
    return f"${r['left']:.0f}" if r["p"]["kind"] == "credit_usd" else f"{r['left']:.0f}%"


def propose(tasks, rows, tiers, opts):
    """Tasks in priority order. A credit pool takes 'api' jobs while dollars fit above its reserve.
    Other jobs go to the eligible subscription with the most known headroom, then unknowns.
    Returns ([(task, pool_id or None, note, eligible_pool_ids)], credit budget left).
    `tiers` is the ordered list of data tiers, least sensitive first. A pool may only take a task
    whose tier is at or below the most sensitive tier the pool lists."""
    rank = {t: i for i, t in enumerate(tiers)}
    reserve = opts.get("credit_reserve", 0.10)
    min_headroom = opts.get("min_headroom_pct", 20)
    sub_max_tier = opts.get("subscription_max_tier", tiers[-1])
    for r in rows:
        for x in r["p"]["tiers"]:
            if x not in rank:
                raise SystemExit(f"pool {r['p']['id']}: tier {x!r} is not in tiers {tiers}")
    credit = next((r for r in rows if r["p"]["kind"] == "credit_usd"), None)
    budget = (credit["left"] or 0) * (1 - reserve) if credit else 0
    out = []
    for t in sorted(tasks, key=lambda t: t["priority"]):
        if t["tier"] not in rank:
            raise SystemExit(f"task {t['id']}: tier {t['tier']!r} is not in tiers {tiers}")
        if t["est_usd"] < 0:
            raise SystemExit(f"task {t['id']}: est_usd {t['est_usd']} is negative")
        elig = [r["p"]["id"] for r in rows
                if rank[t["tier"]] <= max(rank[x] for x in r["p"]["tiers"])]
        if "api" in t.get("needs", []):
            # An API job can only run on API credit. If the credit pool is missing, unread, too small or
            # not tier-eligible, the job stays unassigned instead of landing on a subscription.
            if credit and credit["left"] is not None and t["est_usd"] <= budget and credit["p"]["id"] in elig:
                budget -= t["est_usd"]
                out.append((t, credit["p"]["id"], "", elig))
            else:
                out.append((t, None, "", elig))
            continue
        cand = [r for r in rows if r["p"]["id"] in elig and r["p"]["kind"] != "credit_usd"
                and rank[t["tier"]] <= rank[sub_max_tier] and (r["left"] is None or r["left"] >= min_headroom)]
        cand.sort(key=lambda r: (r["left"] is None, -(r["left"] or 0)))
        if cand:
            c = cand[0]
            tag = "headroom UNKNOWN, confirm first" if c["left"] is None else f"{c['left']:.0f}% left"
            out.append((t, c["p"]["id"], tag, elig))
        else:
            out.append((t, None, "", elig))
    return out, budget


def load(here):
    cfg = json.loads((here / "pools.json").read_text())
    tasks = json.loads((here / "tasks.json").read_text())["tasks"]
    return cfg, tasks


def build_plan(cfg, tasks, now):
    """The plan as data: (pool rows, assignments, credit left). Shared by the printout and the executor."""
    rows = pool_rows(cfg["pools"], now)
    plan, left = propose(tasks, rows, cfg.get("tiers", ["public", "private"]), cfg.get("options", {}))
    return rows, plan, left


def render(here, now):
    cfg, tasks = load(here)
    rows, plan, left = build_plan(cfg, tasks, now)
    lines = [f"USAGE PLAN (read-only) as of {now:%Y-%m-%d %H:%M} UTC", "",
             f"{'pool':<32}{'left':>7}{'hrs to reset':>14}  flags"]
    for r in rows:
        h = f"{r['hours']:.0f}" if r["hours"] is not None else "?"
        lines.append(f"{r['p']['label']:<32}{fmt_left(r):>7}{h:>14}  {', '.join(r['flags'])}")
    lines += ["", "PROPOSED ASSIGNMENT (nothing is sent; you approve each one)"]
    for t, pool, note, elig in plan:
        target = f"{pool} ({note})" if note else pool or "UNASSIGNED"
        lines.append(f" #{t['id']:<2} {t['name'][:52]:<53} tier={t['tier']:<10} ${t['est_usd']:<4} -> {target}")
        if not pool:
            lines.append(f"      eligible pools by tier: {', '.join(elig) or 'none'}")
    if any(r["p"]["kind"] == "credit_usd" for r in rows):
        lines += ["", f"Credit left after plan (reserve held): ${left:.0f}"]
    unk = [r["p"]["label"] for r in rows if "NO READING" in r["flags"] or "STALE" in r["flags"]]
    if unk:
        lines += ["", "NEEDS A FRESH READING BEFORE TRUSTING THE PLAN: " + "; ".join(unk)]
    for r in rows:
        for k in sorted(r["p"].get("reset_tokens") or [], key=lambda k: k["expires_at"]):
            d = hours(parse(k["expires_at"]), now) / 24
            lines.append(f"\nRESET TOKEN on {r['p']['label']}: {k['kind']}, expires in {d:.1f} days. "
                         "Using one is always your call; the planner never spends it.")
    return "\n".join(lines)


def cmd_set(here, args):
    if not args:
        sys.exit("usage: allo-router set <pool> pct=40 | usd=200 | reset=ISO-UTC | note=text")
    pid = args[0]
    if any("=" not in a for a in args[1:]):
        sys.exit("expected key=value pairs, e.g. pct=40")
    kv = dict(a.split("=", 1) for a in args[1:])
    unknown = sorted(set(kv) - {"pct", "usd", "reset", "note"})
    if unknown or not kv:
        sys.exit("unknown key(s): " + (", ".join(unknown) or "none given") + "; use pct=, usd=, reset= or note=")
    try:
        nums = {k: float(kv[k]) for k in ("pct", "usd") if k in kv}
    except ValueError:
        sys.exit("pct= and usd= need a number")
    if "reset" in kv:
        parse(kv["reset"])
    cfg = json.loads((here / "pools.json").read_text())
    p = next((x for x in cfg["pools"] if x["id"] == pid), None)
    if not p:
        sys.exit("unknown pool; ids: " + ", ".join(x["id"] for x in cfg["pools"]))
    if "pct" in nums:
        p["remaining_pct"] = nums["pct"]
    if "usd" in nums:
        p["remaining_usd"] = nums["usd"]
    if "reset" in kv:
        p["resets_at"] = kv["reset"]
    if "note" in kv:
        p["source"] = kv["note"]
    p["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (here / "pools.json").write_text(json.dumps(cfg, indent=2))
    print("updated", pid, "at", p["updated_at"])


def cmd_init(here):
    """Write a starter config with reset times set from today, so the first run looks real."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    iso = lambda d: (now + timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg = json.loads(json.dumps(STARTER_POOLS))
    for p, (reset, seen) in zip(cfg["pools"], [(1, 0), (5, None), (3, 0), (7, 0)]):
        p["resets_at"] = iso(reset)
        p["updated_at"] = iso(seen) if seen is not None else None
    files = (("pools.json", cfg), ("tasks.json", STARTER_TASKS))
    for name, _ in files:
        if (here / name).exists():
            sys.exit(f"{here / name} already exists; not overwriting")
    for name, data in files:
        (here / name).write_text(json.dumps(data, indent=2))
    print("wrote pools.json and tasks.json. Edit them to match your plans, then run: allo-router")


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return
    here = Path(".")
    if "--dir" in argv:
        i = argv.index("--dir")
        if i + 1 >= len(argv):
            sys.exit("--dir needs a folder")
        here = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    try:
        if argv and argv[0] == "--version":
            print("allo-router", __version__)
        elif argv and argv[0] == "init":
            cmd_init(here)
        elif argv and argv[0] == "set":
            cmd_set(here, argv[1:])
        elif argv and argv[0] == "run":
            import allo_exec  # imported here so planning never loads the code that starts processes
            sys.exit(allo_exec.cmd_run(here, argv[1:], datetime.now(timezone.utc)))
        else:
            print(render(here, datetime.now(timezone.utc)))
    except FileNotFoundError as e:
        sys.exit(f"{Path(e.filename).name} not found in {here.resolve()}; run `allo-router init` to write a starter, or use --dir")
    except json.JSONDecodeError as e:
        sys.exit(f"could not read the JSON file: {e}")


def cli():
    main(sys.argv[1:])


if __name__ == "__main__":
    cli()
