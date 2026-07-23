# CLAUDE.md

Guidance for Claude Code working in this repo. See `README.md` for full project docs.

## What this is

A single-page web app showing an ICAHL beer-league team's history (**Parking Lot Beers**, formerly Vinegar Strokes). Seasons through Winter 25/26 are archived from PointStreak (decommissioned 5/31/2026); **Summer 2026 onward is live from GameSheet**, refreshed by a scheduled scraper.

## Commands

```bash
python3 -m http.server 7890     # serve the app locally → http://localhost:7890
python3 update.py               # scrape live GameSheet season → summer_2026.json (+ regenerates app_data.json)
python3 process.py              # rebuild app_data.json from the season files (no scraping)
```

Local Playwright + Chromium are installed and work for debugging scrapes. To dump a live page's parsed text, run a scratch script with `PYTHONPATH=. python3` that imports `update` and calls `update.load_page(page, url)`.

## Git workflow

- A **GitHub Actions bot commits to `main` twice weekly** (`.github/workflows/weekly-update.yml`, Thu/Fri crons) with fresh scraped data. So `main` moves without you.
- **`git pull --rebase` before starting work**, to avoid conflicts on `data/summer_2026.json` / `data/app_data.json`.
- **Do not push. The user pushes to the remote manually.** Commit locally and leave commits staged for them.

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

**Untested risk:** the fix is validated from a residential Mac only. GitHub
Actions runs on datacenter IPs, which Cloudflare challenges harder — verify with
the `workflow_dispatch` manual trigger after pushing. No self-hosted/residential
runner is planned.

**Future optimization (separate commit, not started):** if per-game boxscore
pages turn out to be server-rendered like /scores, full skater stats could be
aggregated from boxscores instead of the blocked leaderboard API.

**Other findings:** GameSheet has partial clean REST JSON — `/api/standings/{season}`,
`/api/season-info/{season}`, `/api/season-divisions/{season}` (all divisions; filter
by divisionId) — but games/scores stream from **Google Firestore** realtime channels
(`gamesheet-production`), and `/api/players/standings` is Cloudflare-gated as above.
`filter[team]=512204` on the players page is IGNORED (returns an all-time league-wide
leaderboard), so it's a dead end.

## GameSheet scraping — gotchas

GameSheet is a Next.js/RSC app with no clean REST JSON. All parsers live in `update.py` and are layout-sensitive. **If scraping breaks, the page HTML almost certainly changed** — dump the page text and compare against the parser's expectations. A ~July 2026 redesign already broke and forced a rewrite of every parser (see `README.md`'s "GameSheet layout change" note and commits `d281e94`, `f641b50`).

- **players** is a *virtualized*, division-wide leaderboard — only visible rows are in the DOM. Must scroll and union rows (`collect_plb_rows()`), never a single read.
- **standings** rows have a blank leading rank cell — drop leading empty cells or every stat shifts one column.
- **scores** page is the authoritative result source (no `FINAL` marker anymore); **schedule** page is upcoming-only, visitor-first.
- Results/history: the game merge **preserves cached games** (never drops old ones). `sanitize_games()` discards field-shift artifacts and de-dupes.

## Data integrity rules

- **A shrinking roster/standings scrape means a broken parser or a blocked scrape, not a real change.** `update.py` guards against this: skaters and goalies are merged over the cached lists by name (scraped rows win, unscraped players keep cached stats — never a wholesale overwrite), standings are kept if a scrape returns fewer rows, and a run where every source returns 0 rows aborts non-zero (CI fails loudly). Don't defeat these guards to make a run pass.
- The web app loads `data/app_data.json`. `data/summer_2026.json` is the live season; older season files are archived PointStreak data and should not be re-scraped.

## GameSheet IDs

Season `14815` · Division `79347` · PLB Team `512204`
