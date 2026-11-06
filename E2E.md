# Offline end-to-end tests

These tests run the real Allo Router entry point, `allo.py` (`cli`, the same function the `allo-router` console script calls). They use a temporary directory and made-up config. They do not log in, upload, publish, or open a network connection.

The suite is `tests/test_e2e.py`. It runs with the rest of the tests:

```
python3 -m pytest tests
```

`tests/conftest.py` blocks sockets in the test process and points `ALLO_ROUTER_CONFIG_DIR` at an empty temp folder, so a machine that has run `allo-router connect` cannot upload a test job.

## What the CLI tests do

| Test | What it runs |
|---|---|
| `test_e2e_init_run_and_status_stay_on_the_dry_run_backend` | `init`, the plan, `sync` (no sync block), `run` (preview), `run --plan … --execute` without `--yes` (refused), then `--execute --yes`. Every starter pool is `dry-run`, so execute logs jobs and starts nothing. Status is `report` and `dashboard --json`. |
| `test_e2e_shell_run_records_ok_and_report_shows_it` | A fake pool whose command is the local Python interpreter. Preview does not run it. Execute does, and `report` / `dashboard --json` show status `ok`. |
| `test_e2e_failed_job_is_exit_1_and_shows_in_status` | The same shape with a command that exits 4. The CLI exits 1 and the status views show `failed`. |
| `test_e2e_missing_config_bad_json_and_init_refuses_to_overwrite` | Missing `pools.json`, a second `init`, broken JSON, a negative `est_usd`. |
| `test_e2e_set_and_run_reject_bad_input` | Unknown `set` key, a timestamp with no zone, `--dir` with no folder, `--yes` without `--execute`, `--execute` with no saved plan. |
| `test_e2e_edited_plan_is_refused_before_anything_starts` | A plan file edited after preview. Execute exits 2 and writes no `runs.jsonl`. |

There is no `status` subcommand. The status of a folder is `allo-router report` (markdown, from `runs.jsonl`) and `allo-router dashboard --json` (the read-only page data: headline, running job, history). Both are local and read-only.

Readings in the fake shell config are stamped from the clock the CLI uses, so a job is not skipped as stale. The starter files from `init` use reset times relative to today and the dry-run backend.

## Local packaging check

`test_offline_wheel_installs_and_help_runs` does this with no package index and no build isolation, so it cannot download or upload anything:

1. Copy the packaging inputs into a temp directory (so the checkout does not gain an `egg-info`).
2. `python3 -m pip wheel … --no-build-isolation --no-deps --no-index`
3. Read the wheel metadata: name `allo-router`, version `0.5.0`, `License-Expression: MIT`, `Requires-Python: >=3.9`, console script `allo-router = allo:cli`.
4. `python3 -m pip install --prefix <temp> --no-index --no-deps --no-build-isolation` the wheel.
5. Run the installed script's `--help` and `--version`.

Build that wheel by hand the same way from a checkout:

```
python3 -m pip wheel . --no-build-isolation --no-deps --no-index -w /tmp/allo-wheels
python3 -m pip install --prefix /tmp/allo-prefix --no-index --no-deps --no-build-isolation /tmp/allo-wheels/allo_router-*.whl
```

That needs `setuptools>=77` and `packaging>=24.2` already installed, because `--no-build-isolation` will not fetch them. An isolated `pip install .` fetches the build requirements itself and still does not upload.

## Metadata fix

`license = "MIT"` is an SPDX expression. setuptools 77 and later canonicalize it with `packaging.licenses`, which exists only in packaging 24.2 and newer. setuptools does not depend on packaging. On Ubuntu 24.04 the first `packaging` on the path is 24.0, and it hides the copy vendored inside setuptools, so `pip wheel --no-build-isolation` dies while generating metadata:

```
ImportError: Cannot import `packaging.licenses`.
Setuptools>=77.0.0 requires "packaging>=24.2" to work properly.
```

`pyproject.toml` now lists `packaging>=24.2` next to `setuptools>=77` in `[build-system] requires`. The license expression and the removed `License :: OSI Approved :: MIT License` classifier are unchanged: current setuptools rejects that classifier once the SPDX expression is set.

Nothing in this check publishes the package or contacts PyPI.
