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

## GameSheet scraping — gotchas

GameSheet is a Next.js/RSC app with no clean REST JSON. All parsers live in `update.py` and are layout-sensitive. **If scraping breaks, the page HTML almost certainly changed** — dump the page text and compare against the parser's expectations. A ~July 2026 redesign already broke and forced a rewrite of every parser (see `README.md`'s "GameSheet layout change" note and commits `d281e94`, `f641b50`).

- **players** is a *virtualized*, division-wide leaderboard — only visible rows are in the DOM. Must scroll and union rows (`collect_plb_rows()`), never a single read.
- **standings** rows have a blank leading rank cell — drop leading empty cells or every stat shifts one column.
- **scores** page is the authoritative result source (no `FINAL` marker anymore); **schedule** page is upcoming-only, visitor-first.
- Results/history: the game merge **preserves cached games** (never drops old ones). `sanitize_games()` discards field-shift artifacts and de-dupes.

## Data integrity rules

- **A shrinking roster/standings scrape means a broken parser, not a real change.** `update.py` guards against this: it aborts (non-zero exit → the CI run fails loudly) rather than overwriting a good roster with a near-empty scrape, and keeps existing standings if a scrape returns fewer rows. Don't defeat these guards to make a run pass.
- The web app loads `data/app_data.json`. `data/summer_2026.json` is the live season; older season files are archived PointStreak data and should not be re-scraped.

## GameSheet IDs

Season `14815` · Division `79347` · PLB Team `512204`
