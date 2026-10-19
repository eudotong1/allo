# Dashboard redesign mockups

Three static directions for `allo-router dashboard`. This redesign gates the x.ai marketplace submission. Pick one (or a mix) and it gets built into `allo_dash_page.py`.

Every mockup uses the same `--example` data the current dashboard ships with (Fri 22:12, job #14 running on Codex, 03:00 run queued), so the comparison is like for like. They are plain HTML with no JavaScript, use only the fonts already in `docs/fonts/`, and are not wired to `/api/state`. Open any file straight from disk.

| | Desktop (1440) | Phone (390) |
|---|---|---|
| **D · Combined (chosen)** | [d-combined.html](d-combined.html) · [png](screenshots/d-combined-desktop.png) · [diagram open](screenshots/d-combined-desktop-diagram-open.png) | [png](screenshots/d-combined-mobile.png) |
| Current | [current-desktop.png](screenshots/current-desktop.png) | n/a |
| A · The Yard | [a-yard.html](a-yard.html) · [png](screenshots/a-yard-desktop.png) | [png](screenshots/a-yard-mobile.png) |
| B · Ledger | [b-ledger.html](b-ledger.html) · [png](screenshots/b-ledger-desktop.png) | [png](screenshots/b-ledger-mobile.png) |
| C · Night Board | [c-night-board.html](c-night-board.html) · [png](screenshots/c-night-board-desktop.png) | [png](screenshots/c-night-board-mobile.png) |

## Chosen direction: D · Combined

Decided 10/19: **B as the base, A's route diagram as an optional expandable view, C's countdown and checklist in B's next-run column.** A, B and C stay below as the options this was picked from.

- **01 Now** keeps B's three columns. The middle column is now C's countdown ("4:48 h:min to go" to the 03:00 run) and a pre-run checklist: plan still matches, launchd agent loaded, and how many plans have a reading. The checklist only reports the count; the "Needs you" column says which plan and what to run, so the warning isn't stated twice.
- **02 Plans** is B's ledger. Under it, "Show route diagram" opens A's diagram, redrawn at B's column width so its text stays readable rather than shrinking. It uses a native `<details>`, so no JavaScript is needed. It is closed by default, so the ledger stays the main view; the label switches to "Hide" when open. On a phone the diagram scrolls sideways.
- **03 Jobs** and **04 This week** are unchanged from B.

Every element maps to a field `/api/state` already returns: the countdown comes from `schedule.next_run`, the checklist from `plan.check`, the launchd agent status and the pools' readings, and the diagram from `pools` and `queue`. Building it into `allo_dash_page.py` needs no backend change. The open/closed state of the diagram could be remembered per browser with `localStorage`.



- **The route diagram collides with itself.** Queued job chips carry truncated job names ("Write tests fo…", "Tighten the READ…"). They overlap each other, and they overlap the Codex reset label ("…esets Sun 02:12"). This is the most visible defect in the screenshot a reviewer will see first.
- **No reading order.** The headline, the next run, the diagram, the jobs table and the week stats all have similar weight, so the eye has no place to start.
- **Warnings sit in body text.** "Claude, personal: NO READING, STALE" is a plain line under the headline, styled the same as everything else.
- **One table mixes three tenses.** The jobs table interleaves what is running, what will run at 03:00 and what already ran, separated only by the "When" column.

All three mockups fix these four. They differ in what the page is *about*.

## A · The Yard: keep the identity, fix the drawing

The route diagram (tasks.json → allo-router → one track per plan, ending at its reset) is the one visual nobody else has. A keeps it and makes it hold up.

- Plan names and amount left move into a fixed left gutter, so nothing on a track can collide with a label.
- Chips show the job number only. Names live in the table, keyed by the same number.
- Headline is answer-first: what is running, why that plan, and how far it is against the job time limit.
- "Needs you" warnings get their own ruled line in red. The jobs table is split into Running / Next run, 03:00 / Done today.

**Tradeoff:** the diagram scrolls sideways on phones (shown in the phone screenshot). It is the least change from today, so it is the cheapest to build.

## B · Ledger: the page as a statement

This treats the dashboard as an account statement: numbered sections (01 Now, 02 Plans, 03 Jobs, 04 This week), a double rule under the masthead, light-weight figures, and colour reduced to small keys.

- Plans become a ledger: amount left as a large figure, a runway bar of what is left, the burn rate needed to use it all, the reset time with a countdown, and the job numbers on that plan.
- API credit gets a thin line instead of a bar, because dollars have no weekly cap to fill against. A full bar would claim a limit that does not exist.
- The Now band has three columns: running, next run, needs you. A first-time reviewer knows the state in about five seconds.

**Tradeoff:** it drops the route diagram, which is the most distinctive visual. It reads as the most "finished product" of the three, and it reflows best on a phone.

## C · Night Board: built for the 22:00 check

People mostly open this page in the evening, to see whether tonight's run will go. C is a departures board on a warm near-black background: a large countdown to the next run, a pre-flight checklist (plan still matches, launchd agent loaded, which plans need a reading), then the departures.

- The meters have 20 ticks instead of a continuous bar. That is honest about precision, because readings are whole percentage points from a sync.
- Plan colours are lifted for contrast on dark. There are no glows or gradients.

**Tradeoff:** it is dark-only, so it would need a light twin before shipping (the current page and the site are light). It has the most personality, and so the most risk of reading as a theme rather than a tool.

## Recommendation (adopted as D)

**B as the base, with A's diagram as an optional fold-out under 02 Plans.** B fixes every defect above, reads well on a phone, and looks the most like a shipped product, which is what the marketplace review is judging. Bringing A's diagram back as a secondary view keeps the visual identity for people who want it. C's countdown and checklist are worth borrowing into B's "Next scheduled run" column whichever way this goes.

## Not decided here

- Whether the marketplace listing wants a screenshot at a specific size. The README hero image (`docs/dashboard.png`) would be regenerated from whichever direction ships.
- The data plumbing. Every element in the mockups maps to a field already in `/api/state` except the job time limit in A's progress bar. Today that limit comes from each pool's `run.timeout_s` and is not exposed on the page.
