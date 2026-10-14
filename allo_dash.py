#!/usr/bin/env python3
"""Allo Router dashboard: a local page that shows what the router is doing, from the files it already keeps.

  allo-router dashboard                 serve it on http://127.0.0.1:8765 (this computer only)
  allo-router dashboard --port 9000     another port
  allo-router dashboard --open          also open it in the browser
  allo-router dashboard --json          print what the page would show, as JSON, and exit
  allo-router dashboard --example       serve made-up example data, labelled "Example data" on the page

It is read-only. It reads pools.json, tasks.json, plan.json, runs.jsonl, runs/current.json (the job running now),
the launchd agents in ~/Library/LaunchAgents that run allo-router for this config folder, and their log. It does
not sync (syncing writes pools.json), never starts a job, and never loads or unloads a launchd agent: it only asks
`launchctl print` whether one is loaded. The server listens on 127.0.0.1 only, answers GET only, and refuses
requests whose Host is not this computer, so a web page elsewhere cannot read it through your browser.
"""
import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import allo
import allo_exec
import allo_report

DEFAULT_PORT = 8765
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
FONTS = Path(__file__).resolve().parent / "docs" / "fonts"   # present in a checkout or editable install
STARTED = allo_report.STARTED
RECENT_DAYS = 8


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def _json(path):
    """(data, error text). A missing file is (None, None): it is not an error for a folder that has not run yet."""
    try:
        return json.loads(path.read_text()), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"{path.name}: {e}"


def family(p):
    """Which colour family a pool is drawn in: codex, claude, credit or other."""
    if p.get("kind") == "credit_usd":
        return "credit"
    src = p["sync"].get("source") if isinstance(p.get("sync"), dict) else None
    text = f"{p.get('id', '')} {p.get('label', '')}".lower()
    if src == "codex" or any(w in text for w in ("codex", "chatgpt", "openai")):
        return "codex"
    if src == "claude" or "claude" in text:
        return "claude"
    return "other"


# launchd: which agents run allo-router for this folder, when, and whether they are loaded

def launchctl_status(label):
    """What `launchctl print` says about a loaded agent, or {"loaded": False}. Read-only."""
    if not shutil.which("launchctl"):
        return {"loaded": None, "note": "launchctl not available on this system"}
    try:
        p = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True,
                           timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"loaded": None, "note": f"launchctl print failed: {e}"}
    if p.returncode != 0:
        return {"loaded": False}
    out = {"loaded": True}
    for key, field in (("state", "state"), ("runs", "runs"), ("last exit code", "last_exit")):
        m = re.search(rf"^\s*{key} = (.+)$", p.stdout, re.M)
        if m:
            out[field] = m.group(1).strip()
    return out


def _intervals(spec):
    if isinstance(spec, dict):
        return [spec]
    return [s for s in spec if isinstance(s, dict)] if isinstance(spec, list) else []


def next_fire(spec, now_local):
    """Next local time a StartCalendarInterval fires. Handles Minute, Hour and Weekday (0 and 7 are Sunday)."""
    best = None
    for s in _intervals(spec):
        for days in range(0, 8):
            day = (now_local + timedelta(days=days)).replace(second=0, microsecond=0)
            if "Weekday" in s and day.isoweekday() % 7 != s["Weekday"] % 7:
                continue
            hours = [s["Hour"]] if "Hour" in s else range(24)
            mins = [s["Minute"]] if "Minute" in s else [0]
            found = None
            for h in hours:
                for m in mins:
                    t = day.replace(hour=h, minute=m)
                    if t > now_local:
                        found = t
                        break
                if found:
                    break
            if found:
                best = found if best is None or found < best else best
                break
    return best


def _opt(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            try:
                return float(args[i + 1])
            except ValueError:
                return default
    return default


def find_agents(here, agents_dir=LAUNCH_AGENTS):
    """launchd agents whose command runs allo-router on this config folder."""
    target = str(here.resolve())
    found = []
    for path in sorted(Path(agents_dir).glob("*.plist")) if Path(agents_dir).is_dir() else []:
        try:
            with open(path, "rb") as f:
                pl = plistlib.load(f)
        except Exception:
            continue
        args = [str(a) for a in pl.get("ProgramArguments") or ([pl["Program"]] if pl.get("Program") else [])]
        text = " ".join(args)
        if "allo-router" not in text and "allo.py" not in text:
            continue
        # A wrapper such as /bin/sh -c "allo-router ... && allo-router ..." keeps its commands in one string.
        words = text.split()
        if not any(w == target or w.startswith(target + "/") for w in words) \
                and str(pl.get("WorkingDirectory", "")) != target:
            continue
        found.append({"label": pl.get("Label", path.stem), "path": str(path), "args": args,
                      "calendar": pl.get("StartCalendarInterval"), "log": pl.get("StandardOutPath"),
                      "executes": "--execute" in words, "refreshes": "--refresh" in words,
                      "max_plan_age": _opt(words, "--max-plan-age", allo_exec.DEFAULT_PLAN_AGE_MIN),
                      "max_review_age": _opt(words, "--max-review-age", allo_exec.DEFAULT_REVIEW_AGE_MIN)})
    return found


def log_tail(path, n=14):
    """Last lines of an agent's log, the outcome of its last run and when the log was last written."""
    try:
        p = Path(path)
        lines = p.read_text(errors="replace").splitlines()
        written = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    except (OSError, TypeError):
        return None
    outcome = None
    for line in reversed(lines):
        if line.startswith("refusing"):
            outcome = {"kind": "refused", "text": line}
            break
        if line.startswith("RUN plan") and "executing" in line:
            outcome = {"kind": "ran", "text": line}
            break
        if line.startswith("REFRESH plan"):
            outcome = {"kind": "refreshed", "text": line}
            break
    return {"path": str(path), "written_at": iso(written), "lines": lines[-n:], "outcome": outcome}


def schedule_state(here, now, agents, status_fn, tz):
    if not agents:
        return None
    now_local = now.astimezone(tz)
    out = []
    for a in agents:
        nxt = next_fire(a["calendar"], now_local) if a["calendar"] else None
        out.append({"label": a["label"], "path": a["path"], "executes": a["executes"], "refreshes": a["refreshes"],
                    "max_plan_age": a["max_plan_age"], "max_review_age": a["max_review_age"],
                    "next_run": iso(nxt), "status": status_fn(a["label"]),
                    "log": log_tail(a["log"]) if a.get("log") else None,
                    "calendar": a["calendar"]})
    runner = next((s for s in out if s["executes"]), out[0])
    return {"agents": out, "runner": runner["label"], "next_run": runner["next_run"],
            "refreshes": any(s["refreshes"] for s in out), "max_plan_age": runner["max_plan_age"],
            "max_review_age": next((s["max_review_age"] for s in out if s["refreshes"]), None)}


# The pieces of state the page shows

def pool_state(rows):
    out = []
    for r in rows:
        p = r["p"]
        ls = p.get("last_sync") if isinstance(p.get("last_sync"), dict) else None
        run_cfg = p.get("run") if isinstance(p.get("run"), dict) else None
        out.append({"id": p["id"], "label": p.get("label", p["id"]), "kind": p["kind"], "family": family(p),
                    "left": r["left"], "left_text": allo.fmt_left(r), "hours_to_reset": r["hours"],
                    "urgency": r["urgency"], "resets_at": p.get("resets_at"), "updated_at": p.get("updated_at"),
                    "flags": r["flags"], "source": p.get("source"), "tiers": p.get("tiers"),
                    "backend": run_cfg.get("backend") if run_cfg else None,
                    "sync": p["sync"].get("source") if isinstance(p.get("sync"), dict) else None,
                    "last_sync": {k: ls.get(k) for k in ("status", "taken_at", "as_of", "window", "used_pct",
                                                          "windows", "note")} if ls else None})
    return out


def queue_state(cfg, tasks, here, now):
    """Where each job in tasks.json would go if a plan were previewed now, and why a job would be skipped."""
    out = []
    for j in allo_exec.select(cfg, tasks, here, now):
        t = j["task"]
        out.append({"task_id": t["id"], "name": t["name"], "pool": j["pool"], "note": j["note"],
                    "priority": t.get("priority"), "tier": t.get("tier"), "recurring": t.get("recurring"),
                    "period": j["period"], "skip": j["skip"],
                    "skip_kind": allo_exec.skip_kind(j["skip"]) if j["skip"] else None,
                    "command": j["backend"].describe(j) if j["backend"] else None})
    return out


def plan_state(here, cfg, tasks, now, schedule):
    path = here / allo_exec.PLAN_FILE
    if not path.exists():
        return None
    try:
        plan = allo_exec.load_plan(path)
    except allo_exec.PlanError as e:
        return {"path": str(path), "error": str(e)}
    max_age = (schedule or {}).get("max_plan_age") or allo_exec.DEFAULT_PLAN_AGE_MIN
    created, reviewed = allo.parse(plan["created_at"]), allo.parse(allo_exec.reviewed_at(plan))
    s = {"path": str(path), "hash": plan["hash"][7:19], "created_at": plan["created_at"],
         "reviewed_at": allo_exec.reviewed_at(plan), "refreshed": "reviewed_plan" in plan,
         "expires_at": iso(created + timedelta(minutes=max_age)), "max_plan_age": max_age,
         "jobs": [{"task_id": j["task_id"], "name": j["task_name"], "pool": j["pool"], "skip": j["skip"]}
                  for j in plan["jobs"]],
         "runnable": sum(1 for j in plan["jobs"] if not j["skip"])}
    try:
        allo_exec.jobs_from_plan(plan, here, cfg, tasks, now, max_age)
        s["check"] = {"ok": True, "text": "still matches; it would run now"}
    except allo_exec.PlanError as e:
        s["check"] = {"ok": False, "text": str(e)}
    nxt = allo.parse(schedule["next_run"]) if schedule and schedule.get("next_run") else None
    if nxt:
        if schedule.get("refreshes"):
            limit = reviewed + timedelta(minutes=schedule.get("max_review_age") or allo_exec.DEFAULT_REVIEW_AGE_MIN)
            s["review_expires_at"] = iso(limit)
            s["ready_for_next_run"] = limit >= nxt
            s["ready_text"] = (f"reviewed {s['reviewed_at']}; the scheduled refresh re-checks it on fresh readings"
                               if s["ready_for_next_run"] else
                               f"reviewed more than {schedule.get('max_review_age') or allo_exec.DEFAULT_REVIEW_AGE_MIN:g} "
                               "minutes before it")
        else:
            s["ready_for_next_run"] = created + timedelta(minutes=max_age) >= nxt
            s["ready_text"] = ("saved recently enough for the next run" if s["ready_for_next_run"] else
                               f"older than {max_age:g} minutes by then")
    return s


def running_state(here, now, alive=None):
    data, _ = _json(here / allo_exec.RUN_DIR / allo_exec.CURRENT)
    if not isinstance(data, dict):
        return None
    alive = alive or _alive
    started = allo.parse(data.get("at")) if data.get("at") else None
    return {"task_id": data.get("task_id"), "name": data.get("task_name"), "pool": data.get("pool"),
            "started_at": data.get("at"), "seconds": (now - started).total_seconds() if started else None,
            "pid": data.get("pid"), "alive": bool(data.get("pid")) and alive(data["pid"])}


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def run_row(r):
    e = r["e"]
    pu = e.get("plan_usage") or {}
    u = e.get("usage") or {}
    return {"at": e.get("at"), "task_id": e.get("task_id"), "name": e.get("task_name"), "pool": e.get("pool"),
            "status": e.get("status"), "seconds": e.get("seconds"), "reason": e.get("reason"),
            "skip_kind": e.get("skip_kind"), "misroute": r["misroute"], "recurring": e.get("recurring"),
            "plan_used_pts": pu.get("used_pts") if pu.get("measurable") else None,
            "plan_window": pu.get("window"), "plan_used_why": None if not pu or pu.get("measurable") else pu.get("why"),
            "cost_usd": u.get("cost_usd"), "output_tokens": u.get("output_tokens"),
            "input_tokens": u.get("input_tokens"), "saved_usd": r["saved"], "basis_source": r["basis_source"]}


def history_state(here, cfg, tasks, now, tz):
    entries, bad = allo_report.load_entries(here)
    if entries is None:
        return {"logged": False, "bad_lines": 0, "today": [], "week": None, "saved": None, "last": None}
    est_now = {(str(t.get("id")), t.get("name")): t["est_usd"] for t in tasks
               if isinstance(t.get("est_usd"), (int, float))}
    prices = allo_report.Prices(cfg, entries, {}, "api")
    rows = allo_report.build(entries, prices, est_now, None)
    local_now = now.astimezone(tz)
    today = local_now.date()
    monday = today - timedelta(days=today.weekday())
    day = lambda r: r["e"]["_at"].astimezone(tz).date()
    week_rows = [r for r in rows if monday <= day(r) <= today]
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        by_pool = {}
        for r in week_rows:
            if day(r) == d and r["e"].get("pool") and r["e"].get("status") != "skipped":
                by_pool[r["e"]["pool"]] = by_pool.get(r["e"]["pool"], 0) + 1
        days.append({"date": d.isoformat(), "label": f"{d:%a}", "by_pool": by_pool, "future": d > today})
    measured = {}
    for r in week_rows:
        if not r["started"] or not r["e"].get("pool"):
            continue
        m = measured.setdefault(r["e"]["pool"], {"pool": r["e"]["pool"], "jobs": 0, "measured": 0, "pts": 0.0,
                                                  "cost_usd": 0.0, "cost_n": 0})
        m["jobs"] += 1
        pu = r["e"].get("plan_usage") or {}
        if pu.get("measurable"):
            m["measured"] += 1
            m["pts"] += pu["used_pts"]
        cost = (r["e"].get("usage") or {}).get("cost_usd")
        if isinstance(cost, (int, float)):
            m["cost_usd"] += cost
            m["cost_n"] += 1
    s_week, s_all = allo_report.summarize(week_rows), allo_report.summarize(rows)
    recent = [r for r in rows if (today - day(r)).days < RECENT_DAYS]
    started = [r for r in rows if r["started"]]
    return {"logged": True, "bad_lines": bad,
            "today": [run_row(r) for r in reversed(rows) if day(r) == today],
            "recent": [run_row(r) for r in reversed(recent)][:40],
            "last": run_row(started[-1]) if started else None,
            "week": {"since": monday.isoformat(), "routed": s_week["routed"], "ran": s_week["ran"], "ok": s_week["ok"],
                     "failed": s_week["failed"], "skipped": s_week["skipped"], "misroutes": s_week["misroutes"],
                     "days": days, "measured": sorted(measured.values(), key=lambda m: -m["jobs"])},
            "saved": {"week_usd": s_week["saved"], "week_priced": s_week["priced_n"], "all_usd": s_all["saved"],
                      "all_priced": s_all["priced_n"], "ran": s_all["ran"], "baseline": prices.base_label}}


def health(state):
    """The headline: what is happening and whether anything needs a person. Returns (level, headline, issues)."""
    issues = []
    for p in state["pools"]:
        if p["flags"]:
            issues.append(f"{p['label']}: {', '.join(p['flags'])}. Run allo-router sync or set before the next plan.")
    sch, plan = state["schedule"], state["plan"]
    if sch:
        for a in sch["agents"]:
            if a["status"].get("loaded") is False:
                issues.append(f"launchd agent {a['label']} is not loaded, so it will not run.")
            out = (a.get("log") or {}).get("outcome")
            if out and out["kind"] == "refused":
                issues.append(f"Last scheduled run refused: {out['text']}")
        if plan is None:
            issues.append("No saved plan, so the next scheduled run will refuse. Preview one with allo-router run.")
        elif plan.get("error"):
            issues.append(f"plan.json cannot be used: {plan['error']}")
        elif plan.get("ready_for_next_run") is False:
            issues.append(f"The next scheduled run will refuse plan.json: it is {plan['ready_text']}. "
                          "Preview and review a new plan before then.")
    last = (state["history"] or {}).get("last")
    if last and last["status"] in allo_report.FAILED:
        issues.append(f"Last job #{last['task_id']} {last['status']} on {last['pool']}.")
    run = state["running"]
    if run and run["alive"]:
        label = next((p["label"] for p in state["pools"] if p["id"] == run["pool"]), run["pool"])
        return "running", f"Running #{run['task_id']} on {label}", issues
    if run and not run["alive"]:
        issues.append(f"#{run['task_id']} was marked running but its process {run['pid']} is gone; "
                      "it did not finish cleanly.")
    if issues:
        return "attention", "Needs attention", issues
    if sch and sch.get("next_run"):
        return "ok", "Idle, all set for the next run", issues
    return "ok", "Idle", issues


def build_state(here, now, tz=None, agents=None, status_fn=launchctl_status, alive=None):
    """Everything the page shows, as one JSON-able dict. Read-only."""
    tz = tz or datetime.now().astimezone().tzinfo
    errors = []
    cfg, err = _json(here / "pools.json")
    errors += [err] if err else []
    tdata, err = _json(here / "tasks.json")
    errors += [err] if err else []
    tasks = (tdata or {}).get("tasks", []) if isinstance(tdata, dict) else []
    state = {"example": False, "version": allo.__version__, "config_dir": str(here.resolve()), "now": iso(now),
             "tz": now.astimezone(tz).strftime("%Z"),
             "tz_offset_min": int(now.astimezone(tz).utcoffset().total_seconds() // 60),
             "pools": [], "queue": [], "plan": None, "schedule": None, "running": None, "history": None,
             "errors": errors}
    agents = find_agents(here) if agents is None else agents
    state["schedule"] = schedule_state(here, now, agents, status_fn, tz)
    if cfg is None:
        errors.append(f"no pools.json in {here.resolve()}; run allo-router init or use --dir")
    else:
        try:
            state["pools"] = pool_state(allo.pool_rows(cfg["pools"], now))
            state["queue"] = queue_state(cfg, tasks, here, now)
            state["plan"] = plan_state(here, cfg, tasks, now, state["schedule"])
        except (SystemExit, KeyError, TypeError) as e:
            errors.append(f"could not read the plan: {e}")
    try:
        state["history"] = history_state(here, cfg, tasks, now, tz)
    except SystemExit as e:
        errors.append(f"could not read {allo_report.RUN_LOG}: {e}")
    state["running"] = running_state(here, now, alive)
    state["level"], state["headline"], state["issues"] = health(state)
    return state


# Example data: a made-up config folder run through the same code, for screenshots and first looks.

def example_state(now, tz=None):
    """State for a made-up week of routing. Every number is invented; the page labels it "Example data"."""
    tz = tz or datetime.now().astimezone().tzinfo
    # The example evening is always 22:12 today, so its log reads the same whenever the page is opened.
    local = now.astimezone(tz).replace(hour=22, minute=12, second=0, microsecond=0)
    now = local.astimezone(timezone.utc)
    at = lambda days, h, m: iso(local.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(days=days))
    in_h = lambda h: iso(now + timedelta(hours=h))
    with tempfile.TemporaryDirectory() as d:
        here = Path(d)
        shell = lambda *cmd: {"backend": "shell", "command": list(cmd)}
        pools = {"tiers": ["public", "private"], "options": {"min_headroom_pct": 20, "subscription_max_tier": "private"},
                 "pools": [
                     {"id": "codex", "label": "Codex", "kind": "subscription", "tiers": ["public"],
                      "remaining_pct": 76, "resets_at": in_h(28), "updated_at": iso(now - timedelta(minutes=3)),
                      "sync": {"source": "codex", "auto": True}, "run": shell("codex", "exec", "--", "{prompt}"),
                      "source": "sync: Codex session log"},
                     {"id": "claude_work", "label": "Claude, work", "kind": "subscription",
                      "tiers": ["public", "private"], "remaining_pct": 41, "resets_at": in_h(85),
                      "updated_at": iso(now - timedelta(minutes=3)), "sync": {"source": "claude", "auto": True},
                      "run": shell("claude", "-p", "{prompt}"), "source": "sync: Claude Usage app cache"},
                     {"id": "api_credit", "label": "API credit", "kind": "credit_usd", "tiers": ["public", "private"],
                      "remaining_usd": 179.66, "resets_at": in_h(400), "updated_at": iso(now - timedelta(hours=2)),
                      "run": shell("claude", "-p", "{prompt}")},
                     {"id": "claude_personal", "label": "Claude, personal", "kind": "subscription",
                      "tiers": ["public"], "remaining_pct": None, "resets_at": in_h(60), "updated_at": None,
                      "sync": {"source": "claude", "home": "~/.claude-personal"},
                      "run": shell("claude", "-p", "{prompt}")}]}
        names = {14: "Write tests for allo_sync.py edge cases", 15: "Tighten the README quickstart",
                 16: "Fix flaky test in test_exec", 17: "Draft replies to this week's customer emails", 18: "Summarize the board call notes",
                 19: "Classify 2,000 support tickets", 12: "Refactor the pricing page copy, show the diff",
                 11: "Summarize customer interview notes", 10: "Triage the overnight error log into issues",
                 9: "Fix type errors in allo_report.py", 2: "Overnight check", 7: "Weekly dependency audit",
                 8: "Review contract redlines"}
        task = lambda i, p, **kw: {"id": i, "name": names[i], "tier": kw.pop("tier", "public"),
                                   "est_usd": kw.pop("est", 0), "needs": kw.pop("needs", []), "priority": p, **kw}
        tasks = [task(14, 1), task(15, 2), task(16, 3), task(17, 4, tier="private"), task(18, 5, tier="private"),
                 task(19, 6, tier="private", needs=["api"], est=2)]
        for p in pools["pools"]:
            if p.get("sync") and p["remaining_pct"] is not None:
                p["last_sync"] = {"status": "FULL", "taken_at": iso(now - timedelta(minutes=3)),
                                  "as_of": p["updated_at"], "window": "week", "used_pct": 100 - p["remaining_pct"]}
        (here / "pools.json").write_text(json.dumps(pools))
        (here / "tasks.json").write_text(json.dumps({"tasks": tasks}))
        log = []

        def ran(i, when, pool, status, secs, pts=None, cost=None, out_tok=None, kind="subscription", **kw):
            e = {"at": when, "task_id": i, "task_name": names[i], "pool": pool, "status": status, "seconds": secs,
                 "pool_kind": kind, "est_usd": kw.pop("est", 0), "pool_flags": [], **kw}
            if pts is not None:
                e["plan_usage"] = {"window": "week", "measurable": True, "used_pts": pts, "windows": {}}
            if cost is not None or out_tok is not None:
                e["usage"] = {k: v for k, v in (("cost_usd", cost), ("output_tokens", out_tok)) if v is not None}
            log.append(e)

        week = [(2, 9, 3), (1, 10, 4)]
        for back, n_codex, n_claude in week:
            for k in range(n_codex):
                ran(9, at(back, 9 + k, 10), "codex", "ok", 300 + 40 * k, pts=2 + k % 2, cost=1.9 + 0.35 * k,
                    out_tok=9000 + 1200 * k)
            for k in range(n_claude):
                ran(10, at(back, 14 + k, 5), "claude_work", "ok", 420, pts=1 + k % 2, cost=1.4 + 0.2 * k,
                    out_tok=7000 + 900 * k)
            ran(11, at(back, 18, 30), "api_credit", "ok", 110, cost=0.41, out_tok=2310, kind="credit_usd", est=1)
        ran(8, at(0, 3, 0), None, "skipped", None, kind=None,
            reason="unassigned: no pool fits this job", skip_kind="unassigned")
        ran(7, at(0, 3, 0), "codex", "skipped", None, reason="already ran ok at Mon 03:00 (this weekly period)",
            skip_kind="already-ran", recurring="weekly")
        ran(2, at(0, 3, 0), "claude_work", "ok", 5, pts=0, cost=0.01, out_tok=352, recurring="daily")
        ran(9, at(0, 3, 0), "codex", "ok", 690, pts=4, cost=3.12, out_tok=21477)
        ran(10, at(0, 19, 3), "claude_work", "timeout", 1800, pts=2, cost=2.2, out_tok=14082)
        ran(11, at(0, 21, 22), "api_credit", "ok", 108, cost=0.41, out_tok=2310, kind="credit_usd", est=1)
        ran(12, at(0, 21, 40), "codex", "ok", 372, pts=3, cost=2.75, out_tok=8904)
        (here / "runs.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log))
        cfg = json.loads((here / "pools.json").read_text())
        saved_at = now - timedelta(minutes=7)
        jobs = allo_exec.select(cfg, tasks, here, saved_at)
        allo_exec.save_plan(here / "plan.json", here, cfg, tasks, jobs, saved_at,
                            {"only": None, "allow_unverified": False, "rerun": False})
        (here / "runs").mkdir()
        (here / "runs" / "current.json").write_text(json.dumps(
            {"task_id": 14, "task_name": names[14], "pool": "codex", "at": iso(now - timedelta(seconds=161)),
             "pid": 1}))
        agents = [{"label": "allo-overnight", "path": "~/Library/LaunchAgents/allo-overnight.plist",
                   "args": [], "calendar": {"Hour": 3, "Minute": 0}, "log": None, "executes": True,
                   "refreshes": True, "max_plan_age": 300, "max_review_age": 960}]
        state = build_state(here, now, tz, agents=agents, status_fn=lambda label: {"loaded": True, "runs": "12"},
                            alive=lambda pid: True)
    state["example"] = True
    state["config_dir"] = "~/my-router (example)"
    if state["plan"]:
        state["plan"]["path"] = "~/my-router/plan.json"
    state["schedule"]["agents"][0]["status"]["last_exit"] = "0"
    return state


# The server: GET only, 127.0.0.1 only, this computer's Host header only.

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               "font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
}


def respond(method, path, host, port, state_fn, fonts=FONTS):
    """(status, content type, body bytes) for one request. Pure, so it can be tested without a socket."""
    if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return 403, "text/plain; charset=utf-8", b"This dashboard only answers to 127.0.0.1 on this computer.\n"
    if method not in ("GET", "HEAD"):
        return 405, "text/plain; charset=utf-8", b"Read-only: GET only.\n"
    route = path.split("?", 1)[0]
    if route in ("/", "/index.html"):
        import allo_dash_page
        return 200, "text/html; charset=utf-8", allo_dash_page.PAGE.encode()
    if route == "/api/state":
        return 200, "application/json", json.dumps(state_fn(), default=str).encode()
    m = re.fullmatch(r"/fonts/([a-z0-9-]+\.woff2)", route)
    if m and fonts and (Path(fonts) / m.group(1)).is_file():
        return 200, "font/woff2", (Path(fonts) / m.group(1)).read_bytes()
    return 404, "text/plain; charset=utf-8", b"Not found.\n"


def serve(port, state_fn, out=print, open_browser=False):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, head_only):
            code, ctype, body = respond(self.command, self.path, self.headers.get("Host", ""), port, state_fn)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self):
            self._send(False)

        def do_HEAD(self):
            self._send(True)

        def do_POST(self):
            self._send(False)

        do_PUT = do_DELETE = do_PATCH = do_POST

        def log_message(self, fmt, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    out(f"Allo Router dashboard on {url} (this computer only, read-only). Ctrl-C to stop.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        out("")
    finally:
        httpd.server_close()
    return 0


def cmd_dashboard(here, argv, now_fn, out=print):
    ap = argparse.ArgumentParser(prog="allo-router dashboard", description="Serve a read-only local page showing "
                                 "plans, the queue, the schedule and what ran, on 127.0.0.1 only.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    ap.add_argument("--open", action="store_true", help="open the page in your browser")
    ap.add_argument("--json", action="store_true", help="print the page's data as JSON and exit")
    ap.add_argument("--example", action="store_true", help='show made-up example data, labelled "Example data"')
    a = ap.parse_args(argv)
    if not 0 < a.port < 65536:
        ap.error("--port must be between 1 and 65535")
    state_fn = (lambda: example_state(now_fn())) if a.example else (lambda: build_state(here, now_fn()))
    if a.json:
        out(json.dumps(state_fn(), indent=2, default=str))
        return 0
    if not a.example and not (here / "pools.json").exists():
        out(f"pools.json not found in {here.resolve()}; run `allo-router init`, use --dir, or try --example")
        return 2
    return serve(a.port, state_fn, out, a.open)
