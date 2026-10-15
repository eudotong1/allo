#!/usr/bin/env python3
"""Write a demo router folder with made-up jobs from this week, to try the hosted dashboard locally.

  python3 hosted/worker/scripts/demo_folder.py /tmp/allo-demo

Then connect it to the local server and upload, as a real computer would (see plans/hosted/README.md).
Every name and number here is made up. The names are deliberately private-looking, so `connect --show`
can be checked for what does not leave.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

POOLS = [
    {"id": "codex", "label": "ChatGPT / Codex", "kind": "subscription", "tiers": ["public"], "remaining_pct": 60,
     "run": {"backend": "shell", "command": ["codex", "exec", "{prompt}"]}},
    {"id": "claude_work", "label": "Claude (work plan)", "kind": "subscription", "tiers": ["public", "private"],
     "remaining_pct": 35, "run": {"backend": "shell", "command": ["claude", "-p", "{prompt}"]}},
    {"id": "api_credit", "label": "API credit", "kind": "credit_usd", "tiers": ["public", "private"],
     "remaining_usd": 120, "run": {"backend": "shell", "command": ["llm", "{prompt}"]}},
]

# (day offset from Monday, minute after 03:00, pool, status, extra)
JOBS = [
    (0, 0, "codex", "ok", {}), (0, 3, "codex", "ok", {}), (0, 6, "codex", "ok", {}), (0, 1, "claude_work", "ok", {}),
    (0, 2, "api_credit", "ok", {}), (0, 4, "api_credit", "ok", {}),
    (1, 0, "codex", "ok", {}), (1, 4, "codex", "ok", {}), (1, 9, "codex", "timeout", {}),
    (1, 2, "claude_work", "ok", {}), (1, 5, "claude_work", "ok", {}), (1, 1, None, "skipped", {"skip_kind": "unassigned"}),
    (1, 6, "api_credit", "ok", {}),
    (2, 0, "codex", "ok", {}), (2, 3, "codex", "ok", {}), (2, 6, "codex", "ok", {}),
    (2, 1, "claude_work", "ok", {"pool_flags": ["STALE"]}), (2, 4, "claude_work", "ok", {}),
    (2, 2, None, "skipped", {"skip_kind": "needs-reading"}), (2, 5, "api_credit", "ok", {}),
]


def main(dest):
    here = Path(dest)
    here.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(hour=3, minute=0, second=0, microsecond=0)
    kinds = {p["id"]: p["kind"] for p in POOLS}
    lines = []
    for i, (day, minute, pool, status, extra) in enumerate(JOBS, start=1):
        at = monday + timedelta(days=day, minutes=minute, seconds=17)
        if at > now:
            continue  # only jobs that already happened this week
        e = {"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "task_id": f"acme-{i}", "task_name": f"Acme client job {i}",
             "pool": pool, "backend": "ShellBackend", "status": status, "est_usd": [3.5, 6.0, 12.25, 8.0][i % 4],
             "pool_kind": kinds.get(pool), "pool_flags": [], "output": f"runs/acme-{i}.log", "tier": "private"}
        if status == "skipped":
            e["reason"] = "unassigned: no plan fits" if extra.get("skip_kind") == "unassigned" else "pool needs a fresh reading"
        e.update(extra)
        lines.append(e)
    lines.sort(key=lambda e: e["at"])
    (here / "pools.json").write_text(json.dumps({"tiers": ["public", "private"], "options": {}, "pools": POOLS}, indent=2))
    (here / "tasks.json").write_text(json.dumps({"tasks": []}))
    (here / "runs.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
    print(f"wrote {len(lines)} made-up jobs to {here / 'runs.jsonl'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "allo-demo")
