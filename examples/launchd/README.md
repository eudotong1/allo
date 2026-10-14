# A nightly run with launchd (macOS)

`allo-router run --plan plan.json --execute` refuses a plan older than `--max-plan-age`, so a plan you review in the evening is too old by a 03:00 run. Running a fresh preview at 03:00 instead would run whatever is in `tasks.json` with nobody having looked at it.

`allo-router run --refresh plan.json` fills that gap without skipping the review. Just before the run it re-checks the plan you reviewed on fresh readings and saves it again only if:

- `pools.json` (apart from readings) and `tasks.json` are exactly as they were when you reviewed it,
- you reviewed it at most `--max-review-age` minutes ago (default 960). This is counted from your review, not from the last refresh, so one review never covers a second night,
- every job you reviewed as runnable comes out with the same plan, prompt, working folder and command. A job that has finished since is skipped, and a job you reviewed as skipped stays skipped.

If anything else is different it saves nothing and exits 2, so the run that follows refuses as it would have without a refresh. A refresh never adds a job and never moves one to another plan.

`allo-overnight.plist` here does both steps in one job, refresh then execute, so they cannot run out of order when the Mac wakes from sleep and launchd starts missed jobs together.

## Install

Fill in the placeholders (absolute paths without spaces), then load it yourself:

    ALLO=$(command -v allo-router); DIR=$HOME/my-router
    sed -e "s#__ALLO__#$ALLO#g" -e "s#__DIR__#$DIR#g" -e "s#__PATH__#$(dirname "$ALLO"):/usr/local/bin:/usr/bin:/bin#g" \
        allo-overnight.plist > ~/Library/LaunchAgents/com.example.allo-overnight.plist
    plutil -lint ~/Library/LaunchAgents/com.example.allo-overnight.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.allo-overnight.plist

`__PATH__` must include the folders that hold the CLIs your pools run (`claude`, `codex`). If an agent with the same job is already loaded, `launchctl bootout` it first.

Each evening, preview and read the plan as usual, before going to bed and after any edit to `tasks.json`:

    allo-router --dir "$DIR" run

`allo-router dashboard` shows whether tonight's run will accept the saved plan, and the outcome of the last one.

## What to know

- With the 600-minute review limit in this plist, a review covers runs until 10 hours later. If the Mac is asleep at 03:00, launchd starts the job when it wakes. A wake within those 10 hours runs the jobs then, perhaps while you are using the same plans; a later wake refuses. Lower `--max-review-age` to narrow that window.
- A `"recurring": "daily"` job counts days in UTC. If you review before midnight UTC and the job already ran that UTC day, the plan you review shows it as already done, and the refresh keeps it skipped. Review after midnight UTC to include it.
- The plan's hash catches accidental edits and drift. It is not a signature: anyone who can write `plan.json` can also write `pools.json`.
