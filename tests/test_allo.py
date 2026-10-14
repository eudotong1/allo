import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
import allo as planner

ROOT = Path(__file__).parent.parent
NOW = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)


def example_cfg():
    return json.loads((ROOT / "examples/pools.json").read_text())


def plan(tasks, pools=None, options=None):
    cfg = example_cfg()
    rows = planner.pool_rows(pools or cfg["pools"], NOW)
    return {t["id"]: pool for t, pool, _, _ in planner.propose(tasks, rows, cfg["tiers"], options or cfg["options"])[0]}


def notes(tasks, pools=None):
    cfg = example_cfg()
    rows = planner.pool_rows(pools or cfg["pools"], NOW)
    return {t["id"]: note for t, _, note, _ in planner.propose(tasks, rows, cfg["tiers"], cfg["options"])[0]}


def task(i, tier, usd, needs, pri):
    return {"id": i, "name": "t", "tier": tier, "est_usd": usd, "needs": needs, "priority": pri}


def pool(pid, kind="subscription", tiers=("public",), left=50, reset="2099-01-05T12:00:00Z",
         updated="2099-01-03T11:00:00Z"):
    p = {"id": pid, "label": pid, "kind": kind, "tiers": list(tiers), "resets_at": reset, "updated_at": updated}
    p["remaining_usd" if kind == "credit_usd" else "remaining_pct"] = left
    return p


# Assignment rules

def test_private_task_never_goes_to_a_public_only_subscription():
    out = plan([task(1, "private", 0, [], 1)])
    assert out[1] in (None, "claude_work")


def test_private_task_reaches_a_private_subscription_when_the_cap_allows():
    opts = dict(example_cfg()["options"], subscription_max_tier="private")
    assert plan([task(1, "private", 0, [], 1)], options=opts)[1] == "claude_work"


def test_subscription_cap_keeps_private_jobs_off_subscriptions():
    # claude_work lists private, but subscription_max_tier is public in the examples
    assert plan([task(1, "private", 0, [], 1)])[1] is None


def test_api_job_uses_credit_and_respects_reserve():
    out = plan([task(1, "public", 170, ["api"], 1), task(2, "public", 20, ["api"], 2)])
    assert out[1] == "api_credit"      # 170 fits in 200 * 0.9 = 180
    assert out[2] != "api_credit"      # 10 left in budget, 20 does not fit


def test_priority_decides_who_gets_the_credit_first():
    out = plan([task(1, "public", 170, ["api"], 2), task(2, "public", 20, ["api"], 1)])
    assert out[2] == "api_credit" and out[1] != "api_credit"


def test_credit_pool_without_the_tier_is_not_used():
    pools = [pool("credit", "credit_usd", ("public",), 200), pool("sub", tiers=("public", "private"))]
    opts = dict(example_cfg()["options"], subscription_max_tier="private")
    assert plan([task(1, "private", 10, ["api"], 1)], pools, opts)[1] == "sub"


def test_non_api_job_goes_to_most_headroom():
    out, why = plan([task(1, "public", 0, [], 1)]), notes([task(1, "public", 0, [], 1)])
    assert out[1] == "chatgpt" and why[1] == "90% left"


def test_subscription_below_min_headroom_is_not_used():
    pools = [pool("low", left=10), pool("ok", left=25)]
    assert plan([task(1, "public", 0, [], 1)], pools)[1] == "ok"
    assert plan([task(1, "public", 0, [], 1)], [pool("low", left=10)])[1] is None


def test_unknown_headroom_is_flagged_not_guessed():
    pools = [p for p in example_cfg()["pools"] if p["id"] == "claude_personal"]
    t = [task(1, "public", 0, [], 1)]
    assert plan(t, pools)[1] == "claude_personal"
    assert "UNKNOWN" in notes(t, pools)[1]


def test_known_headroom_beats_unknown():
    pools = [pool("unknown", left=None), pool("known", left=30)]
    assert plan([task(1, "public", 0, [], 1)], pools)[1] == "known"


def test_unknown_tier_is_rejected():
    with pytest.raises(SystemExit, match="not in tiers"):
        plan([task(1, "secret", 0, [], 1)])


def test_unassigned_job_lists_the_pools_its_tier_allows():
    cfg = example_cfg()
    rows = planner.pool_rows(cfg["pools"], NOW)
    (t, pid, note, elig), = planner.propose([task(1, "private", 999, ["api"], 1)], rows, cfg["tiers"], cfg["options"])[0]
    assert pid is None and note == "" and set(elig) == {"claude_work", "api_credit"}


# Pool rows

def test_stale_and_unread_pools_are_flagged():
    flags = {r["p"]["id"]: r["flags"] for r in planner.pool_rows(example_cfg()["pools"], NOW)}
    assert "NO READING" in flags["claude_personal"] and "STALE" in flags["claude_personal"]
    assert flags["chatgpt"] == []


def test_reading_older_than_a_day_is_stale():
    rows = planner.pool_rows([pool("old", updated="2099-01-02T11:00:00Z")], NOW)
    assert rows[0]["flags"] == ["STALE"]


def test_passed_reset_and_missing_reset_are_flagged():
    rows = {r["p"]["id"]: r for r in planner.pool_rows(
        [pool("past", reset="2099-01-03T00:00:00Z"), pool("none", reset=None)], NOW)}
    assert "RESET PASSED, re-read" in rows["past"]["flags"] and rows["past"]["hours"] is None
    assert "NO RESET TIME" in rows["none"]["flags"] and rows["none"]["urgency"] is None


def test_pools_are_ranked_by_urgency():
    rows = planner.pool_rows([pool("slow", left=50, reset="2099-01-07T12:00:00Z"),   # 50 / 96h
                              pool("fast", left=30, reset="2099-01-03T22:00:00Z"),   # 30 / 10h
                              pool("unknown", left=None)], NOW)
    assert [r["p"]["id"] for r in rows] == ["fast", "slow", "unknown"]


def test_build_plan_returns_pool_ids_not_display_text():
    cfg = example_cfg()
    tasks = json.loads((ROOT / "examples/tasks.json").read_text())["tasks"]
    rows, assignments, left = planner.build_plan(cfg, tasks, NOW)
    assert [pid for _, pid, _, _ in assignments] == ["api_credit", "api_credit", "chatgpt", None]
    assert left == 145


# Printout

def test_render_prints_plan_credit_and_warnings():
    out = planner.render(ROOT / "examples", NOW)
    assert "-> chatgpt (90% left)" in out and "-> UNASSIGNED" in out
    assert "Credit left after plan (reserve held): $145" in out
    assert "NEEDS A FRESH READING BEFORE TRUSTING THE PLAN: Claude (personal plan)" in out


def test_render_mentions_reset_tokens_without_spending_them(tmp_path):
    cfg = example_cfg()
    cfg["pools"][0]["reset_tokens"] = [{"kind": "weekly reset", "expires_at": "2099-01-05T12:00:00Z"}]
    (tmp_path / "pools.json").write_text(json.dumps(cfg))
    (tmp_path / "tasks.json").write_text((ROOT / "examples/tasks.json").read_text())
    out = planner.render(tmp_path, NOW)
    assert "RESET TOKEN on Claude (work plan): weekly reset, expires in 2.0 days" in out


# CLI

def run_cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "allo.py"), *args], capture_output=True, text=True)


def copy_examples(tmp_path):
    for n in ("pools.json", "tasks.json"):
        (tmp_path / n).write_text((ROOT / "examples" / n).read_text())


def test_cli_runs_from_an_empty_copy(tmp_path):
    copy_examples(tmp_path)
    r = run_cli("--dir", str(tmp_path))
    assert r.returncode == 0 and "PROPOSED ASSIGNMENT" in r.stdout
    r = run_cli("--dir", str(tmp_path), "set", "chatgpt", "pct=55")
    assert json.loads((tmp_path / "pools.json").read_text())["pools"][2]["remaining_pct"] == 55.0


def test_set_records_usd_reset_and_note(tmp_path):
    copy_examples(tmp_path)
    planner.cmd_set(tmp_path, ["api_credit", "usd=120", "reset=2099-02-01T00:00:00Z", "note=billing page"])
    p = {x["id"]: x for x in json.loads((tmp_path / "pools.json").read_text())["pools"]}["api_credit"]
    assert p["remaining_usd"] == 120.0 and p["resets_at"] == "2099-02-01T00:00:00Z" and p["source"] == "billing page"
    assert p["updated_at"].endswith("Z")


def test_set_rejects_unknown_pool_and_bad_reset(tmp_path):
    copy_examples(tmp_path)
    with pytest.raises(SystemExit, match="unknown pool"):
        planner.cmd_set(tmp_path, ["nope", "pct=5"])
    with pytest.raises(ValueError):
        planner.cmd_set(tmp_path, ["chatgpt", "reset=next tuesday"])


def test_init_writes_starter_files_and_refuses_to_overwrite(tmp_path):
    assert run_cli("--dir", str(tmp_path), "init").returncode == 0
    r = run_cli("--dir", str(tmp_path))
    assert "PROPOSED ASSIGNMENT" in r.stdout and "633" not in r.stdout
    assert run_cli("--dir", str(tmp_path), "init").returncode != 0


def test_examples_folder_matches_the_built_in_starter():
    assert json.loads((ROOT / "examples/tasks.json").read_text()) == planner.STARTER_TASKS
    assert json.loads((ROOT / "examples/pools.json").read_text()) == planner.STARTER_POOLS


def test_planner_module_never_imports_process_or_network_code():
    src = (ROOT / "allo.py").read_text()
    for mod in ("subprocess", "socket", "urllib", "http", "os.system"):
        assert f"import {mod}" not in src and f"{mod}." not in src
