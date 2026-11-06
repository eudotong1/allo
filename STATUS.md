# Job 297 status

Draft PR only. The feature branch was pushed. Nothing was merged, marked ready, published, or uploaded.

## Files

- `tests/test_e2e.py` — offline end-to-end tests. They run `python3 allo.py` (the `cli` entry point) in a temp directory with made-up config.
- `E2E.md` — what those tests cover and how the local packaging check is run.
- `pyproject.toml` — `[build-system] requires` now includes `packaging>=24.2` next to `setuptools>=77`.
- `tests/test_allo.py` — the publish-metadata test asserts that build requirement.
- `CHANGELOG.md` — unreleased note for the tests and the build dependency.
- `README.md` — the Tests section points at `E2E.md`.
- `STATUS.md` — this file.

## What the tests cover

- **init:** `allo-router init` writes starter `pools.json` and `tasks.json`. Every starter pool stays on the `dry-run` backend. A second `init` refuses to overwrite.
- **run:** preview (`run`) saves `plan.json` and starts nothing. `run --plan plan.json --execute` without a terminal and without `--yes` exits 2. With `--yes`, the starter plan executes as dry-run (log lines only, no `runs/` directory). A fake pool whose command is the local Python interpreter actually runs and logs `ok`.
- **status:** there is no `status` subcommand. Status is `report` and `dashboard --json` (headline, running job, history). Both are read-only and local.
- **error paths:** missing config, broken JSON, negative `est_usd`, unknown `set` key, timestamp with no zone, `--dir` with no folder, `--yes` without `--execute`, `--execute` with no saved plan, a plan file edited after preview (exit 2, no `runs.jsonl`), and a job whose command exits 4 (CLI exit 1, status `failed`).
- **packaging:** copy the project to a temp dir, `pip wheel --no-build-isolation --no-deps --no-index`, check wheel metadata, `pip install --prefix` that wheel, run the installed script's `--help` and `--version`. No index, so this does not download or upload.

## Packaging metadata

A local `pip wheel --no-build-isolation` failed while generating metadata:

```
ImportError: Cannot import `packaging.licenses`.
Setuptools>=77.0.0 requires "packaging>=24.2" to work properly.
```

`license = "MIT"` is an SPDX expression. setuptools 77+ canonicalizes it with `packaging.licenses` (packaging 24.2+). setuptools does not depend on packaging, and Ubuntu 24.04's packaging 24.0 is found first, ahead of the copy vendored inside setuptools. The license expression and the removed OSI license classifier are unchanged. The build requirement now lists `packaging>=24.2`.

An isolated `pip wheel` of the previous metadata did succeed, because a clean build env uses setuptools' vendored packaging. The non-isolated local build is the one that failed, and it is the one the new test runs.

With only setuptools 68.1.2 (the copy that shipped on this machine before a newer setuptools was installed for the test run), the same file fails earlier: `project.license` must be a `{file}` or `{text}` table, not the SPDX string. That is why `[build-system] requires` stays at `setuptools>=77`.

## Test results

Command: `python3 -m pytest tests -q --tb=short`

Interpreter: Python 3.12.3. pytest 9.2.0. setuptools 84.2.1 and packaging 26.3 were installed in the user site so the offline wheel build could run; they are not part of the commit.

```
297 passed in 4.39s
```

Exit code 0. No failures.

## Yes/no questions

1. Should a dedicated `allo-router status` subcommand be added? The tests use `report` and `dashboard --json` because those are the read-only status surfaces that exist today.
2. Should the SPDX license stay (`license = "MIT"`, build needs setuptools>=77 and packaging>=24.2)? The other option is `license = {file = "LICENSE"}`, which setuptools 68 can build with no extra dependency.
