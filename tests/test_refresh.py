"""`allo-router run --refresh`: a reviewed plan re-checked on fresh readings, for a scheduled run shortly after.
It must never let through anything that was not reviewed."""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import allo_exec
from test_exec import ECHO_ARG, NOW, log, run, write_cfg

SHELL = {"backend": "shell", "command": ECHO_ARG}
EVENING = NOW                                   # review time
NIGHT = NOW + timedelta(hours=7)                # 7 hours later: past a 300-minute --max-plan-age
FRESH = {"remaining_pct": 70, "updated_at": "2099-01-03T18:50:00Z"}   # what a sync before the refresh reads


def review(here, *flags):
    code, out = run(here, *flags, now=EVENING)
    assert code == 0, out
    return json.loads((here / "plan.json").read_text())


def refresh(here, *flags, now=NIGHT):
    return run(here, "--refresh", str(here / "plan.json"), *flags, now=now)


def execute_saved(here, now=NIGHT + timedelta(minutes=10)):
    return run(here, "--plan", str(here / "plan.json"), "--execute", "--yes", "--max-plan-age", "300", now=now)


def test_without_refresh_the_night_run_refuses_a_plan_reviewed_in_the_evening(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    code, out = execute_saved(tmp_path)
    assert code == 2 and "stale" in out and not log(tmp_path)


def test_refresh_on_new_readings_lets_the_reviewed_plan_run(tmp_path):
    write_cfg(tmp_path, SHELL)
    reviewed = review(tmp_path)
    write_cfg(tmp_path, SHELL, pool_extra=FRESH)          # readings moved; routing did not
    code, out = refresh(tmp_path)
    assert code == 0 and "every job comes out as reviewed" in out and "Nothing starts" in out
    assert not log(tmp_path)                              # refresh starts nothing
    fresh = json.loads((tmp_path / "plan.json").read_text())
    assert fresh["reviewed_at"] == reviewed["created_at"] and fresh["reviewed_plan"] == reviewed["hash"]
    assert fresh["created_at"] == "2099-01-03T19:00:00Z" and fresh["hash"] != reviewed["hash"]
    code, out = execute_saved(tmp_path)
    assert code == 0 and [e["status"] for e in log(tmp_path)] == ["ok"]


def test_refresh_can_save_elsewhere_and_leave_the_reviewed_file_alone(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    before = (tmp_path / "plan.json").read_text()
    code, _ = refresh(tmp_path, "--save-plan", str(tmp_path / "night.json"))
    assert code == 0 and (tmp_path / "plan.json").read_text() == before
    assert json.loads((tmp_path / "night.json").read_text())["reviewed_plan"]


def test_refresh_refuses_and_saves_nothing_when_a_job_would_go_elsewhere(tmp_path):
    other = {"id": "other", "label": "Other", "kind": "subscription", "tiers": ["public", "private"],
             "remaining_pct": 50, "resets_at": "2099-01-05T00:00:00Z", "updated_at": "2099-01-03T11:00:00Z",
             "run": SHELL}
    write_cfg(tmp_path, SHELL, extra_pools=[other])
    assert review(tmp_path)["jobs"][0]["pool"] == "sub"     # 80% left beats 50%
    before = (tmp_path / "plan.json").read_text()
    write_cfg(tmp_path, SHELL, pool_extra={"remaining_pct": 30, "updated_at": "2099-01-03T18:50:00Z"},
              extra_pools=[other])
    code, out = refresh(tmp_path)
    assert code == 2 and "#1 would now come out differently (pool changed)" in out and "Nothing saved" in out
    assert (tmp_path / "plan.json").read_text() == before
    assert execute_saved(tmp_path)[0] == 2 and not log(tmp_path)


def test_refresh_refuses_when_a_reviewed_job_would_now_be_skipped(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    write_cfg(tmp_path, SHELL, pool_extra={"remaining_pct": None, "updated_at": "2099-01-03T18:50:00Z"})
    code, out = refresh(tmp_path)
    assert code == 2 and "#1 would now be skipped" in out and "NO READING" in out


@pytest.mark.parametrize("what", ["task added", "prompt edited", "command changed"])
def test_refresh_refuses_when_config_or_tasks_changed_since_the_review(tmp_path, what):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    task = {"id": 1, "name": "Say hi", "tier": "public", "est_usd": 0, "needs": [], "priority": 1, "prompt": "hello"}
    if what == "task added":
        write_cfg(tmp_path, SHELL, [task, dict(task, id=2, name="Unreviewed job")], pool_extra=FRESH)
    elif what == "prompt edited":
        write_cfg(tmp_path, SHELL, [dict(task, prompt="rm everything")], pool_extra=FRESH)
    else:
        write_cfg(tmp_path, {"backend": "shell", "command": ["true"]}, pool_extra=FRESH)
    code, out = refresh(tmp_path)
    assert code == 2 and "changed since it was reviewed" in out


def test_refresh_refuses_an_old_review(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    code, out = refresh(tmp_path, now=EVENING + timedelta(minutes=961))
    assert code == 2 and "review is too old: reviewed 961 minutes ago, limit 960" in out
    code, _ = refresh(tmp_path, "--max-review-age", "1000", now=EVENING + timedelta(minutes=961))
    assert code == 0


def test_a_refreshed_plan_cannot_stretch_one_review_over_more_nights(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    assert refresh(tmp_path, now=EVENING + timedelta(hours=7))[0] == 0
    # Refreshing the refreshed copy still counts from the human review, not from the last refresh.
    code, out = refresh(tmp_path, now=EVENING + timedelta(hours=17))
    assert code == 2 and "review is too old" in out


def test_job_reviewed_as_skipped_stays_skipped_after_refresh(tmp_path):
    write_cfg(tmp_path, SHELL)
    run(tmp_path, now=EVENING - timedelta(minutes=5))
    run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes", now=EVENING - timedelta(minutes=4))
    plan = review(tmp_path)                                  # #1 already ran, so it is reviewed as skipped
    assert plan["jobs"][0]["skip"].startswith("already ran ok")
    (tmp_path / "runs.jsonl").unlink()                       # now it would be runnable again
    assert refresh(tmp_path)[0] == 0
    code, out = execute_saved(tmp_path)
    assert code == 0 and "already ran ok" in out and not log(tmp_path)


def test_job_that_finished_since_the_review_is_skipped_not_refused(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes", now=EVENING + timedelta(minutes=5))
    code, out = refresh(tmp_path)
    assert code == 0 and "already ran ok" in out and "0 runnable" in out


def test_refresh_refuses_an_edited_plan_file(tmp_path):
    write_cfg(tmp_path, SHELL)
    review(tmp_path)
    p = tmp_path / "plan.json"
    plan = json.loads(p.read_text())
    plan["jobs"][0]["prompt"] = "something else"
    p.write_text(json.dumps(plan))
    code, out = refresh(tmp_path)
    assert code == 2 and "hash does not match" in out


@pytest.mark.parametrize("extra", [["--execute"], ["--plan", "x.json"], ["--only", "1"], ["--rerun"],
                                   ["--allow-unverified"]])
def test_refresh_only_saves_a_plan(tmp_path, extra, capsys):
    write_cfg(tmp_path, SHELL)
    with pytest.raises(SystemExit):
        refresh(tmp_path, *extra)


def test_cli_refresh_syncs_first_and_then_rechecks(tmp_path, monkeypatch):
    import allo, allo_sync
    calls = []
    monkeypatch.setattr(allo_sync, "auto_sync", lambda here, now: calls.append("sync"))
    monkeypatch.setattr(allo_exec, "cmd_run", lambda here, argv, now: calls.append(argv) or 0)
    with pytest.raises(SystemExit):
        allo.main(["--dir", str(tmp_path), "run", "--refresh", "plan.json"])
    assert calls == ["sync", ["--refresh", "plan.json"]]


# The running-job marker the dashboard reads

def test_running_job_is_marked_while_it_runs_and_cleared_after(tmp_path, monkeypatch):
    monkeypatch.setattr(allo_exec, "BACKENDS", dict(allo_exec.BACKENDS))
    seen = []

    class Peek:
        def __init__(self, cfg):
            pass

        def describe(self, job):
            return "peek"

        def run(self, job):
            seen.append(json.loads((tmp_path / "runs" / "current.json").read_text()))
            return {"status": "ok", "returncode": 0, "stdout": "", "stderr": ""}

    allo_exec.register_backend("peek", Peek)
    write_cfg(tmp_path, {"backend": "peek"})
    review(tmp_path)
    code, _ = run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes", now=EVENING)
    import os
    assert code == 0 and seen[0]["task_id"] == 1 and seen[0]["pool"] == "sub" and seen[0]["pid"] == os.getpid()
    assert not (tmp_path / "runs" / "current.json").exists()
