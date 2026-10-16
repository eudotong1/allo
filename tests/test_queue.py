"""Suggested next and "Send to Grok Bot": titles only go up; a click runs exactly one job, locally."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import allo_live as L
import allo_upload as up

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)


def folder(tmp_path):
    here = tmp_path / "router"
    here.mkdir()
    for f in ("pools.json", "tasks.json"):
        shutil.copy(ROOT / "examples" / f, here / f)
    return here


def state():
    return {"salt": "ab" * 16, "server": "https://allo.test", "token": "allo_up_" + "0" * 64, "live": {}, "pools": {}}


def test_queue_has_titles_and_plan_only(tmp_path):
    here = folder(tmp_path)
    q, where = L.build_queue(state(), [here], NOW)
    assert q and all(set(x) == set(up.QUEUE_KEYS) for x in q)
    blob = json.dumps(q)
    for t in json.loads((here / "tasks.json").read_text())["tasks"]:
        if t.get("prompt"):
            assert t["prompt"] not in blob
    assert str(here) not in blob
    up.check_payload({"schema": up.SCHEMA, "sent_at": "2099-01-03T12:00Z", "jobs": [], "queue": q})
    assert all(v[0] == str(here) for v in where.values())


def test_queue_is_sent_only_when_it_changes():
    s = state()
    q = [{"id": "h:" + "1" * 16, "title": "Docs", "pool": "pool-1", "provider": "codex", "priority": 1}]
    changed, fp = L.queue_changed(q, s)
    assert changed
    s["live"]["queue_fp"] = fp
    assert L.queue_changed(q, s)[0] is False


def test_a_click_starts_one_local_run_and_unknown_ids_fail(tmp_path, monkeypatch):
    here = folder(tmp_path)
    s = state()
    monkeypatch.setattr(up, "load_state", lambda: s)
    monkeypatch.setattr(up, "save_state", lambda x: None)
    monkeypatch.setattr(up, "log_upload", lambda *a: None)
    q, where = L.build_queue(s, [here], NOW)
    known = q[0]["id"]
    known2 = q[1]["id"]
    assert all(x["routes"] for x in q)
    posts, spawned = [], []

    def transport(method, url, body, token):
        if method == "GET" and url.endswith("/v1/requests"):
            return 200, {"requests": [{"id": known, "route": q[0]["routes"][0]["pool"]}, {"id": "h:" + "f" * 16, "route": "pool-1"},
                                      {"id": known2, "route": "pool-99"}]}
        posts.append(body)
        return 200, {}
    assert L.cmd_live(["--dir", str(here)], now=NOW, transport=transport, out=lambda *_: None, spawn=spawned.append) == 0
    pool = L.pool_for_route(s, q[0]["routes"][0]["pool"])
    assert len(spawned) == 1 and spawned[0][-4:] == [where[known][0], where[known][1], known, pool]
    assert any(p.get("queue") for p in posts)  # queue went up with the poll
    results = [r for p in posts for r in p.get("results", [])]
    assert {"id": "h:" + "f" * 16, "status": "refused", "reason": "unknown-job"} in results
    assert {"id": known2, "status": "refused", "reason": "unknown-route"} in results


def test_run_request_plans_then_runs_exactly_that_job_and_reports(tmp_path, monkeypatch):
    here = folder(tmp_path)
    s = state()
    monkeypatch.setattr(up, "load_state", lambda: s)
    monkeypatch.setattr(up, "save_state", lambda x: None)
    monkeypatch.setattr(up, "log_upload", lambda *a: None)
    monkeypatch.setattr(up, "upload_once", lambda *a, **k: (0, False))
    calls, posts = [], []

    def fake_run(h, argv, now, out=print):
        calls.append(argv[:2] if argv[0] == "--only" else argv[:1] + argv[2:])
        return 0
    def transport(method, url, body, token):
        posts.append(body)
        return 200, {}
    assert L.run_request(str(here), "1", "h:" + "2" * 16, "chatgpt", now=NOW, transport=transport, out=lambda *_: None, run=fake_run) == "done"
    assert calls == [["--only", "1"], ["--plan", "--execute", "--yes"]]
    assert posts[-1]["results"] == [{"id": "h:" + "2" * 16, "status": "done", "reason": None}]
    assert L.run_request(str(here), "1", "h:" + "3" * 16, "chatgpt", now=NOW, transport=transport, out=lambda *_: None,
                         run=lambda *a, **k: 2) == "failed"


def test_run_request_with_the_real_executor_runs_one_dry_run_job(tmp_path, monkeypatch):
    here = folder(tmp_path)
    s = state()
    monkeypatch.setattr(up, "load_state", lambda: s)
    monkeypatch.setattr(up, "save_state", lambda x: None)
    monkeypatch.setattr(up, "log_upload", lambda *a: None)
    monkeypatch.setattr(up, "upload_once", lambda *a, **k: (0, False))
    out = []
    r = L.run_request(str(here), "1", "h:" + "4" * 16, "chatgpt", now=NOW, transport=lambda *a: (200, {}), out=out.append)
    assert r == "done"
    rows = [json.loads(x) for x in (here / "runs.jsonl").read_text().splitlines()]
    assert {str(x["task_id"]) for x in rows} == {"1"}  # exactly the one requested task
    assert {x["pool"] for x in rows} == {"chatgpt"}  # on the route that was picked, not the planner's


def test_route_override_still_refuses_what_the_executor_would_skip(tmp_path, monkeypatch):
    here = folder(tmp_path)
    s = state()
    monkeypatch.setattr(up, "load_state", lambda: s)
    posts = []
    tr = lambda m, u, b, t: (posts.append(b), (200, {}))[1]
    ran = []
    fake = lambda *a, **k: ran.append(a) or 0
    # claude_personal has no reading in the example config: needs a fresh reading first.
    assert L.run_request(str(here), "1", "h:" + "5" * 16, "claude_personal", now=NOW, transport=tr, out=lambda *_: None, run=fake) == "refused"
    assert posts[-1]["results"][0]["reason"] == "stale-reading"
    assert L.run_request(str(here), "1", "h:" + "6" * 16, "nope", now=NOW, transport=tr, out=lambda *_: None, run=fake) == "refused"
    assert posts[-1]["results"][0]["reason"] == "unknown-route"
    assert ran == []  # nothing started for a refused request
