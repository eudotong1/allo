#!/usr/bin/env python3
"""Plug your own backend into Allo Router.

This one starts nothing. It hands each job to a folder: every job becomes a markdown file in outbox/
that a person, a cron job or another agent picks up. Point a pool at it in pools.json:

    "run": {"backend": "outbox", "folder": "outbox"}

then run this script instead of `allo-router run`. It takes the same flags:

    python3 examples/custom_backend.py <config-folder>                                        preview, saves plan.json
    python3 examples/custom_backend.py <config-folder> --plan <config-folder>/plan.json --execute --yes   write the files
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import allo_exec


class OutboxBackend:
    def __init__(self, cfg):
        folder = cfg.get("folder", "outbox")
        if not isinstance(folder, str) or not folder:
            raise allo_exec.BackendError('outbox backend "folder" must be a folder name')
        self.folder = folder

    def target(self, job):
        slug = re.sub(r"[^a-z0-9]+", "-", job["task"]["name"].lower()).strip("-")
        return Path(job["here"]) / self.folder / f"task{job['task']['id']}-{slug}.md"

    def describe(self, job):
        return f"outbox: write the prompt to {self.target(job)}"

    def run(self, job):
        path = self.target(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        t = job["task"]
        path.write_text(f"# {t['name']}\n\npool: {job['pool']}\ntier: {t['tier']}\n\n{job['prompt']}\n")
        return {"status": "ok", "returncode": 0, "stdout": f"wrote {path}\n", "stderr": ""}


allo_exec.register_backend("outbox", OutboxBackend)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(allo_exec.cmd_run(Path(sys.argv[1]), sys.argv[2:], datetime.now(timezone.utc)))
