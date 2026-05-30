#!/usr/bin/env python3
"""
PointStreak Hockey Team History Scraper
Scrapes all 12 seasons for ICAHL Wednesday B2 team (Vinegar Strokes / Parking Lot Beers)
URGENT: PointStreak decommissions May 31, 2026 — run this ASAP!
"""

import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

BASE = "http://stats.pointstreak.com/players"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

TEAM_NAMES = ["parking lot beers", "vinegar strokes"]

# All 12 seasons: (label, filename_slug, divisionid, seasonid, known_teamid_or_None)
SEASONS = [
    ("Summer 2020",     "summer_2020",    "115296", "20179", None),
    ("Winter 20/21",    "winter_20_21",   "115638", "20320", "771045"),
    ("Summer 2021",     "summer_2021",    "116497", "20533", None),
    ("Winter 21/22",    "winter_21_22",   "116997", "20626", None),
    ("Summer 2022",     "summer_2022",    "117699", "20825", None),
    ("Winter 22/23",    "winter_22_23",   "118309", "20956", None),
    ("Summer 2023",     "summer_2023",    "118964", "21104", None),
    ("Winter 23/24",    "winter_23_24",   "119492", "21212", "799324"),
    ("Summer 2024",     "summer_2024",    "120118", "21384", None),
    ("Winter 24/25",    "winter_24_25",   "120503", "21463", "807621"),
    ("Summer 2025",     "summer_2025",    "121092", "21589", None),
    ("Winter 25/26",    "winter_25_26",   "121509", "21671", None),
]


def fetch(url, delay=1.5):
    """Fetch a URL with retries and polite delay."""
    time.sleep(delay)
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ⚠  Attempt {attempt+1} failed for {url}: {e}")
            if attempt < 2:
                time.sleep(3)
    return None


def save_raw(slug, name, html):
    """Save raw HTML to data/raw/{slug}/{name}.html"""
    d = os.path.join(RAW_DIR, slug)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def find_team_on_division_page(html, divisionid, seasonid):
    """
    Scan a division page for any game where our team played.
    Returns (team_name, teamid) or (None, None).
    """
    soup = BeautifulSoup(html, "lxml")

    # Strategy 1: look for team links containing our team names
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).lower()
        if any(name in text for name in TEAM_NAMES):
            m = re.search(r"teamid=(\d+)", href)
            if m:
                return a.get_text(strip=True), m.group(1)

    # Strategy 2: look in standings table for team name
    for td in soup.find_all("td"):
        text = td.get_text(strip=True).lower()
        if any(name in text for name in TEAM_NAMES):
            a = td.find("a", href=True)
            if a:
                m = re.search(r"teamid=(\d+)", a["href"])
                if m:
                    return td.get_text(strip=True), m.group(1)

    return None, None


def parse_team_home(html):
    """Parse the team home page for record and basic info."""
    soup = BeautifulSoup(html, "lxml")
    result = {}

    # Try to find the team name from page title or header
    title = soup.find("title")
    if title:
        result["page_title"] = title.get_text(strip=True)

    # Find record table — look for W, L, T/OTL columns
    record = {}
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).upper() for th in table.find_all("th")]
        if any(h in headers for h in ["W", "GP", "PTS", "GF"]):
            rows = table.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells and len(cells) >= 4:
                    # Map headers to values
                    for i, h in enumerate(headers):
                        if i < len(cells):
                            record[h] = cells[i]
            if record:
                break

    result["record_raw"] = record

    # Extract team leaders section
    leaders = {}
    full_text = soup.get_text(" ", strip=True)

    # Look for goals/assists/points leaders
    for label in ["Goals", "Assists", "Points", "PIM", "Wins"]:
        # Look for pattern "Goals: Player Name (N)"
        patterns = [
            rf"{label}[:\s]+([A-Z][a-z]+ [A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*\(?(\d+)\)?",
            rf"{label}\s*\n?\s*([A-Z][a-z]+ [A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, full_text)
            if m:
                leaders[label.lower()] = {"player": m.group(1).strip(), "value": int(m.group(2))}
                break

    result["leaders"] = leaders

    # Try to find GP, W, L, T, OTL, SOL, PTS, GF, GA from structured data
    # Look for any text matching "W-L" patterns
    wl_pattern = re.search(r"(\d+)-(\d+)-(\d+)-(\d+)", full_text)
    if wl_pattern:
        result["record_string"] = wl_pattern.group(0)
        result["w"] = int(wl_pattern.group(1))
        result["l"] = int(wl_pattern.group(2))
        result["otl"] = int(wl_pattern.group(3))
        result["sol"] = int(wl_pattern.group(4))

    return result


def parse_schedule(html):
    """Parse schedule page for games."""
    soup = BeautifulSoup(html, "lxml")
    games = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]

        # Check if this looks like a schedule table
        if not any(h in headers for h in ["date", "home", "away", "visitor", "score"]):
            continue

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]
            game = {"raw": cell_texts}

            # Try to extract game id from any link
            for c in cells:
                for a in c.find_all("a", href=True):
                    m = re.search(r"gameid=(\d+)", a["href"])
                    if m:
                        game["gameid"] = m.group(1)

            # Map by headers
            for i, h in enumerate(headers):
                if i < len(cell_texts):
                    game[h] = cell_texts[i]

            if any(cell_texts):  # skip empty rows
                games.append(game)

    return games


def parse_roster(html):
    """Parse roster/player stats page."""
    soup = BeautifulSoup(html, "lxml")
    players = []
    goalies = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [th.get_text(strip=True).upper() for th in rows[0].find_all(["th", "td"])]

        # Skater table: should have G, A, PTS
        if "G" in headers and "A" in headers and "PTS" in headers:
            for row in rows[1:]:
                cells = row.find_all("td")
                if not cells:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                if not any(cell_texts):
                    continue
                player = {}
                for i, h in enumerate(headers):
                    if i < len(cell_texts):
                        player[h] = cell_texts[i]
                # Extract player link/name
                for c in cells:
                    a = c.find("a", href=True)
                    if a and "playerid" in a["href"]:
                        player["player_link"] = a["href"]
                        if not player.get("NAME"):
                            player["NAME"] = a.get_text(strip=True)
                if player.get("NAME") or any(player.values()):
                    players.append(player)

        # Goalie table: should have GAA or SV%
        elif "GAA" in headers or "SV%" in headers or "SVP" in headers:
            for row in rows[1:]:
                cells = row.find_all("td")
                if not cells:
                    continue
                cell_texts = [c.get_text(strip=True) for c in cells]
                if not any(cell_texts):
                    continue
                goalie = {}
                for i, h in enumerate(headers):
                    if i < len(cell_texts):
                        goalie[h] = cell_texts[i]
                if goalie.get("NAME") or any(goalie.values()):
                    goalies.append(goalie)

    return {"skaters": players, "goalies": goalies}


def parse_standings(html):
    """Parse division standings page."""
    soup = BeautifulSoup(html, "lxml")
    standings = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [th.get_text(strip=True).upper() for th in rows[0].find_all(["th", "td"])]

        if "PTS" not in headers and "W" not in headers:
            continue

        for i, row in enumerate(rows[1:], 1):
            cells = row.find_all("td")
            if not cells:
                continue
            cell_texts = [c.get_text(strip=True) for c in cells]
            if not any(cell_texts):
                continue

            entry = {"rank": i}
            for j, h in enumerate(headers):
                if j < len(cell_texts):
                    entry[h] = cell_texts[j]

            # Find team name and link
            for c in cells:
                a = c.find("a", href=True)
                if a and "teamid" in a["href"]:
                    m = re.search(r"teamid=(\d+)", a["href"])
                    if m:
                        entry["teamid"] = m.group(1)
                    if not entry.get("TEAM"):
                        entry["TEAM"] = a.get_text(strip=True)

            if entry.get("TEAM") or len(cell_texts) > 2:
                standings.append(entry)

    return standings


def parse_boxscore(html, gameid):
    """Parse a game boxscore for goals, assists, penalties."""
    soup = BeautifulSoup(html, "lxml")
    result = {"gameid": gameid, "goals": [], "penalties": []}

    # Try to extract game info (teams, score, date)
    full_text = soup.get_text(" ", strip=True)
    result["full_text_snippet"] = full_text[:500]

    # Look for goal scoring tables
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [th.get_text(strip=True).upper() for th in rows[0].find_all(["th", "td"])]

        if any(h in headers for h in ["GOAL", "SCORER", "PERIOD"]):
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells:
                    goal = {}
                    for i, h in enumerate(headers):
                        if i < len(cells):
                            goal[h] = cells[i]
                    result["goals"].append(goal)

        elif "PENALTY" in headers or "INFRACTION" in headers:
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells:
                    pen = {}
                    for i, h in enumerate(headers):
                        if i < len(cells):
                            pen[h] = cells[i]
                    result["penalties"].append(pen)

    return result


def scrape_season(label, slug, divisionid, seasonid, known_teamid=None):
    """Scrape all data for a single season. Returns a dict of season data."""
    print(f"\n{'='*60}")
    print(f"Scraping: {label} (division={divisionid}, season={seasonid})")
    print(f"{'='*60}")

    season_data = {
        "season": label,
        "slug": slug,
        "divisionid": divisionid,
        "seasonid": seasonid,
        "teamid": known_teamid,
        "team_name": None,
        "source_urls": {},
        "record": {},
        "standings": [],
        "schedule": [],
        "roster": {"skaters": [], "goalies": []},
        "boxscores": [],
        "leaders": {},
    }

    # --- Step A: Fetch division page to find teamid ---
    div_url = f"{BASE}/players-division.html?divisionid={divisionid}&seasonid={seasonid}"
    season_data["source_urls"]["division"] = div_url

    print(f"  Fetching division page...")
    div_html = fetch(div_url)

    if div_html:
        save_raw(slug, "division", div_html)

        if not known_teamid:
            team_name, teamid = find_team_on_division_page(div_html, divisionid, seasonid)
            if teamid:
                season_data["teamid"] = teamid
                season_data["team_name"] = team_name
                print(f"  Found team: '{team_name}' (teamid={teamid})")
            else:
                print(f"  ⚠  Could not find team on division page!")
                # Try standings page directly
        else:
            # Still parse to get team name
            team_name, _ = find_team_on_division_page(div_html, divisionid, seasonid)
            season_data["team_name"] = team_name
            print(f"  Using known teamid={known_teamid}, found name: '{team_name}'")
    else:
        print(f"  ⚠  Failed to fetch division page")

    teamid = season_data["teamid"]
    if not teamid:
        print(f"  ✗ No teamid found — skipping season-specific pages")
        return season_data

    # Build URLs
    team_url = f"{BASE}/players-team.html?teamid={teamid}&seasonid={seasonid}"
    schedule_url = f"{BASE}/players-team-schedule.html?teamid={teamid}&seasonid={seasonid}"
    roster_url = f"{BASE}/players-team-roster.html?teamid={teamid}&seasonid={seasonid}"
    standings_url = f"{BASE}/players-division-standings.html?divisionid={divisionid}&seasonid={seasonid}"

    season_data["source_urls"].update({
        "team_home": team_url,
        "schedule": schedule_url,
        "roster": roster_url,
        "standings": standings_url,
    })

    # --- Step B: Team Home ---
    print(f"  Fetching team home page...")
    team_html = fetch(team_url)
    if team_html:
        save_raw(slug, "team_home", team_html)
        home_data = parse_team_home(team_html)
        season_data["record"] = home_data
        season_data["leaders"] = home_data.get("leaders", {})
        print(f"  ✓ Team home page saved")
    else:
        print(f"  ⚠  Failed to fetch team home page")

    # --- Step B: Schedule ---
    print(f"  Fetching schedule page...")
    sched_html = fetch(schedule_url)
    if sched_html:
        save_raw(slug, "schedule", sched_html)
        games = parse_schedule(sched_html)
        season_data["schedule"] = games
        print(f"  ✓ Schedule saved ({len(games)} games found)")

        # --- Step C: Boxscores ---
        gameids = [g["gameid"] for g in games if g.get("gameid")]
        print(f"  Fetching {len(gameids)} boxscores...")
        for i, gid in enumerate(gameids):
            bs_url = f"{BASE}/players-boxscore.html?gameid={gid}"
            bs_html = fetch(bs_url, delay=1.0)
            if bs_html:
                save_raw(slug, f"boxscore_{gid}", bs_html)
                bs_data = parse_boxscore(bs_html, gid)
                season_data["boxscores"].append(bs_data)
            if (i + 1) % 5 == 0:
                print(f"    ... {i+1}/{len(gameids)} boxscores done")
        print(f"  ✓ Boxscores complete ({len(season_data['boxscores'])} saved)")
    else:
        print(f"  ⚠  Failed to fetch schedule page")

    # --- Step B: Roster ---
    print(f"  Fetching roster page...")
    roster_html = fetch(roster_url)
    if roster_html:
        save_raw(slug, "roster", roster_html)
        roster_data = parse_roster(roster_html)
        season_data["roster"] = roster_data
        skater_count = len(roster_data.get("skaters", []))
        goalie_count = len(roster_data.get("goalies", []))
        print(f"  ✓ Roster saved ({skater_count} skaters, {goalie_count} goalies)")
    else:
        print(f"  ⚠  Failed to fetch roster page")

    # --- Step B: Standings ---
    print(f"  Fetching standings page...")
    stand_html = fetch(standings_url)
    if stand_html:
        save_raw(slug, "standings", stand_html)
        standings = parse_standings(stand_html)
        season_data["standings"] = standings
        print(f"  ✓ Standings saved ({len(standings)} teams)")
    else:
        print(f"  ⚠  Failed to fetch standings page")

    return season_data


def detect_name_transition(all_seasons):
    """Detect when the team name changed from Vinegar Strokes to Parking Lot Beers."""
    transition = None
    last_name = None
    for s in all_seasons:
        name = (s.get("team_name") or "").strip().lower()
        if not name:
            continue
        if last_name and last_name != name:
            if "vinegar" in last_name and "parking" in name:
                transition = s["season"]
        last_name = name
    return transition


def print_summary(all_seasons):
    """Print a terminal summary of scraped data."""
    print("\n" + "="*60)
    print("SCRAPE COMPLETE — SUMMARY")
    print("="*60)

    total_games = sum(len(s.get("schedule", [])) for s in all_seasons)
    seasons_with_data = [s for s in all_seasons if s.get("teamid")]

    print(f"Total seasons collected:  {len(all_seasons)}")
    print(f"Seasons with team data:   {len(seasons_with_data)}")
    print(f"Total games tracked:      {total_games}")

    # All-time points leaders
    player_totals = {}
    for s in all_seasons:
        for p in s.get("roster", {}).get("skaters", []):
            name = (p.get("NAME") or p.get("name") or "").strip()
            if not name:
                continue
            name_key = name.lower()
            if name_key not in player_totals:
                player_totals[name_key] = {"name": name, "pts": 0, "g": 0, "a": 0, "gp": 0}
            try:
                player_totals[name_key]["pts"] += int(p.get("PTS", 0) or 0)
                player_totals[name_key]["g"] += int(p.get("G", 0) or 0)
                player_totals[name_key]["a"] += int(p.get("A", 0) or 0)
                player_totals[name_key]["gp"] += int(p.get("GP", 0) or 0)
            except (ValueError, TypeError):
                pass

    sorted_players = sorted(player_totals.values(), key=lambda x: x["pts"], reverse=True)
    print("\nTop 5 all-time point leaders:")
    for i, p in enumerate(sorted_players[:5], 1):
        print(f"  {i}. {p['name']}: {p['pts']} PTS ({p['g']}G + {p['a']}A in {p['gp']} GP)")

    # Best season by win %
    best_season = None
    best_pct = 0
    for s in all_seasons:
        rec = s.get("record", {})
        w = int(rec.get("w", rec.get("W", 0)) or 0)
        l = int(rec.get("l", rec.get("L", 0)) or 0)
        gp = w + l + int(rec.get("otl", rec.get("OTL", 0)) or 0) + int(rec.get("sol", rec.get("SOL", 0)) or 0)
        if gp > 0:
            pct = w / gp
            if pct > best_pct:
                best_pct = pct
                best_season = s["season"]
    if best_season:
        print(f"\nBest season (by win %): {best_season} ({best_pct:.1%})")

    # Team name transition
    transition = detect_name_transition(all_seasons)
    if transition:
        print(f"\nTeam name transition detected: Vinegar Strokes → Parking Lot Beers in {transition}")
    else:
        print("\nTeam name transition: could not detect automatically from scraped data")

    # Archive confirmation
    raw_count = 0
    for dirpath, dirnames, filenames in os.walk(RAW_DIR):
        raw_count += len([f for f in filenames if f.endswith(".html")])
    print(f"\n✅ Local HTML archive saved: {raw_count} files in data/raw/")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    all_seasons = []

    for label, slug, divisionid, seasonid, known_teamid in SEASONS:
        season_data = scrape_season(label, slug, divisionid, seasonid, known_teamid)
        all_seasons.append(season_data)

        # Save individual season JSON
        season_path = os.path.join(DATA_DIR, f"{slug}.json")
        with open(season_path, "w", encoding="utf-8") as f:
            json.dump(season_data, f, indent=2, ensure_ascii=False)
        print(f"  💾 Saved {season_path}")

    # Save consolidated JSON
    all_path = os.path.join(DATA_DIR, "all_seasons.json")
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(all_seasons, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Saved consolidated data: {all_path}")

    print_summary(all_seasons)


if __name__ == "__main__":
    main()
