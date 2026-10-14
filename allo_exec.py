#!/usr/bin/env python3
"""Allo Router executor: runs exactly the plan you reviewed, through a pluggable backend.

`run` on its own is a preview: it prints the exact command each job would run, starts nothing, and saves that
plan to plan.json with its hash, the time it was made and a fingerprint of the readings it used.
`run --plan plan.json --execute` runs that saved plan and nothing else. It refuses, before starting anything,
if the file was edited, is older than --max-plan-age, or the config, readings or any job's command no longer
match. It asks before each job (or takes --yes), runs it, and records the result in runs.jsonl with the job's
output under runs/. It never edits pools.json or tasks.json: readings stay yours to record with
`allo-router set`, because a finished job does not tell you how much of a plan it used.

Each pool opts in to execution with a "run" block in pools.json. A pool without one is never run, and the
starter config puts every pool on the dry-run backend until you switch it to "shell".

  allo-router run                               preview, and save the plan to plan.json
  allo-router run --plan plan.json --execute    run that plan, asking before each job
  allo-router run --plan plan.json --execute --yes   without asking (needed when there is no terminal)
  allo-router run --plan plan.json              check a saved plan still matches, start nothing
  allo-router run --only 1,3                    preview only these task ids
  allo-router run --allow-unverified            also plan on pools flagged STALE / NO READING / RESET PASSED
  allo-router run --rerun                       also plan jobs that already finished ok in runs.jsonl
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import allo

RUN_LOG = "runs.jsonl"
RUN_DIR = "runs"
DEFAULT_TIMEOUT_S = 1800
FAILED = ("failed", "timeout", "error")
PLACEHOLDER = re.compile(r"\{(prompt|task_id|task_name|tier)\}")


class BackendError(Exception):
    """A pool's run block is invalid. The job is skipped and the reason printed."""


class DryRunBackend:
    """Starts nothing, even under --execute. For a pool you want planned and logged but not run yet.

    run block: {"backend": "dry-run"}, optionally with the shell backend's "command", "env", "cwd" and "timeout_s".
    Those are only checked and shown, so once the dry run prints the command you want, changing "backend" to
    "shell" is the whole opt-in. The starter config puts every pool on this backend.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.would = ShellBackend(cfg) if "command" in cfg else None

    def describe(self, job):
        if self.would is None:
            return "dry-run backend: nothing starts, even with --execute"
        return ('dry-run backend: nothing starts, even with --execute. With "backend": "shell" it would run: '
                + self.would.describe(job))

    def run(self, job):
        return {"status": "dry-run", "returncode": None, "stdout": "", "stderr": ""}


class ShellBackend:
    """Runs a CLI such as `claude -p` from an argv list. Never goes through a shell, so a prompt cannot inject one.

    run block: {"backend": "shell", "command": ["claude", "-p", "{prompt}"], "env": {...}, "cwd": "...", "timeout_s": 1800}
    Items in `command` may hold {prompt}, {task_id}, {task_name} and {tier}. With no {prompt}, the prompt goes on stdin.
    `env` adds variables on top of yours (for example which config dir a CLI uses). Never put secrets in pools.json.
    `cwd` (or a task's own `cwd`) is relative to the config folder, which is also the default.
    """

    def __init__(self, cfg):
        cmd = cfg.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) and c for c in cmd):
            raise BackendError('shell backend needs "command" as a list of strings, e.g. ["claude", "-p", "{prompt}"]')
        env = cfg.get("env") or {}
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise BackendError('shell backend "env" must map names to string values')
        timeout = cfg.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise BackendError('shell backend "timeout_s" must be a positive number of seconds')
        self.command, self.env, self.timeout = cmd, env, timeout

    def argv(self, job):
        t = job["task"]
        subs = {"prompt": job["prompt"], "task_id": str(t["id"]), "task_name": t["name"], "tier": t["tier"]}
        # One pass per item, so text inside a substituted prompt is never substituted again.
        return [PLACEHOLDER.sub(lambda m: subs[m.group(1)], c) for c in self.command]

    def uses_stdin(self):
        return not any("{prompt}" in c for c in self.command)

    def env_for_run(self):
        return {**os.environ, **self.env}

    def flag_risk(self, job):
        """A filled-in argument that starts with '-' would be read by the CLI as an option, not as text.
        Allowed only after a '--' item, which tells most CLIs that options have ended."""
        filled = self.argv(job)
        for i, (raw, arg) in enumerate(zip(self.command, filled)):
            if raw != arg and arg.startswith("-") and "--" not in self.command[:i]:
                return f"{raw} becomes {arg[:30]!r}, which the CLI would read as an option; reword it or put \"--\" before it in command"
        return None

    def executable(self):
        """Full path of the command, or None when it is not installed (looked up on the PATH the job will get)."""
        return shutil.which(self.command[0], path=self.env_for_run().get("PATH"))

    def describe(self, job):
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(self.env.items()))
        line = (env + " " if env else "") + shlex.join(self.argv(job))
        if self.uses_stdin():
            line += "  (prompt on stdin)"
        if job["cwd"]:
            line += f"  (in {job['cwd']})"
        if self.executable() is None:
            line += f"  [NOT FOUND: {self.command[0]} is not installed or not on PATH]"
        risk = self.flag_risk(job)
        if risk:
            line += f"  [WILL NOT RUN: {risk}]"
        return line

    def run(self, job):
        exe = self.executable()
        if exe is None:
            return {"status": "error", "returncode": None, "stdout": "", "stderr": f"command not found: {self.command[0]}"}
        risk = self.flag_risk(job)
        if risk:
            return {"status": "error", "returncode": None, "stdout": "", "stderr": risk}
        try:
            p = subprocess.run([exe] + self.argv(job)[1:], input=job["prompt"] if self.uses_stdin() else None,
                               capture_output=True, text=True, cwd=job["cwd"], timeout=self.timeout,
                               env=self.env_for_run())
        except subprocess.TimeoutExpired as e:
            return {"status": "timeout", "returncode": None, "stdout": _text(e.stdout),
                    "stderr": _text(e.stderr) + f"\nstopped after {self.timeout}s (timeout_s)"}
        except OSError as e:
            return {"status": "error", "returncode": None, "stdout": "", "stderr": str(e)}
        return {"status": "ok" if p.returncode == 0 else "failed", "returncode": p.returncode,
                "stdout": p.stdout, "stderr": p.stderr}


def _text(b):
    if b is None:
        return ""
    return b.decode(errors="replace") if isinstance(b, bytes) else b


# Backend name in a pool's run block -> class taking that run block. Add your own with register_backend.
BACKENDS = {"dry-run": DryRunBackend, "shell": ShellBackend}


def register_backend(name, cls):
    """Make `cls` available as {"backend": name}. cls(run_block) may raise BackendError to reject its config.
    Instances need describe(job) -> str and run(job) -> dict with status ('ok', 'failed', 'timeout', 'error'
    or 'dry-run'), returncode, stdout and stderr. A job is a dict: task, pool, note, prompt, cwd, here (the
    config folder), backend, skip. describe() must not start anything: it is what a dry run prints."""
    BACKENDS[name] = cls


def make_backend(run_cfg):
    if not isinstance(run_cfg, dict):
        raise BackendError("run block must be an object")
    name = run_cfg.get("backend")
    if name not in BACKENDS:
        raise BackendError(f"unknown backend {name!r}; known: {', '.join(sorted(BACKENDS))}")
    return BACKENDS[name](run_cfg)


def previous_ok(here, plan=None):
    """(task id, task name) -> time of its last ok run, from runs.jsonl. With `plan`, only runs of that plan.
    The key leaves out the prompt on purpose: editing a finished job's prompt does not make it run again."""
    done, log = {}, here / RUN_LOG
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("status") == "ok" and (plan is None or e.get("plan") == plan):
                done[(str(e.get("task_id")), e.get("task_name"))] = e.get("at")
    return done


def select(cfg, tasks, here, now, only=None, allow_unverified=False, rerun=False):
    """Turn the plan into jobs in priority order. Each job carries its backend or the reason it is skipped."""
    rows, plan, _ = allo.build_plan(cfg, tasks, now)
    flags = {r["p"]["id"]: r["flags"] for r in rows}
    pools = {p["id"]: p for p in cfg["pools"]}
    done = previous_ok(here)
    jobs = []
    for t, pid, note, _ in plan:
        if only and str(t["id"]) not in only:
            continue
        run_cfg = pools[pid].get("run") if pid else None
        cwd = t.get("cwd") or (run_cfg.get("cwd") if isinstance(run_cfg, dict) else None)
        # Jobs run in the config folder unless told otherwise, never wherever allo-router happened to be started.
        job = {"task": t, "pool": pid, "note": note, "prompt": t.get("prompt") or t["name"],
               "cwd": str(here / cwd) if cwd else str(here), "here": str(here), "backend": None, "skip": None}
        key = (str(t["id"]), t["name"])
        if pid is None:
            job["skip"] = "unassigned: no pool fits this job"
        elif flags[pid] and not allow_unverified:
            job["skip"] = (f"pool needs a fresh reading ({', '.join(flags[pid])}): "
                           f"run `allo-router set {pid} ...` first, or pass --allow-unverified")
        elif key in done and not rerun:
            job["skip"] = f"already ran ok at {done[key]}; pass --rerun to run it again"
        elif run_cfg is None:
            job["skip"] = f"pool {pid} has no run block; start this job yourself"
        else:
            try:
                job["backend"] = make_backend(run_cfg)
            except BackendError as e:
                job["skip"] = f"bad run block on {pid}: {e}"
        jobs.append(job)
    return jobs


# Saved plans. A preview saves the plan it printed; `run --plan FILE --execute` runs exactly that plan or nothing.

PLAN_FILE = "plan.json"
PLAN_FORMAT = "allo-router-plan/1"
PLAN_KEYS = ("format", "created_at", "config_dir", "options", "config_hash", "readings_fingerprint", "jobs", "hash")
DEFAULT_PLAN_AGE_MIN = 60
FUTURE_SKEW_S = 300
# Pool fields `allo-router set` writes. If any changed, the plan was made on other readings.
READING_KEYS = ("remaining_pct", "remaining_usd", "resets_at", "updated_at", "source")


class PlanError(Exception):
    """A saved plan cannot run as it is. Nothing starts; the message says why."""


def digest(data):
    return "sha256:" + hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprints(cfg, tasks):
    """(config hash, readings fingerprint). Readings are what `set` changes; config is everything else."""
    readings = {p["id"]: {k: p.get(k) for k in READING_KEYS} for p in cfg["pools"]}
    pools = [{k: v for k, v in p.items() if k not in READING_KEYS} for p in cfg["pools"]]
    return digest({"config": dict(cfg, pools=pools), "tasks": tasks}), digest(readings)


def job_record(job):
    """What a reviewed job promises: which pool, which prompt, which folder, which command."""
    t = job["task"]
    return {"task_id": str(t["id"]), "task_name": t["name"], "pool": job["pool"], "prompt": job["prompt"],
            "cwd": job["cwd"], "command": job["backend"].describe(job) if job["backend"] else None,
            "skip": job["skip"]}


def save_plan(path, here, cfg, tasks, jobs, now, options):
    """Write the previewed plan to `path` and return its hash."""
    config_hash, readings = fingerprints(cfg, tasks)
    plan = {"format": PLAN_FORMAT, "allo_router": allo.__version__, "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "config_dir": str(here.resolve()), "options": options, "config_hash": config_hash,
            "readings_fingerprint": readings, "jobs": [job_record(j) for j in jobs]}
    plan["hash"] = digest(plan)
    Path(path).write_text(json.dumps(plan, indent=2) + "\n")
    return plan["hash"]


def load_plan(path):
    try:
        plan = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise PlanError(f"cannot read it: {e}")
    if not isinstance(plan, dict) or plan.get("format") != PLAN_FORMAT or not all(k in plan for k in PLAN_KEYS):
        raise PlanError(f"not an Allo Router plan file ({PLAN_FORMAT})")
    if plan["hash"] != digest({k: v for k, v in plan.items() if k != "hash"}):
        raise PlanError("it was changed after it was saved (hash does not match); preview again to save a new one")
    return plan


def jobs_from_plan(plan, here, cfg, tasks, now, max_age_min):
    """The saved plan's jobs, checked against the clock, config, readings and run log as they are now.
    Raises PlanError if anything that was reviewed would come out differently."""
    age = (now - allo.parse(plan["created_at"])).total_seconds()
    if age < -FUTURE_SKEW_S:
        raise PlanError(f"it was saved at {plan['created_at']}, which is in the future; check the clock")
    if age > max_age_min * 60:
        raise PlanError(f"it is stale: saved {age / 60:.0f} minutes ago, limit {max_age_min:g} (--max-plan-age); "
                        "preview again")
    if plan["config_dir"] != str(here.resolve()):
        raise PlanError(f"it was saved for {plan['config_dir']}, not {here.resolve()}")
    config_hash, readings = fingerprints(cfg, tasks)
    if plan["readings_fingerprint"] != readings:
        raise PlanError("readings in pools.json changed since it was saved; preview again to plan on the new ones")
    if plan["config_hash"] != config_hash:
        raise PlanError("pools.json or tasks.json changed since it was saved; preview again")
    o = plan["options"]
    current = {str(j["task"]["id"]): j for j in
               select(cfg, tasks, here, now, set(o["only"]) if o["only"] else None, o["allow_unverified"], o["rerun"])}
    done, done_in_plan = previous_ok(here), previous_ok(here, plan["hash"])
    jobs = []
    for saved in plan["jobs"]:
        job = current.get(saved["task_id"])
        if job is None:
            raise PlanError(f"task #{saved['task_id']} is in it but no longer planned")
        key = (saved["task_id"], saved["task_name"])
        job["plan"] = plan["hash"]
        if saved["skip"]:
            job["skip"], job["backend"] = saved["skip"], None   # not reviewed as runnable, so it never runs
        elif key in done_in_plan:
            job["skip"], job["backend"] = f"already ran ok under this plan at {done_in_plan[key]}", None
        elif key in done and not o["rerun"]:
            job["skip"], job["backend"] = f"already ran ok at {done[key]}, after this plan was saved", None
        elif job["skip"]:
            raise PlanError(f"#{saved['task_id']} would now be skipped: {job['skip']}")
        else:
            changed = [k for k, v in job_record(job).items() if saved.get(k) != v]
            if changed:
                raise PlanError(f"#{saved['task_id']} no longer matches it ({', '.join(changed)} changed)")
        jobs.append(job)
    return jobs


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(here, job, res, started, secs):
    """Append one line to runs.jsonl and keep the job's output in runs/. Returns the output file, if any."""
    t, out = job["task"], None
    if res["status"] != "dry-run":
        (here / RUN_DIR).mkdir(exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(t["id"]))
        out = here / RUN_DIR / f"{started.replace(':', '').replace('-', '')}-task{safe_id}.log"
        out.write_text(f"# task {t['id']}: {t['name']}\n# pool {job['pool']}\n# {job['backend'].describe(job)}\n"
                       f"# status {res['status']}, exit {res['returncode']}\n\n"
                       f"## stdout\n{res['stdout']}\n## stderr\n{res['stderr']}\n")
    entry = {"at": started, "task_id": t["id"], "task_name": t["name"], "pool": job["pool"],
             "backend": type(job["backend"]).__name__, "status": res["status"], "returncode": res["returncode"],
             "seconds": round(secs, 1), "output": str(out.relative_to(here)) if out else None,
             "plan": job.get("plan")}
    with open(here / RUN_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return out


def run_jobs(jobs, here, execute=False, confirm=None, out=print):
    """Print every job; under execute, run the runnable ones in order. Returns the number that failed."""
    failed = 0
    for job in jobs:
        t = job["task"]
        target = f"{job['pool']} ({job['note']})" if job["note"] else job["pool"] or "UNASSIGNED"
        out(f" #{t['id']:<2} {t['name'][:52]:<53} -> {target}")
        if job["skip"]:
            out(f"      skip: {job['skip']}")
            continue
        out(f"      {job['backend'].describe(job)}")
        if not execute:
            continue
        if job["cwd"] and not Path(job["cwd"]).is_dir():
            out(f"      skip: working folder {job['cwd']} does not exist")
            continue
        if confirm and not confirm(job):
            out("      skip: you said no")
            continue
        started, t0 = _stamp(), time.monotonic()
        try:
            res = job["backend"].run(job)
        except Exception as e:  # a plugged-in backend must not take down the rest of the run
            res = {"status": "error", "returncode": None, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
        path = record(here, job, res, started, time.monotonic() - t0)
        rc = "" if res["returncode"] is None else f", exit {res['returncode']}"
        out(f"      {res['status']}{rc}" + (f", output in {path}" if path else ""))
        if res["status"] in FAILED:
            failed += 1
            if res["stderr"].strip():
                out("      " + res["stderr"].strip().splitlines()[-1])
    return failed


def ask(job):
    return input(f"      run #{job['task']['id']} on {job['pool']}? [y/N] ").strip().lower() in ("y", "yes")


def cmd_run(here, argv, now, confirm=ask, out=print, interactive=None):
    """`allo-router run`. Returns the process exit code: 0 fine, 1 a job failed, 2 refused to start."""
    ap = argparse.ArgumentParser(prog="allo-router run", description="Preview the plan's jobs and save the plan "
                                 "(default), or run a saved plan with --plan FILE --execute.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print what would run and start nothing (default)")
    mode.add_argument("--execute", action="store_true", help="actually start the jobs of the --plan file")
    ap.add_argument("--yes", action="store_true", help="with --execute, do not ask before each job")
    ap.add_argument("--plan", metavar="FILE", help="use this saved plan instead of planning again; "
                    "required with --execute")
    ap.add_argument("--save-plan", metavar="FILE",
                    help=f"where a preview saves its plan (default: {PLAN_FILE} in the config folder)")
    ap.add_argument("--max-plan-age", type=float, default=DEFAULT_PLAN_AGE_MIN, metavar="MINUTES",
                    help=f"refuse a saved plan older than this (default {DEFAULT_PLAN_AGE_MIN})")
    ap.add_argument("--only", help="comma-separated task ids to consider")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="also run on pools flagged STALE, NO READING, NO RESET TIME or RESET PASSED")
    ap.add_argument("--rerun", action="store_true", help="run jobs even if runs.jsonl shows they finished ok")
    a = ap.parse_args(argv)
    if a.yes and not a.execute:
        ap.error("--yes only makes sense with --execute")
    if a.plan and (a.only or a.allow_unverified or a.rerun):
        ap.error("--only, --allow-unverified and --rerun shape the plan: pass them when previewing, not with --plan")
    if a.plan and a.save_plan:
        ap.error("--save-plan is for a preview; --plan uses a plan that is already saved")
    if a.max_plan_age <= 0:
        ap.error("--max-plan-age must be a positive number of minutes")
    if a.execute and not a.plan:
        out(f"refusing to execute: --execute runs a reviewed plan. Preview with `allo-router run`, which saves "
            f"{PLAN_FILE}, then run `allo-router run --plan {PLAN_FILE} --execute`.")
        return 2
    if interactive is None:
        interactive = sys.stdin.isatty()
    if a.execute and not a.yes and not interactive:
        out("refusing to execute: no terminal to confirm each job. Pass --yes to run without asking.")
        return 2
    cfg, tasks = allo.load(here)
    if a.plan:
        try:
            plan = load_plan(a.plan)
            jobs = jobs_from_plan(plan, here, cfg, tasks, now, a.max_plan_age)
        except PlanError as e:
            out(f"refusing to run plan {a.plan}: {e}")
            return 2
        head = f"RUN plan {plan['hash'][7:19]} saved {plan['created_at']}"
        if a.execute:
            out(head + ": executing" + ("" if a.yes else ", asking before each job"))
        else:
            out(head + ": it still matches, nothing starts. Add --execute to run it.")
        options = None
    else:
        only = {x.strip() for x in a.only.split(",") if x.strip()} if a.only else None
        jobs = select(cfg, tasks, here, now, only, a.allow_unverified, a.rerun)
        options = {"only": sorted(only) if only else None, "allow_unverified": a.allow_unverified, "rerun": a.rerun}
        out(f"RUN as of {now:%Y-%m-%d %H:%M} UTC: dry run, nothing starts.")
    out("")
    failed = run_jobs(jobs, here, a.execute, None if a.yes else confirm, out)
    runnable = sum(1 for j in jobs if not j["skip"])
    out("")
    out(f"{len(jobs)} jobs: {runnable} runnable, {len(jobs) - runnable} skipped"
        + (f", {failed} failed" if a.execute else ""))
    if options is not None:
        path = Path(a.save_plan) if a.save_plan else here / PLAN_FILE
        h = save_plan(path, here, cfg, tasks, jobs, now, options)
        cmd = "allo-router" + ("" if str(here) == "." else f" --dir {shlex.quote(str(here))}")
        out(f"Saved this plan to {path} ({h[7:19]}). To run exactly it, within {a.max_plan_age:g} minutes and "
            "before readings or config change:")
        out(f"  {cmd} run --plan {shlex.quote(str(path))} --execute")
    if a.execute:
        out("Record fresh readings with `allo-router set` before the next plan; the executor does not measure usage.")
    return 1 if failed else 0
