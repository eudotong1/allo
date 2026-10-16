import json
from datetime import datetime, timezone
import allo_live as L


def test_api_value_prices_known_model_only():
    u = {"input_tokens": 1_000_000, "cached_input_tokens": 400_000, "output_tokens": 100_000}
    assert round(L.api_value("gpt-6.1-sol", u), 4) == round(0.6 * 2 + 0.4 * 0.10 + 0.1 * 10, 4)
    assert L.api_value("mystery-model", u) is None
    assert L.api_value("gpt-6.1-sol", None) is None


def test_codex_run_value_matches_lane_dir_and_skips_unpriced(tmp_path):
    started = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    loc = started.astimezone()
    d = tmp_path / f"{loc.year:04d}" / f"{loc.month:02d}" / f"{loc.day:02d}"
    d.mkdir(parents=True)
    stamp = loc.strftime("%Y-%m-%dT%H-%M-%S")
    def write(name, cwd, usage):
        lines = [{"type": "session_meta", "payload": {"cwd": cwd}},
                 {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
                 {"type": "event_msg", "payload": {"type": "token_count", "info": ({"total_token_usage": usage} if usage else None)}}]
        (d / f"rollout-{stamp}-{name}.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    write("a", "/lane", {"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 0})
    write("b", "/other", {"input_tokens": 10**6, "cached_input_tokens": 0, "output_tokens": 0})
    write("c", "/lane", None)  # out-of-credits restart: no usage
    assert L.codex_run_value("/lane", started, started, root=tmp_path) == 0.002
    assert L.codex_run_value("/nowhere", started, started, root=tmp_path) is None


def test_codex_run_info_reports_the_session_model(tmp_path):
    started = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    loc = started.astimezone()
    d = tmp_path / f"{loc.year:04d}" / f"{loc.month:02d}" / f"{loc.day:02d}"
    d.mkdir(parents=True)
    lines = [{"type": "session_meta", "payload": {"cwd": "/lane"}},
             {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}]
    (d / f"rollout-{loc.strftime('%Y-%m-%dT%H-%M-%S')}-x.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    assert L.codex_run_info("/lane", started, started, root=tmp_path) == (None, "gpt-6.1-sol")
    assert L.codex_run_info("/none", started, started, root=tmp_path) == (None, None)


def test_watch_survives_a_network_error_and_keeps_the_cursor(monkeypatch):
    state = {"salt": "00" * 16, "server": "https://x", "token": "t", "live": {"run_log_at": "2026-10-15T12:00Z"}}
    monkeypatch.setattr(L.up, "load_state", lambda: state)
    monkeypatch.setattr(L.up, "save_state", lambda s: None)
    monkeypatch.setattr(L.up, "log_upload", lambda *a: None)
    monkeypatch.setattr(L, "build_live_jobs", lambda st, n, d, backfill=False: (st["live"].update(run_log_at="2026-10-15T13:00Z"),
        [L.job_row(id="h:" + "0" * 16, at="2026-10-15T13:00Z", pool="pool-3", plan_type="subscription", provider="codex", status="running", title="E2E tests")])[1])
    calls = {"n": 0}
    def transport(*a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("handshake timed out")
        raise KeyboardInterrupt
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    out = []
    assert L.cmd_live(["--watch"], transport=transport, out=out.append) == 0
    assert calls["n"] == 2  # kept polling after the error
    assert any("upload failed, retrying" in o for o in out)
