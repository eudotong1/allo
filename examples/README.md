# Examples

- `pools.json`, `tasks.json`: the same starter files `allo-router init` writes. Readings are dated 2099 so they never go stale here; `init` writes dates relative to today instead.
- `custom_backend.py`: a backend that writes each job to an `outbox/` folder instead of starting it.

Try them without touching your own files:

    python3 allo.py --dir examples              # the plan
    python3 allo.py --dir examples run          # what would run, and how

In the starter config:

| pool | run block | what `run --execute` does |
|------|-----------|---------------------------|
| `claude_work` | `shell`: `claude -p {prompt}` | runs Claude Code non-interactively |
| `chatgpt` | `shell`: `codex exec {prompt}` | runs the Codex CLI non-interactively |
| `api_credit` | `dry-run` | logs the job, starts nothing |
| `claude_personal` | none | never runs; start those jobs yourself |

Check those commands against the CLI versions you have before running with `--execute`. Running `--execute` against `--dir examples` writes `runs.jsonl` and `runs/` into this folder; both are gitignored.

To try the custom backend, copy the starter files somewhere, change a pool's run block to `{"backend": "outbox", "folder": "outbox"}`, then:

    python3 examples/custom_backend.py <that-folder>                    # dry run
    python3 examples/custom_backend.py <that-folder> --execute --yes    # writes outbox/*.md
