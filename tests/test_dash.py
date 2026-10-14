"""`allo-router dashboard`: read-only state from the files the router keeps, served to this computer only."""
import json
import plistlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import allo
import allo_dash
import allo_exec
from test_exec import ECHO_ARG, NOW, run, write_cfg

UTC = timezone.utc
SHELL = {"backend": "shell", "command": ECHO_ARG}
LOADED = lambda label: {"loaded": True, "runs": "3", "last_exit": "0"}


def state(here, now=NOW, agents=(), status=LOADED, alive=None):
    return allo_dash.build_state(here, now, UTC, agents=list(agents), status_fn=status, alive=alive)


def snapshot(here):
    return {p.relative_to(here): p.read_bytes() for p in sorted(here.rglob("*")) if p.is_file()}


def plist(dir_, label, args, hour=3, minute=0, log=None):
    data = {"Label": label, "ProgramArguments": args, "StartCalendarInterval": {"Hour": hour, "Minute": minute}}
    if log:
        data["StandardOutPath"] = str(log)
    with open(dir_ / f"{label}.plist", "wb") as f:
        plistlib.dump(data, f)


def test_fresh_folder_shows_plans_and_queue_with_nothing_run(tmp_path):
    write_cfg(tmp_path, SHELL)
    s = state(tmp_path)
    assert s["example"] is False and s["errors"] == []
    (p,) = s["pools"]
    assert (p["id"], p["left"], p["left_text"], p["flags"], p["family"]) == ("sub", 80, "80%", [], "other")
    (q,) = s["queue"]
    assert q["pool"] == "sub" and q["skip"] is None and "ARG" in q["command"]
    assert s["plan"] is None and s["schedule"] is None and s["running"] is None
    assert s["history"]["logged"] is False
    assert (s["level"], s["headline"]) == ("ok", "Idle")


def test_after_a_run_it_shows_the_job_the_plan_and_the_saved_estimate(tmp_path):
    now = datetime.now(UTC)                     # runs.jsonl is stamped with the real clock
    stamp = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_cfg(tmp_path, SHELL, pool_extra={"updated_at": stamp(now - timedelta(hours=1)),
                                           "resets_at": stamp(now + timedelta(days=2))})
    run(tmp_path, now=now)
    run(tmp_path, "--plan", str(tmp_path / "plan.json"), "--execute", "--yes", now=now)
    s = state(tmp_path, now=now + timedelta(seconds=5))
    (row,) = s["history"]["today"]
    assert (row["task_id"], row["pool"], row["status"]) == (1, "sub", "ok")
    assert s["history"]["last"]["task_id"] == 1
    assert s["history"]["week"]["ok"] == 1 and s["history"]["week"]["routed"] == 1
    assert s["history"]["saved"]["week_usd"] == 0      # est_usd 0 on a subscription: nothing to save
    assert s["queue"][0]["skip_kind"] == "already-ran"
    assert s["plan"]["runnable"] == 1 and s["plan"]["check"]["ok"] is True


def test_building_the_state_writes_nothing_and_never_syncs(tmp_path, monkeypatch):
    import allo_sync
    monkeypatch.setattr(allo_sync, "sync", lambda *a, **k: pytest.fail("the dashboard must not sync"))
    write_cfg(tmp_path, SHELL, pool_extra={"sync": {"source": "claude", "auto": True}})
    allo_exec.cmd_run(tmp_path, [], NOW, out=lambda *_: None)          # a saved plan, as after a preview
    before = snapshot(tmp_path)
    state(tmp_path)
    allo_dash.example_state(NOW, UTC)
    assert snapshot(tmp_path) == before


def test_cli_dashboard_does_not_auto_sync(tmp_path, monkeypatch, capsys):
    import allo_sync
    monkeypatch.setattr(allo_sync, "auto_sync", lambda *a, **k: pytest.fail("the dashboard must not sync"))
    write_cfg(tmp_path, SHELL, pool_extra={"sync": {"source": "claude", "auto": True}})
    monkeypatch.setattr(allo_dash, "find_agents", lambda here: [])
    with pytest.raises(SystemExit) as e:
        allo.main(["--dir", str(tmp_path), "dashboard", "--json"])
    assert e.value.code == 0 and json.loads(capsys.readouterr().out)["pools"][0]["id"] == "sub"


def test_missing_pools_json_is_reported_not_crashed(tmp_path):
    s = state(tmp_path)
    assert any("no pools.json" in e for e in s["errors"])
    out = []
    assert allo_dash.cmd_dashboard(tmp_path, [], lambda: NOW, out=out.append) == 2 and "allo-router init" in out[0]


# Schedule: launchd agents for this folder, read-only

def test_finds_only_agents_that_run_allo_router_on_this_folder(tmp_path):
    here, agents = tmp_path / "cfg", tmp_path / "agents"
    here.mkdir(); agents.mkdir()
    plist(agents, "mine", ["/bin/allo-router", "--dir", str(here.resolve()), "run", "--plan", "p.json", "--execute",
                           "--yes", "--max-plan-age", "300"])
    plist(agents, "other-folder", ["/bin/allo-router", "--dir", "/somewhere/else", "run"])
    plist(agents, "sibling-folder", ["/bin/allo-router", "--dir", str(here.resolve()) + "2", "run"])
    plist(agents, "not-allo", ["/bin/backup", str(here.resolve())])
    (agents / "broken.plist").write_text("not a plist")
    (found,) = allo_dash.find_agents(here, agents)
    assert found["label"] == "mine" and found["executes"] and not found["refreshes"] and found["max_plan_age"] == 300


def test_a_shell_wrapper_that_refreshes_then_executes_is_recognised(tmp_path):
    here, agents = tmp_path / "cfg", tmp_path / "agents"
    here.mkdir(); agents.mkdir()
    d = str(here.resolve())
    plist(agents, "wrapped", ["/bin/sh", "-c", f"allo-router --dir {d} run --refresh {d}/plan.json --max-review-age 600"
                              f" && allo-router --dir {d} run --plan {d}/plan.json --execute --yes --max-plan-age 30"])
    (a,) = allo_dash.find_agents(here, agents)
    assert a["refreshes"] and a["executes"] and a["max_review_age"] == 600 and a["max_plan_age"] == 30


@pytest.mark.parametrize("spec,now,expect", [
    ({"Hour": 3, "Minute": 0}, datetime(2099, 1, 3, 22, 12), datetime(2099, 1, 4, 3, 0)),
    ({"Hour": 3, "Minute": 0}, datetime(2099, 1, 4, 2, 59), datetime(2099, 1, 4, 3, 0)),
    ({"Hour": 3, "Minute": 0}, datetime(2099, 1, 4, 3, 0), datetime(2099, 1, 5, 3, 0)),
    ({"Minute": 30}, datetime(2099, 1, 4, 10, 40), datetime(2099, 1, 4, 11, 30)),
    ({"Weekday": 1, "Hour": 9, "Minute": 0}, datetime(2099, 1, 3, 12, 0), datetime(2099, 1, 5, 9, 0)),   # Sat -> Mon
    ([{"Hour": 23, "Minute": 0}, {"Hour": 3, "Minute": 0}], datetime(2099, 1, 3, 22, 0), datetime(2099, 1, 3, 23, 0)),
])
def test_next_fire(spec, now, expect):
    assert allo_dash.next_fire(spec, now.replace(tzinfo=UTC)) == expect.replace(tzinfo=UTC)


def agent(executes=True, refreshes=False, plan_age=300, review_age=960, log=None):
    return {"label": "night", "path": "x.plist", "args": [], "calendar": {"Hour": 3, "Minute": 0}, "log": log,
            "executes": executes, "refreshes": refreshes, "max_plan_age": plan_age, "max_review_age": review_age}


def test_evening_plan_without_refresh_is_flagged_before_the_night_run_refuses_it(tmp_path):
    write_cfg(tmp_path, SHELL)
    run(tmp_path, now=NOW)                                  # saved 12:00 UTC; next 03:00 is 15 hours later
    s = state(tmp_path, agents=[agent()])
    assert s["schedule"]["next_run"] == "2099-01-04T03:00:00Z"
    assert s["plan"]["ready_for_next_run"] is False and s["level"] == "attention"
    assert any("next scheduled run will refuse plan.json: it is older than 300 minutes" in i for i in s["issues"])


def test_evening_plan_with_a_refresh_step_is_ready_while_the_review_is_recent(tmp_path):
    write_cfg(tmp_path, SHELL)
    run(tmp_path, now=NOW)
    s = state(tmp_path, agents=[agent(refreshes=True)])     # review limit 960 min covers 15 hours
    assert s["plan"]["ready_for_next_run"] is True and s["level"] == "ok"
    s = state(tmp_path, agents=[agent(refreshes=True, review_age=600)])
    assert s["plan"]["ready_for_next_run"] is False


def test_unloaded_agent_and_refused_last_run_need_attention(tmp_path):
    write_cfg(tmp_path, SHELL)
    run(tmp_path)
    log = tmp_path / "overnight.log"
    log.write_text("RUN plan abc saved x: executing\n\nrefusing to run plan p.json: it is stale: saved 400 minutes ago\n")
    s = state(tmp_path, agents=[agent(log=str(log))], status=lambda label: {"loaded": False})
    issues = " ".join(s["issues"])
    assert "not loaded" in issues and "Last scheduled run refused: refusing to run plan p.json" in issues
    assert s["schedule"]["agents"][0]["log"]["outcome"]["kind"] == "refused"


def test_launchctl_status_reads_print_output(monkeypatch):
    class P:
        returncode, stdout = 0, "\tstate = not running\n\truns = 4\n\tlast exit code = 0\n"
    monkeypatch.setattr(allo_dash.shutil, "which", lambda name: "/bin/launchctl")
    calls = []
    monkeypatch.setattr(allo_dash.subprocess, "run", lambda argv, **k: calls.append(argv) or P())
    assert allo_dash.launchctl_status("x.y") == {"loaded": True, "state": "not running", "runs": "4", "last_exit": "0"}
    assert calls[0][:2] == ["launchctl", "print"]           # never load, unload, bootstrap or kickstart


# Running job

def test_live_running_job_leads_the_headline(tmp_path):
    write_cfg(tmp_path, SHELL)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "current.json").write_text(json.dumps(
        {"task_id": 1, "task_name": "Say hi", "pool": "sub", "at": "2099-01-03T11:58:00Z", "pid": 4242}))
    s = state(tmp_path, alive=lambda pid: True)
    assert (s["level"], s["headline"]) == ("running", "Running #1 on Sub") and s["running"]["seconds"] == 120


def test_marker_left_by_a_dead_process_is_an_issue(tmp_path):
    write_cfg(tmp_path, SHELL)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "current.json").write_text(json.dumps(
        {"task_id": 1, "task_name": "Say hi", "pool": "sub", "at": "2099-01-03T11:58:00Z", "pid": 4242}))
    s = state(tmp_path, alive=lambda pid: False)
    assert s["level"] == "attention" and any("process 4242 is gone" in i for i in s["issues"])


# Example data

def test_example_data_is_flagged_and_routed_by_the_real_planner():
    s = allo_dash.example_state(NOW, UTC)
    assert s["example"] is True and "(example)" in s["config_dir"]
    pools = {p["id"]: p for p in s["pools"]}
    for q in s["queue"]:
        if q["pool"] and pools[q["pool"]]["kind"] == "subscription" and q["tier"] == "public":
            # a public subscription job goes to the eligible plan with the most of its limit left
            assert q["pool"] == "codex" and pools["codex"]["left"] > pools["claude_work"]["left"]
    assert s["running"]["pool"] == "codex" and s["level"] == "running"
    assert s["history"]["saved"]["week_usd"] > 0


# The server

PORT = 8765


def call(method="GET", path="/", host=f"127.0.0.1:{PORT}", fonts=None):
    return allo_dash.respond(method, path, host, PORT, lambda: {"ok": True}, fonts=fonts)


def test_serves_the_page_and_state_to_this_computer():
    code, ctype, body = call()
    assert code == 200 and ctype.startswith("text/html") and b"/api/state" in body
    code, ctype, body = call(path="/api/state", host=f"localhost:{PORT}")
    assert code == 200 and json.loads(body) == {"ok": True}


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1:9999", "", "127.0.0.1.evil.example:8765"])
def test_refuses_other_hosts_so_a_web_page_cannot_read_it_through_dns_rebinding(host):
    assert call(path="/api/state", host=host)[0] == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_is_read_only(method):
    assert call(method, "/api/state")[0] == 405


def test_fonts_are_served_by_name_only(tmp_path):
    (tmp_path / "a.woff2").write_bytes(b"font")
    assert call(path="/fonts/a.woff2", fonts=tmp_path)[:2] == (200, "font/woff2")
    assert call(path="/fonts/../allo.py", fonts=tmp_path)[0] == 404
    assert call(path="/fonts/missing.woff2", fonts=tmp_path)[0] == 404
    assert call(path="/nope")[0] == 404


def test_server_binds_to_loopback_only(monkeypatch):
    import http.server
    bound = []

    class Fake:
        def __init__(self, addr, handler):
            bound.append(addr)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", Fake)
    assert allo_dash.serve(9001, lambda: {}, out=lambda *_: None) == 0
    assert bound == [("127.0.0.1", 9001)]


def test_example_launchd_template_is_recognised_as_refresh_then_execute(tmp_path):
    here, agents = tmp_path / "cfg", tmp_path / "agents"
    here.mkdir(); agents.mkdir()
    text = (Path(__file__).parent.parent / "examples" / "launchd" / "allo-overnight.plist").read_text()
    text = (text.replace("__ALLO__", "/opt/bin/allo-router").replace("__DIR__", str(here.resolve()))
            .replace("__PATH__", "/opt/bin:/usr/bin:/bin"))
    (agents / "allo-overnight.plist").write_text(text)
    (a,) = allo_dash.find_agents(here, agents)
    assert a["refreshes"] and a["executes"] and a["max_review_age"] == 600 and a["max_plan_age"] == 30
    assert a["calendar"] == {"Hour": 3, "Minute": 0} and a["log"] == f"{here.resolve()}/overnight.log"
    refresh_at = a["args"][2].index("--refresh")
    assert refresh_at < a["args"][2].index("--execute") and "&&" in a["args"][2]
