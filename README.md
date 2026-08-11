# Parking Lot Beers — ICAHL Wednesday B2 Team History

A single-page web app displaying the complete history of an ICAHL (Ice Centre Adult Hockey League) Wednesday B2 team — originally called **Vinegar Strokes**, permanently renamed to **Parking Lot Beers** starting Summer 2023.

All data was scraped from [PointStreak](http://stats.pointstreak.com) and archived locally before PointStreak's decommission on **May 31, 2026**.

---

## Quick Start

Serve the app locally (no build step needed):

```bash
python3 -m http.server 7890
```

Then open [http://localhost:7890](http://localhost:7890) in your browser.

---

## Project Structure

```
hockey-team-history/
├── index.html          # Single-page app (no framework, no build tools)
├── styles.css          # Hockey-themed responsive styles
├── app.js              # All interactivity — tables, chart, season browser
├── scrape.py           # Step 1: Fetches raw data from PointStreak (seasons 2020–2026)
├── process.py          # Step 2: Cleans raw data → app_data.json
├── update.py           # Weekly updater for live GameSheet seasons (run each Thursday)
├── requirements.txt    # Python dependencies (requests, beautifulsoup4, lxml, playwright)
├── data/
│   ├── app_data.json           # Cleaned data loaded by the web app
│   ├── all_seasons.json        # Raw parsed JSON (all 12 PointStreak seasons combined)
│   ├── summer_2026.json        # Live GameSheet season (updated weekly by update.py)
│   ├── summer_2020.json        # Per-season raw JSON files (PointStreak)
│   ├── winter_20_21.json
│   ├── ...
│   └── raw/                    # Permanent HTML archive from PointStreak
│       ├── summer_2020/
│       │   ├── division.html
│       │   ├── team_home.html
│       │   ├── schedule.html
│       │   ├── roster.html
│       │   ├── standings.html
│       │   └── boxscore_<gameid>.html  (one per game)
│       ├── winter_20_21/
│       └── ... (one folder per season)
└── .claude/
    └── launch.json     # Preview server config for Claude Code
```

---

## Scripts

### `scrape.py` — Fetch & Archive from PointStreak

Scrapes all 12 seasons from PointStreak and saves both raw HTML and parsed JSON locally.

**What it does:**
1. Visits each season's division page to auto-detect which team is ours (matching "Parking Lot Beers" or "Vinegar Strokes")
2. Discovers the `teamid` for each season
3. Fetches 4 pages per season: Team Home, Schedule, Roster, Division Standings
4. Fetches individual boxscore pages for every game (goal scorers, goalies, penalties)
5. Saves raw HTML to `data/raw/<season>/` for permanent archival
6. Saves parsed JSON to `data/<season>.json` and `data/all_seasons.json`

**Usage:**
```bash
pip install -r requirements.txt
python3 scrape.py
```

**Politeness:** 1.5-second delay between requests, browser-like User-Agent header.

**Output summary** (printed at completion):
- Total seasons and games collected
- Top 5 all-time point leaders
- Detected team name transition season
- Count of archived HTML files

---

### `process.py` — Clean Raw Data for the Web App

Reads the raw parsed JSON from `scrape.py` and produces a clean, web-ready `data/app_data.json`.

**What it does:**
1. **Parses schedule games** — separates team names from scores (PointStreak stores them concatenated, e.g. `"Whalers4"`), infers full dates (adding the correct year based on the season), and determines W/L/OT/SO result for each game
2. **Normalizes player names** — merges known spelling variants across seasons (e.g. "Andy Pung" → "Andrew Pung", "Brayden Schmid" → "Braden Schmid", "Bryan Sallico" → "Bryan Salicco")
3. **Parses boxscore HTML** from `data/raw/` — extracts goal scorers, assists, goalie stats, and per-game player stats
4. **Finds our standing** in each season's division standings table
5. **Aggregates career stats** across all seasons for each player
6. **Detects the team name transition** season
7. Outputs `data/app_data.json` — the single file the web app loads

**Usage:**
```bash
python3 process.py
```

Run this after `scrape.py` whenever you want to refresh the web app data.

---

---

### `update.py` — Weekly Live Updater (GameSheet seasons)

Pulls the latest scores, standings, and player stats from [GameSheet](https://gamesheetstats.com) for the current live season using a headless Playwright browser (required because GameSheet renders data via JavaScript).

**What it does:**
1. Launches a headless Chromium browser via Playwright and scrolls each page to trigger lazy-loaded content
2. Fetches the division **scores** page — completed games with final scores (visitor-first layout); this is the authoritative source for results
3. Fetches the division **schedule** page — upcoming/scheduled games (dates, times, matchups)
4. Merges scores + schedule into the season game list, **preserving cached history** (old games are never dropped) and de-duplicating via `sanitize_games()`, which also discards field-shift parse artifacts
5. Fetches division **standings** — all 6 teams' GP/W/L/OTL/SOL/PTS/GF/GA
6. Fetches **skater stats from per-game lineups** via `collect_boxscores()` — the authoritative source. Completed games are enumerated from the *team* schedule (the division `/scores` page only returns roughly the last 18 games), then each game's dressed roster is read from `?tab=lineups` and summed into season totals, with **GP = the number of lineups a player appears in**. Raw lineups cache in `season["boxscores"]` so later runs only fetch newly-played games
7. Also fetches the division **player leaderboard** via `collect_plb_rows()` — kept only as a fallback, since it server-renders just its top ~20 division-wide rows and loads the rest through a Cloudflare-gated request
8. **Guards against partial scrapes**: the lineup aggregate is used only when *every* completed game parsed (a missing lineup would undercount), otherwise it falls back to merging leaderboard rows over the cached roster; standings are kept if a scrape returns fewer rows; and a run where every source returns 0 rows aborts non-zero so the failure is loud instead of committed
9. Calculates PLB's W-L-OTL-SOL record, saves `data/summer_2026.json`, and calls `process.py` to regenerate `data/app_data.json`
10. Prints a summary: record, upcoming games, top scorers

> **⚠️ GameSheet layout change (July 2026).** GameSheet rebuilt its stats site (now a Next.js/RSC app), changing the HTML of every page and breaking the original scrapers — scores stopped syncing (the old `FINAL` marker was removed) and schedule rows produced garbled duplicates (an interleaved `B2 - Wed/Thu` division label shifted the fields). The parsers in `update.py` were rewritten to match the new layout: results now come from a separate **scores** page, the **schedule** page is upcoming-only, **standings** rows carry a blank leading rank cell, and **players** is a virtualized leaderboard scraped by incremental scrolling. See commits `d281e94` and `f641b50`. If scraping breaks again, the page layouts likely changed — dump a page with `PYTHONPATH=. python3` importing `update`'s `load_page`, and compare against the parser expectations.

**Usage:**
```bash
pip install playwright
playwright install chromium
python3 update.py
```

Run each Thursday evening after scores are posted. The web app refreshes automatically when `app_data.json` is regenerated.

**GameSheet IDs:**
- Season: `14815` · Division: `79347` · PLB Team: `512204`

---

## Updating for a New Season

**PointStreak seasons (2020–2026, archived):**

1. Add the new season's division URL to the `SEASONS` list in `scrape.py`
2. Run `scrape.py` to fetch and archive the HTML
3. Run `process.py` to regenerate `data/app_data.json`
4. Reload the web app — the new season appears automatically

**GameSheet seasons (Summer 2026+):**

1. Create a new `data/<slug>.json` with the season metadata (see `summer_2026.json` for format)
2. Add the filename to the `gs_files` list in `process.py`
3. Update the **rollover constants** at the top of `update.py` — `GS_SEASON`, `GS_DIVISION`, `GS_TEAM`, `GS_SEASON_START`, `SEASON_FILE` (and `OUR_TEAM` if the team was renamed). **`SEASON_FILE` matters most:** it's the file the scraper writes to, so leaving it on the old season would overwrite an archived one. `update.py` refuses to run if `SEASON_FILE`'s `gs_season_id` doesn't match `GS_SEASON`, which catches exactly that mistake
4. Set the finished season's `"live"` to `false` so it stops being treated as the live season
5. Re-enable the `schedule:` triggers in `.github/workflows/weekly-update.yml` if they were turned off at season end
6. Run `python3 update.py` each Thursday to sync live data

> **⚠️ When a season ends,** `update.py` keeps pointing at the old `GS_SEASON` and the crons keep scraping it. Nothing is corrupted (an unchanged scrape produces no commit), but the runs are pointless and will start failing once GameSheet retires the season page — the all-sources-empty guard exits non-zero, turning CI red three times a week. Repoint the constants at the new season, or disable the workflow's `schedule` triggers between seasons.

---

## Seasons Covered

| Season | Team Name | TeamID |
|--------|-----------|--------|
| Summer 2020 | Vinegar Strokes | 768342 |
| Winter 20/21 | Vinegar Strokes | 771045 |
| Summer 2021 | Vinegar Strokes | 776956 |
| Winter 21/22 | Vinegar Strokes | 780172 |
| Summer 2022 | Parking Lot Beers | 786222 |
| Winter 22/23 | Vinegar Strokes | 789778 |
| Summer 2023 | Parking Lot Beers | 795120 |
| Winter 23/24 | Parking Lot Beers | 799324 |
| Summer 2024 | Parking Lot Beers | 804585 |
| Winter 24/25 | Parking Lot Beers | 807621 |
| Summer 2025 | Parking Lot Beers | 811691 |
| Winter 25/26 | Parking Lot Beers | 814840 |
| Summer 2026 | Parking Lot Beers | 512204 (GameSheet) |

**Team name history:** The team first used "Parking Lot Beers" in Summer 2022, briefly reverted to "Vinegar Strokes" for Winter 22/23, then permanently switched starting Summer 2023.

**Platform change:** Seasons through Winter 25/26 were tracked on PointStreak (decommissioned 5/31/2026). Summer 2026 onward uses [GameSheet](https://gamesheetstats.com) via [icecentre.com](https://icecentre.com/programs/adult-hockey/icahl-summer-2026/b2/).

---

## Archive Note

279 raw HTML files are saved in `data/raw/` — one subfolder per season containing the division page, team home, schedule, roster, standings, and a boxscore file for every game. These serve as a permanent record independent of PointStreak.
