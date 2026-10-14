# Roadmap

Allo Router goes from *telling* you where each job should run, to *running* it, to keeping your plans busy with less of your attention. Each step keeps the same rules: nothing starts without your say-so, unknown numbers stay unknown, and no step quietly adds network access or secrets.

## Done
- **0.1 Planner.** Ranks plans by what is left before reset, assigns jobs by data tier, credit reserve and headroom, and flags stale or missing readings.
- **0.2 Executor.** `allo-router run` previews and saves a plan; `run --plan plan.json --execute` runs exactly that reviewed plan and refuses if it was edited, is stale, or no longer matches the config or readings. A question before each job, `shell` and `dry-run` backends, `register_backend` for your own, `runs.jsonl` and per-job output logs, a guard against running a finished job twice. The starter config puts every pool on `dry-run`. See [CHANGELOG.md](../CHANGELOG.md).
- **0.3 Readings and results.** `allo-router sync` reads plan usage from local Claude Code and Codex files (FULL, PARTIAL or UNAVAILABLE; never invented), optionally before every plan. The executor reads usage before and after each job, and `allo-router report` shows the measured share of the plan limit used apart from the labelled API-price estimate. Recurring daily and weekly jobs. See [CHANGELOG.md](../CHANGELOG.md).
- **0.4 Dashboard and scheduled runs.** `allo-router dashboard`, a read-only page on your own computer that opens in your browser, with `--install-launcher` for a double-click launcher; `run --refresh` and a launchd recipe so an evening review reaches an overnight run.

## Next
1. **Per-task backend overrides.** Some jobs need flags of their own (a model, a working folder, an output format). Let a task carry `run_args` that extend its pool's command.
2. **Unattended previews for recurring jobs.** `run --refresh` (0.4) carries one evening review to that night's run. Next: let you approve a recurring task once, so a scheduled preview may plan it on later nights without a person, while anything new still waits for review.
3. **Parallel runs with a cap per pool.** Run jobs on different pools at the same time, at most N per pool.
4. **Streamed output.** Show a job's output as it arrives while still saving it to `runs/`.
5. **Backend discovery.** Load backends from installed packages through a Python entry point (`allo_router.backends`), so a plug-in does not need its own launcher script.

## Later
- **Documented usage sources.** Sync reads undocumented local files. Switch each source to a documented interface (a CLI command or file format the provider commits to) when one exists.

## Not planned
- Scraping provider dashboards or reusing browser sessions.
- Storing API keys or passwords.
- Spending reset tokens or buying credit on your behalf.
- Deciding whether a provider's terms allow a use. That stays with you.
