#!/usr/bin/env python3
"""Allo Router upload: the opt-in upload to the hosted dashboard at app.allorouter.com. Off by default.

Nothing is uploaded until you run `allo-router connect` and approve this computer in the browser. This is the only
module that opens a network connection, and only for these commands, or after `run --execute` once connected.

  allo-router connect               approve this computer once in the browser; uploads stay off until you do
  allo-router connect --show        print exactly the JSON the next upload would send, and send nothing
  allo-router upload                send the new lines of runs.jsonl now
  allo-router disconnect            revoke this computer's token, delete its history on the server, forget it here
  allo-router disconnect --keep-history   revoke and forget, but keep the history on the server

What an upload carries, per job: a hashed id, when it started (rounded to the minute), which pool took it (as
pool-1, pool-2...; the pool ids you typed never leave), the plan type (subscription / credit_usd), the provider
(claude / claude_personal / claude_team / codex / grok / gemini / api / other), a short job title (from the task name,
max 48 chars), the model and effort when those are set on the pool's run command, the outcome, why it missed,
and the estimated saving in USD.
It never carries prompts, commands, code, output, file names or paths, readings ("% left"), token counts,
pool labels, hostnames or keys. The payload is built field by field from an allowlist, and checked against it
again before sending; the server rejects anything else.

Once connected, `run --execute` uploads after jobs finish, at most once every 15 seconds, and once more at the
end. A failed upload never fails the run; the lines go on the next one. Every upload is logged in connect.log.
The token, a random salt for the hashes, and the pool numbering live in ~/.config/allo-router/connect.json
(mode 0600; ALLO_ROUTER_CONFIG_DIR overrides the folder), outside the router folder so it is never committed.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import allo
import allo_report

DEFAULT_SERVER = "https://app.allorouter.com"
STATE_FILE = "connect.json"
LOG_FILE = "connect.log"
SCHEMA = 1
MAX_ROWS = 500
DEBOUNCE_S = 15

# The allowlist. The server holds the same lists (hosted/worker/src/schema.js) and rejects anything else.
TOP_KEYS = ("schema", "sent_at", "jobs")
OPTIONAL_TOP_KEYS = ("plans",)
# One row per plan: its current reading and reset time, so the dashboard can show what is left and when it resets.
# "how" says where the reading came from: "auto" (allo-router sync read it from the provider's own files) or
# "manual" (typed in with allo-router set). Nothing here is ever estimated.
PLAN_KEYS = ("pool", "plan_type", "provider", "left_pct", "left_usd", "resets_at", "window", "as_of", "how")
WINDOW_NAMES = ("5h", "week")
HOWS = ("auto", "manual")
JOB_KEYS = ("id", "at", "pool", "plan_type", "provider", "status", "skip", "miss", "est_saved_usd",
            "title", "model", "effort", "heartbeat_at")
STATUSES = ("ok", "failed", "timeout", "error", "skipped", "dry-run", "running")
SKIPS = ("unassigned", "needs-reading", "already-ran", "bad-run-block", "missing-cwd", "declined", "manual", "other")
MISSES = ("failed", "timeout", "error", "unverified-reading", "unassigned", "needs-reading", "bad-run-block",
          "missing-cwd", "other")
PLAN_TYPES = ("subscription", "credit_usd", "orchestrator", "other")
PROVIDERS = ("claude", "claude_personal", "claude_team", "codex", "grok", "gemini", "api", "other")
MODELS = ("opus", "sonnet", "haiku", "gpt-5", "gpt-5-codex", "o3", "o4-mini", "gpt-6.1-sol", "gpt-6-sol", "gpt-5.3-codex", "other")
EFFORTS = ("low", "medium", "high", "very_high", "xhigh")
TITLE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-/:#]{0,47}$")
MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
JOB_ID = re.compile(r"^h:[0-9a-f]{16}$")
POOL_REF = re.compile(r"^pool-[0-9]{1,3}$")


class UploadError(Exception):
    pass


# ---------- local state ----------

def config_dir():
    if os.environ.get("ALLO_ROUTER_CONFIG_DIR"):
        return Path(os.environ["ALLO_ROUTER_CONFIG_DIR"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "allo-router"


def load_state():
    """The connection, or None when this computer is not connected (the default)."""
    path = config_dir() / STATE_FILE
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("token") else None


def save_state(state):
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / STATE_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(state, indent=2) + "\n")
    os.chmod(path, 0o600)


def new_state(server, token, now):
    return {"server": server, "token": token, "salt": secrets.token_hex(32), "pools": {}, "cursors": {},
            "connected_at": minute(now), "last_sent": 0}


def folder_key(here):
    """Which router folder a cursor belongs to. Kept only in the local state file, never sent."""
    return hashlib.sha256(str(Path(here).resolve()).encode()).hexdigest()[:16]


def log_upload(payload, result):
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / LOG_FILE, "a") as f:
        f.write(json.dumps({"logged_at": minute(datetime.now(timezone.utc)), "result": result, "sent": payload}) + "\n")


# ---------- the payload: pure, built from the allowlist ----------

def minute(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def pool_ref(pool_id, pools):
    """pool-1, pool-2... in the order pools are first seen. `pools` (the local numbering) is updated in place."""
    if pool_id is None:
        return None
    key = str(pool_id)
    if key not in pools:
        pools[key] = f"pool-{len(pools) + 1}"
    return pools[key]


def plan_type(kind):
    return kind if kind in ("subscription", "credit_usd", "orchestrator") else "other"


def _run_exe(pool_cfg):
    cmd = (pool_cfg.get("run") or {}).get("command") if isinstance(pool_cfg.get("run"), dict) else None
    if isinstance(cmd, list) and cmd and isinstance(cmd[0], str):
        return os.path.basename(cmd[0]).lower()
    return None


def _personal_home(pool_cfg):
    """True when this Claude pool uses a personal config dir (never reads the label)."""
    sync = pool_cfg.get("sync") if isinstance(pool_cfg.get("sync"), dict) else {}
    run = pool_cfg.get("run") if isinstance(pool_cfg.get("run"), dict) else {}
    env = run.get("env") if isinstance(run.get("env"), dict) else {}
    home = f"{sync.get('home') or ''} {env.get('CLAUDE_CONFIG_DIR') or ''}".lower()
    return "personal" in home


def _cmd_tokens(pool_cfg):
    cmd = (pool_cfg.get("run") or {}).get("command") if isinstance(pool_cfg.get("run"), dict) else None
    return [str(x).lower() for x in cmd] if isinstance(cmd, list) else []


def provider(pool_cfg, kind):
    """An enum read off the pool's config, never its id or label."""
    if kind == "credit_usd":
        return "api"
    sync = pool_cfg.get("sync") if isinstance(pool_cfg.get("sync"), dict) else {}
    source = sync.get("source")
    exe = _run_exe(pool_cfg)
    is_claude = source == "claude" or (exe and (exe == "claude" or exe.startswith("claude-")))
    is_codex = source == "codex" or (exe and (exe == "codex" or exe.startswith("codex-")))
    is_grok = source == "grok" or (exe and (exe == "grok" or exe.startswith("grok-")))
    is_gemini = exe and (exe == "gemini" or exe.startswith("gemini-"))
    if kind == "orchestrator" or is_grok:
        return "grok"
    if is_claude:
        return "claude_personal" if _personal_home(pool_cfg) else "claude_team"
    if is_codex:
        return "codex"
    if is_gemini:
        return "gemini"
    return "other"


def short_title(name):
    """First line of the task name, allowlisted chars, max 48. None when empty or not allowed."""
    if not isinstance(name, str):
        return None
    line = name.strip().split("\n", 1)[0].strip()
    if not line or not TITLE_OK.match(line):
        # Soften: drop disallowed chars and retry
        cleaned = "".join(c for c in line if c.isalnum() or c in " ._-/:#")
        cleaned = cleaned.strip(" ._-/:#")
        if not cleaned or not TITLE_OK.match(cleaned[:48]):
            return None
        line = cleaned
    return line[:48]


def model_and_effort(pool_cfg):
    """Model and effort flags from the pool's run command only (never from prompts)."""
    toks = _cmd_tokens(pool_cfg)
    model = effort = None
    for i, t in enumerate(toks):
        if t in ("--model", "-m") and i + 1 < len(toks):
            cand = toks[i + 1].replace("_", "-")
            if cand in MODELS:
                model = cand
            elif cand.startswith("gpt-5"):
                model = "gpt-5-codex" if "codex" in cand else "gpt-5"
            elif "opus" in cand:
                model = "opus"
            elif "sonnet" in cand:
                model = "sonnet"
            elif "haiku" in cand:
                model = "haiku"
            else:
                model = "other"
        if t in ("--effort",) and i + 1 < len(toks):
            cand = toks[i + 1].replace("-", "_")
            if cand in EFFORTS:
                effort = cand
        if t.startswith("model_reasoning_effort="):
            cand = t.split("=", 1)[1].replace("-", "_")
            if cand in EFFORTS:
                effort = cand
        if "model_reasoning_effort" in t and i + 1 < len(toks) and toks[i + 1] in EFFORTS:
            effort = toks[i + 1]
    # Codex default when the exe is codex and no model flag
    if model is None and (_run_exe(pool_cfg) or "").startswith("codex"):
        model = "gpt-5-codex"
    return model, effort


def job_id(salt, e):
    msg = json.dumps([str(e.get("task_id")), e.get("at"), e.get("pool")]).encode()
    return "h:" + hmac.new(bytes.fromhex(salt), msg, hashlib.sha256).hexdigest()[:16]


def misses(e):
    """The report's misroute rules (allo_report.misroutes) as enums, without any of its text."""
    out = []
    if e.get("status") in allo_report.FAILED:
        out.append(e["status"])
    if e.get("status") in allo_report.STARTED and e.get("pool_flags"):
        out.append("unverified-reading")
    if e.get("status") == "skipped" and e.get("skip_kind", "other") in allo_report.MISROUTE_SKIPS:
        k = e.get("skip_kind", "other")
        out.append(k if k in MISSES else "other")
    return out


def build_payload(entries, cfg, salt, pools, now, est_now=None, share_readings=False):
    """The upload for these runs.jsonl entries. Every field is copied into a new dict by name; nothing from an
    entry is passed through whole. `pools` is the local pool numbering and gains any new pool."""
    by_id = {p.get("id"): p for p in (cfg or {}).get("pools", [])}
    prices = allo_report.Prices(cfg, entries, {}, "api")
    jobs = []
    for e in entries:
        pid = e.get("pool")
        pcfg = by_id.get(pid, {})
        kind = pcfg.get("kind") or e.get("pool_kind")
        status = e.get("status") if e.get("status") in STATUSES else None
        if status is None:
            continue
        saved = None  # API value (est.): only a cost the CLI itself reported, never a guess
        if status in allo_report.STARTED:
            cost = (e.get("usage") or {}).get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
                saved = round(float(cost), 4)
        skip = None
        if status == "skipped":
            skip = e.get("skip_kind") if e.get("skip_kind") in SKIPS else "other"
        model, effort = model_and_effort(pcfg) if pid is not None else (None, None)
        jobs.append({
            "id": job_id(salt, e),
            "at": minute(e["_at"]),
            "pool": pool_ref(pid, pools),
            "plan_type": plan_type(kind) if pid is not None else None,
            "provider": provider(pcfg, kind) if pid is not None else None,
            "status": status,
            "skip": skip,
            "miss": misses(e),
            "est_saved_usd": saved,
            "title": short_title(e.get("task_name")),
            "model": model,
            "effort": effort,
            "heartbeat_at": minute(e["_at"]) if e.get("status") == "running" else None,
        })
    payload = {"schema": SCHEMA, "sent_at": minute(now), "jobs": jobs}
    plans = plan_rows(cfg, pools) if share_readings else []
    if plans:
        payload["plans"] = plans
    check_payload(payload)
    return payload


def _num(v, lo, hi):
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else None


def _minute_or_none(text):
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return minute(d if d.tzinfo else d.replace(tzinfo=timezone.utc))


def plan_rows(cfg, pools):
    """Each configured pool's latest reading, copied field by field. Pools with neither a reading nor a reset
    time are left out. `pools` (the local numbering) gains any new pool."""
    out = []
    for p in (cfg or {}).get("pools", []):
        if p.get("id") is None:
            continue
        left_pct = _num(p.get("remaining_pct"), 0, 100)
        left_usd = _num(p.get("remaining_usd"), 0, 1e7)
        resets = _minute_or_none(p.get("resets_at")) if p.get("resets_at") else None
        if left_pct is None and left_usd is None and resets is None:
            continue
        as_of = _minute_or_none(p.get("updated_at")) if p.get("updated_at") else None
        src = str(p.get("source") or "")
        how = "auto" if src.startswith("sync:") else ("manual" if as_of else None)
        sync = p.get("sync") if isinstance(p.get("sync"), dict) else {}
        window = sync.get("window", "week") if sync else None
        out.append({
            "pool": pool_ref(p["id"], pools), "plan_type": plan_type(p.get("kind")), "provider": provider(p, p.get("kind")),
            "left_pct": left_pct, "left_usd": left_usd, "resets_at": resets,
            "window": window if window in WINDOW_NAMES else None, "as_of": as_of, "how": how,
        })
    return out[:50]


def check_payload(payload):
    """Raise UploadError unless the payload holds exactly the allowlisted keys and values. Run before every send."""
    if not isinstance(payload, dict) or not (set(TOP_KEYS) <= set(payload) <= set(TOP_KEYS + OPTIONAL_TOP_KEYS)):
        raise UploadError(f"payload keys {sorted(payload) if isinstance(payload, dict) else payload!r} "
                          f"are not exactly {list(TOP_KEYS)}")
    if payload["schema"] != SCHEMA or not MINUTE.match(str(payload["sent_at"])):
        raise UploadError("bad schema or sent_at")
    if not isinstance(payload["jobs"], list) or len(payload["jobs"]) > MAX_ROWS:
        raise UploadError(f"jobs must be a list of at most {MAX_ROWS}")
    for j in payload["jobs"]:
        if not isinstance(j, dict) or set(j) != set(JOB_KEYS):
            raise UploadError(f"job keys {sorted(j) if isinstance(j, dict) else j!r} are not exactly {list(JOB_KEYS)}")
        ok = (JOB_ID.match(str(j["id"])) and MINUTE.match(str(j["at"]))
              and (j["pool"] is None or POOL_REF.match(str(j["pool"])))
              and j["plan_type"] in PLAN_TYPES + (None,) and j["provider"] in PROVIDERS + (None,)
              and j["status"] in STATUSES and j["skip"] in SKIPS + (None,)
              and isinstance(j["miss"], list) and all(m in MISSES for m in j["miss"])
              and (j["est_saved_usd"] is None or (isinstance(j["est_saved_usd"], (int, float))
                                                  and not isinstance(j["est_saved_usd"], bool)))
              and (j["title"] is None or (isinstance(j["title"], str) and TITLE_OK.match(j["title"])))
              and j["model"] in MODELS + (None,) and j["effort"] in EFFORTS + (None,)
              and (j["heartbeat_at"] is None or MINUTE.match(str(j["heartbeat_at"]))))
        if not ok:
            raise UploadError(f"job {j.get('id')!r} has a value outside the allowlist")
    plans = payload.get("plans", [])
    if not isinstance(plans, list) or len(plans) > 50:
        raise UploadError("plans must be a list of at most 50")
    num = lambda v, hi: v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= hi)
    for r in plans:
        if not isinstance(r, dict) or set(r) != set(PLAN_KEYS):
            raise UploadError(f"plan keys {sorted(r) if isinstance(r, dict) else r!r} are not exactly {list(PLAN_KEYS)}")
        ok = (POOL_REF.match(str(r["pool"])) and r["plan_type"] in PLAN_TYPES and r["provider"] in PROVIDERS
              and num(r["left_pct"], 100) and num(r["left_usd"], 1e7)
              and all(r[k] is None or MINUTE.match(str(r[k])) for k in ("resets_at", "as_of"))
              and r["window"] in WINDOW_NAMES + (None,) and r["how"] in HOWS + (None,))
        if not ok:
            raise UploadError(f"plan {r.get('pool')!r} has a value outside the allowlist")


# ---------- reading new runs ----------

def new_entries(here, offset, limit=MAX_ROWS):
    """(entries, new offset) for complete runs.jsonl lines after byte `offset`, at most `limit` of them."""
    log = Path(here) / allo_report.RUN_LOG
    if not log.exists():
        return [], 0
    data = log.read_bytes()
    if offset > len(data):
        offset = 0  # the log was replaced; the server ignores rows it already has
    entries, pos = [], offset
    while len(entries) < limit:
        end = data.find(b"\n", pos)
        if end < 0:
            break
        line = data[pos:end].decode("utf-8", "replace").strip()
        pos = end + 1
        if not line:
            continue
        try:
            e = json.loads(line)
            e["_at"] = allo.parse(e["at"])
        except (ValueError, KeyError, TypeError, AttributeError, SystemExit):
            continue
        entries.append(e)
    return entries, pos


def _config(here):
    try:
        cfg = json.loads((Path(here) / "pools.json").read_text())
    except (OSError, ValueError):
        cfg = None
    try:
        tasks = json.loads((Path(here) / "tasks.json").read_text())["tasks"]
        est_now = {(str(t["id"]), t["name"]): t["est_usd"] for t in tasks
                   if isinstance(t.get("est_usd"), (int, float))}
    except (OSError, ValueError, KeyError, TypeError):
        est_now = {}
    return cfg, est_now


def next_payload(here, state, now):
    """(payload, new offset, pool numbering) for the next upload from this folder. Changes nothing."""
    offset = state.get("cursors", {}).get(folder_key(here), 0)
    entries, pos = new_entries(here, offset)
    cfg, est_now = _config(here)
    pools = dict(state.get("pools", {}))
    return build_payload(entries, cfg, state["salt"], pools, now, est_now,
                         share_readings=bool(state.get("share_readings"))), pos, pools


# ---------- transport ----------

def http(method, url, body=None, token=None, timeout=10):
    """(status, parsed JSON body or None). The only function in Allo Router that opens a connection."""
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", f"allo-router/{allo.__version__}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def upload_once(here, state, now, transport=http, clock=time.time):
    """Send one batch. Returns (rows sent, more waiting). Saves the cursor only after the server accepts it."""
    payload, pos, pools = next_payload(here, state, now)
    # A new plan reading (from sync or set) goes up on its own, even with no new jobs; an unchanged one doesn't.
    plans_changed = bool(payload.get("plans")) and payload.get("plans") != state.get("last_plans")
    if not payload["jobs"] and not plans_changed:
        state.setdefault("cursors", {})[folder_key(here)] = pos
        save_state(state)
        return 0, False
    check_payload(payload)
    status, resp = transport("POST", state["server"].rstrip("/") + "/v1/jobs", payload, state["token"])
    log_upload(payload, status)
    if status != 200:
        why = (resp or {}).get("error") if isinstance(resp, dict) else None
        raise UploadError(f"server answered {status}" + (f": {why}" if why else ""))
    state.setdefault("cursors", {})[folder_key(here)] = pos
    state["pools"] = pools
    if payload.get("plans"):
        state["last_plans"] = payload["plans"]
    state["last_sent"] = clock()
    save_state(state)
    return len(payload["jobs"]), len(payload["jobs"]) == MAX_ROWS


class AfterJobs:
    """Uploads during `run --execute` once connected: after a job, at most once per DEBOUNCE_S, and at the end.
    Never raises; a failed upload is one printed line, and the lines go on the next upload."""

    def __init__(self, here, state, out=print, transport=http, clock=time.time, sleep=time.sleep):
        self.here, self.state, self.out = here, state, out
        self.transport, self.clock, self.sleep = transport, clock, sleep
        self.pending = False

    @classmethod
    def if_connected(cls, here, **kw):
        state = load_state()
        return cls(here, state, **kw) if state else None

    def _send(self):
        self.pending = False
        try:
            n, _ = upload_once(self.here, self.state, datetime.now(timezone.utc), self.transport, self.clock)
            if n:
                self.out(f"      uploaded {n} job row(s) to {self.state['server']}")
        except Exception as e:  # never fail or stop a run over an upload
            self.state["last_sent"] = self.clock()
            self.out(f"      upload skipped ({e}); it goes with the next one")

    def job_done(self):
        if self.clock() - self.state.get("last_sent", 0) >= DEBOUNCE_S:
            self._send()
        else:
            self.pending = True

    def finish(self):
        if not self.pending:
            return
        wait = DEBOUNCE_S - (self.clock() - self.state.get("last_sent", 0))
        if wait > 0:
            self.sleep(wait)
        self._send()


# ---------- commands ----------

def cmd_connect(here, argv, now, transport=http, out=print, err=None, sleep=time.sleep, open_url=None):
    err = err or (lambda s: print(s, file=sys.stderr))
    ap = argparse.ArgumentParser(prog="allo-router connect", description="Opt in to the hosted dashboard: approve "
                                 "this computer once in the browser. Uploads stay off until you do.")
    ap.add_argument("--show", action="store_true", help="print exactly the JSON the next upload would send; sends nothing")
    ap.add_argument("--server", default=os.environ.get("ALLO_ROUTER_SERVER", DEFAULT_SERVER),
                    help=f"dashboard server (default {DEFAULT_SERVER})")
    ap.add_argument("--no-browser", action="store_true", help="print the approval link instead of opening it")
    ap.add_argument("--share-readings", choices=("on", "off"),
                    help="on: also send each plan's %% or $ left, reset time and when it was read, so the dashboard "
                         "can show them. Off by default. Labels and sources still never leave.")
    a = ap.parse_args(argv)
    state = load_state()
    if a.share_readings:
        if state is None:
            out("Not connected yet. Run `allo-router connect` first, then turn readings on.")
            return 1
        state["share_readings"] = a.share_readings == "on"
        state.pop("last_plans", None)
        save_state(state)
        out("Plan readings will be sent with uploads." if state["share_readings"] else "Plan readings are no longer sent.")
        return 0
    if a.show:
        if state is None:
            err("Not connected, so nothing is uploaded. This is what the first upload would send after "
                "`allo-router connect`; the hashed ids will differ, because connecting makes a new salt.")
            state = {"salt": secrets.token_hex(32), "pools": {}, "cursors": {}}
        payload, _, _ = next_payload(here, state, now)
        out(json.dumps(payload, indent=2))
        return 0
    if state:
        out(f"Already connected to {state['server']}. `allo-router disconnect` to stop.")
        return 0
    server = a.server.rstrip("/")
    try:
        status, dev = transport("POST", server + "/v1/device", {})
    except OSError as e:
        out(f"could not reach {server}: {e}")
        return 1
    if status != 200 or not isinstance(dev, dict) or "device_code" not in dev:
        out(f"{server} did not start a connection (status {status})")
        return 1
    link = dev.get("verification_uri_complete") or f"{server}/connect?code={dev['user_code']}"
    out(f"To connect this computer, open {link}")
    out(f"sign in, check the code {dev['user_code']} and approve. Waiting...")
    if not a.no_browser:
        try:
            (open_url or __import__("webbrowser").open)(link)
        except Exception:
            pass
    deadline = time.monotonic() + float(dev.get("expires_in", 900))
    interval = max(1.0, float(dev.get("interval", 5)))
    while time.monotonic() < deadline:
        sleep(interval)
        try:
            status, resp = transport("POST", server + "/v1/device/token", {"device_code": dev["device_code"]})
        except OSError:
            continue
        if status == 200 and isinstance(resp, dict) and resp.get("token"):
            save_state(new_state(server, resp["token"], now))
            out(f"Connected as {resp.get('computer', 'this computer')}. Uploads start after your next "
                "`run --execute`, or now with `allo-router upload`.")
            out("See exactly what goes first with `allo-router connect --show`.")
            return 0
        error = (resp or {}).get("error") if isinstance(resp, dict) else None
        if error in ("access_denied", "expired_token"):
            out("The connection was " + ("declined." if error == "access_denied" else "not approved in time."))
            return 1
        if error == "slow_down":
            interval += 5
    out("The connection was not approved in time. Run `allo-router connect` again.")
    return 1


def cmd_upload(here, argv, now, transport=http, out=print):
    ap = argparse.ArgumentParser(prog="allo-router upload", description="Send the new lines of runs.jsonl to the "
                                 "hosted dashboard now. Only works after `allo-router connect`.")
    ap.parse_args(argv)
    state = load_state()
    if state is None:
        out("Not connected; nothing was sent. Uploads are off until you run `allo-router connect`.")
        return 1
    total = 0
    try:
        for _ in range(100):
            n, more = upload_once(here, state, now, transport)
            total += n
            if not more:
                break
    except (UploadError, OSError) as e:
        out(f"upload failed after {total} row(s): {e}")
        return 1
    out(f"uploaded {total} job row(s) to {state['server']}" if total else "nothing new to upload")
    return 0


def cmd_disconnect(here, argv, now, transport=http, out=print):
    ap = argparse.ArgumentParser(prog="allo-router disconnect", description="Revoke this computer's upload token, "
                                 "delete its history on the server and forget the connection here.")
    ap.add_argument("--keep-history", action="store_true", help="keep this computer's job rows on the server")
    a = ap.parse_args(argv)
    state = load_state()
    if state is None:
        out("Not connected; nothing to do.")
        return 0
    code = 0
    try:
        status, _ = transport("POST", state["server"].rstrip("/") + "/v1/disconnect",
                              {"keep_history": a.keep_history}, state["token"])
        if status == 200:
            out("Token revoked" + ("; history kept on the server." if a.keep_history else " and history deleted on the server."))
        elif status == 401:
            out("The server no longer knew this token (already revoked).")
        else:
            out(f"The server answered {status}; revoke this computer on the web page too.")
            code = 1
    except OSError as e:
        out(f"Could not reach {state['server']} ({e}); revoke this computer on the web page too.")
        code = 1
    (config_dir() / STATE_FILE).unlink(missing_ok=True)
    out("Forgot the connection on this computer. Nothing more will be uploaded.")
    return code
