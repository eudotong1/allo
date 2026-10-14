import json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
import allo, allo_exec, allo_report, allo_sync

NOW = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)
PY = sys.executable
TS = int(datetime(2099, 1, 3, 11, 30, tzinfo=timezone.utc).timestamp())   # when the fake sources took their reading


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """No test reads this computer's real ~/.claude or ~/.codex: HOME points at an empty folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(allo_sync, "sleep", lambda s: pytest.fail("a test waited"))
    return home


def app_cache(folder, used5=7, used_week=21, ts=TS, reset5="2099-01-03T15:00:00Z", reset_week="2099-01-06T15:00:00Z"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".statusline-usage-cache").write_text(
        f"UTILIZATION={used5}\nRESETS_AT={reset5}\nTIMESTAMP={ts}\nPROFILE_NAME=Test\n"
        f"WEEKLY_UTILIZATION={used_week}\nWEEKLY_RESETS_AT={reset_week}\nCOST_USED=1.00\n")


def code_cache(folder, used_week=30, fetched=TS - 3600, reset_week="2099-01-06T15:00:00.125+00:00"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": {}, "cachedUsageUtilization": {
        "fetchedAtMs": fetched * 1000, "utilization": {
            "five_hour": {"utilization": 4, "resets_at": "2099-01-03T15:00:00.1+00:00"},
            "seven_day": {"utilization": used_week, "resets_at": reset_week}}}}))


def transcript(folder, lines):
    d = folder / "projects" / "-proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    os.utime(d / "s.jsonl", (TS, TS))   # readers skip files last written before the window; the test clock is 2099


def msg(mid, rid, at="2099-01-03T10:00:00Z", inp=10, out=5, cache=100, model="claude-haiku-5-5"):
    return {"type": "assistant", "timestamp": at, "requestId": rid,
            "message": {"id": mid, "model": model, "usage": {"input_tokens": inp, "output_tokens": out,
                                                             "cache_read_input_tokens": cache}}}


def codex_session(folder, events, name="rollout-a.jsonl"):
    d = folder / "sessions" / "2099" / "01" / "03"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(json.dumps(e) for e in events) + "\n")
    os.utime(d / name, (TS, TS))


def tc(at, total_in, total_out, primary=None, secondary=None, limit_id="codex", rl=True):
    p = {"type": "token_count", "info": {"total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0,
                                                               "output_tokens": total_out,
                                                               "total_tokens": total_in + total_out}}}
    if rl:
        p["rate_limits"] = {"limit_id": limit_id, "primary": primary, "secondary": secondary, "plan_type": "pro"}
    return {"timestamp": at, "type": "event_msg", "payload": p}


WEEK = {"used_percent": 12.5, "window_minutes": 10080, "resets_at": int(datetime(2099, 1, 7, tzinfo=timezone.utc).timestamp())}
FIVE = {"used_percent": 40.0, "window_minutes": 300, "resets_at": int(datetime(2099, 1, 3, 14, tzinfo=timezone.utc).timestamp())}


def pool(pid="claude", sync=None, **kw):
    p = {"id": pid, "label": pid, "kind": "subscription", "tiers": ["public"], "remaining_pct": None,
         "resets_at": None, "updated_at": None}
    if sync is not None:
        p["sync"] = sync
    p.update(kw)
    return p


def write(here, pools, tasks=()):
    (here / "pools.json").write_text(json.dumps({"tiers": ["public", "private"], "pools": pools}))
    (here / "tasks.json").write_text(json.dumps({"tasks": list(tasks)}))


def pools_now(here):
    return {p["id"]: p for p in json.loads((here / "pools.json").read_text())["pools"]}


# Claude readings

def test_claude_usage_app_cache_gives_a_full_reading(tmp_path):
    app_cache(tmp_path / "c")
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(tmp_path / "c")}), NOW)
    assert r["status"] == "FULL" and r["used_pct"] == 21 and r["resets_at"] == "2099-01-06T15:00:00Z"
    assert r["as_of"] == "2099-01-03T11:30:00Z"   # when the app took it, not when we read the file
    assert r["windows"]["5h"]["used_pct"] == 7 and "Claude Usage app" in r["source"]


def test_claude_freshest_source_wins_and_window_is_selectable(tmp_path):
    c = tmp_path / "c"
    app_cache(c, used_week=21, ts=TS)
    code_cache(c, used_week=30, fetched=TS - 3600)
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c)}), NOW)
    assert r["used_pct"] == 21 and "Claude Usage app" in r["source"]
    code_cache(c, used_week=30, fetched=TS + 60)
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c)}), NOW)
    assert r["used_pct"] == 30 and "cachedUsageUtilization" in r["source"]
    assert r["resets_at"] == "2099-01-06T15:00:00Z"
    code_cache(c, used_week=30, fetched=TS - 3600)
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c), "usage_app": False}), NOW)
    assert r["used_pct"] == 30 and "Claude Usage app" not in r["source"]   # the app can be switched off
    r5 = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c), "window": "5h"}), NOW)
    assert r5["window"] == "5h" and r5["used_pct"] == 7


def test_a_reading_from_a_window_that_has_reset_is_not_used(tmp_path):
    c = tmp_path / "c"
    app_cache(c, reset_week="2099-01-03T00:00:00Z")   # reset before NOW: today's use is unknown
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c)}), NOW)
    assert r["status"] == "UNAVAILABLE" and "reset" in r["note"] and "used_pct" not in r


def test_claude_tokens_are_deduplicated_and_windowed(tmp_path):
    c = tmp_path / "c"
    app_cache(c)
    transcript(c, [msg("m1", "r1"), msg("m1", "r1"),           # one response logged twice (two content blocks)
                   msg("m2", "r2", inp=1, out=1, cache=0),
                   msg("m3", "r3", at="2098-12-01T00:00:00Z"),   # before the window started
                   msg("m4", "r4", model="<synthetic>"),         # an error message, not an API response
                   {"type": "user", "message": {"content": "hi"}}, "not json"])
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c)}), NOW)
    t = r["tokens"]
    assert (t["input_tokens"], t["output_tokens"], t["cache_read_input_tokens"], t["messages"]) == (11, 6, 100, 2)
    assert t["since"] == "2098-12-30T15:00:00Z"   # weekly window start = reset - 7 days


def test_tokens_without_a_limit_are_partial_and_never_a_percentage(tmp_path):
    c = tmp_path / "c"
    transcript(c, [msg("m1", "r1", at="2099-01-03T11:00:00Z")])
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c)}), NOW)
    assert r["status"] == "PARTIAL" and "used_pct" not in r and r["tokens"]["input_tokens"] == 10
    assert "not a share of the plan limit" in r["note"]


def test_nothing_found_is_unavailable(tmp_path):
    r = allo_sync.read_pool(pool(sync={"source": "claude", "home": str(tmp_path / "nowhere")}), NOW)
    assert r["status"] == "UNAVAILABLE" and "no week limit" in r["note"] and "tokens" not in r


def test_default_paths_follow_home_and_claude_config_dir(tmp_path, fake_home, monkeypatch):
    app_cache(fake_home / ".claude", used_week=55)
    assert allo_sync.read_pool(pool(sync={"source": "claude"}), NOW)["used_pct"] == 55
    app_cache(tmp_path / "alt", used_week=66)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "alt"))
    assert allo_sync.read_pool(pool(sync={"source": "claude"}), NOW)["used_pct"] == 66


def test_broken_sources_are_unavailable_not_a_crash(tmp_path):
    c = tmp_path / "c"
    c.mkdir()
    (c / ".statusline-usage-cache").write_text("UTILIZATION=lots\nTIMESTAMP=soon\n")
    (c / ".claude.json").write_text("{not json")
    assert allo_sync.read_pool(pool(sync={"source": "claude", "home": str(c)}), NOW)["status"] == "UNAVAILABLE"


# Codex readings

def test_codex_rate_limits_give_full_readings_per_window(tmp_path):
    x = tmp_path / "x"
    codex_session(x, [tc("2099-01-03T09:00:00Z", 100, 10, primary=dict(FIVE, used_percent=20.0), secondary=WEEK),
                      tc("2099-01-03T11:00:00Z", 300, 30, primary=FIVE, secondary=WEEK),
                      tc("2099-01-03T11:05:00Z", 400, 40, primary={"used_percent": 99.0, "window_minutes": 10080,
                                                                  "resets_at": WEEK["resets_at"]}, limit_id="premium")])
    r = allo_sync.read_pool(pool(sync={"source": "codex", "home": str(x)}), NOW)
    assert r["status"] == "FULL" and r["used_pct"] == 12.5 and r["resets_at"] == "2099-01-07T00:00:00Z"
    assert r["as_of"] == "2099-01-03T11:00:00Z" and "premium" not in r["source"]   # other limit ids are ignored
    assert allo_sync.read_pool(pool(sync={"source": "codex", "home": str(x), "window": "5h"}), NOW)["used_pct"] == 40
    # Tokens are the increase in the running total within the window, across events and files.
    assert r["tokens"]["input_tokens"] == 400 and r["tokens"]["output_tokens"] == 40


def test_codex_older_resets_in_seconds_is_understood(tmp_path):
    x = tmp_path / "x"
    codex_session(x, [tc("2099-01-03T11:00:00Z", 1, 1, primary={"used_percent": 5, "window_minutes": 10080,
                                                               "resets_in_seconds": 3600})])
    r = allo_sync.read_pool(pool(sync={"source": "codex", "home": str(x)}), NOW)
    assert r["windows"]["week"]["resets_at"] == "2099-01-03T12:00:00Z" and r["status"] == "PARTIAL"   # reset is not after NOW
    r = allo_sync.read_pool(pool(sync={"source": "codex", "home": str(x)}), NOW - timedelta(minutes=30))
    assert r["status"] == "FULL" and r["used_pct"] == 5


def test_codex_without_the_window_is_partial(tmp_path):
    x = tmp_path / "x"
    codex_session(x, [tc("2099-01-03T11:00:00Z", 50, 5, primary=WEEK)])
    r = allo_sync.read_pool(pool(sync={"source": "codex", "home": str(x), "window": "5h"}), NOW)
    assert r["status"] == "PARTIAL" and "5h" in r["note"] and r["tokens"]["input_tokens"] == 50


@pytest.mark.parametrize("block, why", [({"source": "gemini"}, "sync source"), ({"source": "claude", "window": "day"}, "window"),
                                        ({"source": "codex", "settle_s": -1}, "settle_s"), ("claude", "must be an object")])
def test_bad_sync_blocks_stop_with_a_message(tmp_path, block, why):
    with pytest.raises(SystemExit, match=why):
        allo_sync.read_pool(pool(sync=block), NOW)


# allo-router sync writes pools.json

def test_sync_writes_full_readings_with_the_sources_time(tmp_path):
    app_cache(tmp_path / "c")
    write(tmp_path, [pool(sync={"source": "claude", "home": str(tmp_path / "c")}), pool("manual")])
    out = []
    assert allo_sync.cmd_sync(tmp_path, [], NOW, out=out.append) == 0
    p = pools_now(tmp_path)
    assert p["claude"]["remaining_pct"] == 79 and p["claude"]["resets_at"] == "2099-01-06T15:00:00Z"
    assert p["claude"]["updated_at"] == "2099-01-03T11:30:00Z" and p["claude"]["source"].startswith("sync: ")
    assert p["claude"]["last_sync"]["status"] == "FULL" and "last_sync" not in p["manual"]
    rows = allo.pool_rows(list(p.values()), NOW)
    assert next(r for r in rows if r["p"]["id"] == "claude")["flags"] == []   # a fresh, planable reading


def test_sync_keeps_a_newer_reading_you_typed_in(tmp_path):
    app_cache(tmp_path / "c")
    write(tmp_path, [pool(sync={"source": "claude", "home": str(tmp_path / "c")}, remaining_pct=50,
                          resets_at="2099-01-06T15:00:00Z", updated_at="2099-01-03T11:45:00Z", source="claude.ai")])
    out = []
    allo_sync.cmd_sync(tmp_path, [], NOW, out=out.append)
    p = pools_now(tmp_path)["claude"]
    assert p["remaining_pct"] == 50 and p["source"] == "claude.ai" and "unchanged" in "\n".join(out)


def test_sync_partial_leaves_the_reading_alone(tmp_path):
    transcript(tmp_path / "c", [msg("m1", "r1", at="2099-01-03T11:00:00Z")])
    write(tmp_path, [pool(sync={"source": "claude", "home": str(tmp_path / "c")}, remaining_pct=40,
                          resets_at="2099-01-06T15:00:00Z", updated_at="2099-01-03T08:00:00Z")])
    allo_sync.cmd_sync(tmp_path, [], NOW, out=lambda s: None)
    p = pools_now(tmp_path)["claude"]
    assert p["remaining_pct"] == 40 and p["updated_at"] == "2099-01-03T08:00:00Z"
    assert p["last_sync"]["status"] == "PARTIAL" and p["last_sync"]["tokens"]["input_tokens"] == 10


def test_sync_dry_run_and_no_blocks(tmp_path):
    app_cache(tmp_path / "c")
    write(tmp_path, [pool(sync={"source": "claude", "home": str(tmp_path / "c")})])
    before = (tmp_path / "pools.json").read_text()
    out = []
    allo_sync.cmd_sync(tmp_path, ["--dry-run"], NOW, out=out.append)
    assert (tmp_path / "pools.json").read_text() == before and "FULL" in out[0] and "not changed" in out[-1]
    write(tmp_path, [pool()])
    out = []
    allo_sync.cmd_sync(tmp_path, [], NOW, out=out.append)
    assert "no pool" in out[-1]
    with pytest.raises(SystemExit, match="no pool with a sync block"):
        allo_sync.cmd_sync(tmp_path, ["--pool", "claude"], NOW, out=out.append)


# Auto-sync before a plan, never before a saved plan runs

def test_auto_sync_before_plan_and_preview_only_for_opted_in_pools(tmp_path, monkeypatch, capsys):
    app_cache(tmp_path / "c")
    write(tmp_path, [pool(sync={"source": "claude", "home": str(tmp_path / "c"), "auto": True}),
                     pool("other", sync={"source": "claude", "home": str(tmp_path / "c")})])
    allo.main(["--dir", str(tmp_path)])
    p = pools_now(tmp_path)
    assert p["claude"]["remaining_pct"] == 79 and p["other"]["remaining_pct"] is None
    assert "sync claude: FULL" in capsys.readouterr().out
    write(tmp_path, [pool(sync={"source": "claude", "home": str(tmp_path / "c"), "auto": True})])
    allo.main(["--dir", str(tmp_path), "--no-sync"])
    assert pools_now(tmp_path)["claude"]["remaining_pct"] is None


def test_a_saved_plan_is_not_resynced(tmp_path, monkeypatch, capsys):
    # Preview syncs and saves the plan. If running the plan re-synced, the fresher reading would change pools.json
    # and the reviewed plan would refuse to run.
    c = tmp_path / "c"
    app_cache(c, ts=int(time.time()) - 60, reset_week="2099-01-06T15:00:00Z")
    write(tmp_path, [pool(sync={"source": "claude", "home": str(c), "auto": True},
                          run={"backend": "dry-run", "command": ["claude", "-p", "{prompt}"]})],
          [{"id": 1, "name": "T", "tier": "public", "est_usd": 0, "needs": [], "priority": 1}])
    with pytest.raises(SystemExit) as e:
        allo.main(["--dir", str(tmp_path), "run"])
    assert e.value.code == 0
    app_cache(c, used_week=50, ts=int(time.time()))   # the source refreshed since the preview
    before = (tmp_path / "pools.json").read_text()
    with pytest.raises(SystemExit) as e:
        allo.main(["--dir", str(tmp_path), "run", "--plan", str(tmp_path / "plan.json")])
    assert e.value.code == 0 and (tmp_path / "pools.json").read_text() == before
    assert "still matches" in capsys.readouterr().out


def test_sync_changes_the_readings_fingerprint(tmp_path):
    a = {"pools": [pool(last_sync={"status": "FULL", "taken_at": "2099-01-03T11:00:00Z"})]}
    b = {"pools": [pool(last_sync={"status": "FULL", "taken_at": "2099-01-03T11:05:00Z"})]}
    assert allo_exec.fingerprints(a, [])[0] == allo_exec.fingerprints(b, [])[0]   # config hash: same
    assert allo_exec.fingerprints(a, [])[1] != allo_exec.fingerprints(b, [])[1]   # readings: changed


# Readings around each job under --execute

def bump_cache(folder, used_week):
    """A stand-in CLI that also plays the usage source refreshing while the job runs. Never the real claude."""
    return [PY, "-c", "import sys, time, pathlib; p = pathlib.Path(sys.argv[1]); "
            "t = p.read_text().splitlines(); "
            "t = [l for l in t if not l.startswith(('TIMESTAMP=', 'WEEKLY_UTILIZATION='))]; "
            f"t += ['TIMESTAMP=%d' % (time.time() + 2), 'WEEKLY_UTILIZATION={used_week}']; "
            "p.write_text(chr(10).join(t) + chr(10)); "
            "print('{\"type\": \"result\", \"total_cost_usd\": 0.01, \"usage\": {\"input_tokens\": 5, \"output_tokens\": 7}}')",
            str(folder / ".statusline-usage-cache")]


def execute(here, now=None, *flags):
    now = now or datetime.now(timezone.utc)
    out = []
    assert allo_exec.cmd_run(here, list(flags), now, out=out.append, interactive=True) == 0, out
    code = allo_exec.cmd_run(here, ["--plan", str(here / "plan.json"), "--execute", "--yes"], now,
                             out=out.append, interactive=True)
    return code, "\n".join(out)


def runs(here):
    return [json.loads(l) for l in (here / "runs.jsonl").read_text().splitlines()]


def sync_pool(c, command, **sync):
    return pool(sync={"source": "claude", "home": str(c), **sync}, remaining_pct=80, resets_at="2099-01-06T15:00:00Z",
                updated_at=allo_sync.iso(datetime.now(timezone.utc)),
                run={"backend": "shell", "command": command, "timeout_s": 30})


TASK = {"id": 1, "name": "T", "tier": "public", "est_usd": 0, "needs": [], "priority": 1, "prompt": "hi"}


def test_job_records_before_and_after_readings_and_the_share_used(tmp_path):
    c = tmp_path / "c"
    app_cache(c, used_week=21, ts=int(time.time()) - 30)
    write(tmp_path, [sync_pool(c, bump_cache(c, 23))], [TASK])
    code, out = execute(tmp_path)
    assert code == 0 and "plan used: 2 pts of the week limit" in out
    e = runs(tmp_path)[0]
    assert e["readings"]["before"]["used_pct"] == 21 and e["readings"]["after"]["used_pct"] == 23
    pu = e["plan_usage"]
    assert pu["measurable"] and pu["used_pts"] == 2 and pu["window"] == "week" and "account-wide" in pu["note"]
    assert pu["windows"]["5h"]["used_pts"] == 0 and e["usage"]["input_tokens"] == 5
    assert pools_now(tmp_path)["claude"]["remaining_pct"] == 80   # readings around a job never touch pools.json


def test_a_source_that_did_not_refresh_is_not_measurable(tmp_path):
    c = tmp_path / "c"
    app_cache(c, ts=int(time.time()) - 30)
    write(tmp_path, [sync_pool(c, [PY, "-c", "print('ok')"])], [TASK])
    code, out = execute(tmp_path)
    pu = runs(tmp_path)[0]["plan_usage"]
    assert code == 0 and not pu["measurable"] and "had not refreshed" in pu["why"] and "used_pts" not in pu
    assert "not measurable" in out


def test_settle_waits_for_a_reading_taken_after_the_job(tmp_path, monkeypatch):
    c = tmp_path / "c"
    app_cache(c, used_week=21, ts=int(time.time()) - 30)
    write(tmp_path, [sync_pool(c, [PY, "-c", "print('ok')"], settle_s=60)], [TASK])
    waits = []

    def refresh(s):   # the source refreshes while we wait
        waits.append(s)
        app_cache(c, used_week=22, ts=int(time.time()) + 5)
    monkeypatch.setattr(allo_sync, "sleep", refresh)
    code, out = execute(tmp_path)
    pu = runs(tmp_path)[0]["plan_usage"]
    assert code == 0 and len(waits) == 1 and pu["measurable"] and pu["used_pts"] == 1


def test_no_readings_for_dry_run_or_pools_without_sync(tmp_path):
    c = tmp_path / "c"
    app_cache(c, ts=int(time.time()))
    p = sync_pool(c, ["claude", "-p", "{prompt}"])
    p["run"]["backend"] = "dry-run"
    write(tmp_path, [p], [TASK])
    execute(tmp_path)
    assert "readings" not in runs(tmp_path)[0]


def test_measure_refuses_across_a_reset_or_a_drop():
    t0, t1 = datetime(2099, 1, 3, 11, tzinfo=timezone.utc), datetime(2099, 1, 3, 11, 1, tzinfo=timezone.utc)
    full = lambda used, reset, as_of: {"status": "FULL", "window": "week", "as_of": as_of, "used_pct": used,
                                       "resets_at": reset, "windows": {"week": {"used_pct": used, "resets_at": reset}}}
    m = allo_sync.measure(full(90, "2099-01-03T11:00:30Z", "2099-01-03T10:59:00Z"),
                          full(1, "2099-01-10T11:00:30Z", "2099-01-03T11:02:00Z"), t0, t1)
    assert not m["measurable"] and "reset" in m["why"]
    m = allo_sync.measure(full(30, "2099-01-06T15:00:00Z", "2099-01-03T10:59:00Z"),
                          full(29, "2099-01-06T15:00:00Z", "2099-01-03T11:02:00Z"), t0, t1)
    assert not m["measurable"] and "went down" in m["why"]
    m = allo_sync.measure({"status": "PARTIAL"}, full(29, "2099-01-06T15:00:00Z", "2099-01-03T11:02:00Z"), t0, t1)
    assert not m["measurable"] and "PARTIAL" in m["why"]


def test_report_shows_measured_share_apart_from_the_estimate(tmp_path):
    c = tmp_path / "c"
    app_cache(c, used_week=21, ts=int(time.time()) - 30)
    write(tmp_path, [sync_pool(c, bump_cache(c, 24))], [TASK])
    execute(tmp_path)
    out = []
    allo_report.cmd_report(tmp_path, ["--csv", str(tmp_path / "r.csv")], datetime.now(timezone.utc), out=out.append)
    text = out[0]
    measured = text.index("## Plan used (MEASURED")
    assert measured < text.index("## Estimated usage saved (ESTIMATE")
    section = text[measured:text.index("## Estimated usage saved")]
    assert "| claude | week | 1 of 1 | 1 | 3 pts | 5 / 0 / 7 (1 of 1 jobs) |" in section
    assert "| 21% | 24% | 3 pts |" in section and "whole percents" in section
    import csv
    row = next(csv.DictReader(open(tmp_path / "r.csv")))
    assert (row["plan_window"], row["plan_used_pts"], row["plan_measurable"]) == ("week", "3.0", "True")


def test_report_without_readings_says_so(tmp_path):
    (tmp_path / "runs.jsonl").write_text(json.dumps({"at": "2099-01-03T10:00:00Z", "task_id": 1, "task_name": "T",
                                                     "pool": "sub", "status": "ok", "seconds": 1}) + "\n")
    out = []
    allo_report.cmd_report(tmp_path, [], NOW, out=out.append)
    assert "No job in this range has usage readings" in out[0]


# Recurring jobs

def rec_task(recurring, tid=7):
    return {"id": tid, "name": "Nightly check", "tier": "public", "est_usd": 0, "needs": [], "priority": 1,
            "prompt": "hi", "recurring": recurring}


def echo_pool():
    return pool("sub", remaining_pct=80, resets_at="2099-01-20T00:00:00Z", updated_at="2099-01-03T11:00:00Z",
                run={"backend": "shell", "command": [PY, "-c", "print('ok')"]})


def test_recurring_daily_runs_once_per_utc_day(tmp_path):
    write(tmp_path, [echo_pool()], [rec_task("daily")])
    day1, later, day2 = NOW, NOW + timedelta(hours=6), NOW + timedelta(days=1)
    execute(tmp_path, day1)
    code, out = execute(tmp_path, later)
    assert "already ran ok" in out and "this daily period, 2099-01-03" in out
    execute(tmp_path, day2.replace(hour=0, minute=30))
    ok = [e for e in runs(tmp_path) if e["status"] == "ok"]
    assert [e["period"] for e in ok] == ["2099-01-03", "2099-01-04"] and ok[0]["recurring"] == "daily"


def test_recurring_weekly_uses_the_iso_week(tmp_path):
    write(tmp_path, [echo_pool()], [rec_task("weekly")])
    stale_ok = "--allow-unverified"   # the fixed test reading goes STALE after a day; not what this test is about
    execute(tmp_path, datetime(2099, 1, 5, 9, tzinfo=timezone.utc), stale_ok)     # Monday
    code, out = execute(tmp_path, datetime(2099, 1, 11, 9, tzinfo=timezone.utc), stale_ok)   # Sunday, same ISO week
    assert "already ran ok" in out
    execute(tmp_path, datetime(2099, 1, 12, 9, tzinfo=timezone.utc), stale_ok)    # next Monday
    assert [e["period"] for e in runs(tmp_path) if e["status"] == "ok"] == ["2099-W02", "2099-W03"]


def test_one_off_tasks_and_old_logs_keep_the_old_key(tmp_path):
    write(tmp_path, [echo_pool()], [dict(rec_task(None), recurring=None)])
    execute(tmp_path, NOW)
    code, out = execute(tmp_path, NOW + timedelta(days=3), "--allow-unverified")
    assert "already ran ok" in out and "period" not in runs(tmp_path)[0]
    # A task that becomes recurring runs again: its earlier one-off run had no period.
    write(tmp_path, [echo_pool()], [rec_task("daily")])
    execute(tmp_path, NOW + timedelta(days=3), "--allow-unverified")
    assert sum(e["status"] == "ok" for e in runs(tmp_path)) == 2


def test_bad_recurring_value_stops_the_plan(tmp_path):
    with pytest.raises(SystemExit, match="recurring 'hourly'"):
        allo.recurring_period({"id": 1, "recurring": "hourly"}, NOW)
