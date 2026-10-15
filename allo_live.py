#!/usr/bin/env python3
"""Allo Router live: upload running / finished events from real local lanes.

Sources (all real, nothing invented):
  - runs/current.json in a connected router folder (allo-router run --execute)
  - ~/Shield/chief-inbox/codex-run/run.log (Chief / Grok Bot dispatch of com.chief.codex-* lanes)
  - launchctl list for com.chief.codex-* (running Codex lane processes)

Grok Bot is the orchestrator: each "start <lane>" in run.log is a Grok dispatch (provider=grok,
plan_type=orchestrator, no usage %). The matching Codex work is heartbeated on the ChatGPT pool
while the launchd job is alive, then marked ok/failed on "end <lane>".

  allo-router live                  one shot: upload current running + new log events
  allo-router live --watch          poll every few seconds until Ctrl-C
  allo-router live --watch --every 3
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import allo
import allo_upload as up

RUN_LOG = Path.home() / "Shield/chief-inbox/codex-run/run.log"
LANE_TITLES = {
    "industry": "Industry pages",
    "i18n": "i18n",
    "sec": "Security audit",
    "e2e": "E2E tests",
    "docs": "Docs",
    "yard": "Yard review",
    "pages": "Pages",
}
CHIEF_RE = re.compile(r"^com\.chief\.codex-(.+)$")
LOG_RE = re.compile(
    r"^=== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (start|end) (\S+)(?: in (.+)| exit (-?\d+))?\s*$"
)


def utc_minute(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def parse_local(stamp: str) -> datetime:
    # run.log is local Mac time (America/New_York on this machine)
    naive = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    local = datetime.now().astimezone().tzinfo
    return naive.replace(tzinfo=local).astimezone(timezone.utc)


def hid(salt: str, parts) -> str:
    msg = json.dumps([str(x) for x in parts]).encode()
    return "h:" + hmac.new(bytes.fromhex(salt), msg, hashlib.sha256).hexdigest()[:16]


def pool_ref(state, key: str) -> str:
    pools = state.setdefault("pools", {})
    return up.pool_ref(key, pools)


def job_row(*, id, at, pool, plan_type, provider, status, title, model=None, effort=None, heartbeat_at=None, api_usd=None):
    return {
        "id": id,
        "at": at,
        "pool": pool,
        "plan_type": plan_type,
        "provider": provider,
        "status": status,
        "skip": None,
        "miss": [] if status != "failed" else ["failed"],
        "est_saved_usd": api_usd,  # API value (est.): only from real token usage; None otherwise
        "title": up.short_title(title),
        "model": model,
        "effort": effort,
        "heartbeat_at": heartbeat_at,
    }


# Public standard API prices, USD per 1M tokens: (input, cached input, output).
# Source: developers.openai.com/api/docs/models (checked 2026-10-15). Unknown models are never priced.
API_PRICES = {
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-5.3-codex": (1.75, 0.175, 14.00),
}
CODEX_SESSIONS = Path.home() / ".codex/sessions"
_SESSION_CACHE: dict = {}
_NAME_RE = re.compile(r"rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-")


def api_value(model, usage):
    """USD at public API list price for reported token usage, or None if it can't be priced honestly."""
    if not usage or model not in API_PRICES:
        return None
    pin, pcache, pout = API_PRICES[model]
    inp = int(usage.get("input_tokens") or 0)
    cached = min(int(usage.get("cached_input_tokens") or 0), inp)  # Codex counts cached inside input
    out = int(usage.get("output_tokens") or 0)
    if inp == 0 and out == 0:
        return None
    return ((inp - cached) * pin + cached * pcache + out * pout) / 1_000_000


def read_session(path: Path):
    """(cwd, model, total_token_usage) from a Codex session file; cached by size."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    hit = _SESSION_CACHE.get(path)
    if hit and hit[0] == size:
        return hit[1]
    cwd = model = usage = None
    try:
        with path.open(errors="replace") as f:
            for line in f:
                if '"session_meta"' in line and cwd is None:
                    cwd = (json.loads(line).get("payload") or {}).get("cwd")
                elif '"turn_context"' in line:
                    model = (json.loads(line).get("payload") or {}).get("model") or model
                elif '"total_token_usage"' in line:
                    info = (json.loads(line).get("payload") or {}).get("info") or {}
                    usage = info.get("total_token_usage") or usage
    except (OSError, ValueError):
        return None
    res = (cwd, model, usage)
    _SESSION_CACHE[path] = (size, res)
    return res


_FOLDER_CACHE: dict = {}


def _folder_index(folder: Path):
    """[(timestamp, path)] for the rollout files in one day folder; cached by the folder's mtime."""
    try:
        mt = folder.stat().st_mtime
    except OSError:
        return []
    hit = _FOLDER_CACHE.get(folder)
    if hit and hit[0] == mt:
        return hit[1]
    out = []
    for p in folder.glob("rollout-*.jsonl"):
        m = _NAME_RE.match(p.name)
        if m:
            out.append((datetime(*map(int, m.groups())).astimezone().timestamp(), p))  # names use local time
    out.sort()
    _FOLDER_CACHE[folder] = (mt, out)
    return out


def codex_run_value(lane_dir, started: datetime, ended: datetime, root: Path | None = None, next_start: datetime | None = None):
    """API value of the Codex sessions that started in lane_dir between this run's start minute and the end of
    its end minute, stopping before the lane's next start minute so a session is never priced on two runs.
    None when no session reported token usage."""
    root = root or CODEX_SESSIONS
    if not lane_dir:
        return None
    lo = started.replace(second=0, microsecond=0).timestamp()
    hi = ended.replace(second=0, microsecond=0).timestamp() + 59
    if next_start is not None:
        hi = min(hi, next_start.replace(second=0, microsecond=0).timestamp() - 1)
    total, any_priced = 0.0, False
    for d in sorted({started.astimezone().date(), ended.astimezone().date()}):
        folder = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        for t, p in _folder_index(folder):
            if t < lo or t > hi:
                continue
            info = read_session(p)
            if not info or info[0] != lane_dir:
                continue
            v = api_value(info[1], info[2])
            if v is not None:
                total += v
                any_priced = True
    return round(total, 4) if any_priced else None


def launchctl_running() -> dict[str, int]:
    """lane_name -> pid for com.chief.codex-* that are running."""
    out = {}
    try:
        p = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        pid, _last, label = parts[0], parts[1], parts[2]
        m = CHIEF_RE.match(label)
        if not m or pid == "-":
            continue
        try:
            out[m.group(1)] = int(pid)
        except ValueError:
            continue
    return out


def parse_run_log(path: Path):
    """Yield dicts for start/end events from Chief's run.log."""
    if not path.exists():
        return
    for line in path.read_text(errors="replace").splitlines():
        m = LOG_RE.match(line.strip())
        if not m:
            continue
        stamp, kind, name, where, code = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        yield {
            "at": parse_local(stamp),
            "kind": kind,
            "lane": name,
            "dir": where.strip() if where else None,
            "exit": int(code) if code is not None else None,
        }


def open_starts(events):
    """Map lane -> last unpaired start event (for matching ends and live heartbeats)."""
    open_ = {}
    for e in events:
        if e["kind"] == "start":
            open_[e["lane"]] = e
        elif e["kind"] == "end" and e["lane"] in open_:
            del open_[e["lane"]]
    return open_


def build_live_jobs(state, now: datetime, router_dirs: list[Path], *, backfill: bool = False):
    """Build upload rows. By default only *new* run.log events since the live cursor, plus
    whatever is running right now. Pass backfill=True once to import today's log history."""
    salt = state["salt"]
    jobs = []
    events = list(parse_run_log(RUN_LOG))
    running_lanes = launchctl_running()
    cursor = state.setdefault("live", {})
    last_seen = cursor.get("run_log_at")  # ISO minute UTC of last consumed event

    def after_cursor(e):
        if backfill or not last_seen:
            return True
        return utc_minute(e["at"]) >= last_seen

    # Pair starts/ends across the whole log so open_starts is correct, but only *emit*
    # events that are new (or currently running).
    next_start = {}  # id(start event) -> the same lane's following start time
    last_by_lane = {}
    for e in events:
        if e["kind"] == "start":
            prev = last_by_lane.get(e["lane"])
            if prev is not None:
                next_start[id(prev)] = e["at"]
            last_by_lane[e["lane"]] = e
    stack = {}
    newest = last_seen
    for e in events:
        stamp = utc_minute(e["at"])
        if e["kind"] == "start":
            stack[e["lane"]] = e
            continue
        start = stack.pop(e["lane"], None)
        started = start["at"] if start else e["at"]
        if not after_cursor(e) and not (start and after_cursor(start)):
            continue
        status = "ok" if (e["exit"] is None or e["exit"] == 0) else "failed"
        title_g = f"Dispatch {LANE_TITLES.get(e['lane'], e['lane'])}"
        title_c = LANE_TITLES.get(e["lane"], e["lane"])
        value = codex_run_value((start or {}).get("dir"), started, e["at"],
                                next_start=next_start.get(id(start)) if start else None)
        jobs.append(job_row(
            id=hid(salt, ["grok-dispatch", e["lane"], utc_minute(started)]),
            at=utc_minute(started), pool=pool_ref(state, "grok_bot"), plan_type="orchestrator",
            provider="grok", status=status, title=title_g, heartbeat_at=stamp,
        ))
        jobs.append(job_row(
            id=hid(salt, ["codex-lane", e["lane"], utc_minute(started)]),
            at=utc_minute(started), pool=pool_ref(state, "chatgpt"), plan_type="subscription",
            provider="codex", status=status, title=title_c, model="gpt-5-codex", effort="high",
            heartbeat_at=stamp, api_usd=value,
        ))
        newest = stamp if (newest is None or stamp > newest) else newest

    # Currently running lanes (launchd) → running heartbeats on Grok + ChatGPT
    open_ = open_starts(events)
    for lane, pid in running_lanes.items():
        start = open_.get(lane)
        started = start["at"] if start else now
        title_g = f"Dispatch {LANE_TITLES.get(lane, lane)}"
        title_c = LANE_TITLES.get(lane, lane)
        jobs.append(job_row(
            id=hid(salt, ["grok-dispatch", lane, utc_minute(started)]),
            at=utc_minute(started), pool=pool_ref(state, "grok_bot"), plan_type="orchestrator",
            provider="grok", status="running", title=title_g, heartbeat_at=utc_minute(now),
        ))
        jobs.append(job_row(
            id=hid(salt, ["codex-lane", lane, utc_minute(started)]),
            at=utc_minute(started), pool=pool_ref(state, "chatgpt"), plan_type="subscription",
            provider="codex", status="running", title=title_c, model="gpt-5-codex", effort="high",
            heartbeat_at=utc_minute(now),
        ))

    # allo-router current.json
    for here in router_dirs:
        cur = here / "runs" / "current.json"
        if not cur.exists():
            continue
        try:
            data = json.loads(cur.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("task_id"):
            continue
        pid = data.get("pid")
        alive = False
        if pid:
            try:
                os.kill(int(pid), 0)
                alive = True
            except PermissionError:
                alive = True
            except (OSError, ValueError, TypeError):
                alive = False
        if not alive:
            continue
        pool_id = data.get("pool") or "unknown"
        provider, plan = "other", "subscription"
        known = False
        try:
            cfg = json.loads((here / "pools.json").read_text())
            for pcfg in cfg.get("pools") or []:
                if pcfg.get("id") == pool_id:
                    provider = up.provider(pcfg, pcfg.get("kind") or "subscription")
                    plan = up.plan_type(pcfg.get("kind") or "subscription")
                    known = True
                    break
        except (OSError, ValueError):
            pass
        if not known:
            continue  # pool not in this folder's pools.json (e.g. a test fixture marker): not real work
        started = allo.parse(data["at"]) if data.get("at") else now
        jobs.append(job_row(
            id=hid(salt, ["allo-current", data.get("task_id"), data.get("at"), pool_id]),
            at=utc_minute(started), pool=pool_ref(state, pool_id), plan_type=plan,
            provider=provider, status="running",
            title=data.get("task_name") or f"Task {data.get('task_id')}",
            heartbeat_at=utc_minute(now),
        ))

    rank = {"running": 2, "ok": 1, "failed": 1, "timeout": 1, "error": 1, "skipped": 0, "dry-run": 0}
    best = {}
    for j in jobs:
        prev = best.get(j["id"])
        if not prev or rank.get(j["status"], 0) >= rank.get(prev["status"], 0):
            best[j["id"]] = j
    if newest:
        cursor["run_log_at"] = newest
    return list(best.values())


def send(jobs, state, now, transport=up.http):
    if not jobs:
        return 0
    # Cap to MAX_ROWS: prefer running, then newest
    jobs = sorted(jobs, key=lambda j: (0 if j["status"] == "running" else 1, j["at"]), reverse=True)
    jobs = jobs[: up.MAX_ROWS]
    payload = {"schema": up.SCHEMA, "sent_at": utc_minute(now), "jobs": jobs}
    up.check_payload(payload)
    status, body = transport("POST", state["server"].rstrip("/") + "/v1/jobs", payload, state["token"])
    up.log_upload(payload, status)
    if status != 200:
        raise up.UploadError(f"upload HTTP {status}: {body}")
    up.save_state(state)
    return len(jobs)


def default_router_dirs():
    out = []
    for p in (
        Path.home() / "Shield/Projects/allo-hosted",
        Path.home() / "Shield/chief-inbox/allo-dogfood",
    ):
        if (p / "pools.json").exists():
            out.append(p)
    return out


def cmd_live(argv, now=None, transport=up.http, out=print):
    ap = argparse.ArgumentParser(prog="allo-router live", description="Upload live lane / Grok dispatch events")
    ap.add_argument("--watch", action="store_true", help="poll until interrupted")
    ap.add_argument("--every", type=float, default=4.0, help="seconds between polls in --watch (default 4)")
    ap.add_argument("--dir", action="append", default=[], help="router folder with runs/current.json (repeatable)")
    ap.add_argument("--backfill", action="store_true", help="import all events in run.log once (not only new)")
    a = ap.parse_args(argv)
    state = up.load_state()
    if not state:
        out("Not connected. Run allo-router connect first.")
        return 1
    dirs = [Path(d) for d in a.dir] or default_router_dirs()

    def once():
        n = datetime.now(timezone.utc) if now is None else now
        jobs = build_live_jobs(state, n, dirs, backfill=a.backfill)
        running = sum(1 for j in jobs if j["status"] == "running")
        sent = send(jobs, state, n, transport)
        out(f"live: uploaded {sent} row(s) ({running} running) to {state['server']}")
        if a.backfill:
            a.backfill = False  # only the first pass in --watch
        return 0

    if not a.watch:
        return once()
    out(f"live watch every {a.every:g}s; Ctrl-C to stop")
    try:
        while True:
            once()
            time.sleep(max(1.0, a.every))
    except KeyboardInterrupt:
        out("live watch stopped")
        return 0


if __name__ == "__main__":
    sys.exit(cmd_live(sys.argv[1:]))
