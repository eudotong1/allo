#!/usr/bin/env python3
"""Allo: the agent router for maximizing usage. Plans which AI plan should take which job before limits reset.

Reads pools.json and tasks.json from the directory you point it at (default: current directory).
Read-only by default. It never calls a network, never sends anything and never starts a job.
Unknown readings are printed as unknown, not guessed.

  allo.py                      print the plan
  allo.py set <pool> pct=40    update a reading (also usd=, reset=ISO-UTC, note=)
  allo.py init                 write a sample pools.json and tasks.json (dates relative to now)
  allo.py --dir examples       use another folder
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STALE_H = 24

# Starter files written by `allo init`. Kept in code so a pip install works without extra data files.
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
      "updated_at": "2099-01-03T09:00:00Z"
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
      "updated_at": None
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
      "updated_at": "2099-01-03T09:00:00Z"
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
      "updated_at": "2099-01-03T09:00:00Z"
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
      "priority": 1
    },
    {
      "id": 2,
      "name": "Summarize customer interview notes",
      "tier": "private",
      "est_usd": 15,
      "needs": [
        "api"
      ],
      "priority": 2
    },
    {
      "id": 3,
      "name": "Refactor the pricing page",
      "tier": "public",
      "est_usd": 0,
      "needs": [],
      "priority": 3
    },
    {
      "id": 4,
      "name": "Review contract redlines",
      "tier": "private",
      "est_usd": 500,
      "needs": [
        "api"
      ],
      "priority": 4
    }
  ]
}


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


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
    `tiers` is the ordered list of data tiers, least sensitive first. A pool may only take a task
    whose tier is at or below the most sensitive tier the pool lists."""
    rank = {t: i for i, t in enumerate(tiers)}
    reserve = opts.get("credit_reserve", 0.10)
    min_headroom = opts.get("min_headroom_pct", 20)
    sub_max_tier = opts.get("subscription_max_tier", tiers[-1])
    credit = next((r for r in rows if r["p"]["kind"] == "credit_usd"), None)
    budget = (credit["left"] or 0) * (1 - reserve) if credit else 0
    out = []
    for t in sorted(tasks, key=lambda t: t["priority"]):
        if t["tier"] not in rank:
            raise SystemExit(f"task {t['id']}: tier {t['tier']!r} is not in tiers {tiers}")
        elig = [r["p"]["id"] for r in rows
                if rank[t["tier"]] <= max(rank[x] for x in r["p"]["tiers"])]
        if credit and "api" in t.get("needs", []) and t["est_usd"] <= budget and credit["p"]["id"] in elig:
            budget -= t["est_usd"]
            out.append((t, credit["p"]["id"], elig))
            continue
        cand = [r for r in rows if r["p"]["id"] in elig and r["p"]["kind"] != "credit_usd"
                and rank[t["tier"]] <= rank[sub_max_tier] and (r["left"] is None or r["left"] >= min_headroom)]
        cand.sort(key=lambda r: (r["left"] is None, -(r["left"] or 0)))
        if cand:
            c = cand[0]
            tag = "headroom UNKNOWN, confirm first" if c["left"] is None else f"{c['left']:.0f}% left"
            out.append((t, f"{c['p']['id']} ({tag})", elig))
        else:
            out.append((t, None, elig))
    return out, budget


def render(here, now):
    cfg = json.loads((here / "pools.json").read_text())
    tasks = json.loads((here / "tasks.json").read_text())["tasks"]
    tiers = cfg.get("tiers", ["public", "private"])
    rows = pool_rows(cfg["pools"], now)
    lines = [f"USAGE PLAN (read-only) as of {now:%Y-%m-%d %H:%M} UTC", "",
             f"{'pool':<32}{'left':>7}{'hrs to reset':>14}  flags"]
    for r in rows:
        h = f"{r['hours']:.0f}" if r["hours"] is not None else "?"
        lines.append(f"{r['p']['label']:<32}{fmt_left(r):>7}{h:>14}  {', '.join(r['flags'])}")
    plan, left = propose(tasks, rows, tiers, cfg.get("options", {}))
    lines += ["", "PROPOSED ASSIGNMENT (nothing is sent; you approve each one)"]
    for t, pool, elig in plan:
        lines.append(f" #{t['id']:<2} {t['name'][:52]:<53} tier={t['tier']:<10} ${t['est_usd']:<4} -> {pool or 'UNASSIGNED'}")
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
    pid, kv = args[0], dict(a.split("=", 1) for a in args[1:])
    cfg = json.loads((here / "pools.json").read_text())
    p = next((x for x in cfg["pools"] if x["id"] == pid), None)
    if not p:
        sys.exit("unknown pool; ids: " + ", ".join(x["id"] for x in cfg["pools"]))
    if "pct" in kv:
        p["remaining_pct"] = float(kv["pct"])
    if "usd" in kv:
        p["remaining_usd"] = float(kv["usd"])
    if "reset" in kv:
        parse(kv["reset"])
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
    for name, data in (("pools.json", cfg), ("tasks.json", STARTER_TASKS)):
        target = here / name
        if target.exists():
            sys.exit(f"{target} already exists; not overwriting")
        target.write_text(json.dumps(data, indent=2))
    print("wrote pools.json and tasks.json. Edit them to match your plans, then run: allo")


def main(argv):
    here = Path(".")
    if "--dir" in argv:
        i = argv.index("--dir")
        here = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if argv and argv[0] == "init":
        cmd_init(here)
    elif argv and argv[0] == "set":
        cmd_set(here, argv[1:])
    else:
        print(render(here, datetime.now(timezone.utc)))


def cli():
    main(sys.argv[1:])


if __name__ == "__main__":
    cli()
