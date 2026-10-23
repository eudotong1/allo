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


# Opening it: the browser by default, and a launcher to double-click

def test_dashboard_opens_the_browser_unless_no_open(tmp_path, monkeypatch):
    write_cfg(tmp_path, SHELL)
    calls = []
    monkeypatch.setattr(allo_dash, "serve", lambda port, fn, out, open_browser: calls.append(open_browser) or 0)
    assert allo_dash.cmd_dashboard(tmp_path, [], lambda: NOW, out=lambda *_: None) == 0
    assert allo_dash.cmd_dashboard(tmp_path, ["--no-open"], lambda: NOW, out=lambda *_: None) == 0
    assert calls == [True, False]


def fake_server(monkeypatch, opened, error=None):
    import http.server
    import webbrowser

    class Fake:
        def __init__(self, addr, handler):
            if error:
                raise error

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", Fake)
    monkeypatch.setattr(webbrowser, "open", opened.append)


def test_serve_opens_the_page_once_it_is_listening(monkeypatch):
    opened = []
    fake_server(monkeypatch, opened)
    assert allo_dash.serve(9001, lambda: {}, out=lambda *_: None, open_browser=True) == 0
    assert opened == ["http://127.0.0.1:9001/"]


def test_a_second_start_opens_the_running_dashboard_instead_of_failing(monkeypatch):
    import errno
    opened, out = [], []
    fake_server(monkeypatch, opened, OSError(errno.EADDRINUSE, "in use"))
    monkeypatch.setattr(allo_dash, "already_serving", lambda port: True)
    assert allo_dash.serve(9001, lambda: {}, out=out.append, open_browser=True) == 0
    assert opened == ["http://127.0.0.1:9001/"] and "already running" in out[0]


def test_a_port_taken_by_another_program_is_reported(monkeypatch):
    import errno
    opened, out = [], []
    fake_server(monkeypatch, opened, OSError(errno.EADDRINUSE, "in use"))
    monkeypatch.setattr(allo_dash, "already_serving", lambda port: False)
    assert allo_dash.serve(9001, lambda: {}, out=out.append, open_browser=True) == 2
    assert opened == [] and "--port" in out[0]


@pytest.mark.parametrize("answer, expect", [(b'{"config_dir": "/x", "pools": []}', True), (b'{"other": 1}', False),
                                            (b"<html>", False), (None, False)])
def test_already_serving_only_recognises_a_dashboard(monkeypatch, answer, expect):
    import io
    import urllib.error
    import urllib.request
    seen = []

    class Resp(io.BytesIO):
        status = 200

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("Host")))
        if answer is None:
            raise urllib.error.URLError("refused")
        return Resp(answer)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert allo_dash.already_serving(9001) is expect
    assert seen == [("http://127.0.0.1:9001/api/state", "127.0.0.1:9001")]


def test_macos_launcher_is_an_executable_command_for_this_folder(tmp_path):
    here, home = tmp_path / "my router", tmp_path / "home"
    here.mkdir()
    (paths) = allo_dash.install_launcher(here, platform="darwin", home=home, argv=["/opt/bin/allo-router"],
                                         osacompile=None)
    (cmd,) = paths
    assert cmd == home / "Applications" / "Allo Dashboard.command"
    assert cmd.stat().st_mode & 0o111 == 0o111
    text = cmd.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert f"exec /opt/bin/allo-router --dir '{here.resolve()}' dashboard\n" in text
    assert "--no-open" not in text and "--port" not in text


def test_macos_launcher_runs_the_dashboard_command_even_with_spaces_in_the_path(tmp_path):
    import subprocess
    here, home = tmp_path / "it's my router", tmp_path / "home"
    here.mkdir()
    (cmd,) = allo_dash.install_launcher(here, port=9100, platform="darwin", home=home,
                                        argv=[sys.executable, "-c", "import sys; print(sys.argv[1:])"],
                                        osacompile=None)
    r = subprocess.run([str(cmd)], capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout.strip() == str(["--dir", str(here.resolve()), "dashboard", "--port", "9100"])


def test_macos_app_is_built_with_osacompile_to_open_the_command(tmp_path):
    calls = []
    run = lambda args, **kw: calls.append(args) or type("R", (), {"returncode": 0, "stderr": ""})()
    paths = allo_dash.install_launcher(tmp_path, platform="darwin", home=tmp_path / "home", argv=["allo-router"],
                                       osacompile="/usr/bin/osacompile", run=run)
    cmd, app = paths
    assert app == tmp_path / "home" / "Applications" / "Allo Dashboard.app"
    ((tool, o, out, e, script),) = calls
    assert (tool, o, out, e) == ("/usr/bin/osacompile", "-o", str(app), "-e")
    assert script == f'do shell script "open " & quoted form of "{cmd}"'


def test_a_failed_osacompile_still_leaves_the_command(tmp_path):
    run = lambda args, **kw: type("R", (), {"returncode": 1, "stderr": "boom"})()
    out = []
    paths = allo_dash.install_launcher(tmp_path, platform="darwin", home=tmp_path / "home", argv=["allo-router"],
                                       osacompile="/usr/bin/osacompile", run=run, out=out.append)
    assert [p.name for p in paths] == ["Allo Dashboard.command"] and "boom" in out[0]


def test_linux_launcher_is_a_desktop_entry_run_in_a_terminal(tmp_path):
    here, home = tmp_path / "my router 100%", tmp_path / "home"
    here.mkdir()
    (desk,) = allo_dash.install_launcher(here, platform="linux", home=home, argv=["/usr/bin/allo-router"])
    assert desk == home / ".local" / "share" / "applications" / "allo-dashboard.desktop"
    lines = dict(l.split("=", 1) for l in desk.read_text().splitlines()[1:])
    assert lines["Type"] == "Application" and lines["Name"] == "Allo Dashboard" and lines["Terminal"] == "true"
    folder = str(here.resolve()).replace("%", "%%")
    assert lines["Exec"] == f'/usr/bin/allo-router --dir "{folder}" dashboard'
    assert desk.stat().st_mode & 0o111


def test_desktop_exec_quoting():
    assert allo_dash._desktop_arg("plain") == "plain"
    assert allo_dash._desktop_arg("a b") == '"a b"'
    assert allo_dash._desktop_arg('say "hi" $x') == '"say \\\\"hi\\\\" \\\\$x"'
    assert allo_dash._desktop_arg("50%") == "50%%"


def test_no_launcher_on_other_platforms(tmp_path):
    with pytest.raises(ValueError, match="dashboard"):
        allo_dash.install_launcher(tmp_path, platform="win32", home=tmp_path, argv=["allo-router"])


def test_cli_install_launcher_writes_it_and_starts_nothing(tmp_path, monkeypatch):
    write_cfg(tmp_path, SHELL)
    got = []
    monkeypatch.setattr(allo_dash, "serve", lambda *a, **k: pytest.fail("installing must not start the server"))
    monkeypatch.setattr(allo_dash, "install_launcher",
                        lambda here, port, out: got.append((here, port)) or [tmp_path / "Allo Dashboard.command"])
    out = []
    assert allo_dash.cmd_dashboard(tmp_path, ["--install-launcher"], lambda: NOW, out=out.append) == 0
    assert got == [(tmp_path, 8765)] and out[0].startswith("wrote ") and "close it to stop" in out[-1]


def test_cli_install_launcher_needs_a_config_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(allo_dash, "install_launcher", lambda *a, **k: pytest.fail("no folder to point it at"))
    out = []
    assert allo_dash.cmd_dashboard(tmp_path, ["--install-launcher"], lambda: NOW, out=out.append) == 2
    assert "allo-router init" in out[0]
    with pytest.raises(SystemExit):
        allo_dash.cmd_dashboard(tmp_path, ["--install-launcher", "--example"], lambda: NOW)


def test_dashboard_startup_line_is_flushed_when_stdout_is_piped(tmp_path):
    """The banner is printed once, then the server blocks. Without flush=True a pipe keeps it in the block buffer."""
    import socket
    import subprocess
    import threading
    write_cfg(tmp_path, SHELL)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent.parent / "allo.py"),
         "--dir", str(tmp_path), "dashboard", "--no-open", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = {}

    def read():
        line["text"] = proc.stdout.readline()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(5)
    try:
        assert not thread.is_alive(), "startup line stayed in the block buffer"
        assert line["text"] == (
            f"Allo Router dashboard on http://127.0.0.1:{port}/ "
            "(this computer only, read-only). Ctrl-C to stop.\n")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_landing_and_readme_feature_the_released_dashboard():
    root = Path(__file__).parent.parent
    page, readme = (root / "docs" / "index.html").read_text(), (root / "README.md").read_text()
    for text in (page, readme):
        assert "next release" not in text.lower()
        assert "--install-launcher" in text and "dashboard.png" in text and "Example data" in text
    assert f'<span class="ver">{allo.__version__}</span>' in page
