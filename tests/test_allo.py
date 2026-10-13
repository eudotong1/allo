import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import allo as planner

ROOT = Path(__file__).parent.parent
NOW = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)


def plan(tasks, pools=None):
    cfg = json.loads((ROOT / "examples/pools.json").read_text())
    rows = planner.pool_rows(pools or cfg["pools"], NOW)
    return {t["id"]: pool for t, pool, _ in planner.propose(tasks, rows, cfg["tiers"], cfg["options"])[0]}


def task(i, tier, usd, needs, pri):
    return {"id": i, "name": "t", "tier": tier, "est_usd": usd, "needs": needs, "priority": pri}


def test_private_task_never_goes_to_a_public_only_subscription():
    out = plan([task(1, "private", 0, [], 1)])
    assert out[1] is None or out[1].startswith("claude_work")


def test_api_job_uses_credit_and_respects_reserve():
    out = plan([task(1, "public", 170, ["api"], 1), task(2, "public", 20, ["api"], 2)])
    assert out[1] == "api_credit"      # 170 fits in 200 * 0.9 = 180
    assert out[2] != "api_credit"      # 10 left in budget, 20 does not fit


def test_unknown_headroom_is_flagged_not_guessed():
    pools = json.loads((ROOT / "examples/pools.json").read_text())["pools"]
    pools = [p for p in pools if p["id"] == "claude_personal"]
    assert "UNKNOWN" in plan([task(1, "public", 0, [], 1)], pools)[1]


def test_unknown_tier_is_rejected():
    try:
        plan([task(1, "secret", 0, [], 1)])
        assert False
    except SystemExit as e:
        assert "not in tiers" in str(e)


def test_stale_and_unread_pools_are_flagged():
    cfg = json.loads((ROOT / "examples/pools.json").read_text())
    flags = {r["p"]["id"]: r["flags"] for r in planner.pool_rows(cfg["pools"], NOW)}
    assert "NO READING" in flags["claude_personal"] and "STALE" in flags["claude_personal"]
    assert flags["chatgpt"] == []


def test_cli_runs_from_an_empty_copy(tmp_path):
    for n in ("pools.json", "tasks.json"):
        (tmp_path / n).write_text((ROOT / "examples" / n).read_text())
    r = subprocess.run([sys.executable, str(ROOT / "allo.py"), "--dir", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0 and "PROPOSED ASSIGNMENT" in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "allo.py"), "--dir", str(tmp_path), "set", "chatgpt", "pct=55"], capture_output=True, text=True)
    assert json.loads((tmp_path / "pools.json").read_text())["pools"][2]["remaining_pct"] == 55.0


def test_init_writes_starter_files_and_refuses_to_overwrite(tmp_path):
    cmd = [sys.executable, str(ROOT / "allo.py"), "--dir", str(tmp_path), "init"]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    r = subprocess.run([sys.executable, str(ROOT / "allo.py"), "--dir", str(tmp_path)], capture_output=True, text=True)
    assert "PROPOSED ASSIGNMENT" in r.stdout and "633" not in r.stdout
    assert subprocess.run(cmd, capture_output=True, text=True).returncode != 0


def test_examples_folder_matches_the_built_in_starter():
    assert json.loads((ROOT / "examples/tasks.json").read_text()) == planner.STARTER_TASKS
    assert json.loads((ROOT / "examples/pools.json").read_text()) == planner.STARTER_POOLS


def test_api_job_stays_unassigned_when_credit_cannot_take_it():
    overflow = plan([task(1, "public", 170, ["api"], 1), task(2, "public", 20, ["api"], 2)])
    assert overflow[1] == "api_credit"
    assert overflow[2] is None
    pools = [p for p in json.loads((ROOT / "examples/pools.json").read_text())["pools"] if p["kind"] != "credit_usd"]
    assert plan([task(1, "public", 10, ["api"], 1)], pools)[1] is None


def test_bad_timestamps_and_estimates_are_rejected():
    assert planner.parse("2026-10-16T20:00:00Z") == datetime(2026, 10, 16, 20, tzinfo=timezone.utc)
    for bad in ("2026-10-16T20:00:00", "tomorrow"):
        try:
            planner.parse(bad)
            assert False
        except SystemExit as e:
            assert bad in str(e)
    try:
        plan([task(1, "public", -1, [], 1)])
        assert False
    except SystemExit as e:
        assert "negative" in str(e)
    pools = json.loads((ROOT / "examples/pools.json").read_text())["pools"]
    pools[0] = dict(pools[0], tiers=["nope"])
    try:
        plan([task(1, "public", 0, [], 1)], pools)
        assert False
    except SystemExit as e:
        assert "not in tiers" in str(e)


def test_set_and_init_refuse_bad_input(tmp_path):
    for n in ("pools.json", "tasks.json"):
        (tmp_path / n).write_text((ROOT / "examples" / n).read_text())
    cmd = [sys.executable, str(ROOT / "allo.py")]
    bad = subprocess.run(cmd + ["--dir", str(tmp_path), "set", "chatgpt", "percent=55"], capture_output=True, text=True)
    assert bad.returncode != 0 and "unknown key" in bad.stderr
    bare = subprocess.run(cmd + ["--dir", str(tmp_path), "set", "chatgpt", "reset=2026-10-16T20:00:00"], capture_output=True, text=True)
    assert bare.returncode != 0 and "no zone" in bare.stderr
    help_out = subprocess.run(cmd + ["--help"], capture_output=True, text=True)
    assert help_out.returncode == 0 and "allo-router" in help_out.stdout
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "tasks.json").write_text("{}\n")
    init = subprocess.run(cmd + ["--dir", str(occupied), "init"], capture_output=True, text=True)
    assert init.returncode != 0 and "already exists" in init.stderr
    assert not (occupied / "pools.json").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = subprocess.run(cmd + ["--dir", str(empty)], capture_output=True, text=True)
    assert missing.returncode != 0 and "pools.json" in missing.stderr and "init" in missing.stderr
