import csv, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent.parent))
import allo_exec, allo_report

ROOT = Path(__file__).parent.parent
NOW = datetime(2099, 1, 3, 12, tzinfo=timezone.utc)
REPORT_NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
PY = sys.executable
# Stand-ins for real CLIs: print what `claude -p --output-format json` / `codex exec --json` print. Never the real thing.
FAKE_CLAUDE = [PY, "-c", "import json; print(json.dumps({'type': 'result', 'result': 'done', 'total_cost_usd': 0.25, "
               "'usage': {'input_tokens': 1000, 'output_tokens': 200}}))"]
FAKE_CODEX = [PY, "-c", "import json; print(json.dumps({'type': 'thread.started'})); "
              "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 50, 'cached_input_tokens': 10, "
              "'output_tokens': 7}}))"]

SUB = {"id": "sub", "label": "Sub", "kind": "subscription", "tiers": ["public", "private"], "remaining_pct": 80,
       "resets_at": "2099-01-05T00:00:00Z", "updated_at": "2099-01-03T11:00:00Z"}
API = {"id": "api", "label": "API", "kind": "credit_usd", "tiers": ["public", "private"], "remaining_usd": 100,
       "resets_at": "2099-01-05T00:00:00Z", "updated_at": "2099-01-03T11:00:00Z"}


def write_cfg(here, pools, tasks):
    cfg = {"tiers": ["public", "private"], "options": {"subscription_max_tier": "private"}, "pools": pools}
    (here / "pools.json").write_text(json.dumps(cfg))
    (here / "tasks.json").write_text(json.dumps({"tasks": tasks}))


def task(i, name="T", est=0, needs=(), pri=None, prompt="hello"):
    return {"id": i, "name": f"{name}{i}", "tier": "public", "est_usd": est, "needs": list(needs),
            "priority": pri or i, "prompt": prompt}


def entry(at, tid, pool="sub", status="ok", **kw):
    e = {"at": at, "task_id": tid, "task_name": f"T{tid}", "pool": pool, "backend": "ShellBackend",
         "status": status, "returncode": 0 if status == "ok" else 1, "seconds": 60.0, "output": None, "plan": "sha256:p"}
    e.update(kw)
    return e


def write_log(here, entries, extra_lines=()):
    (here / "runs.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries) + "".join(extra_lines))


def report(here, *argv):
    lines = []
    code = allo_report.cmd_report(here, list(argv), REPORT_NOW, out=lines.append)
    return code, "\n".join(lines)


def execute(here, *flags, confirm=None):
    lines = []
    assert allo_exec.cmd_run(here, list(flags), NOW, out=lines.append) == 0, lines
    yes = [] if confirm else ["--yes"]
    allo_exec.cmd_run(here, ["--plan", str(here / "plan.json"), "--execute", *yes], NOW,
                      confirm=confirm, out=lines.append, interactive=True)
    return "\n".join(lines)


def log(here):
    return [json.loads(l) for l in (here / "runs.jsonl").read_text().splitlines()]


# Usage the CLI printed

@pytest.mark.parametrize("stdout, want", [
    ('{"type":"result","total_cost_usd":0.25,"usage":{"input_tokens":1000,"output_tokens":200}}',
     {"cost_usd": 0.25, "input_tokens": 1000, "output_tokens": 200}),
    ('{"type":"thread.started"}\n{"type":"turn.completed","usage":{"input_tokens":50,"cached_input_tokens":10,"output_tokens":7}}\n',
     {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 7}),
    ("plain text answer", None),
    ("", None),
    ('{"usage": {"input_tokens": true}, "total_cost_usd": "1.0"}', None),   # wrong types are not usage
    ('{"result": "no usage here"}', None),
])
def test_usage_is_read_only_from_what_the_cli_printed(stdout, want):
    assert allo_report.usage_from_output(stdout) == want


# What the executor now logs for the report

def test_execute_logs_usage_tier_estimate_and_reading_flags(tmp_path):
    write_cfg(tmp_path, [dict(SUB, run={"backend": "shell", "command": FAKE_CLAUDE})], [task(1, est=3)])
    execute(tmp_path)
    (e,) = log(tmp_path)
    assert e["status"] == "ok" and e["usage"] == {"cost_usd": 0.25, "input_tokens": 1000, "output_tokens": 200}
    assert (e["tier"], e["est_usd"], e["pool_kind"], e["pool_flags"]) == ("public", 3, "subscription", [])


def test_claude_cache_tokens_are_shown_next_to_input_tokens():
    u = {"input_tokens": 6, "output_tokens": 672, "cache_read_input_tokens": 14949, "cache_creation_input_tokens": 10343,
         "cost_usd": 0.00255469}
    assert allo_report.usage_text(u) == "$0.0026 reported, 6 in (+25,292 cache) / 672 out tok"
    assert allo_report.usage_text({"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 7}) == "50 in / 7 out tok"


def test_codex_style_jsonl_usage_is_logged(tmp_path):
    write_cfg(tmp_path, [dict(SUB, run={"backend": "shell", "command": FAKE_CODEX})], [task(1)])
    execute(tmp_path)
    assert log(tmp_path)[0]["usage"] == {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 7}


def test_a_backend_can_report_usage_itself(tmp_path):
    class Metered:
        def __init__(self, cfg): pass
        def describe(self, job): return "metered"
        def run(self, job): return {"status": "ok", "returncode": 0, "stdout": "", "stderr": "", "usage": {"cost_usd": 2.0}}
    allo_exec.register_backend("metered-test", Metered)
    try:
        write_cfg(tmp_path, [dict(SUB, run={"backend": "metered-test"})], [task(1)])
        execute(tmp_path)
    finally:
        allo_exec.BACKENDS.pop("metered-test")
    assert log(tmp_path)[0]["usage"] == {"cost_usd": 2.0}


def test_running_on_a_flagged_pool_records_the_flags(tmp_path):
    stale = dict(SUB, updated_at="2099-01-01T00:00:00Z", run={"backend": "shell", "command": FAKE_CLAUDE})
    write_cfg(tmp_path, [stale], [task(1)])
    execute(tmp_path, "--allow-unverified")
    assert log(tmp_path)[0]["pool_flags"] == ["STALE"]
    code, out = report(tmp_path)
    assert "ran on an unverified reading (STALE)" in out


def test_skipped_jobs_are_logged_with_a_kind_under_execute_but_not_in_a_preview(tmp_path):
    runnable = {"backend": "shell", "command": [PY, "-c", "print('hi')"]}
    pools = [dict(SUB, run=runnable), dict(API, id="api", remaining_usd=10)]   # api has no run block
    tasks = [task(1), task(2, est=100, needs=["api"]), task(3, est=1, needs=["api"]), task(4)]
    write_cfg(tmp_path, pools, tasks)
    allo_exec.cmd_run(tmp_path, [], NOW, out=lambda *_: None)
    assert not (tmp_path / "runs.jsonl").exists()                           # a preview logs nothing
    execute(tmp_path, confirm=lambda job: job["task"]["id"] != 4)
    kinds = {e["task_id"]: (e["status"], e.get("skip_kind")) for e in log(tmp_path)}
    assert kinds == {1: ("ok", None), 2: ("skipped", "unassigned"), 3: ("skipped", "manual"),
                     4: ("skipped", "declined")}
    assert all(e["output"] is None for e in log(tmp_path) if e["status"] == "skipped")
    assert not [p for p in (tmp_path / "runs").iterdir() if "task2" in p.name or "task3" in p.name]


@pytest.mark.parametrize("reason, kind", [
    ("unassigned: no pool fits this job", "unassigned"),
    ("pool needs a fresh reading (STALE): run `allo-router set sub ...` first", "needs-reading"),
    ("already ran ok at 2099-01-03T12:00:00Z; pass --rerun to run it again", "already-ran"),
    ("already ran ok under this plan at 2099-01-03T12:00:00Z", "already-ran"),
    ("pool api has no run block; start this job yourself", "manual"),
    ("bad run block on sub: shell backend needs ...", "bad-run-block"),
    ("working folder /x does not exist", "missing-cwd"),
    ("you said no", "declined"),
    ("something new", "other"),
])
def test_skip_kinds_match_the_executors_reasons(reason, kind):
    assert allo_exec.skip_kind(reason) == kind


def test_skip_kinds_cover_the_reasons_select_actually_gives(tmp_path):
    stale = dict(SUB, updated_at="2099-01-01T00:00:00Z", run={"backend": "shell", "command": ["x"]})
    write_cfg(tmp_path, [stale], [task(1)])
    (job,) = allo_exec.select(*allo_exec.allo.load(tmp_path), tmp_path, NOW)
    assert allo_exec.skip_kind(job["skip"]) == "needs-reading"
    write_cfg(tmp_path, [dict(SUB, run={"backend": "shell", "command": "x"})], [task(1)])
    (job,) = allo_exec.select(*allo_exec.allo.load(tmp_path), tmp_path, NOW)
    assert allo_exec.skip_kind(job["skip"]) == "bad-run-block"


# The report

def mixed_log(here):
    write_cfg(here, [SUB, API], [task(1), task(2, est=5)])
    write_log(here, [
        entry("2026-10-12T09:00:00Z", 1),                                                   # 0.2.0-style: no extras
        entry("2026-10-13T10:00:00Z", 2, tier="public", est_usd=0, pool_kind="subscription", pool_flags=[],
              usage={"cost_usd": 0.5, "input_tokens": 100, "output_tokens": 20}),
        entry("2026-10-13T11:00:00Z", 3, pool="api", status="failed", est_usd=4, pool_kind="credit_usd",
              pool_flags=["STALE"]),
        entry("2026-10-14T08:00:00Z", 4, pool=None, status="skipped", backend=None, seconds=None,
              reason="unassigned: no pool fits this job", skip_kind="unassigned", pool_flags=[]),
        entry("2026-10-14T08:00:00Z", 5, status="skipped", reason="you said no", skip_kind="declined", pool_flags=[]),
        entry("2026-10-20T08:00:00Z", 6, status="dry-run", backend="DryRunBackend", returncode=None, pool_flags=[]),
    ], extra_lines=["not json\n", '{"no": "at"}\n'])


def test_report_counts_outcomes_misroutes_and_time(tmp_path):
    mixed_log(tmp_path)
    code, out = report(tmp_path)
    assert code == 0 and out.startswith("# Allo Router report")
    assert "6 entries in range (2 unreadable line(s) skipped)" in out
    # entries | routed | ran | ok | failed | skipped | dry-run | misroutes | run time | reported cost | saved
    assert "| all | 6 | 5 | 3 | 2 | 1 | 2 | 1 | 2 | 3.0 min | $0.50 (1 of 3 jobs) | $0.50 |" in out
    misroutes = out.split("## Misroutes")[1].split("## Jobs")[0]
    assert "#3 T3 | api | failed; ran on an unverified reading (STALE)" in misroutes
    assert "#4 T4 | UNASSIGNED | skipped: unassigned: no pool fits this job" in misroutes
    assert "T5" not in misroutes and "T6" not in misroutes        # declined and dry-run are not misroutes
    jobs = out.split("## Jobs")[1]
    assert "| #1 T1 | sub | ShellBackend | ok | 60.0s | not reported | not recorded | $0.00 (est_usd (tasks.json now)) |" in jobs
    assert "$0.50 reported, 100 in / 20 out tok | fresh | $0.50 (reported)" in jobs


def test_estimate_is_labelled_and_explained(tmp_path):
    mixed_log(tmp_path)
    out = report(tmp_path)[1]
    assert "## Estimated usage saved (ESTIMATE, not a measurement)" in out
    assert "Baseline: api: every job at API price, factor 1.0." in out
    assert "How this is worked out:" in out and "cost basis x (baseline factor - factor of the pool it ran on)" in out
    # api job: $4 est at factor 1 vs baseline 1 saves 0; sub job: $0.50 reported at factor 0 saves $0.50
    assert "| api | credit_usd | 1 | 1 | $4.00 | $4.00 | $4.00 | $0.00 |" in out
    assert "| sub | subscription | 0 | 2 | $0.50 | $0.00 | $0.50 | $0.50 |" in out
    assert "**Est. saved: $0.50** over 3 of 3 jobs that ran" in out


def test_baseline_and_prices_are_configurable(tmp_path):
    mixed_log(tmp_path)
    out = report(tmp_path, "--baseline", "sub")[1]
    assert "Baseline: sub: every job on that pool, factor 0" in out and "**Est. saved: -$4.00**" in out
    out = report(tmp_path, "--baseline", "max", "--price", "sub=0.5")[1]
    assert "max: every job on the priciest pool (api, factor 1)" in out and "**Est. saved: $0.25**" in out
    out = report(tmp_path, "--price", "api=2")[1]
    assert "**Est. saved: -$3.50**" in out


@pytest.mark.parametrize("argv, err", [
    (["--baseline", "nope"], "use api, max or a pool id"),
    (["--price", "ghost=1"], "unknown pool"),
])
def test_bad_baseline_or_price_pool_stops_with_a_message(tmp_path, argv, err):
    mixed_log(tmp_path)
    with pytest.raises(SystemExit, match=err):
        report(tmp_path, *argv)


@pytest.mark.parametrize("argv", [["--price", "sub"], ["--price", "sub=-1"], ["--period", "month"],
                                  ["--since", "2026-10-14", "--until", "2026-10-13"]])
def test_bad_flags_are_usage_errors(tmp_path, argv):
    mixed_log(tmp_path)
    with pytest.raises(SystemExit):
        report(tmp_path, *argv)


def test_since_until_take_whole_utc_days(tmp_path):
    mixed_log(tmp_path)
    out = report(tmp_path, "--since", "2026-10-13", "--until", "2026-10-13")[1]
    assert "2 entries in range" in out and "#1 T1" not in out and "#4 T4" not in out
    assert "from 10/13/2026 00:00 UTC, until before 10/14/2026 00:00 UTC" in out
    out = report(tmp_path, "--since", "2026-10-13T10:30:00Z")[1]
    assert "4 entries in range" in out
    with pytest.raises(SystemExit, match="no zone"):
        report(tmp_path, "--since", "2026-10-13T10:30:00")


def test_period_day_and_week_group_rows_and_add_a_total(tmp_path):
    mixed_log(tmp_path)
    out = report(tmp_path, "--period", "day")[1]
    for label in ("10/12/2026", "10/13/2026", "10/14/2026", "10/20/2026", "**total**"):
        assert f"| {label} |" in out
    out = report(tmp_path, "--period", "week")[1]
    assert "| week of 10/12/2026 | 5 |" in out and "| week of 10/19/2026 | 1 |" in out and "| **total** | 6 |" in out


def test_csv_has_one_row_per_job_with_the_estimate_columns(tmp_path):
    mixed_log(tmp_path)
    code, out = report(tmp_path, "--period", "week", "--csv", str(tmp_path / "r.csv"))
    rows = list(csv.DictReader((tmp_path / "r.csv").open()))
    assert code == 0 and len(rows) == 6 and list(rows[0]) == list(allo_report.CSV_FIELDS)
    r2 = next(r for r in rows if r["task_id"] == "2")
    assert (r2["period"], r2["reported_cost_usd"], r2["cost_basis_source"], r2["est_saved_usd"]) == \
           ("week of 10/12/2026", "0.5", "reported", "0.5")
    r3 = next(r for r in rows if r["task_id"] == "3")
    assert r3["pool_flags"] == "STALE" and r3["misroute"].startswith("failed")
    assert "wrote" not in out                                        # the notice goes to stderr, not the report


def test_missing_log_is_not_an_error(tmp_path):
    code, out = report(tmp_path)
    assert code == 0 and "nothing has been executed there yet" in out


def test_report_works_without_pools_or_tasks_files(tmp_path):
    write_log(tmp_path, [entry("2026-10-13T10:00:00Z", 1, pool_kind="subscription", est_usd=2)])
    out = report(tmp_path)[1]
    assert "| sub | subscription | 0 | 1 | $2.00 | $0.00 | $2.00 | $2.00 |" in out


def test_report_end_to_end_from_a_real_execute(tmp_path):
    pools = [dict(SUB, run={"backend": "shell", "command": FAKE_CLAUDE}),
             dict(API, run={"backend": "shell", "command": [PY, "-c", "raise SystemExit(2)"]})]
    write_cfg(tmp_path, pools, [task(1, name="Summarize"), task(2, name="Api", est=3, needs=["api"])])
    execute(tmp_path)
    code, out = report(tmp_path, "--period", "day", "--csv", str(tmp_path / "r.csv"))
    assert code == 0
    assert "#1 Summarize1 | sub | ShellBackend | ok" in out and "$0.25 reported, 1,000 in / 200 out tok" in out
    assert "#2 Api2 | api | failed" in out.split("## Misroutes")[1]
    assert "**Est. saved: $0.25** over 2 of 2 jobs that ran" in out


def test_report_module_never_imports_process_or_network_code():
    src = (ROOT / "allo_report.py").read_text()
    for mod in ("subprocess", "socket", "urllib", "http", "os.system", "allo_exec"):
        assert f"import {mod}" not in src and f"{mod}." not in src


def test_cli_report_subcommand(tmp_path):
    mixed_log(tmp_path)
    r = subprocess.run([PY, str(ROOT / "allo.py"), "--dir", str(tmp_path), "report", "--csv", str(tmp_path / "c.csv")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("# Allo Router report") and "wrote 6 rows" in r.stderr
    assert (tmp_path / "c.csv").exists()
    help_out = subprocess.run([PY, str(ROOT / "allo.py"), "--help"], capture_output=True, text=True).stdout
    assert "allo-router report" in help_out
