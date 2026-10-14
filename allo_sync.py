#!/usr/bin/env python3
"""Allo Router sync: plan readings from files already on this computer. It opens no network connection, and it only
reads its sources. The one thing it writes is the readings in pools.json, and only for pools that opt in.

A pool opts in with a "sync" block in pools.json:

  "sync": {"source": "claude", "window": "week", "auto": true, "settle_s": 180}

  source    "claude" or "codex" (required)
  window    which plan limit sets the pool's remaining_pct: "week" (default) or "5h"
  auto      true: sync this pool before `allo-router` prints a plan and before `allo-router run` previews one
  settle_s  under --execute, wait up to this many seconds after a job for the source to refresh, so the
            "after" reading postdates the job (default 0: take whatever is there)
  home      read another config folder instead of ~/.claude (claude) or ~/.codex (codex)
  usage_app claude only: false ignores the Claude Usage app's cache (default true)
  limit_id  codex only: which of Codex's limits to read (default "codex")

Where readings come from (each source is a file the provider's own software writes; nothing here guesses):

  claude  Plan limit % and reset time: Claude Code's cache in ~/.claude.json (cachedUsageUtilization, refreshed
          on Claude Code's own schedule, which can leave it days old) and the Claude Usage menu-bar app's
          ~/.claude/.statusline-usage-cache (refreshed by the app every few minutes). The freshest one wins.
          The app gets its numbers from claude.ai with your browser login; Allo Router only reads the file it
          leaves behind. Set "usage_app": false to use Claude Code's own cache only.
          Tokens: assistant messages in ~/.claude/projects/**/*.jsonl, de-duplicated by message and request id.
  codex   Plan limit % and reset time: the rate_limits Codex logs with each turn in ~/.codex/sessions/**/*.jsonl.
          Tokens: the cumulative token counts in the same files.

Each reading is one of:
  FULL         the source gave % of the plan limit used and when it resets, for a window that has not reset yet.
               `sync` writes it to the pool (remaining_pct = 100 - used, resets_at, updated_at = when the source
               took it, not when sync ran), unless the pool already holds a newer reading.
  PARTIAL      tokens only: no source gave a limit. remaining_pct is left as it was; tokens are not a share of a plan.
  UNAVAILABLE  nothing usable was found. The reason is printed.

  allo-router sync                  read every pool with a sync block and update pools.json
  allo-router sync --dry-run        print the readings, write nothing
  allo-router sync --pool claude_work --no-tokens
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

FULL, PARTIAL, UNAVAILABLE = "FULL", "PARTIAL", "UNAVAILABLE"
WINDOWS = {"5h": timedelta(hours=5), "week": timedelta(days=7)}
CODEX_WINDOWS = {300: "5h", 10080: "week"}   # window_minutes Codex reports -> our window names
SOURCES = ("claude", "codex")
RESET_MATCH_S = 120   # two readings of one window may give its reset time a little differently
sleep = time.sleep    # tests replace this so nothing waits


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def _ts(text):
    """A source's timestamp as an aware UTC datetime, or None. Unlike allo.parse this never exits: a bad source
    value makes that one source unusable, not the whole sync."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return datetime.fromtimestamp(text, timezone.utc)
    if not isinstance(text, str) or not text:
        return None
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else None


def _pct(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def check_block(pool):
    """The pool's sync block, checked. Raises SystemExit with a message for a bad one."""
    s = pool.get("sync")
    if not isinstance(s, dict):
        raise SystemExit(f"pool {pool['id']}: \"sync\" must be an object, e.g. {{\"source\": \"claude\"}}")
    if s.get("source") not in SOURCES:
        raise SystemExit(f"pool {pool['id']}: sync source {s.get('source')!r}; use {' or '.join(SOURCES)}")
    if s.get("window", "week") not in WINDOWS:
        raise SystemExit(f"pool {pool['id']}: sync window {s.get('window')!r}; use {' or '.join(WINDOWS)}")
    settle = s.get("settle_s", 0)
    if not isinstance(settle, (int, float)) or isinstance(settle, bool) or settle < 0:
        raise SystemExit(f"pool {pool['id']}: sync settle_s must be a number of seconds, 0 or more")
    return s


# Claude ------------------------------------------------------------------------------------------------------------

def claude_paths(s):
    """(Claude Code's state file, Claude Usage app cache, transcripts folder)."""
    if s.get("home"):
        home = Path(s["home"]).expanduser()
        return home / ".claude.json", home / ".statusline-usage-cache", home / "projects"
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        home = Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser()
        return home / ".claude.json", home / ".statusline-usage-cache", home / "projects"
    return Path.home() / ".claude.json", Path.home() / ".claude" / ".statusline-usage-cache", Path.home() / ".claude" / "projects"


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def claude_code_cache(path):
    """Claude Code's own last usage fetch, from cachedUsageUtilization in ~/.claude.json."""
    d = _read_json(path)
    c = d.get("cachedUsageUtilization") if isinstance(d, dict) else None
    if not isinstance(c, dict) or not isinstance(c.get("utilization"), dict):
        return None
    as_of = _ts(c["fetchedAtMs"] / 1000) if isinstance(c.get("fetchedAtMs"), (int, float)) else None
    windows = {}
    for key, name in (("five_hour", "5h"), ("seven_day", "week")):
        w = c["utilization"].get(key)
        if isinstance(w, dict) and _pct(w.get("utilization")) is not None and _ts(w.get("resets_at")):
            windows[name] = {"used_pct": _pct(w["utilization"]), "resets_at": iso(_ts(w["resets_at"]))}
    if not as_of or not windows:
        return None
    return {"source": f"Claude Code usage cache ({path.name}, cachedUsageUtilization)", "as_of": as_of,
            "windows": windows, "resolution_pts": 1}


def usage_app_cache(path):
    """The Claude Usage menu-bar app's cache: KEY=VALUE lines, refreshed by the app."""
    try:
        kv = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    except OSError:
        return None
    try:
        as_of = _ts(int(kv.get("TIMESTAMP", "")))
    except ValueError:
        return None
    windows = {}
    for used, reset, name in (("UTILIZATION", "RESETS_AT", "5h"), ("WEEKLY_UTILIZATION", "WEEKLY_RESETS_AT", "week")):
        try:
            pct = float(kv.get(used, ""))
        except ValueError:
            continue
        if _ts(kv.get(reset)):
            windows[name] = {"used_pct": pct, "resets_at": iso(_ts(kv[reset]))}
    if not as_of or not windows:
        return None
    profile = kv.get("PROFILE_NAME", "").strip().strip('"')
    return {"source": f"Claude Usage app cache ({path.name}" + (f", profile {profile}" if profile else "") + ")",
            "as_of": as_of, "windows": windows, "resolution_pts": 1}


def claude_tokens(projects, since):
    """Tokens in local Claude Code transcripts since `since`. Each API response is logged once per content block,
    so lines are de-duplicated by (message id, request id), the way ccusage counts them."""
    if not projects.is_dir():
        return None
    seen, t = set(), {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}
    files = 0
    cutoff = since.timestamp()
    for f in projects.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            files += 1
            with open(f, errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    m = o.get("message") if isinstance(o, dict) else None
                    if not isinstance(m, dict) or not isinstance(m.get("usage"), dict) or m.get("model") == "<synthetic>":
                        continue
                    at = _ts(o.get("timestamp"))
                    if not at or at < since:
                        continue
                    key = (m.get("id"), o.get("requestId"))
                    if key != (None, None):
                        if key in seen:
                            continue
                        seen.add(key)
                    for k in t:
                        v = m["usage"].get(k)
                        if isinstance(v, int) and not isinstance(v, bool):
                            t[k] += v
        except OSError:
            continue
    return dict(t, since=iso(since), messages=len(seen), files=files,
                scope="Claude Code transcripts on this computer only (not claude.ai, other devices, or "
                      "`claude -p --no-session-persistence` jobs)")


def read_claude(s, now, tokens=True):
    state, app, projects = claude_paths(s)
    use_app = s.get("usage_app", True) is not False
    cands = [c for c in (claude_code_cache(state), usage_app_cache(app) if use_app else None) if c]
    tried = f"{state}" + (f", {app}" if use_app else "")
    return _finish(s, now, cands, tried, lambda since: claude_tokens(projects, since) if tokens else None)


# Codex -------------------------------------------------------------------------------------------------------------

def codex_sessions(s):
    home = Path(s["home"]).expanduser() if s.get("home") else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home / "sessions"


def _codex_files(sessions, newest_first=True):
    files = []
    for f in sessions.rglob("*.jsonl"):
        try:
            files.append((f.stat().st_mtime, f))
        except OSError:
            continue
    return [f for _, f in sorted(files, reverse=newest_first)]


def _events(f):
    try:
        with open(f, errors="replace") as fh:
            for line in fh:
                if '"token_count"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                p = o.get("payload") if isinstance(o, dict) else None
                if isinstance(p, dict) and p.get("type") == "token_count":
                    yield _ts(o.get("timestamp")), p
    except OSError:
        return


def codex_limits(sessions, limit_id="codex", scan=8):
    """The latest rate_limits Codex logged for `limit_id`. Looks through the newest few session files."""
    best = None
    for f in _codex_files(sessions)[:scan]:
        for at, p in _events(f):
            rl = p.get("rate_limits")
            if not at or not isinstance(rl, dict) or rl.get("limit_id", limit_id) != limit_id:
                continue
            if best and at <= best[0]:
                continue
            windows = {}
            for slot in ("primary", "secondary"):
                w = rl.get(slot)
                if not isinstance(w, dict) or _pct(w.get("used_percent")) is None:
                    continue
                name = CODEX_WINDOWS.get(w.get("window_minutes"), f"{w.get('window_minutes')}min")
                reset = _ts(w.get("resets_at"))
                if reset is None and isinstance(w.get("resets_in_seconds"), (int, float)):
                    reset = at + timedelta(seconds=w["resets_in_seconds"])
                if reset:
                    windows[name] = {"used_pct": _pct(w["used_percent"]), "resets_at": iso(reset)}
            if windows:
                best = (at, windows, rl.get("plan_type"))
    if not best:
        return None
    return {"source": f"Codex session logs ({sessions}, rate_limits {limit_id}"
                      + (f", plan {best[2]}" if best[2] else "") + ")",
            "as_of": best[0], "windows": best[1], "resolution_pts": None}


def codex_tokens(sessions, since):
    """Tokens Codex logged since `since`. Each session file carries a running total, so a turn's tokens are the
    increase over the previous event in the same file."""
    if not sessions.is_dir():
        return None
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    t, files = dict.fromkeys(keys, 0), 0
    for f in _codex_files(sessions):
        try:
            if f.stat().st_mtime < since.timestamp():
                break   # newest first, so every later file is older
        except OSError:
            continue
        files += 1
        prev = None
        for at, p in _events(f):
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            tot = info.get("total_token_usage")
            if not isinstance(tot, dict):
                continue
            if at and at >= since:
                for k in keys:
                    v, before = tot.get(k), (prev or {}).get(k, 0)
                    if isinstance(v, int) and isinstance(before, int) and v >= before:
                        t[k] += v - before
            prev = tot
    return dict(t, since=iso(since), files=files, scope="Codex sessions on this computer only "
                "(not ChatGPT, Codex cloud or other devices)")


def read_codex(s, now, tokens=True):
    sessions = codex_sessions(s)
    c = codex_limits(sessions, s.get("limit_id", "codex")) if sessions.is_dir() else None
    return _finish(s, now, [c] if c else [], str(sessions), lambda since: codex_tokens(sessions, since) if tokens else None)


# Shared ------------------------------------------------------------------------------------------------------------

def _finish(s, now, cands, tried, count_tokens):
    """Pick the freshest limit reading for the pool's window and add token counts."""
    window = s.get("window", "week")
    have = [c for c in cands if window in c["windows"]]
    best = max(have, key=lambda c: c["as_of"], default=None)
    r = {"status": UNAVAILABLE, "window": window, "taken_at": iso(now)}
    if best is None:
        r["note"] = (f"no {window} limit in any source (looked in {tried})" if not cands
                     else f"sources found but none reports a {window} limit")
    else:
        w = best["windows"][window]
        r.update(source=best["source"], as_of=iso(best["as_of"]), windows=best["windows"],
                 resolution_pts=best["resolution_pts"])
        if _ts(w["resets_at"]) <= now:
            r["note"] = (f"latest reading ({iso(best['as_of'])}) is from a {window} window that reset at "
                         f"{w['resets_at']}; the source has not refreshed since, so current use is unknown")
        else:
            r.update(status=FULL, used_pct=w["used_pct"], resets_at=w["resets_at"])
    since = (_ts(r["resets_at"]) - WINDOWS[window]) if r["status"] == FULL else now - WINDOWS[window]
    tok = count_tokens(since)
    if tok and (tok.get("files") or any(tok.get(k) for k in ("input_tokens", "output_tokens"))):
        r["tokens"] = tok
        if r["status"] == UNAVAILABLE:
            r["status"] = PARTIAL
            r["note"] = (r.get("note", "") + "; tokens only, which are not a share of the plan limit").lstrip("; ")
    return r


def read_pool(pool, now, tokens=True):
    s = check_block(pool)
    return (read_claude if s["source"] == "claude" else read_codex)(s, now, tokens)


def summary(r):
    """One line for a reading."""
    if r["status"] == FULL:
        left = 100 - r["used_pct"]
        others = ", ".join(f"{k} {v['used_pct']:g}% used" for k, v in sorted(r["windows"].items()) if k != r["window"])
        line = (f"FULL  {r['window']} {r['used_pct']:g}% used ({left:g}% left), resets {r['resets_at']}"
                + (f"; {others}" if others else "") + f"; as of {r['as_of']} from {r['source']}")
    else:
        line = f"{r['status']}  {r.get('note', '')}"
        if r.get("as_of"):
            line += f" [{r['source']}]"
    tok = r.get("tokens")
    if tok:
        cache = tok.get("cache_read_input_tokens", 0) + tok.get("cache_creation_input_tokens", 0) + tok.get("cached_input_tokens", 0)
        line += (f"\n      tokens since {tok['since']}: {tok['input_tokens']:,} in"
                 + (f" (cache {cache:,})" if cache else "") + f" / {tok['output_tokens']:,} out; {tok['scope']}")
    return line


def apply(pool, r):
    """Write a reading into the pool dict. Returns what changed, as text."""
    keep = {k: r[k] for k in ("status", "taken_at", "source", "as_of", "window", "used_pct", "resets_at", "windows",
                              "note") if k in r}
    if r.get("tokens"):
        keep["tokens"] = {k: v for k, v in r["tokens"].items() if k != "scope"}
    pool["last_sync"] = keep
    if r["status"] != FULL:
        return "readings left as they were (" + r["status"] + ")"
    current = _ts(pool.get("updated_at"))
    if current and current >= _ts(r["as_of"]):
        return f"unchanged: the pool already holds a reading from {pool['updated_at']}, as new or newer"
    pool["remaining_pct"] = round(100 - r["used_pct"], 2)
    pool["resets_at"] = r["resets_at"]
    pool["updated_at"] = r["as_of"]
    pool["source"] = "sync: " + r["source"]
    return f"remaining_pct {pool['remaining_pct']:g}, resets_at {pool['resets_at']}, updated_at {pool['updated_at']}"


def sync(here, now, only=None, auto_only=False, write=True, tokens=True, out=print):
    """Read every pool with a sync block (or just `only`, or just auto ones) and write pools.json.
    Returns the number of pools read."""
    path = here / "pools.json"
    cfg = json.loads(path.read_text())
    pools = [p for p in cfg["pools"] if "sync" in p and (not only or p["id"] in only)
             and (not auto_only or (isinstance(p["sync"], dict) and p["sync"].get("auto")))]
    if only and not pools:
        raise SystemExit("no pool with a sync block among: " + ", ".join(sorted(only)))
    for p in pools:
        r = read_pool(p, now, tokens)
        out(f"sync {p['id']}: {summary(r)}")
        if write:
            out(f"      -> {apply(p, r)}")
    if write and pools:
        path.write_text(json.dumps(cfg, indent=2) + "\n")
    return len(pools)


def auto_sync(here, now, out=print):
    """Before a plan: sync pools with "auto": true. Quiet when there are none."""
    try:
        cfg = json.loads((here / "pools.json").read_text())
    except (OSError, ValueError):
        return 0   # the planner reports a missing or broken file in its own words
    if not any(isinstance(p.get("sync"), dict) and p["sync"].get("auto") for p in cfg.get("pools", [])):
        return 0
    n = sync(here, now, auto_only=True, out=out)
    out("")
    return n


# Readings around one job (allo_exec calls these under --execute). Never written to pools.json, so the reviewed
# plan keeps matching; they go to runs.jsonl.

def job_reading(pool_sync, now):
    s = dict(pool_sync)
    try:
        return (read_claude if s.get("source") == "claude" else read_codex)(s, now, tokens=False)
    except Exception as e:  # a reading must never stop a job
        return {"status": UNAVAILABLE, "note": f"reading failed: {type(e).__name__}: {e}", "taken_at": iso(now)}


def reading_after(pool_sync, ended, clock=lambda: datetime.now(timezone.utc)):
    """Take the after-job reading, waiting up to settle_s for one the source took after the job ended."""
    deadline = time.monotonic() + float(pool_sync.get("settle_s", 0) or 0)
    while True:
        r = job_reading(pool_sync, clock())
        fresh = r.get("as_of") and _ts(r["as_of"]) >= ended
        if fresh or time.monotonic() >= deadline:
            return r
        sleep(min(10.0, max(0.5, deadline - time.monotonic())))


def measure(before, after, started, ended):
    """Share of the plan limit a job used, from the readings around it, per window. Only computed when both
    readings are FULL, cover the same window (same reset time) and the after reading was taken after the job ended.
    The difference is account-wide: any other use of the plan between the two readings is in it too."""
    m = {"window": (before or {}).get("window") or (after or {}).get("window"), "measurable": False, "windows": {}}
    if not before or not after:
        m["why"] = "no readings"
        return m
    m.update(before_as_of=before.get("as_of"), after_as_of=after.get("as_of"),
             source=after.get("source") or before.get("source"), resolution_pts=after.get("resolution_pts"))
    if before.get("status") != FULL or after.get("status") != FULL:
        m["why"] = f"readings were {before.get('status')} / {after.get('status')}, not FULL"
        return m
    if _ts(after["as_of"]) < ended:
        m["why"] = (f"the source had not refreshed since the job ended (latest {after['as_of']}, job ended "
                    f"{iso(ended)}); raise sync settle_s to wait for it")
        return m
    for name, a in after["windows"].items():
        b = before.get("windows", {}).get(name)
        if not b:
            continue
        same = abs((_ts(a["resets_at"]) - _ts(b["resets_at"])).total_seconds()) <= RESET_MATCH_S
        row = {"before_pct": b["used_pct"], "after_pct": a["used_pct"], "resets_at": a["resets_at"]}
        if not same:
            row["why"] = "the window reset between the readings"
        elif a["used_pct"] < b["used_pct"]:
            row["why"] = "the reading went down, which use cannot do"
        else:
            row["used_pts"] = round(a["used_pct"] - b["used_pct"], 4)
        m["windows"][name] = row
    main = m["windows"].get(m["window"])
    if main and "used_pts" in main:
        m.update(measurable=True, used_pts=main["used_pts"],
                 note="account-wide: includes any other use of this plan between "
                      f"{before['as_of']} and {after['as_of']}")
    else:
        m["why"] = (main or {}).get("why", f"no {m['window']} window in both readings")
    return m


def cmd_sync(here, argv, now, out=print):
    ap = argparse.ArgumentParser(prog="allo-router sync", description="Read plan usage from local files for pools "
                                 "with a \"sync\" block, and record it in pools.json.")
    ap.add_argument("--pool", action="append", help="only this pool id (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="print the readings, write nothing")
    ap.add_argument("--no-tokens", action="store_true", help="skip counting tokens in transcripts (faster)")
    a = ap.parse_args(argv)
    n = sync(here, now, set(a.pool) if a.pool else None, write=not a.dry_run, tokens=not a.no_tokens, out=out)
    if not n:
        out("no pool in pools.json has a \"sync\" block; add one, e.g. \"sync\": {\"source\": \"claude\"}. "
            "See `allo-router sync --help`.")
    elif a.dry_run:
        out("dry run: pools.json not changed")
    return 0
