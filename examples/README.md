# Examples

- `pools.json`, `tasks.json`: the same starter files `allo-router init` writes. Readings are dated 2099 so they never go stale here; `init` writes dates relative to today instead.
- `custom_backend.py`: a backend that writes each job to an `outbox/` folder instead of starting it.

Try them without touching your own files:

    python3 allo.py --dir examples              # the plan
    python3 allo.py --dir examples run          # what would run, and how (also saves examples/plan.json, gitignored)

In the starter config:

Every starter pool is on the `dry-run` backend, so `run --execute` logs each job and starts nothing:

| pool | run block | command shown in the dry run |
|------|-----------|------------------------------|
| `claude_work` | `dry-run` | `claude -p {prompt}` |
| `claude_personal` | `dry-run` | `claude -p {prompt}` |
| `chatgpt` | `dry-run` | `codex exec {prompt}` |
| `api_credit` | `dry-run` | none |

To make a pool run for real, copy the starter files to your own folder (or use `allo-router init`), check the shown command against the CLI you have, and change that pool's `"backend"` to `"shell"`. Do not opt in pools in this folder; it is the reference copy. If two pools use the same CLI under different logins, give each an `env` that points the CLI at its own config folder (see your CLI's docs for the variable).

To try the custom backend, copy the starter files somewhere, change a pool's run block to `{"backend": "outbox", "folder": "outbox"}`, then:

    python3 examples/custom_backend.py <that-folder>                                           # preview, saves plan.json
    python3 examples/custom_backend.py <that-folder> --plan <that-folder>/plan.json --execute --yes   # writes outbox/*.md
