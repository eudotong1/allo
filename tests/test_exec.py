import importlib.util, json, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
import allo_exec

ROOT = Path(__file__).parent.parent
NOW = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)
PY = sys.executable
ECHO_ARG = [PY, "-c", "import sys; print('ARG:' + sys.argv[1])", "{prompt}"]
ECHO_STDIN = [PY, "-c", "import sys; print('STDIN:' + sys.stdin.read())"]


def write_cfg(here, run=None, tasks=None, pool_extra=None, extra_pools=()):
    p = {"id": "sub", "label": "Sub", "kind": "subscription", "tiers": ["public", "private"], "remaining_pct": 80,
         "resets_at": "2099-01-05T00:00:00Z", "updated_at": "2099-01-03T11:00:00Z"}
    if run is not None:
        p["run"] = run
    p.update(pool_extra or {})
    cfg = {"tiers": ["public", "private"], "options": {"subscription_max_tier": "private"}, "pools": [p, *extra_pools]}
    tasks = tasks or [{"id": 1, "name": "Say hi", "tier": "public", "est_usd": 0, "needs": [], "priority": 1,
                       "prompt": "hello"}]
    (here / "pools.json").write_text(json.dumps(cfg))
    (here / "tasks.json").write_text(json.dumps({"tasks": tasks}))


def run(here, *argv, confirm=None, interactive=True, now=NOW):
    lines = []
    code = allo_exec.cmd_run(here, list(argv), now, confirm=confirm or (lambda job: True),
                             out=lines.append, interactive=interactive)
    return code, "\n".join(lines)


def execute(here, *flags, yes=True, confirm=None, interactive=True, now=NOW):
    """Preview with `flags`, which saves plan.json, then run exactly that plan."""
    code, out = run(here, *flags, now=now)
    assert code == 0, out
    return run(here, "--plan", str(here / "plan.json"), "--execute", *(["--yes"] if yes else []),
               confirm=confirm, interactive=interactive, now=now)


def log(here):
    p = here / "runs.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def no_subprocess(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("dry run must not start a process")
    monkeypatch.setattr(allo_exec.subprocess, "run", refuse)


# Dry run is the default and starts nothing

def test_dry_run_is_default_and_starts_nothing(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    code, out = run(tmp_path)
    assert code == 0 and "dry run, nothing starts" in out
    assert "'import sys; print('\"'\"'ARG:'" in out   # the command is shown, quoted for a shell
    assert "ARG:hello" not in out                       # and never run
    assert not (tmp_path / "runs.jsonl").exists() and not (tmp_path / "runs").exists()


def test_explicit_dry_run_flag_matches_default(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    assert run(tmp_path, "--dry-run") == run(tmp_path)


def test_dry_run_and_execute_cannot_be_combined(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    with pytest.raises(SystemExit):
        run(tmp_path, "--dry-run", "--execute")


def test_yes_without_execute_is_an_error(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    with pytest.raises(SystemExit):
        run(tmp_path, "--yes")


def test_execute_without_a_terminal_needs_yes(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    code, out = execute(tmp_path, yes=False, interactive=False)
    assert code == 2 and "Pass --yes" in out and not log(tmp_path)


# Shell backend

def test_shell_backend_passes_prompt_as_argument_and_logs(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    code, out = execute(tmp_path)
    assert code == 0 and "ok, exit 0" in out
    (entry,) = log(tmp_path)
    assert entry["status"] == "ok" and entry["pool"] == "sub" and entry["task_id"] == 1
    assert "ARG:hello" in (tmp_path / entry["output"]).read_text()


def test_shell_backend_sends_prompt_on_stdin_without_placeholder(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_STDIN})
    code, out = run(tmp_path)
    assert "(prompt on stdin)" in out
    execute(tmp_path)
    assert "STDIN:hello" in (tmp_path / log(tmp_path)[0]["output"]).read_text()


def test_placeholders_are_filled_once_and_never_reach_a_shell(tmp_path):
    cmd = [PY, "-c", "import sys; print('|'.join(sys.argv[1:]))", "{task_id}", "{tier}", "{task_name}", "{prompt}"]
    tricky = "{tier} $(touch pwned) ; rm -rf x"
    write_cfg(tmp_path, {"backend": "shell", "command": cmd},
              [{"id": 7, "name": "N", "tier": "private", "est_usd": 0, "needs": [], "priority": 1, "prompt": tricky}])
    execute(tmp_path)
    assert f"7|private|N|{tricky}" in (tmp_path / log(tmp_path)[0]["output"]).read_text()
    assert not (tmp_path / "pwned").exists()


def test_prompt_that_looks_like_a_flag_is_refused_unless_after_double_dash(tmp_path):
    flaggy = [{"id": 1, "name": "N", "tier": "public", "est_usd": 0, "needs": [], "priority": 1,
               "prompt": "--dangerously-do-anything"}]
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, flaggy)
    assert "WILL NOT RUN" in run(tmp_path)[1]
    code, out = execute(tmp_path)
    assert code == 1 and "read as an option" in out and log(tmp_path)[0]["status"] == "error"
    write_cfg(tmp_path, {"backend": "shell", "command": [PY, "-c", "import sys; print('ARG:' + sys.argv[-1])", "--", "{prompt}"]}, flaggy)
    code, out = execute(tmp_path, "--rerun")
    assert code == 0 and "WILL NOT RUN" not in out
    assert "ARG:--dangerously-do-anything" in (tmp_path / log(tmp_path)[-1]["output"]).read_text()


def test_prompt_falls_back_to_task_name(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG},
              [{"id": 1, "name": "Only a name", "tier": "public", "est_usd": 0, "needs": [], "priority": 1}])
    execute(tmp_path)
    assert "ARG:Only a name" in (tmp_path / log(tmp_path)[0]["output"]).read_text()


def test_nonzero_exit_is_failed_and_exit_code_is_1(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": [PY, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]})
    code, out = execute(tmp_path)
    assert code == 1 and "failed, exit 3" in out and "boom" in out
    assert log(tmp_path)[0]["status"] == "failed" and log(tmp_path)[0]["returncode"] == 3


def test_timeout_stops_the_job(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": [PY, "-c", "import time; time.sleep(10)"], "timeout_s": 0.5})
    code, out = execute(tmp_path)
    assert code == 1 and "timeout" in out and log(tmp_path)[0]["status"] == "timeout"


def test_missing_command_is_an_error_not_a_crash(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ["allo-no-such-cli-xyz", "{prompt}"]})
    assert "NOT FOUND: allo-no-such-cli-xyz" in run(tmp_path)[1]   # the dry run warns first
    code, out = execute(tmp_path)
    assert code == 1 and "command not found: allo-no-such-cli-xyz" in out


def test_env_is_added_on_top_of_yours(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLO_TEST_KEEP", "kept")
    cmd = [PY, "-c", "import os; print(os.environ['ALLO_TEST_KEEP'], os.environ['ALLO_TEST_ADD'])"]
    write_cfg(tmp_path, {"backend": "shell", "command": cmd, "env": {"ALLO_TEST_ADD": "added"}})
    code, out = run(tmp_path)
    assert "ALLO_TEST_ADD=added" in out
    execute(tmp_path)
    assert "kept added" in (tmp_path / log(tmp_path)[0]["output"]).read_text()


def test_task_cwd_is_relative_to_the_config_folder(tmp_path):
    (tmp_path / "repo").mkdir()
    write_cfg(tmp_path, {"backend": "shell", "command": [PY, "-c", "import os; print('CWD:' + os.getcwd())"]},
              [{"id": 1, "name": "N", "tier": "public", "est_usd": 0, "needs": [], "priority": 1, "cwd": "repo"}])
    execute(tmp_path)
    assert "CWD:" + str((tmp_path / "repo").resolve()) in (tmp_path / log(tmp_path)[0]["output"]).read_text()


def test_jobs_run_in_the_config_folder_by_default(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    write_cfg(cfg, {"backend": "shell", "command": [PY, "-c", "import os; print('CWD:' + os.getcwd())"]})
    execute(cfg)
    assert "CWD:" + str(cfg.resolve()) + "\n" in (cfg / log(cfg)[0]["output"]).read_text()


def test_missing_cwd_skips_the_job(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG, "cwd": "nope"})
    code, out = execute(tmp_path)
    assert code == 0 and "does not exist" in out and not log(tmp_path)


@pytest.mark.parametrize("bad, why", [
    ({"backend": "shell", "command": "claude -p {prompt}"}, "list of strings"),
    ({"backend": "shell", "command": []}, "list of strings"),
    ({"backend": "shell", "command": ["x"], "env": {"A": 1}}, "env"),
    ({"backend": "shell", "command": ["x"], "timeout_s": 0}, "timeout_s"),
    ({"backend": "teleport"}, "unknown backend 'teleport'"),
    ({"command": ["x"]}, "unknown backend None"),
    ("shell", "must be an object"),
])
def test_bad_run_blocks_are_skipped_with_the_reason(tmp_path, bad, why, no_subprocess):
    write_cfg(tmp_path, bad)
    code, out = execute(tmp_path)
    assert code == 0 and "bad run block on sub" in out and why in out and not log(tmp_path)


# What the executor refuses to run

def test_pool_without_run_block_is_never_run(tmp_path, no_subprocess):
    write_cfg(tmp_path)
    code, out = execute(tmp_path)
    assert "has no run block; start this job yourself" in out and not log(tmp_path)


def test_unassigned_job_is_skipped(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, pool_extra={"remaining_pct": 5})
    code, out = execute(tmp_path)
    assert "-> UNASSIGNED" in out and "unassigned" in out and not log(tmp_path)


@pytest.mark.parametrize("extra, flag", [
    ({"updated_at": "2099-01-01T00:00:00Z"}, "STALE"),
    ({"remaining_pct": None}, "NO READING"),
    ({"resets_at": None}, "NO RESET TIME"),
    ({"resets_at": "2099-01-03T00:00:00Z"}, "RESET PASSED"),
])
def test_flagged_pool_needs_a_fresh_reading_or_allow_unverified(tmp_path, extra, flag):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, pool_extra=extra)
    code, out = execute(tmp_path)
    assert flag in out and "allo-router set sub" in out and not log(tmp_path)
    code, out = execute(tmp_path, "--allow-unverified")
    assert code == 0 and log(tmp_path)[0]["status"] == "ok"


def test_finished_job_is_not_run_twice_unless_rerun(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    execute(tmp_path)
    code, out = execute(tmp_path)
    assert "already ran ok" in out and len(log(tmp_path)) == 1
    execute(tmp_path, "--rerun")
    assert len(log(tmp_path)) == 2


def test_failed_job_is_retried_on_the_next_run(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": [PY, "-c", "raise SystemExit(1)"]})
    execute(tmp_path)
    execute(tmp_path)
    assert [e["status"] for e in log(tmp_path)] == ["failed", "failed"]


def test_rerun_guard_survives_a_corrupt_log_line(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    execute(tmp_path)
    with open(tmp_path / "runs.jsonl", "a") as f:
        f.write("not json\n")
    assert "already ran ok" in execute(tmp_path)[1]


def test_saying_no_skips_the_job(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    asked = []
    code, out = execute(tmp_path, yes=False, confirm=lambda job: asked.append(job["task"]["id"]) or False)
    assert asked == [1] and "you said no" in out and not log(tmp_path)


def test_yes_skips_the_question(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    def never(job):
        raise AssertionError("--yes must not ask")
    assert execute(tmp_path, confirm=never)[0] == 0


def test_only_limits_to_listed_task_ids(tmp_path):
    tasks = [{"id": i, "name": f"T{i}", "tier": "public", "est_usd": 0, "needs": [], "priority": i} for i in (1, 2, 3)]
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, tasks)
    code, out = execute(tmp_path, "--only", "1, 3")
    assert [e["task_id"] for e in log(tmp_path)] == [1, 3] and "T2" not in out


def test_jobs_run_in_priority_order(tmp_path):
    tasks = [{"id": i, "name": f"T{i}", "tier": "public", "est_usd": 0, "needs": [], "priority": p}
             for i, p in ((1, 3), (2, 1), (3, 2))]
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, tasks)
    execute(tmp_path)
    assert [e["task_id"] for e in log(tmp_path)] == [2, 3, 1]


# Saved plans: --execute runs exactly the plan a preview saved

def plan_file(here):
    return json.loads((here / "plan.json").read_text())


def test_execute_without_a_saved_plan_is_refused(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    code, out = run(tmp_path, "--execute", "--yes")
    assert code == 2 and "runs a reviewed plan" in out and "--plan plan.json --execute" in out and not log(tmp_path)


def test_preview_saves_the_plan_with_hash_time_and_readings_fingerprint(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    code, out = run(tmp_path)
    plan = plan_file(tmp_path)
    assert f"Saved this plan to {tmp_path / 'plan.json'} ({plan['hash'][7:19]})" in out
    assert plan["created_at"] == "2099-01-03T12:00:00Z" and plan["config_dir"] == str(tmp_path.resolve())
    assert plan["hash"] == allo_exec.digest({k: v for k, v in plan.items() if k != "hash"})
    assert plan["readings_fingerprint"].startswith("sha256:") and plan["config_hash"].startswith("sha256:")
    (job,) = plan["jobs"]
    assert job["task_id"] == "1" and job["pool"] == "sub" and job["prompt"] == "hello" and job["skip"] is None
    assert "ARG:" in job["command"]


def test_save_plan_picks_the_file(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path, "--save-plan", str(tmp_path / "reviewed.json"))
    assert (tmp_path / "reviewed.json").exists() and not (tmp_path / "plan.json").exists()


def test_checking_a_saved_plan_starts_nothing(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    code, out = run(tmp_path, "--plan", str(tmp_path / "plan.json"))
    assert code == 0 and "still matches, nothing starts" in out and not log(tmp_path)


def test_executed_jobs_are_logged_with_the_plan_hash(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    execute(tmp_path)
    assert log(tmp_path)[0]["plan"] == plan_file(tmp_path)["hash"]


def refused(here, *argv, now=NOW):
    code, out = run(here, "--plan", str(here / "plan.json"), "--execute", "--yes", *argv, now=now)
    assert code == 2 and "refusing to run plan" in out and not log(here), out
    return out


def test_edited_plan_file_is_refused(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    plan = plan_file(tmp_path)
    plan["jobs"][0]["prompt"] = "something nobody reviewed"
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    assert "changed after it was saved" in refused(tmp_path)


@pytest.mark.parametrize("content, why", [(None, "cannot read it"), ("{}", "not an Allo Router plan file"),
                                          ("not json", "cannot read it")])
def test_missing_or_foreign_plan_file_is_refused(tmp_path, content, why, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    if content is not None:
        (tmp_path / "plan.json").write_text(content)
    assert why in refused(tmp_path)


def test_changed_readings_refuse_the_plan(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, pool_extra={"remaining_pct": 70})
    assert "readings in pools.json changed" in refused(tmp_path)


@pytest.mark.parametrize("change", ["command", "prompt"])
def test_changed_config_refuses_the_plan(tmp_path, change, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    if change == "command":
        write_cfg(tmp_path, {"backend": "shell", "command": ECHO_STDIN})
    else:
        write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, [{"id": 1, "name": "Say hi", "tier": "public",
                  "est_usd": 0, "needs": [], "priority": 1, "prompt": "hello, edited"}])
    assert "pools.json or tasks.json changed" in refused(tmp_path)


def test_stale_plan_is_refused_unless_the_age_limit_allows_it(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    later = NOW + timedelta(minutes=61)
    assert "stale: saved 61 minutes ago, limit 60" in refused(tmp_path, now=later)
    code, out = run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes", "--max-plan-age", "90",
                    now=later)
    assert code == 0 and log(tmp_path)[0]["status"] == "ok"


def test_plan_from_the_future_is_refused(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    assert "in the future" in refused(tmp_path, now=NOW - timedelta(minutes=10))


def test_plan_saved_for_another_config_folder_is_refused(tmp_path, no_subprocess):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        write_cfg(d, {"backend": "shell", "command": ECHO_ARG})
    run(a)
    (b / "plan.json").write_text((a / "plan.json").read_text())
    assert f"saved for {a.resolve()}" in refused(b)


def test_job_that_would_now_be_skipped_refuses_the_plan(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path)
    # A day later the reading is STALE. Even with a generous age limit, the reviewed job no longer qualifies.
    out = refused(tmp_path, "--max-plan-age", "3000", now=NOW + timedelta(hours=25))
    assert "#1 would now be skipped" in out and "STALE" in out


def test_command_that_comes_out_differently_refuses_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(allo_exec, "BACKENDS", dict(allo_exec.BACKENDS))
    shown = ["v1"]

    class Shifty:
        def __init__(self, cfg):
            pass

        def describe(self, job):
            return f"shifty {shown[0]}"

        def run(self, job):
            raise AssertionError("must not run")

    allo_exec.register_backend("shifty", Shifty)
    write_cfg(tmp_path, {"backend": "shifty"})
    run(tmp_path)
    shown[0] = "v2"   # same config and readings, different command (e.g. another CLI found on PATH)
    assert "#1 no longer matches it (command changed)" in refused(tmp_path)


def test_job_skipped_in_the_plan_never_runs_even_if_it_became_runnable(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    execute(tmp_path)
    run(tmp_path)                                   # preview: #1 already ran ok, so the plan skips it
    (tmp_path / "runs.jsonl").unlink()
    code, out = run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes")
    assert code == 0 and "already ran ok" in out and not log(tmp_path)


def test_a_plan_runs_each_job_once_even_when_saved_with_rerun(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    execute(tmp_path, "--rerun")
    code, out = run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes")
    assert code == 0 and "already ran ok under this plan" in out and len(log(tmp_path)) == 1


def test_job_finished_under_another_plan_is_skipped(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    run(tmp_path, "--save-plan", str(tmp_path / "a.json"))
    run(tmp_path, "--save-plan", str(tmp_path / "b.json"), now=NOW + timedelta(minutes=1))   # a different plan
    run(tmp_path, "--plan", str(tmp_path / "a.json"), "--execute", "--yes", now=NOW + timedelta(minutes=2))
    code, out = run(tmp_path, "--plan", str(tmp_path / "b.json"), "--execute", "--yes", now=NOW + timedelta(minutes=2))
    assert code == 0 and "after this plan was saved" in out and len(log(tmp_path)) == 1


@pytest.mark.parametrize("extra", [["--only", "1"], ["--allow-unverified"], ["--rerun"], ["--save-plan", "x.json"]])
def test_planning_flags_belong_to_the_preview_not_to_plan(tmp_path, extra):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG})
    with pytest.raises(SystemExit):
        run(tmp_path, "--plan", "plan.json", *extra)


# Dry-run backend and plug-ins

def test_dry_run_backend_never_starts_anything_even_under_execute(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "dry-run"})
    code, out = execute(tmp_path)
    assert code == 0 and "dry-run" in out
    (entry,) = log(tmp_path)
    assert entry["status"] == "dry-run" and entry["output"] is None and not (tmp_path / "runs").exists()
    assert "already ran" not in execute(tmp_path)[1]   # a dry run is not a finished job


def test_dry_run_backend_shows_the_command_it_would_run_and_still_starts_nothing(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "dry-run", "command": ECHO_ARG})
    code, out = execute(tmp_path)
    assert code == 0 and 'With "backend": "shell" it would run:' in out and "hello" in out
    assert log(tmp_path)[0]["status"] == "dry-run" and not (tmp_path / "runs").exists()


def test_dry_run_backend_checks_the_command_it_shows(tmp_path, no_subprocess):
    write_cfg(tmp_path, {"backend": "dry-run", "command": "claude -p {prompt}"})
    code, out = execute(tmp_path)
    assert "bad run block on sub" in out and "list of strings" in out and not log(tmp_path)


def test_registered_backend_is_used_and_a_crash_does_not_stop_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(allo_exec, "BACKENDS", dict(allo_exec.BACKENDS))
    seen = []

    class Recorder:
        def __init__(self, cfg):
            self.cfg = cfg

        def describe(self, job):
            return "recorder"

        def run(self, job):
            seen.append(job["task"]["id"])
            if job["task"]["id"] == 1:
                raise RuntimeError("backend broke")
            return {"status": "ok", "returncode": 0, "stdout": "done", "stderr": ""}

    allo_exec.register_backend("recorder", Recorder)
    tasks = [{"id": i, "name": f"T{i}", "tier": "public", "est_usd": 0, "needs": [], "priority": i} for i in (1, 2)]
    write_cfg(tmp_path, {"backend": "recorder"}, tasks)
    code, out = execute(tmp_path)
    assert seen == [1, 2] and code == 1 and "RuntimeError: backend broke" in out
    assert [e["status"] for e in log(tmp_path)] == ["error", "ok"]


def test_example_outbox_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(allo_exec, "BACKENDS", dict(allo_exec.BACKENDS))
    spec = importlib.util.spec_from_file_location("custom_backend", ROOT / "examples/custom_backend.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    write_cfg(tmp_path, {"backend": "outbox", "folder": "outbox"})
    assert "outbox: write the prompt to" in run(tmp_path)[1] and not (tmp_path / "outbox").exists()
    execute(tmp_path)
    assert "hello" in (tmp_path / "outbox" / "task1-say-hi.md").read_text()


# Starter config and the CLI

def test_starter_config_dry_run_routes_each_job(tmp_path, no_subprocess):
    for n in ("pools.json", "tasks.json"):
        (tmp_path / n).write_text((ROOT / "examples" / n).read_text())
    code, out = run(tmp_path)
    assert code == 0
    assert "would run: codex exec 'Refactor the pricing page" in out
    assert out.count("dry-run backend") == 3 and "unassigned" in out


def test_every_starter_pool_is_on_the_dry_run_backend(tmp_path):
    # A new install must not be able to start a real agent on its first --execute. Checked on the config
    # itself: tests never run --execute against the starter files.
    import allo
    assert all(p["run"]["backend"] == "dry-run" for p in allo.STARTER_POOLS["pools"])
    assert all(isinstance(allo_exec.make_backend(p["run"]), allo_exec.DryRunBackend)
               for p in allo.STARTER_POOLS["pools"])
    allo.cmd_init(tmp_path)
    written = json.loads((tmp_path / "pools.json").read_text())["pools"]
    assert [p["run"]["backend"] for p in written] == ["dry-run"] * len(allo.STARTER_POOLS["pools"])


def test_cli_run_subcommand_preview_then_execute_the_saved_plan(tmp_path):
    write_cfg(tmp_path, {"backend": "shell", "command": ECHO_ARG}, pool_extra={"updated_at": "2099-01-03T11:00:00Z"})
    # The CLI uses the real clock, so the 2099 fixture readings are not stale and resets are in the future.
    base = [PY, str(ROOT / "allo.py"), "--dir", str(tmp_path), "run"]
    r = subprocess.run(base, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and "dry run" in r.stdout and not log(tmp_path)
    assert f"run --plan {tmp_path / 'plan.json'} --execute" in r.stdout
    r = subprocess.run(base + ["--execute", "--yes"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 2 and "reviewed plan" in r.stdout and not log(tmp_path)
    plan = ["--plan", str(tmp_path / "plan.json"), "--execute"]
    r = subprocess.run(base + plan, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 2 and not log(tmp_path)
    r = subprocess.run(base + plan + ["--yes"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and log(tmp_path)[0]["status"] == "ok"


def test_network_is_blocked_in_tests():
    import socket
    with pytest.raises(RuntimeError, match="must not use the network"):
        socket.create_connection(("example.com", 80))
