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
2. Fetches the full division **schedule** — parses game dates, times, scores, and results; filters to PLB games
3. Fetches division **standings** — extracts all 6 teams' GP/W/L/OTL/SOL/PTS/GF/GA
4. Fetches division **player stats** — extracts skater stats filtered to PLB players
5. Calculates PLB's W-L-OTL-SOL record from played games
6. Saves updated data to `data/summer_2026.json`
7. Calls `process.py` automatically to regenerate `data/app_data.json`
8. Prints a summary: record, upcoming games, top scorers

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
3. Add `update.py` constants: new `GS_SEASON`, `GS_DIVISION`, and `OUR_TEAM` values
4. Run `python3 update.py` each Thursday to sync live data

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
