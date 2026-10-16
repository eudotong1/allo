"""allo-router live: each plan's % left and reset time, read from local files and sent when it changes."""
import json
from datetime import datetime, timezone

import allo_live as L

NOW = datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc)


def folder(tmp_path, team_home, personal_home):
    cfg = {"tiers": ["public"], "pools": [
        {"id": "claude_team", "kind": "subscription", "tiers": ["public"], "remaining_pct": 51, "resets_at": "2026-10-18T15:00:00Z",
         "updated_at": "2026-10-16T02:14:00Z", "source": "sync: old", "sync": {"source": "claude", "window": "week", "home": str(team_home)}},
        {"id": "claude_personal", "kind": "subscription", "tiers": ["public"], "remaining_pct": 85, "resets_at": "2026-10-22T04:00:00Z",
         "updated_at": "2026-10-15T04:55:00Z", "source": "typed in", "sync": {"source": "claude", "window": "week", "home": str(personal_home)}},
        {"id": "grok_bot", "kind": "orchestrator", "tiers": ["public"]},
    ]}
    (tmp_path / "pools.json").write_text(json.dumps(cfg))
    return tmp_path


def usage_cache(home, weekly_used, ts=int(datetime(2026, 10, 16, 19, 43, tzinfo=timezone.utc).timestamp())):
    home.mkdir(parents=True, exist_ok=True)
    (home / ".statusline-usage-cache").write_text(
        f"UTILIZATION=28\nRESETS_AT=2026-10-16T22:00:00Z\nTIMESTAMP={ts}\nWEEKLY_UTILIZATION={weekly_used}\nWEEKLY_RESETS_AT=2026-10-18T15:00:00Z\n")


def test_read_plans_reads_local_files_and_never_writes_pools_json(tmp_path):
    usage_cache(tmp_path / "team", 63)
    (tmp_path / "f").mkdir()
    here = folder(tmp_path / "f", tmp_path / "team", tmp_path / "personal")
    before = (here / "pools.json").read_text()
    state = {}
    rows, lines = L.read_plans(state, [here], NOW)
    assert (here / "pools.json").read_text() == before
    by = {r["provider"]: r for r in rows}
    assert by["claude_team"]["left_pct"] == 37 and by["claude_team"]["how"] == "auto"
    assert by["claude_team"]["as_of"] == "2026-10-16T19:43Z" and by["claude_team"]["resets_at"] == "2026-10-18T15:00Z"
    # No local source for the personal plan: the hand-typed reading stays, labelled manual.
    assert by["claude_personal"]["left_pct"] == 85 and by["claude_personal"]["how"] == "manual"
    assert "grok" not in by  # orchestrator: no plan limit, no row
    assert any(ln.startswith("grok_bot (Grok Bot): no plan limit") for ln in lines)
    assert any("claude_personal" in ln and "no limit in local files" in ln for ln in lines)
    assert set(state["pools"]) == {"claude_team", "claude_personal"}


def test_a_newer_hand_typed_reading_is_kept(tmp_path):
    usage_cache(tmp_path / "team", 63)
    (tmp_path / "f").mkdir()
    here = folder(tmp_path / "f", tmp_path / "team", tmp_path / "personal")
    cfg = json.loads((here / "pools.json").read_text())
    cfg["pools"][0].update(remaining_pct=20, updated_at="2026-10-16T19:50:00Z", source="typed in")
    (here / "pools.json").write_text(json.dumps(cfg))
    rows, _ = L.read_plans({}, [here], NOW)
    assert rows[0]["left_pct"] == 20 and rows[0]["how"] == "manual"


def run_once(monkeypatch, state, here):
    monkeypatch.setattr(L.up, "load_state", lambda: state)
    monkeypatch.setattr(L.up, "save_state", lambda s: None)
    monkeypatch.setattr(L.up, "log_upload", lambda *a: None)
    monkeypatch.setattr(L, "build_live_jobs", lambda st, n, d, backfill=False: [])
    monkeypatch.setattr(L, "build_queue", lambda st, d, n: ([], {}))
    monkeypatch.setattr(L, "take_requests", lambda st, t: [])
    sent, out = [], []

    def transport(m, url, payload, tok):
        sent.append(payload)
        return 200, {}
    assert L.cmd_live(["--dir", str(here)], now=NOW, transport=transport, out=out.append) == 0
    return sent, out


def test_live_sends_plans_once_when_sharing_is_on(tmp_path, monkeypatch):
    usage_cache(tmp_path / "team", 63)
    (tmp_path / "f").mkdir()
    here = folder(tmp_path / "f", tmp_path / "team", tmp_path / "personal")
    state = {"salt": "00" * 16, "server": "https://x", "token": "t", "share_readings": True,
             "live": {"queue_fp": L.queue_changed([], {})[1]}}
    sent, out = run_once(monkeypatch, state, here)
    assert len(sent) == 1 and sent[0]["jobs"] == []
    assert [p["left_pct"] for p in sent[0]["plans"]] == [37, 85]
    assert any("claude_team (Claude Team): 37% left" in o for o in out)
    sent, _ = run_once(monkeypatch, state, here)
    assert sent == []  # unchanged: nothing sent
    usage_cache(tmp_path / "team", 70, ts=int(datetime(2026, 10, 16, 19, 55, tzinfo=timezone.utc).timestamp()))
    sent, _ = run_once(monkeypatch, state, here)
    assert sent[0]["plans"][0]["left_pct"] == 30


def test_live_keeps_readings_local_when_sharing_is_off(tmp_path, monkeypatch):
    usage_cache(tmp_path / "team", 63)
    (tmp_path / "f").mkdir()
    here = folder(tmp_path / "f", tmp_path / "team", tmp_path / "personal")
    state = {"salt": "00" * 16, "server": "https://x", "token": "t", "live": {"queue_fp": L.queue_changed([], {})[1]}}
    sent, out = run_once(monkeypatch, state, here)
    assert sent == []
    assert any("not sent" in o and "37% left" in o for o in out)
