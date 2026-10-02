# CLAUDE.md

Guidance for Claude Code working in this repo. See `README.md` for full project docs.

## What this is

A single-page web app showing an ICAHL beer-league team's history (**Parking Lot Beers**, formerly Vinegar Strokes). Seasons through Winter 25/26 are archived from PointStreak (decommissioned 5/31/2026); **Summer 2026 onward is from GameSheet**, refreshed by a scheduled scraper. The live season is **Winter 2026-27** (`data/winter_26_27.json`); Summer 2026 is archived.

## Commands

```bash
python3 -m http.server 7890     # serve the app locally → http://localhost:7890
python3 update.py               # scrape live GameSheet season → winter_26_27.json (+ regenerates app_data.json)
python3 process.py              # rebuild app_data.json from the season files (no scraping)
```

Local Playwright + Chromium are installed and work for debugging scrapes. To dump a live page's parsed text, run a scratch script with `PYTHONPATH=. python3` that imports `update` and calls `update.load_page(page, url)`.

## Git workflow

- A **GitHub Actions bot commits to `main` up to three times weekly** (`.github/workflows/weekly-update.yml`, Thu/Fri/Sat crons) with fresh scraped data. So `main` moves without you.
- **`git pull --rebase` before starting work**, to avoid conflicts on `data/winter_26_27.json` / `data/app_data.json`.
- **Do not push. The user pushes to the remote manually.** Commit locally and leave commits staged for them.

## STATUS 2026-10-01 — rolled over to Winter 2026-27; /games redesign

GameSheet redesigned again (~Sep 2026). What changed and what `update.py` does now:

- `/scores` and `/schedule` **redirect to `/games?filter[status]=completed|scheduled`**;
  `update.py` loads `/games` directly. The list is client-rendered (a raw fetch of
  the HTML has an empty `<main>`) and is **responsive**: at desktop width (≥ ~768px,
  which the scraper's 1280px viewport gets) it is the same 9-line table the old
  pages had, so `parse_scores` / `parse_schedule` needed no layout change. Narrow
  viewports get an upper-cased **card** layout (`GM#: 51` / rink / visitor / div /
  home / div / `4` / `FINAL` / `7`, or time / short date); `parse_game_cards()`
  handles it as a fallback when the table parse yields nothing. Cards have no
  game-type column.
- **The team schedule page ignores `filter[status]=completed`** — it lists the whole
  season. Completed rows show `L 4 - 7` where upcoming rows show the time.
  `collect_boxscores()` keeps only ids whose row date is a completed date
  (`team_completed_dates()`); without that every unplayed game is fetched, fails,
  and the completeness guard blocks the aggregate forever. The same page text is
  also mined for PLB's full-season upcoming schedule (the division view only
  shows the next few weeks).
- `?tab=lineups` still server-renders both rosters (checked on game 3009030).
- **3-point standings** (reg W 3, OT/SO W 2, OT/SO L 1, tie 0). `derive_record()`
  uses the `PTS_*` constants; when the standings row's GP matches, GameSheet's own
  PTS overrides it. `app.js` / `process.py` never compute points.
- **Goalie columns were shifted all of Summer 2026**: `_row_nums` dropped
  leading-dot decimals, so SV% (`.857`) vanished and W/L/T each read one column
  late. Fixed; archived `summer_2026.json` goalies still hold the shifted values
  (Chris Moore `w 4, l 0, sv_pct 6.0` is really 6 W, 4 L).
- **Not yet seen:** how an OT/SO result renders on `/games` (none played yet). The
  parsers accept the old own-line `OT`/`SO` after the score and `FINAL/OT` on
  cards; a level score with no marker is recorded as a tie.
- The 2026-10-01 local run had standings challenged by Cloudflare and the lineup
  fetch 403 (local IP, as usual). Standings and game 3009030's lineup in
  `winter_26_27.json` were seeded from the in-app browser (`/api/standings/15870`
  and the lineups page). **The headless lineup fetch is still unproven for this
  season — watch the first CI run with a new game (Thu 2026-10-08).**
- `app.js` picks the current season by `"live": true` (falling back to the last
  season) and sets the header + GameSheet iframes from its ids — nothing in the
  front end is season-specific any more. Season order = `all_seasons.json` then
  `gs_files` order in `process.py`; the browser defaults to the last one.

## STATUS 2026-07-22 — Cloudflare fix landed (players degrade gracefully)

Both weekly-update CI runs failed the week of ~Jul 14 (last good bot commit Jul 10).
**Root cause:** GameSheet added Cloudflare bot protection. The old `update.py`
launched plain default-headless Chromium with a truncated UA → served the
"Performing security verification" interstitial → 0 rows → the guard aborts. The
existing `main` data was never clobbered (the guard did its job).

**The fix (in `update.py`):**
- Present as a real browser: full `BROWSER_UA`, launch flag
  `--disable-blink-features=AutomationControlled`, hide `navigator.webdriver`
  (all in `_new_page`). No CAPTCHA solving.
- **Fresh browser context per page** (`load_page` / `collect_plb_rows` take
  `browser`, not a shared `page`). Cloudflare lets each new context through on its
  *first* navigation, then hard-challenges reuse — so every page gets its own
  short-lived session. Recovers **scores, schedule, standings, goalies** (their
  data is server-rendered into the initial HTML).
- **Players full roster is NOT obtainable headlessly** — only the top ~20
  division-wide rows are server-rendered (2–6 PLB); the rest load via
  `GET /api/players/standings/…`, a Cloudflare-gated XHR that 403s without a
  `cf_clearance` cookie (needs an interactive residential browser; user has ruled
  that out). So `update.py` now **merges** scraped skater rows over the cached
  roster by lower-cased name instead of aborting: unscraped players keep cached
  stats, and since counting stats only grow the merge can never regress data.
  Goalies merge the same way. A run where EVERY source returns 0 rows still
  aborts non-zero (fully-blocked run → CI red, no no-op commit).

**Untested risk — RESOLVED 2026-08-10, and it was backwards.** GitHub Actions
datacenter IPs clear Cloudflare fine (bot commits have landed on schedule ever
since). The fragile IP is the *local* one: ~9 navigations in ~4 minutes from the
residential Mac escalated it to a blanket challenge that had not decayed 85
minutes later. Budget hours, not minutes — and never debug-scrape iteratively
from this machine. See the 2026-08-10 status below.

## STATUS 2026-08-10 — skater stats now aggregated from per-game lineups

The frozen-roster problem is **fixed at the source**, so the leaderboard's ~20-row
cutoff no longer matters. Every completed game's page server-renders BOTH teams'
dressed rosters with G/A/PTS/PIM at `?tab=lineups`, so full-season skater totals
are summed from them and **GP is simply how many lineups a player appears in** —
the number the leaderboard could never refresh (a 6-GP player sat at 6 GP all
season while actually playing 8).

- `collect_boxscores()` enumerates completed games from the **team** schedule
  (`/teams/{GS_TEAM}/schedule?filter[status]=completed`) — *not* `/scores`, which
  only returns roughly the last 18 division games and silently drops the early
  season — then pulls each lineup with a **same-origin `fetch` from the already
  loaded page**. One navigation gets a Cloudflare-cleared context and the fetches
  ride its cookies: 12 XHRs instead of 12 page loads, far lighter and much less
  likely to trip bot protection. Results cache in `season["boxscores"]` keyed by
  game id, so later runs only fetch newly-played games (`process.py` strips the
  key, so it never reaches `app_data.json`).
- **Completeness guard:** totals replace the roster only when EVERY completed game
  has a parsed lineup. One missing game would silently undercount, so a partial
  set falls back to the old leaderboard merge instead.
- **Verified:** the four players who *do* render on the leaderboard (Asensio,
  Wilson, Hanson, Jenson) aggregate to exactly their leaderboard numbers, which is
  what confirms the method; the 12 rows that changed are all sub-cutoff players.
  Aggregation also fills in jersey numbers, which were empty before.
- Goalies still come from `/goalies` (only ~12 division-wide, so no cutoff
  problem) — lineups carry no W/L.

**Do NOT use the team page's Stats tab as a shortcut.** It looks like the whole
roster in one request, but it is **not division-scoped** — it aggregates each
player across every team they appear on in the season (players skate for teams in
multiple divisions), reporting GP far above the team's games played (e.g. 21 GP in
a 12-game season). Per-game lineups are the only division-correct source.

**Other findings:** GameSheet has partial clean REST JSON — `/api/standings/{season}`,
`/api/season-info/{season}`, `/api/season-divisions/{season}` (all divisions; filter
by divisionId) — but games/scores stream from **Google Firestore** realtime channels
(`gamesheet-production`), and `/api/players/standings` is Cloudflare-gated as above.
`filter[team]=512204` on the players page is IGNORED (returns an all-time league-wide
leaderboard), so it's a dead end.

## GameSheet scraping — gotchas

GameSheet is a Next.js/RSC app with no clean REST JSON. All parsers live in `update.py` and are layout-sensitive. **If scraping breaks, the page HTML almost certainly changed** — dump the page text and compare against the parser's expectations. A ~July 2026 redesign already broke and forced a rewrite of every parser (see `README.md`'s "GameSheet layout change" note and commits `d281e94`, `f641b50`).

- **players** is a *virtualized*, division-wide leaderboard — only visible rows are in the DOM. Must scroll and union rows (`collect_plb_rows()`), never a single read. It is now only a **fallback**; skater stats come from per-game lineups (see the 2026-08-10 status).
- **game pages** render each tab's content server-side from a `?tab=` query param (`lineups`, `box-score`, `play-by-play`) — not a path segment. `?tab=lineups` is the roster source; the box score lists only players who recorded a point.
- **standings** rows have a blank leading rank cell — drop leading empty cells or every stat shifts one column.
- **`/games?filter[status]=completed`** is the authoritative result source; **`…=scheduled`** is upcoming-only. Both visitor-first. Layout depends on viewport width (table vs cards) — see the 2026-10-01 status.
- Results/history: the game merge **preserves cached games** (never drops old ones). `sanitize_games()` discards field-shift artifacts and de-dupes.

## Data integrity rules

- **A shrinking roster/standings scrape means a broken parser or a blocked scrape, not a real change.** `update.py` guards against this: skaters and goalies are merged over the cached lists by name (scraped rows win, unscraped players keep cached stats — never a wholesale overwrite), standings are kept if a scrape returns fewer rows, and a run where every source returns 0 rows aborts non-zero (CI fails loudly). Don't defeat these guards to make a run pass.
- **Lineup-aggregated skater totals are only valid when every completed game was read.** Unlike the leaderboard merge (where counting stats only grow, so a partial scrape can't regress), a missing lineup *undercounts* — so `collect_boxscores()` returns a `complete` flag and the aggregate is used only when it's True. Never relax that to make a run produce numbers.
- The web app loads `data/app_data.json`. `data/winter_26_27.json` is the live season; `summer_2026.json` is archived GameSheet data and older season files are archived PointStreak data — none of those should be re-scraped.

## GameSheet IDs

Winter 2026-27 (live): Season `15870` · Division `86566` · PLB Team `560174` · League `1148562`

Summer 2026 (archived): Season `14815` · Division `79347` · PLB Team `512204`

`GET /api/leagues/1148562` (same-origin from a loaded page) returns `active_season` — the lookup for the next rollover.

## Season rollover

Summer 2026 ended 2026-08-06 (`"live": false`); Winter 2026-27 (runs 2026-09-24 → 2027-05-17) was rolled over on 2026-10-01. For the next season, change the
**rollover constants together** at the top of `update.py`: `GS_SEASON`,
`GS_DIVISION`, `GS_TEAM`, `GS_SEASON_START`, `SEASON_FILE`. `SEASON_FILE` is the
one that bites — it used to be hardcoded inside `main()`, so repointing
`GS_SEASON` alone would scrape the new season straight over the archived
`summer_2026.json`. `main()` now aborts when `SEASON_FILE`'s `gs_season_id`
doesn't match `GS_SEASON`. Also: add the new file to `gs_files` in `process.py`, change the `git add` path in the workflow's commit step, set the `PTS_*` constants for the season's points system, set the old season's `"live"` to false, and re-enable the workflow crons.

Note the lineup-fetch path has **never run in CI**: every Summer 2026 lineup was
cached, and Winter's first game was seeded by hand (see the 2026-10-01 status).
The first real test is the 2026-10-08 run.
