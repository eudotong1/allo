"""Offline end-to-end tests.

They run the real entry point (`python allo.py`, the same `cli` a console script
calls) against a temp folder and made-up config. Nothing is uploaded and nothing
is published. The socket fixture in conftest.py still applies to this process;
the subprocesses are given no index and no reason to open a connection.
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
PY = sys.executable


def cli(here, *args, stdin=subprocess.DEVNULL):
    """Run the installed-style entry point against `here`. Returns the CompletedProcess."""
    return subprocess.run(
        [PY, str(ROOT / "allo.py"), "--dir", str(here), *args],
        capture_output=True, text=True, stdin=stdin,
    )


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_local(here, command, tasks):
    """A one-pool config whose readings are fresh against the real clock the CLI uses."""
    now = datetime.now(timezone.utc)
    cfg = {
        "tiers": ["public"],
        "options": {"credit_reserve": 0.1, "min_headroom_pct": 20, "subscription_max_tier": "public"},
        "pools": [{
            "id": "local",
            "label": "Local",
            "kind": "subscription",
            "tiers": ["public"],
            "remaining_pct": 80,
            "resets_at": iso(now + timedelta(days=2)),
            "updated_at": iso(now),
            "run": {"backend": "shell", "command": command},
        }],
    }
    (here / "pools.json").write_text(json.dumps(cfg))
    (here / "tasks.json").write_text(json.dumps({"tasks": tasks}))


def task(i, name, prompt, priority=1):
    return {"id": i, "name": name, "tier": "public", "est_usd": 0, "needs": [], "priority": priority,
            "prompt": prompt}


def log_lines(here):
    path = here / "runs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# Init, run, status

def test_e2e_init_run_and_status_stay_on_the_dry_run_backend(tmp_path):
    """Starter init, a reviewed plan, execute, then the two read-only status views."""
    init = cli(tmp_path, "init")
    assert init.returncode == 0, init.stderr
    assert "wrote pools.json and tasks.json" in init.stdout
    pools = json.loads((tmp_path / "pools.json").read_text())
    assert [p["run"]["backend"] for p in pools["pools"]] == ["dry-run"] * len(pools["pools"])

    plan = cli(tmp_path)
    assert plan.returncode == 0, plan.stderr
    assert "PROPOSED ASSIGNMENT" in plan.stdout
    assert "NO READING" in plan.stdout

    sync = cli(tmp_path, "sync")
    assert sync.returncode == 0 and "no pool" in sync.stdout and "sync" in sync.stdout

    preview = cli(tmp_path, "run")
    assert preview.returncode == 0, preview.stderr
    assert "dry run, nothing starts" in preview.stdout
    assert (tmp_path / "plan.json").is_file()
    assert not (tmp_path / "runs.jsonl").exists()

    # No terminal, so a real execute has to be asked for with --yes. Dry-run still starts nothing.
    refused = cli(tmp_path, "run", "--plan", str(tmp_path / "plan.json"), "--execute")
    assert refused.returncode == 2 and "Pass --yes" in refused.stdout and not (tmp_path / "runs.jsonl").exists()

    executed = cli(tmp_path, "run", "--plan", str(tmp_path / "plan.json"), "--execute", "--yes")
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert "executing" in executed.stdout
    rows = log_lines(tmp_path)
    assert rows and {r["status"] for r in rows} <= {"dry-run", "skipped"}
    assert any(r["status"] == "dry-run" for r in rows)
    assert not (tmp_path / "runs").exists()

    report = cli(tmp_path, "report")
    assert report.returncode == 0, report.stderr
    assert "# Allo Router report" in report.stdout and "ESTIMATE" in report.stdout
    for row in rows:
        if row["status"] == "dry-run":
            assert row["task_name"] in report.stdout

    status = cli(tmp_path, "dashboard", "--json")
    assert status.returncode == 0, status.stderr
    state = json.loads(status.stdout)
    assert state["example"] is False
    assert state["config_dir"] == str(tmp_path.resolve())
    assert state["running"] is None
    assert state["history"]["logged"] is True
    assert state["headline"]
    logged = state["history"]["today"] + (state["history"].get("recent") or [])
    assert any(row["status"] == "dry-run" for row in logged)


def test_e2e_shell_run_records_ok_and_report_shows_it(tmp_path):
    write_local(tmp_path, [PY, "-c", "import sys; print('OUT:' + sys.argv[1])", "{prompt}"],
                [task(1, "Say hi", "hello-from-e2e")])
    preview = cli(tmp_path, "run")
    assert preview.returncode == 0, preview.stderr
    assert "dry run, nothing starts" in preview.stdout and "hello-from-e2e" in preview.stdout
    assert not (tmp_path / "runs.jsonl").exists()
    executed = cli(tmp_path, "run", "--plan", str(tmp_path / "plan.json"), "--execute", "--yes")
    assert executed.returncode == 0, executed.stdout + executed.stderr
    (entry,) = [r for r in log_lines(tmp_path) if r["status"] != "skipped"]
    assert entry["status"] == "ok" and entry["pool"] == "local"
    assert "OUT:hello-from-e2e" in (tmp_path / entry["output"]).read_text()

    report = cli(tmp_path, "report", "--period", "day")
    assert report.returncode == 0 and "Say hi" in report.stdout and "ShellBackend" in report.stdout
    status = json.loads(cli(tmp_path, "dashboard", "--json").stdout)
    assert status["history"]["last"]["status"] == "ok"
    assert status["history"]["last"]["name"] == "Say hi"
    assert status["running"] is None


def test_e2e_failed_job_is_exit_1_and_shows_in_status(tmp_path):
    write_local(tmp_path, [PY, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(4)"],
                [task(1, "Breaks", "unused")])
    assert cli(tmp_path, "run").returncode == 0
    executed = cli(tmp_path, "run", "--plan", str(tmp_path / "plan.json"), "--execute", "--yes")
    assert executed.returncode == 1 and "failed, exit 4" in executed.stdout and "boom" in executed.stdout
    assert log_lines(tmp_path)[0]["status"] == "failed"
    report = cli(tmp_path, "report")
    assert report.returncode == 0
    assert any("Breaks" in line and "failed" in line for line in report.stdout.splitlines())
    status = json.loads(cli(tmp_path, "dashboard", "--json").stdout)
    assert status["history"]["last"]["status"] == "failed"


# Error paths, still through the real entry point

def test_e2e_missing_config_bad_json_and_init_refuses_to_overwrite(tmp_path):
    missing = cli(tmp_path)
    assert missing.returncode != 0
    assert "pools.json" in missing.stderr and "init" in missing.stderr

    assert cli(tmp_path, "init").returncode == 0
    again = cli(tmp_path, "init")
    assert again.returncode != 0 and "already exists" in again.stderr
    # The first files are still the starter, not a second truncated write.
    assert json.loads((tmp_path / "pools.json").read_text())["pools"]

    (tmp_path / "pools.json").write_text("{")
    bad = cli(tmp_path)
    assert bad.returncode != 0 and "could not read the JSON" in bad.stderr

    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": 1, "name": "n", "tier": "public", "est_usd": -1, "needs": [], "priority": 1}]}))
    (tmp_path / "pools.json").write_text((ROOT / "examples" / "pools.json").read_text())
    negative = cli(tmp_path)
    assert negative.returncode != 0 and "negative" in negative.stderr


def test_e2e_set_and_run_reject_bad_input(tmp_path):
    assert cli(tmp_path, "init").returncode == 0
    unknown = cli(tmp_path, "set", "chatgpt", "percent=55")
    assert unknown.returncode != 0 and "unknown key" in unknown.stderr
    bare = cli(tmp_path, "set", "chatgpt", "reset=2026-10-16T20:00:00")
    assert bare.returncode != 0 and "no zone" in bare.stderr
    nochdir = subprocess.run([PY, str(ROOT / "allo.py"), "--dir"], capture_output=True, text=True)
    assert nochdir.returncode != 0 and "--dir needs a folder" in nochdir.stderr

    yes = cli(tmp_path, "run", "--yes")
    assert yes.returncode != 0 and "--yes only makes sense with --execute" in yes.stderr
    naked = cli(tmp_path, "run", "--execute", "--yes")
    assert naked.returncode == 2 and "reviewed plan" in naked.stdout and not (tmp_path / "runs.jsonl").exists()


def test_e2e_edited_plan_is_refused_before_anything_starts(tmp_path):
    write_local(tmp_path, [PY, "-c", "import sys; print(sys.argv[1])", "{prompt}"],
                [task(1, "Say hi", "hello-from-e2e")])
    assert cli(tmp_path, "run").returncode == 0
    plan = tmp_path / "plan.json"
    data = json.loads(plan.read_text())
    data["jobs"][0]["prompt"] = "tampered"
    plan.write_text(json.dumps(data))
    refused = cli(tmp_path, "run", "--plan", str(plan), "--execute", "--yes")
    assert refused.returncode == 2 and "hash does not match" in refused.stdout
    assert not (tmp_path / "runs.jsonl").exists()


# Local packaging: build, install into a prefix, run --help. No upload.

PACKAGING_FILES = (
    "pyproject.toml", "README.md", "LICENSE",
    "allo.py", "allo_dash.py", "allo_dash_page.py", "allo_exec.py",
    "allo_live.py", "allo_report.py", "allo_sync.py", "allo_upload.py",
)


def test_offline_wheel_installs_and_help_runs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in PACKAGING_FILES:
        shutil.copy(ROOT / name, src / name)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    build = subprocess.run(
        [PY, "-m", "pip", "wheel", str(src), "--no-build-isolation", "--no-deps", "--no-index", "-w", str(wheels)],
        capture_output=True, text=True, env=env,
    )
    assert build.returncode == 0, build.stderr
    (wheel,) = wheels.glob("allo_router-*.whl")
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta = zf.read(next(n for n in names if n.endswith("/METADATA"))).decode()
        entry = zf.read(next(n for n in names if n.endswith("/entry_points.txt"))).decode()
    assert "Name: allo-router" in meta
    assert "Version: 0.5.0" in meta
    assert "License-Expression: MIT" in meta
    assert "Requires-Python: >=3.9" in meta
    assert "License-File: LICENSE" in meta
    assert "allo-router = allo:cli" in entry
    assert all(not n.endswith(".env") and "credential" not in n.lower() for n in names)

    prefix = tmp_path / "prefix"
    install = subprocess.run(
        [PY, "-m", "pip", "install", "--prefix", str(prefix), "--no-index", "--no-deps",
         "--no-build-isolation", str(wheel)],
        capture_output=True, text=True, env=env,
    )
    assert install.returncode == 0, install.stderr
    (script,) = [p for p in prefix.rglob("allo-router") if p.is_file()]
    libs = [p for p in prefix.rglob("*") if p.is_dir() and p.name in ("site-packages", "dist-packages")]
    run_env = {**env, "PYTHONPATH": os.pathsep.join(str(p) for p in libs)}
    help_out = subprocess.run([PY, str(script), "--help"], capture_output=True, text=True, env=run_env)
    assert help_out.returncode == 0, help_out.stderr
    assert "allo-router init" in help_out.stdout
    assert "allo-router run" in help_out.stdout
    version = subprocess.run([PY, str(script), "--version"], capture_output=True, text=True, env=run_env)
    assert version.returncode == 0 and version.stdout.strip() == "allo-router 0.5.0"
