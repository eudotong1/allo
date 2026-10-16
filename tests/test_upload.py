import json, os, re, stat, sys
from datetime import datetime, timezone
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
import allo
import allo_exec
import allo_upload as up

ROOT = Path(__file__).parent.parent
NOW = datetime(2099, 1, 3, 12, 7, 41, tzinfo=timezone.utc)
CANARY = "CANARY-SECRET"


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    monkeypatch.setenv("ALLO_ROUTER_CONFIG_DIR", str(d))
    return d


class Server:
    """A stand-in for the hosted server. Only these tests stub the transport; every other test has sockets blocked."""

    def __init__(self, token_after=1, jobs_status=200):
        self.calls, self.polls, self.token_after, self.jobs_status = [], 0, token_after, jobs_status

    def __call__(self, method, url, body=None, token=None, timeout=10):
        self.calls.append((method, url, body, token))
        path = url.split("://", 1)[1].split("/", 1)[1]
        if path == "v1/device":
            return 200, {"device_code": "dc", "user_code": "WXYZ-1234", "interval": 1, "expires_in": 60,
                         "verification_uri_complete": "http://test/connect?code=WXYZ-1234"}
        if path == "v1/device/token":
            self.polls += 1
            if self.polls < self.token_after:
                return 400, {"error": "authorization_pending"}
            return 200, {"token": "allo_up_test", "computer": "Computer 1"}
        if path == "v1/jobs":
            return self.jobs_status, ({"stored": len(body["jobs"])} if self.jobs_status == 200 else {"error": "nope"})
        if path == "v1/disconnect":
            return 200, {"ok": True}
        raise AssertionError(url)

    def uploads(self):
        return [c[2] for c in self.calls if c[1].endswith("/v1/jobs")]


def never(*a, **k):
    raise AssertionError("must not send anything")


def write_folder(here, pool_id="acme-redlines", entries=None):
    cfg = {"tiers": ["public", "private"], "pools": [
        {"id": pool_id, "label": f"Acme {CANARY} plan", "kind": "subscription", "tiers": ["public"],
         "remaining_pct": 42, "run": {"backend": "shell", "command": ["/opt/" + CANARY + "/claude", "-p", "{prompt}"],
                                      "cwd": "/Users/someone/" + CANARY}},
        {"id": "api_credit", "label": "API", "kind": "credit_usd", "tiers": ["public"], "remaining_usd": 50},
        {"id": "codex_home", "label": "Codex", "kind": "subscription", "tiers": ["public"],
         "sync": {"source": "codex", "window": "week"}},
    ]}
    (here / "pools.json").write_text(json.dumps(cfg))
    (here / "tasks.json").write_text(json.dumps({"tasks": []}))
    if entries is None:
        entries = sample_entries(pool_id)
    (here / "runs.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))


def sample_entries(pool_id="acme-redlines"):
    secret = {"task_name": "Review client contract", "output": f"runs/{CANARY}.log", "tier": "private",
              "backend": "ShellBackend", "returncode": 0, "seconds": 12.5, "plan": "sha256:" + CANARY}
    return [
        {"at": "2099-01-03T03:00:27Z", "task_id": f"{CANARY}-1", "pool": pool_id, "status": "ok", "est_usd": 20,
         "pool_kind": "subscription", "pool_flags": [], **secret,
         "readings": {"before": {"used_pct": 58, "note": CANARY}, "after": {"used_pct": 61}},
         "plan_usage": {"measurable": True, "used_pts": 3, "window": "week"},
         "usage": {"cost_usd": 4.12, "input_tokens": 1234, "output_tokens": 99}},
        {"at": "2099-01-03T03:05:59Z", "task_id": 14, "pool": "api_credit", "status": "ok", "est_usd": 3,
         "pool_kind": "credit_usd", "pool_flags": [], **secret},
        {"at": "2099-01-03T03:06:00Z", "task_id": 15, "pool": "codex_home", "status": "timeout", "est_usd": 2,
         "pool_kind": "subscription", "pool_flags": ["STALE"], **secret},
        {"at": "2099-01-03T03:07:00Z", "task_id": 16, "pool": None, "status": "skipped", "est_usd": 5,
         "reason": f"unassigned: {CANARY} needs api", "skip_kind": "unassigned", **secret},
        {"at": "2099-01-03T03:08:00Z", "task_id": 17, "pool": pool_id, "status": "skipped", "est_usd": 5,
         "reason": f"already ran ok at {CANARY}", "skip_kind": "already-ran", **secret},
    ]


def show(here, now=NOW):
    lines, errs = [], []
    assert up.cmd_connect(here, ["--show"], now, transport=never, out=lines.append, err=errs.append) == 0
    return json.loads("\n".join(lines)), errs


# ---------- the payload ----------

def test_payload_holds_only_allowlisted_keys(tmp_path):
    write_folder(tmp_path)
    payload, _ = show(tmp_path)
    assert set(payload) == set(up.TOP_KEYS) == {"schema", "sent_at", "jobs"}  # readings are off by default
    assert len(payload["jobs"]) == 5
    for j in payload["jobs"]:
        assert set(j) == set(up.JOB_KEYS)
        assert set(up.JOB_KEYS) == {"id", "at", "pool", "plan_type", "provider", "status", "skip", "miss",
                                    "est_saved_usd", "title", "model", "effort", "heartbeat_at"}


def test_canary_never_leaves(tmp_path):
    """Task names, ids typed as text, prompts, commands, folders, output paths, reasons, readings and labels."""
    write_folder(tmp_path)
    payload, _ = show(tmp_path)
    text = json.dumps(payload)
    for leak in (CANARY, "acme", "Acme", "redlines", "codex_home", "api_credit", "/Users", "runs/", "sha256",
                 "ShellBackend", "private", "1234", "STALE", "used_pct"):
        assert leak not in text, leak


def test_no_percent_left_or_reading_field(tmp_path):
    write_folder(tmp_path)
    payload, _ = show(tmp_path)
    keys = set(payload) | {k for j in payload["jobs"] for k in j}
    for bad in ("pct", "left", "remaining", "reading", "used", "token", "name", "label", "prompt", "path", "cwd"):
        assert not any(bad in k for k in keys), bad
    # remaining_pct in pools.json is 42. Ids are random-salted hex and may contain "42", so they are left out.
    assert "42" not in json.dumps([{k: v for k, v in j.items() if k != "id"} for j in payload["jobs"]])


def test_values(tmp_path):
    write_folder(tmp_path)
    payload, _ = show(tmp_path)
    ok, credit, timeout, unassigned, already = payload["jobs"]
    assert payload["schema"] == 1 and payload["sent_at"] == "2099-01-03T12:07Z"
    assert [j["at"] for j in payload["jobs"]] == ["2099-01-03T03:00Z", "2099-01-03T03:05Z", "2099-01-03T03:06Z",
                                                  "2099-01-03T03:07Z", "2099-01-03T03:08Z"]
    assert all(re.fullmatch(r"h:[0-9a-f]{16}", j["id"]) for j in payload["jobs"])
    assert len({j["id"] for j in payload["jobs"]}) == 5
    # pools are numbered in the order they are first seen; the typed ids never appear
    assert [j["pool"] for j in payload["jobs"]] == ["pool-1", "pool-2", "pool-3", None, "pool-1"]
    assert (ok["plan_type"], ok["provider"]) == ("subscription", "claude_team")  # claude exe, no personal home
    assert (credit["plan_type"], credit["provider"]) == ("credit_usd", "api")
    assert (timeout["plan_type"], timeout["provider"]) == ("subscription", "codex")  # from its sync source
    assert (unassigned["plan_type"], unassigned["provider"]) == (None, None)
    # API value (est.): only a cost the CLI reported; est_usd guesses are never sent
    assert ok["est_saved_usd"] == 4.12 and credit["est_saved_usd"] is None and timeout["est_saved_usd"] is None
    assert unassigned["est_saved_usd"] is None
    assert ok["miss"] == [] and credit["miss"] == []
    assert timeout["miss"] == ["timeout", "unverified-reading"]
    assert unassigned["miss"] == ["unassigned"] and unassigned["skip"] == "unassigned"
    assert already["miss"] == [] and already["skip"] == "already-ran" and already["status"] == "skipped"
    assert ok["title"] == "Review client contract"
    assert timeout["provider"] == "codex" and timeout["model"] is None  # sync source only, no run command


def test_pool_numbering_and_hashes_are_stable_once_connected(tmp_path):
    write_folder(tmp_path)
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))
    a, _ = show(tmp_path)
    b, _ = show(tmp_path)
    assert a == b


def test_show_sends_nothing_and_writes_nothing(tmp_path, config_dir):
    write_folder(tmp_path)
    payload, errs = show(tmp_path)
    assert payload["jobs"] and "Not connected" in errs[0]
    assert not config_dir.exists()


def test_show_prints_exactly_what_upload_sends(tmp_path):
    write_folder(tmp_path)
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))
    shown, errs = show(tmp_path)
    assert errs == []
    srv = Server()
    assert up.cmd_upload(tmp_path, [], NOW, transport=srv, out=lambda s: None) == 0
    assert srv.uploads() == [shown]


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=1),
    lambda p: p["jobs"][0].update(task_name="x"),
    lambda p: p["jobs"][0].update(remaining_pct=40),
    lambda p: p["jobs"][0].pop("miss"),
    lambda p: p["jobs"][0].update(pool="acme-redlines"),
    lambda p: p["jobs"][0].update(provider="my-claude"),
    lambda p: p["jobs"][0].update(at="2099-01-03T03:00:27Z"),
    lambda p: p["jobs"][0].update(miss=["because reasons"]),
    lambda p: p["jobs"][0].update(est_saved_usd="4.12"),
    lambda p: p.update(sent_at="2099-01-03T12:07:41Z"),
])
def test_check_payload_rejects_anything_off_the_list(tmp_path, mutate):
    write_folder(tmp_path)
    payload, _ = show(tmp_path)
    up.check_payload(payload)
    mutate(payload)
    with pytest.raises(up.UploadError):
        up.check_payload(payload)


def test_allowlist_matches_the_server(tmp_path):
    """The Worker keeps the same lists; if one side changes, this fails."""
    js = (ROOT / "hosted" / "worker" / "src" / "schema.js").read_text()

    def arr(name):
        m = re.search(rf"export const {name} = (\[[^\]]*\])", js)
        assert m, name
        return json.loads(m.group(1))

    assert arr("TOP_KEYS") == list(up.TOP_KEYS)
    assert arr("JOB_KEYS") == list(up.JOB_KEYS)
    assert arr("STATUSES") == list(up.STATUSES)
    assert arr("SKIPS") == list(up.SKIPS)
    assert arr("MISSES") == list(up.MISSES)
    assert arr("PLAN_TYPES") == list(up.PLAN_TYPES)
    assert arr("PROVIDERS") == list(up.PROVIDERS)
    assert arr("OPTIONAL_TOP_KEYS") == list(up.OPTIONAL_TOP_KEYS)
    assert arr("PLAN_KEYS") == list(up.PLAN_KEYS)
    assert arr("WINDOW_NAMES") == list(up.WINDOW_NAMES)
    assert arr("HOWS") == list(up.HOWS)
    assert arr("MODELS") == list(up.MODELS)
    assert arr("EFFORTS") == list(up.EFFORTS)
    assert arr("QUEUE_KEYS") == list(up.QUEUE_KEYS)
    assert arr("RESULT_KEYS") == list(up.RESULT_KEYS)
    assert arr("RESULT_STATUSES") == list(up.RESULT_STATUSES)
    assert arr("ROUTE_KEYS") == list(up.ROUTE_KEYS)
    assert arr("REASONS") == list(up.REASONS)
    assert f"export const MAX_ROUTES = {up.MAX_ROUTES};" in js
    assert f"export const MAX_QUEUE = {up.MAX_QUEUE};" in js
    assert f"export const MAX_ROWS = {up.MAX_ROWS};" in js


def test_landing_page_counter_is_fetched_never_hardcoded():
    """The "X of 100 free spots left" on allorouter.com comes from the server's real count, hidden until it loads."""
    html = (ROOT / "docs" / "index.html").read_text()
    assert html.count("data-spots hidden") == 2
    assert '"/v1/spots"' in html and ".catch(function () {})" in html
    assert not re.search(r"\d+\s*(</b>)?\s*of\s*(<b>)?\s*\d+\s*(</b>)?\s*free spots", html)


# ---------- off by default ----------

def test_upload_is_off_until_connect(tmp_path):
    write_folder(tmp_path)
    lines = []
    assert up.cmd_upload(tmp_path, [], NOW, transport=never, out=lines.append) == 1
    assert "Not connected" in lines[0]
    assert up.load_state() is None
    assert up.AfterJobs.if_connected(tmp_path) is None


def test_run_execute_sends_nothing_when_not_connected(tmp_path, monkeypatch):
    """The whole CLI path: preview, then execute a dry-run plan. Sockets are blocked by conftest, and http is
    replaced too, so any attempt to send fails the test."""
    monkeypatch.setattr(up, "http", never)
    (tmp_path / "pools.json").write_text(json.dumps({"tiers": ["public"], "options": {}, "pools": [
        {"id": "sub", "label": "Sub", "kind": "subscription", "tiers": ["public"], "remaining_pct": 80,
         "resets_at": "2099-01-05T00:00:00Z", "updated_at": "2099-01-03T11:00:00Z", "run": {"backend": "dry-run"}}]}))
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": 1, "name": "Hi", "tier": "public", "est_usd": 0, "needs": [], "priority": 1, "prompt": "hi"}]}))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        allo.main(["--no-sync", "run", "--allow-unverified"])
    assert e.value.code == 0
    with pytest.raises(SystemExit) as e:
        allo.main(["run", "--plan", "plan.json", "--execute", "--yes"])
    assert e.value.code == 0
    assert (tmp_path / "runs.jsonl").exists()


def test_only_the_upload_module_has_network_code():
    """allo_dash.py is the one exception, and only for loopback: it checks whether its own page is already running
    on 127.0.0.1 and opens that page in your browser."""
    for name in ("allo.py", "allo_exec.py", "allo_report.py", "allo_sync.py", "allo_dash.py", "allo_dash_page.py"):
        src = (ROOT / name).read_text()
        if name == "allo_dash.py":
            assert src.count("urllib.request.Request(") == 1
            assert 'urllib.request.Request(f"http://127.0.0.1:' in src
            src = src.replace("urllib", "").replace("webbrowser.open(", "")
        for needle in ("urllib", "http.client", "create_connection", "import requests", "socket.socket(",
                       "webbrowser.open("):
            assert needle not in src, (name, needle)


# ---------- connect, upload, disconnect ----------

def test_connect_approves_once_and_stores_token_privately(tmp_path, config_dir):
    srv, lines, opened = Server(token_after=3), [], []
    code = up.cmd_connect(tmp_path, ["--server", "http://test"], NOW, transport=srv, out=lines.append,
                          sleep=lambda s: None, open_url=opened.append)
    assert code == 0, lines
    assert opened == ["http://test/connect?code=WXYZ-1234"]
    assert srv.calls[0][2] == {}  # the CLI sends nothing about this computer to start a connection
    path = config_dir / "connect.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    state = json.loads(path.read_text())
    assert state["token"] == "allo_up_test" and state["server"] == "http://test" and len(state["salt"]) == 64
    lines.clear()
    assert up.cmd_connect(tmp_path, [], NOW, transport=never, out=lines.append) == 0
    assert "Already connected" in lines[0]


def test_connect_declined(tmp_path, config_dir):
    def srv(method, url, body=None, token=None, timeout=10):
        if url.endswith("/v1/device"):
            return 200, {"device_code": "dc", "user_code": "AAAA-0000", "interval": 1, "expires_in": 60}
        return 400, {"error": "access_denied"}
    lines = []
    assert up.cmd_connect(tmp_path, ["--no-browser"], NOW, transport=srv, out=lines.append, sleep=lambda s: None) == 1
    assert "declined" in lines[-1] and not (config_dir / "connect.json").exists()


def test_upload_advances_cursor_and_retries_after_failure(tmp_path):
    write_folder(tmp_path)
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))
    bad = Server(jobs_status=500)
    assert up.cmd_upload(tmp_path, [], NOW, transport=bad, out=lambda s: None) == 1
    srv = Server()
    assert up.cmd_upload(tmp_path, [], NOW, transport=srv, out=lambda s: None) == 0
    assert len(srv.uploads()) == 1 and len(srv.uploads()[0]["jobs"]) == 5
    assert srv.calls[0][3] == "allo_up_x"
    lines = []
    assert up.cmd_upload(tmp_path, [], NOW, transport=srv, out=lines.append) == 0
    assert lines == ["nothing new to upload"] and len(srv.uploads()) == 1
    with open(tmp_path / "runs.jsonl", "a") as f:
        f.write(json.dumps({"at": "2099-01-03T04:00:00Z", "task_id": 99, "pool": "api_credit", "status": "ok",
                            "est_usd": 1, "pool_kind": "credit_usd"}) + "\n")
        f.write('{"at": "2099-01-03T04:01:00Z", "task_id": 100')  # a line still being written
    up.cmd_upload(tmp_path, [], NOW, transport=srv, out=lambda s: None)
    assert len(srv.uploads()[1]["jobs"]) == 1 and srv.uploads()[1]["jobs"][0]["pool"] == "pool-2"
    log = (up.config_dir() / "connect.log").read_text().splitlines()
    assert len(log) == 3 and json.loads(log[0])["result"] == 500


def test_upload_batches_large_logs(tmp_path):
    entries = [{"at": "2099-01-03T03:00:00Z", "task_id": i, "pool": "api_credit", "status": "ok", "est_usd": 1,
                "pool_kind": "credit_usd"} for i in range(1203)]
    write_folder(tmp_path, entries=entries)
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))
    srv = Server()
    assert up.cmd_upload(tmp_path, [], NOW, transport=srv, out=lambda s: None) == 0
    assert [len(p["jobs"]) for p in srv.uploads()] == [500, 500, 203]


def test_disconnect_revokes_and_forgets(tmp_path, config_dir):
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))
    srv, lines = Server(), []
    assert up.cmd_disconnect(tmp_path, ["--keep-history"], NOW, transport=srv, out=lines.append) == 0
    assert srv.calls == [("POST", "http://test/v1/disconnect", {"keep_history": True}, "allo_up_x")]
    assert not (config_dir / "connect.json").exists()


def test_disconnect_forgets_even_when_server_is_unreachable(tmp_path, config_dir):
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))

    def down(*a, **k):
        raise OSError("connection refused")
    lines = []
    assert up.cmd_disconnect(tmp_path, [], NOW, transport=down, out=lines.append) == 1
    assert not (config_dir / "connect.json").exists() and "web page" in lines[0]


# ---------- uploads during a run: debounced ----------

class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_after_jobs_sends_at_most_once_per_15s_and_flushes_at_the_end(tmp_path):
    write_folder(tmp_path)
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))
    clock, srv = Clock(), Server()
    aj = up.AfterJobs.if_connected(tmp_path, out=lambda s: None, transport=srv, clock=clock, sleep=clock.sleep)

    def job(i):
        with open(tmp_path / "runs.jsonl", "a") as f:
            f.write(json.dumps({"at": "2099-01-03T04:00:00Z", "task_id": i, "pool": "api_credit", "status": "ok",
                                "est_usd": 1, "pool_kind": "credit_usd"}) + "\n")
        aj.job_done()

    aj.job_done()                       # first job: sends at once
    assert len(srv.uploads()) == 1
    clock.t += 5; job(20)               # 5 s later: waits
    clock.t += 5; job(21)               # 10 s: still waits
    assert len(srv.uploads()) == 1
    clock.t += 1; aj.finish()           # run ends at 11 s: waits out the last 4 s, then sends both rows once
    assert clock.slept == [pytest.approx(4)] and len(srv.uploads()) == 2 and len(srv.uploads()[1]["jobs"]) == 2
    aj.finish()                         # nothing pending: nothing sent
    assert len(srv.uploads()) == 2
    clock.t += 16; job(22)              # a later job, past the window: sends at once
    assert len(srv.uploads()) == 3


def test_after_jobs_never_raises(tmp_path):
    write_folder(tmp_path)
    up.save_state(up.new_state("http://test", "allo_up_x", NOW))

    def down(*a, **k):
        raise OSError("no route")
    lines = []
    aj = up.AfterJobs.if_connected(tmp_path, out=lines.append, transport=down, clock=Clock(), sleep=lambda s: None)
    aj.job_done()
    assert "upload skipped" in lines[0]


def test_run_calls_after_job_for_every_logged_line(tmp_path):
    (tmp_path / "pools.json").write_text(json.dumps({"tiers": ["public"], "options": {}, "pools": [
        {"id": "sub", "label": "Sub", "kind": "subscription", "tiers": ["public"], "remaining_pct": 80,
         "resets_at": "2099-01-05T00:00:00Z", "updated_at": "2099-01-03T11:00:00Z", "run": {"backend": "dry-run"}}]}))
    (tmp_path / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": i, "name": f"T{i}", "tier": "public", "est_usd": 0, "needs": ["api"] if i == 3 else [],
         "priority": i, "prompt": "hi"} for i in (1, 2, 3)]}))
    plan_now = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)
    assert allo_exec.cmd_run(tmp_path, [], plan_now, out=lambda s: None) == 0
    calls = []
    allo_exec.cmd_run(tmp_path, ["--plan", str(tmp_path / "plan.json"), "--execute", "--yes"], plan_now,
                      out=lambda s: None, after_job=lambda: calls.append(1))
    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 3 and len(calls) == 3   # two dry runs and one unassigned skip
    # a preview never calls it
    calls.clear()
    allo_exec.cmd_run(tmp_path, [], plan_now, out=lambda s: None, after_job=lambda: calls.append(1))
    assert calls == []


def test_provider_personal_vs_team_and_title_model_effort(tmp_path):
    cfg = {"tiers": ["public"], "pools": [
        {"id": "personal", "kind": "subscription", "tiers": ["public"],
         "sync": {"source": "claude", "home": "/Users/x/.claude-personal"},
         "run": {"backend": "shell", "command": ["claude", "-p", "--model", "opus", "--effort", "high", "{prompt}"],
                 "env": {"CLAUDE_CONFIG_DIR": "/Users/x/.claude-personal"}}},
        {"id": "team", "kind": "subscription", "tiers": ["public"],
         "sync": {"source": "claude"},
         "run": {"backend": "shell", "command": ["claude", "-p", "--effort", "very_high", "{prompt}"]}},
        {"id": "gpt", "kind": "subscription", "tiers": ["public"],
         "sync": {"source": "codex"},
         "run": {"backend": "shell", "command": ["codex", "exec", "{prompt}"]}},
    ]}
    (tmp_path / "pools.json").write_text(__import__("json").dumps(cfg))
    (tmp_path / "tasks.json").write_text('{"tasks": []}')
    entries = [
        {"at": "2099-01-03T03:00:00Z", "task_id": 1, "task_name": "Industry pages", "pool": "personal",
         "status": "ok", "pool_kind": "subscription", "pool_flags": [], "est_usd": 1},
        {"at": "2099-01-03T03:01:00Z", "task_id": 2, "task_name": "Security audit", "pool": "team",
         "status": "ok", "pool_kind": "subscription", "pool_flags": [], "est_usd": 1},
        {"at": "2099-01-03T03:02:00Z", "task_id": 3, "task_name": "i18n", "pool": "gpt",
         "status": "ok", "pool_kind": "subscription", "pool_flags": [], "est_usd": 1},
    ]
    (tmp_path / "runs.jsonl").write_text("".join(__import__("json").dumps(e) + "\n" for e in entries))
    payload, _ = show(tmp_path)
    a, b, c = payload["jobs"]
    assert (a["provider"], a["title"], a["model"], a["effort"]) == ("claude_personal", "Industry pages", "opus", "high")
    assert (b["provider"], b["title"], b["effort"]) == ("claude_team", "Security audit", "very_high")
    assert (c["provider"], c["title"], c["model"]) == ("codex", "i18n", "gpt-5-codex")


def test_plan_rows_label_auto_and_manual_and_drop_labels():
    cfg = {"pools": [
        {"id": "codex_main", "label": "ChatGPT (secret label)", "kind": "subscription", "remaining_pct": 61.5,
         "resets_at": "2026-10-16T22:30:00Z", "updated_at": "2026-10-15T23:58:12Z", "source": "sync: codex",
         "sync": {"source": "codex", "window": "week"}, "run": {"command": ["codex", "exec", "{prompt}"]}},
        {"id": "grok", "kind": "subscription", "remaining_pct": 40, "updated_at": "2026-10-15T20:00:00Z", "source": "manual"},
        {"id": "empty", "kind": "subscription"},
    ]}
    pools = {}
    rows = up.plan_rows(cfg, pools)
    assert [r["how"] for r in rows] == ["auto", "manual"]
    assert rows[0]["left_pct"] == 61.5 and rows[0]["resets_at"] == "2026-10-16T22:30Z" and rows[0]["window"] == "week"
    assert rows[1]["resets_at"] is None and rows[1]["window"] is None
    assert "secret" not in json.dumps(rows) and "codex_main" not in json.dumps(rows)
    payload = {"schema": up.SCHEMA, "sent_at": "2026-10-15T23:59Z", "jobs": [], "plans": rows}
    up.check_payload(payload)


def test_readings_go_up_only_when_shared(tmp_path):
    cfg = {"pools": [{"id": "codex_main", "kind": "subscription", "remaining_pct": 50, "resets_at": "2026-10-16T22:30:00Z",
                      "updated_at": "2026-10-15T23:58:00Z", "source": "sync: codex", "sync": {"source": "codex"}}]}
    now = up.datetime(2026, 10, 16, tzinfo=up.timezone.utc)
    assert "plans" not in up.build_payload([], cfg, "s" * 64, {}, now)
    shared = up.build_payload([], cfg, "s" * 64, {}, now, share_readings=True)
    assert shared["plans"][0]["how"] == "auto" and shared["plans"][0]["window"] == "week"
