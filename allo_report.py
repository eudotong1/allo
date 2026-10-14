#!/usr/bin/env python3
"""Allo Router report: what the router did, read from runs.jsonl. Read-only: it starts nothing and writes only
the --csv file you name.

  allo-router report                            every logged job, as markdown on stdout
  allo-router report --period week              one summary row per ISO week (or: day)
  allo-router report --since 2026-10-01 --until 2026-10-14   a date range in UTC; --until's day is included
  allo-router report --csv runs.csv             also write one CSV row per job
  allo-router report --baseline max             compare against putting every job on the priciest pool
  allo-router report --price claude_work=0.2    value a pool at 0.2x API price instead of its default

Usage saved is an ESTIMATE, never a measurement. How it is worked out is printed with every report (METHOD).
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import allo

RUN_LOG = "runs.jsonl"   # same file allo_exec writes
STARTED = ("ok", "failed", "timeout", "error")
FAILED = ("failed", "timeout", "error")
# Skips that mean the router could not do its job. "already-ran", "manual" (pool has no run block) and
# "declined" (you said no) are the router working as configured, so they are listed but not counted.
MISROUTE_SKIPS = ("unassigned", "needs-reading", "bad-run-block", "missing-cwd", "other")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
              "cached_input_tokens")

METHOD = """\
- Each job that started (ok, failed or timed out) gets a cost basis in USD: the cost the CLI reported for it
  (`claude -p --output-format json` reports an API-list-price figure, even on a subscription), else the task's
  own `est_usd` guess. Jobs with neither are left out, and the total says how many.
- Each pool gets a price factor: what one dollar of API-priced work costs you there. Defaults: `credit_usd`
  pools 1.0 (you pay API prices), `subscription` pools 0.0 (already paid for; the planner only routes to a
  plan with headroom left, so the job uses capacity that would otherwise expire at reset), unknown pools 1.0.
  Override with `--price POOL=FACTOR` if a plan's capacity is worth something to you.
- Baseline: the same jobs, all at the baseline's factor ({baseline}).
- Est. saved = sum over jobs of cost basis x (baseline factor - factor of the pool it ran on). It can be
  negative when the router used a pricier pool than the baseline.
- Not counted: whether a job would have fit on the baseline pool at all, retries, and any real plan usage.
  Usage is not measured by Allo Router; record readings with `allo-router set`."""


def usage_from_output(text):
    """Usage a CLI printed about itself, or None. Takes the last JSON object in the output that carries
    `total_cost_usd` or a `usage` block: `claude -p --output-format json` prints one, `codex exec --json`
    ends with a turn event that has one. Nothing is estimated here; missing fields stay missing."""
    if not text or "{" not in text:
        return None
    for chunk in [text.strip()] + [line.strip() for line in reversed(text.strip().splitlines())]:
        if not chunk.startswith("{"):
            continue
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        u = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
        found = {k: u[k] for k in USAGE_KEYS if isinstance(u.get(k), int) and not isinstance(u.get(k), bool)}
        cost = obj.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            found["cost_usd"] = cost
        if found:
            return found
    return None


def load_entries(here):
    """(entries, unreadable line count). Entries keep file order and gain a parsed `_at`."""
    entries, bad, log = [], 0, here / RUN_LOG
    if not log.exists():
        return None, 0
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            e["_at"] = allo.parse(e["at"])
        except (ValueError, KeyError, TypeError, SystemExit):
            bad += 1
            continue
        entries.append(e)
    return entries, bad


def bound(text, end):
    """--since/--until. A bare date is a whole UTC day, so --until 2026-10-14 includes all of the 14th."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        d = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return d + timedelta(days=1) if end else d
    return allo.parse(text)


def period_of(at, period):
    """(sort key, label) of the period an entry falls in."""
    if period == "day":
        return at.date().isoformat(), at.strftime("%m/%d/%Y")
    if period == "week":
        monday = (at - timedelta(days=at.weekday())).date()
        return monday.isoformat(), f"week of {monday:%m/%d/%Y}"
    return "", "all"


def misroutes(e):
    """Why this entry counts as a misroute; empty if it does not."""
    why = []
    if e.get("status") in FAILED:
        why.append(e["status"])
    if e.get("status") in STARTED and e.get("pool_flags"):
        why.append("ran on an unverified reading (" + ", ".join(e["pool_flags"]) + ")")
    if e.get("status") == "skipped" and e.get("skip_kind", "other") in MISROUTE_SKIPS:
        why.append("skipped: " + (e.get("reason") or e.get("skip_kind", "other")))
    return why


class Prices:
    """Price factor per pool and the baseline factor. See METHOD."""

    def __init__(self, cfg, entries, overrides, baseline):
        self.kinds = {p["id"]: p.get("kind") for p in (cfg or {}).get("pools", [])}
        for e in entries:
            if e.get("pool") and e.get("pool_kind"):
                self.kinds.setdefault(e["pool"], e["pool_kind"])
        self.overrides = overrides
        unknown = sorted(set(overrides) - set(self.kinds) - {e.get("pool") for e in entries})
        if unknown:
            raise SystemExit(f"--price for unknown pool(s): {', '.join(unknown)}")
        if baseline == "api":
            self.base, self.base_label = 1.0, "api: every job at API price, factor 1.0"
        elif baseline == "max":
            pools = set(self.kinds) | {e["pool"] for e in entries if e.get("pool")}
            top = max(sorted(pools), key=self.factor, default=None)
            self.base = self.factor(top) if top else 1.0
            self.base_label = f"max: every job on the priciest pool ({top}, factor {self.base:g})"
        elif baseline in self.kinds or any(e.get("pool") == baseline for e in entries):
            self.base = self.factor(baseline)
            self.base_label = f"{baseline}: every job on that pool, factor {self.base:g}"
        else:
            raise SystemExit(f"--baseline {baseline!r}: use api, max or a pool id ({', '.join(sorted(self.kinds))})")

    def factor(self, pool):
        if pool in self.overrides:
            return self.overrides[pool]
        return 0.0 if self.kinds.get(pool) == "subscription" else 1.0


def cost_basis(e, est_now):
    """(usd, source) for a started job, or (None, None)."""
    cost = (e.get("usage") or {}).get("cost_usd")
    if isinstance(cost, (int, float)):
        return float(cost), "reported"
    if isinstance(e.get("est_usd"), (int, float)):
        return float(e["est_usd"]), "est_usd"
    key = (str(e.get("task_id")), e.get("task_name"))
    if key in est_now:
        return float(est_now[key]), "est_usd (tasks.json now)"
    return None, None


def build(entries, prices, est_now, period):
    """One row per entry with the report's derived columns."""
    rows = []
    for e in entries:
        key, label = period_of(e["_at"], period)
        started = e.get("status") in STARTED
        basis, source = cost_basis(e, est_now) if started else (None, None)
        r = {"e": e, "period_key": key, "period": label, "started": started, "misroute": misroutes(e),
             "basis": basis, "basis_source": source, "factor": None, "est": None, "base": None, "saved": None}
        if basis is not None:
            r["factor"] = prices.factor(e.get("pool"))
            r["est"], r["base"] = basis * r["factor"], basis * prices.base
            r["saved"] = r["base"] - r["est"]
        rows.append(r)
    return rows


def money(x):
    if x is None:
        return "n/a"
    digits = 2 if x == 0 or abs(x) >= 0.1 else 4   # a few cents of CLI-reported cost stays visible
    return ("-" if x < 0 else "") + f"${abs(x):,.{digits}f}"


def usage_text(u):
    if not u:
        return "not reported"
    parts = [money(u["cost_usd"]) + " reported"] if "cost_usd" in u else []
    if "input_tokens" in u or "output_tokens" in u:
        # Claude counts cache reads and writes outside input_tokens; Codex's cached_input_tokens is already inside it.
        cache = u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        parts.append(f"{u.get('input_tokens', 0):,} in" + (f" (+{cache:,} cache)" if cache else "")
                     + f" / {u.get('output_tokens', 0):,} out tok")
    return ", ".join(parts)


def reading_text(e):
    if e.get("status") not in STARTED:
        return ""
    if "pool_flags" not in e:
        return "not recorded"
    return ", ".join(e["pool_flags"]) or "fresh"


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def summarize(rows):
    s = {"entries": len(rows), "routed": sum(1 for r in rows if r["e"].get("pool")),
         "ran": sum(1 for r in rows if r["started"]),
         "ok": sum(1 for r in rows if r["e"].get("status") == "ok"),
         "failed": sum(1 for r in rows if r["e"].get("status") in FAILED),
         "skipped": sum(1 for r in rows if r["e"].get("status") == "skipped"),
         "dry": sum(1 for r in rows if r["e"].get("status") == "dry-run"),
         "misroutes": sum(1 for r in rows if r["misroute"]),
         "seconds": sum(r["e"].get("seconds") or 0 for r in rows if r["started"])}
    reported = [r["e"]["usage"]["cost_usd"] for r in rows if r["started"] and "cost_usd" in (r["e"].get("usage") or {})]
    s["reported"], s["reported_n"] = sum(reported), len(reported)
    priced = [r for r in rows if r["saved"] is not None]
    s["saved"] = sum(r["saved"] for r in priced) if priced else None
    s["priced_n"] = len(priced)
    return s


def render(here, rows, prices, scope, bad, now):
    lines = ["# Allo Router report", "",
             f"- Config folder: `{here.resolve()}`",
             f"- Runs log: `{RUN_LOG}`, {len(rows)} entries in range" + (f" ({bad} unreadable line(s) skipped)" if bad else ""),
             f"- Range: {scope}",
             f"- Generated {now:%m/%d/%Y %H:%M} UTC by allo-router {allo.__version__}", ""]
    lines += ["## Summary", "",
              "| period | entries | routed | ran | ok | failed | skipped | dry-run | misroutes | run time | reported cost | est. saved (ESTIMATE) |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    periods = sorted({(r["period_key"], r["period"]) for r in rows})
    groups = [(label, [r for r in rows if r["period_key"] == key]) for key, label in periods]
    if len(groups) > 1 or not groups:
        groups.append(("**total**", rows))
    for label, rs in groups:
        s = summarize(rs)
        rep = f"{money(s['reported'])} ({s['reported_n']} of {s['ran']} jobs)" if s["ran"] else "n/a"
        lines.append(f"| {label} | {s['entries']} | {s['routed']} | {s['ran']} | {s['ok']} | {s['failed']} | "
                     f"{s['skipped']} | {s['dry']} | {s['misroutes']} | {s['seconds'] / 60:.1f} min | {rep} | "
                     f"{money(s['saved'])} |")
    total = summarize(rows)
    lines += ["", "`entries` is every line in runs.jsonl for the range: jobs that ran, jobs logged as skipped and "
              "dry-run backend jobs. `ran` counts jobs that actually started. Logs written by 0.2.0 only show jobs "
              "that started; skips are logged after that.", ""]

    lines += ["## Estimated usage saved (ESTIMATE, not a measurement)", "",
              f"Baseline: {prices.base_label}.", "",
              "| pool | kind | price factor | jobs priced | cost basis | est. cost | baseline est. | est. saved |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for pool in sorted({r["e"].get("pool") for r in rows if r["basis"] is not None}):
        rs = [r for r in rows if r["basis"] is not None and r["e"].get("pool") == pool]
        lines.append(f"| {pool} | {prices.kinds.get(pool) or 'unknown'} | {prices.factor(pool):g} | {len(rs)} | "
                     f"{money(sum(r['basis'] for r in rs))} | {money(sum(r['est'] for r in rs))} | "
                     f"{money(sum(r['base'] for r in rs))} | {money(sum(r['saved'] for r in rs))} |")
    unpriced = total["ran"] - total["priced_n"]
    sources = sorted({r["basis_source"] for r in rows if r["basis_source"]})
    lines += ["", f"**Est. saved: {money(total['saved'])}** over {total['priced_n']} of {total['ran']} jobs that ran"
              + (f"; {unpriced} left out (no reported cost or est_usd)" if unpriced else "")
              + (f". Cost basis from: {', '.join(sources)}." if sources else "."), "", "How this is worked out:",
              METHOD.format(baseline=prices.base_label), ""]

    lines += ["## Misroutes", ""]
    bad_rows = [r for r in rows if r["misroute"]]
    if bad_rows:
        lines += ["| when (UTC) | task | pool | why |", "|---|---|---|---|"]
        for r in bad_rows:
            e = r["e"]
            why = "; ".join(r["misroute"])
            lines.append(f"| {e['_at']:%m/%d/%Y %H:%M} | #{e.get('task_id')} {cell(e.get('task_name'))} | "
                         f"{e.get('pool') or 'UNASSIGNED'} | {cell(why)} |")
    else:
        lines.append("None in this range.")
    lines += ["", "A misroute is a job that failed or timed out, ran on a pool whose reading was flagged (STALE, "
              "NO READING, ...) under `--allow-unverified`, or was skipped because no pool fit, the pool needed a "
              "fresh reading, the run block was bad or the working folder was missing. Skips because a job already "
              "ran, its pool has no run block, or you said no are not misroutes.", ""]

    lines += ["## Jobs", "",
              "| when (UTC) | period | task | pool | backend | status | time | usage | reading at run | cost basis |",
              "|---|---|---|---|---|---|---:|---|---|---:|"]
    for r in rows:
        e = r["e"]
        status = e.get("status", "?") + (f" ({e['skip_kind']})" if e.get("skip_kind") else "")
        secs = f"{e['seconds']:.1f}s" if r["started"] and isinstance(e.get("seconds"), (int, float)) else ""
        basis = f"{money(r['basis'])} ({r['basis_source']})" if r["basis"] is not None else ""
        lines.append(f"| {e['_at']:%m/%d/%Y %H:%M} | {r['period']} | #{e.get('task_id')} {cell(e.get('task_name'))} | "
                     f"{e.get('pool') or 'UNASSIGNED'} | {e.get('backend') or ''} | {status} | {secs} | "
                     f"{usage_text(e.get('usage')) if r['started'] else ''} | {reading_text(e)} | {basis} |")
    return "\n".join(lines) + "\n"


CSV_FIELDS = ("period", "at", "task_id", "task_name", "pool", "pool_kind", "backend", "status", "returncode",
              "seconds", "skip_kind", "reason", "pool_flags", "reported_cost_usd", "input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens", "cached_input_tokens",
              "cost_basis_usd", "cost_basis_source", "price_factor", "baseline_factor", "est_cost_usd",
              "est_baseline_usd", "est_saved_usd", "misroute", "plan", "output")


def write_csv(path, rows, prices):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            e, u = r["e"], r["e"].get("usage") or {}
            w.writerow({"period": r["period"], "at": e.get("at"), "task_id": e.get("task_id"),
                        "task_name": e.get("task_name"), "pool": e.get("pool"),
                        "pool_kind": prices.kinds.get(e.get("pool")) or e.get("pool_kind"),
                        "backend": e.get("backend"), "status": e.get("status"), "returncode": e.get("returncode"),
                        "seconds": e.get("seconds"), "skip_kind": e.get("skip_kind"), "reason": e.get("reason"),
                        "pool_flags": ";".join(e.get("pool_flags") or []), "reported_cost_usd": u.get("cost_usd"),
                        **{k: u.get(k) for k in USAGE_KEYS},
                        "cost_basis_usd": r["basis"], "cost_basis_source": r["basis_source"],
                        "price_factor": r["factor"], "baseline_factor": prices.base if r["basis"] is not None else None,
                        "est_cost_usd": r["est"], "est_baseline_usd": r["base"], "est_saved_usd": r["saved"],
                        "misroute": "; ".join(r["misroute"]), "plan": e.get("plan"), "output": e.get("output")})


def _optional_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def cmd_report(here, argv, now, out=print):
    """`allo-router report`. Returns the process exit code."""
    ap = argparse.ArgumentParser(prog="allo-router report", description="Summarize runs.jsonl: jobs routed, "
                                 "pools, outcomes, time, reported usage, misroutes and an ESTIMATE of usage saved.")
    ap.add_argument("--since", help="first day or time to include (UTC), e.g. 2026-10-01 or 2026-10-01T09:00:00Z")
    ap.add_argument("--until", help="last day to include, or a time to stop before (UTC)")
    ap.add_argument("--period", choices=("day", "week"), help="one summary row per UTC day or ISO week")
    ap.add_argument("--csv", metavar="FILE", help="also write one row per job to FILE")
    ap.add_argument("--baseline", default="api", help="what to compare routing against: api (default), max "
                    "(the priciest pool) or a pool id")
    ap.add_argument("--price", action="append", default=[], metavar="POOL=FACTOR",
                    help="price factor for a pool (repeatable); default 1.0 for credit_usd, 0.0 for subscription")
    a = ap.parse_args(argv)
    overrides = {}
    for item in a.price:
        pool, _, val = item.partition("=")
        try:
            overrides[pool] = float(val)
        except ValueError:
            ap.error(f"--price needs POOL=FACTOR, e.g. claude_work=0.2; got {item!r}")
        if overrides[pool] < 0:
            ap.error(f"--price factor for {pool} is negative")
    since = bound(a.since, False) if a.since else None
    until = bound(a.until, True) if a.until else None
    if since and until and since >= until:
        ap.error("--since must be before --until")
    entries, bad = load_entries(here)
    if entries is None:
        out(f"No {RUN_LOG} in {here.resolve()}: nothing has been executed there yet. "
            "Jobs are logged by `allo-router run --plan plan.json --execute`.")
        return 0
    entries = [e for e in entries if (not since or e["_at"] >= since) and (not until or e["_at"] < until)]
    cfg = _optional_json(here / "pools.json")
    tasks = (_optional_json(here / "tasks.json") or {}).get("tasks", [])
    est_now = {(str(t.get("id")), t.get("name")): t["est_usd"] for t in tasks
               if isinstance(t.get("est_usd"), (int, float))}
    prices = Prices(cfg, entries, overrides, a.baseline)
    rows = build(entries, prices, est_now, a.period)
    scope = ", ".join(([f"from {since:%m/%d/%Y %H:%M} UTC"] if since else [])
                      + ([f"until before {until:%m/%d/%Y %H:%M} UTC"] if until else [])) or "all entries"
    out(render(here, rows, prices, scope + (f", by {a.period}" if a.period else ""), bad, now).rstrip("\n"))
    if a.csv:
        write_csv(a.csv, rows, prices)
        print(f"wrote {len(rows)} rows to {a.csv}", file=sys.stderr)   # stderr, so `> report.md` stays clean
    return 0
