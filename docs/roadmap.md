# Roadmap

Allo Router goes from *telling* you where each job should run, to *running* it, to keeping your plans busy with less of your attention. Each step keeps the same rules: nothing starts without your say-so, unknown numbers stay unknown, and no step quietly adds network access or secrets.

## Done
- **0.1 Planner.** Ranks plans by what is left before reset, assigns jobs by data tier, credit reserve and headroom, and flags stale or missing readings.
- **Executor (this branch).** `allo-router run`: dry run by default, `--execute` with a question before each job, `shell` and `dry-run` backends, `register_backend` for your own, `runs.jsonl` and per-job output logs, a guard against running a finished job twice.

## Next
1. **Run exactly the plan you approved.** `run` rebuilds the plan when it starts, so a reading that changed after your dry run can change who gets what. Add `allo-router plan --json > plan.json` and `allo-router run --plan plan.json` so the executor runs a saved, reviewed plan, and refuses if the plan is older than a set age.
2. **Per-task backend overrides.** Some jobs need flags of their own (a model, a working folder, an output format). Let a task carry `run_args` that extend its pool's command.
3. **Spend tracking between readings.** Show "estimated used since last reading" from `runs.jsonl` and `est_usd`, next to the reading itself, without ever overwriting the reading.
4. **Parallel runs with a cap per pool.** Run jobs on different pools at the same time, at most N per pool.
5. **Streamed output.** Show a job's output as it arrives while still saving it to `runs/`.
6. **Backend discovery.** Load backends from installed packages through a Python entry point (`allo_router.backends`), so a plug-in does not need its own launcher script.

## Later
- **Scheduling recipe.** A documented cron or launchd setup that runs `--execute --yes --only ...` for pre-approved jobs shortly before a plan resets.
- **Readings from CLIs that report them.** Where a provider's CLI prints its own usage in a documented way, a backend could offer that reading to `allo-router set` for you to confirm. Never by scraping a web dashboard.
- **A local status page** showing pools, the plan and recent runs from the same files.

## Not planned
- Scraping provider dashboards or reusing browser sessions.
- Storing API keys or passwords.
- Spending reset tokens or buying credit on your behalf.
- Deciding whether a provider's terms allow a use. That stays with you.
