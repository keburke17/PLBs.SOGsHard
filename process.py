#!/usr/bin/env python3
"""
Post-processor: converts raw scraped JSON into clean web-app data.
Reads data/*.json and data/raw/*/boxscore_*.html
Outputs data/app_data.json
"""

import json
import os
import re
from bs4 import BeautifulSoup

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")

OUR_TEAM_NAMES = {"parking lot beers", "vinegar strokes"}

# Season year ranges: (slug, year_for_early_months, year_for_late_months, split_month)
# split_month: months >= split_month get year_for_late_months (Jan=1)
# For summer seasons, split_month=None (all same year)
SEASON_YEARS = {
    "summer_2020":   {"type": "summer", "years": [2020]},
    "winter_20_21":  {"type": "winter", "years": [2020, 2021], "split": 7},
    "summer_2021":   {"type": "summer", "years": [2021]},
    "winter_21_22":  {"type": "winter", "years": [2021, 2022], "split": 7},
    "summer_2022":   {"type": "summer", "years": [2022]},
    "winter_22_23":  {"type": "winter", "years": [2022, 2023], "split": 7},
    "summer_2023":   {"type": "summer", "years": [2023]},
    "winter_23_24":  {"type": "winter", "years": [2023, 2024], "split": 7},
    "summer_2024":   {"type": "summer", "years": [2024]},
    "winter_24_25":  {"type": "winter", "years": [2024, 2025], "split": 7},
    "summer_2025":   {"type": "summer", "years": [2025]},
    "winter_25_26":  {"type": "winter", "years": [2025, 2026], "split": 7},
}

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Known player name normalizations (variant → canonical)
# None = skip this entry entirely
NAME_NORMALIZATIONS = {
    "christopher moore": "Chris Moore",
    "sub goalie": None,
    "house goalie": None,
    "team goalie": None,
    # Spelling variants found in scraped data
    "andrew paulosky": "Andrew Pavlosky",
    "andy pung": "Andrew Pung",
    "brayden schmid": "Braden Schmid",
    "bryan sallico": "Bryan Salicco",
    "nick hostovsky": "Nicholas Hostovsky",
    "peter bernadi": "Peter Bernardi",
    "jonathon sakalas": "Jon Sakalas",
    "matt zagers": "Matt Zagers",
    "loius connor": "Louis Connor",
}


def normalize_name(name):
    """Normalize a player name to canonical form."""
    if not name:
        return None
    stripped = name.strip()
    lower = stripped.lower()
    if lower in NAME_NORMALIZATIONS:
        return NAME_NORMALIZATIONS[lower]
    # Trim jersey number prefixes like "16Nate Jenson"
    m = re.match(r"^\d+(.+)$", stripped)
    if m and not stripped[0].isalpha():
        stripped = m.group(1).strip()
    return stripped


def parse_team_score(s):
    """Split 'TeamName7' into ('TeamName', 7). Returns (name, None) if no score."""
    if not s:
        return "", None
    m = re.match(r"^(.*?)(\d+)$", s.strip())
    if m:
        return m.group(1).strip(), int(m.group(2))
    return s.strip(), None


def infer_date(date_str, slug):
    """
    Convert 'Wed, Oct 04' to '2023-10-04' using season slug context.
    """
    if not date_str:
        return None
    # Parse month and day
    m = re.search(r"([A-Za-z]{3})\s+(\d{1,2})", date_str)
    if not m:
        return None
    month_abbr = m.group(1).lower()
    day = int(m.group(2))
    month = MONTH_MAP.get(month_abbr)
    if not month:
        return None

    info = SEASON_YEARS.get(slug, {})
    years = info.get("years", [2020])

    if len(years) == 1:
        year = years[0]
    else:
        # Winter season: months >= 7 (Jul+) are first year, < 7 are second year
        split = info.get("split", 7)
        year = years[0] if month >= split else years[1]

    return f"{year}-{month:02d}-{day:02d}"


def parse_boxscore_html(html, gameid, our_team_name):
    """Parse a boxscore HTML file for goals, goalies, and player stats."""
    soup = BeautifulSoup(html, "lxml")
    result = {
        "gameid": gameid,
        "goals": [],
        "penalties": [],
        "goalies": [],
        "game_players": [],
    }

    # Extract game header info (teams, score, date from page)
    full_text = soup.get_text(" ")
    # Try to find "Team A at Team B" pattern
    at_match = re.search(r"([\w\s]+) at ([\w\s]+) - Ice Centre", full_text)
    if at_match:
        result["away_team"] = at_match.group(1).strip()
        result["home_team"] = at_match.group(2).strip()

    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        rows = table.find_all("tr")

        # Goal scoring table: headers = ['team', 'player (assist)', 'time']
        if headers and "player (assist)" in " ".join(headers):
            current_period = None
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                if len(cells) == 1:
                    # Period header like "Period 1" or "Overtime"
                    current_period = cells[0]
                    continue
                if len(cells) >= 3:
                    team = cells[0].strip()
                    player_assist = cells[1].strip()
                    time = cells[2].strip()
                    # Parse scorer and assists
                    pa_m = re.match(r"^([^(]+?)(?:\(([^)]+)\))?$", player_assist)
                    scorer = pa_m.group(1).strip() if pa_m else player_assist
                    assists_raw = pa_m.group(2) if pa_m and pa_m.group(2) else ""
                    assists = [a.strip() for a in assists_raw.split(",") if a.strip()] if assists_raw else []
                    result["goals"].append({
                        "period": current_period,
                        "team": team,
                        "scorer": scorer,
                        "assists": assists,
                        "time": time,
                    })

        # Penalty table
        elif headers and ("player (infraction)" in " ".join(headers) or "infraction" in " ".join(headers)):
            current_period = None
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells or cells[0] == "(no penalties)":
                    continue
                if len(cells) == 1:
                    current_period = cells[0]
                    continue
                if len(cells) >= 3:
                    result["penalties"].append({
                        "period": current_period,
                        "team": cells[0].strip(),
                        "player_infraction": cells[1].strip(),
                        "time": cells[2].strip(),
                    })

        # Goalie table: headers = ['name', 'min', 'shots', 'saves']
        elif headers and "min" in headers and "saves" in headers:
            current_team = None
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                if len(cells) == 1:
                    current_team = cells[0]
                    continue
                if len(cells) >= 4:
                    name = normalize_name(cells[0])
                    if name:
                        result["goalies"].append({
                            "team": current_team,
                            "name": name,
                            "min": cells[1],
                            "shots": cells[2],
                            "saves": cells[3],
                        })

        # Player game stats: headers = ['g', 'a', 'pts', 'pim', 'gwg'] with # Name leading
        elif "g" in headers and "a" in headers and "pts" in headers and "pim" in headers:
            current_team = None
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                if len(cells) == 1:
                    current_team = cells[0]
                    continue
                # Row format: [#, Name, G, A, PTS, PIM, GWG]
                if len(cells) >= 6:
                    # First cell is jersey #, second is name
                    number = cells[0]
                    name = normalize_name(cells[1])
                    if name:
                        try:
                            result["game_players"].append({
                                "team": current_team,
                                "number": number,
                                "name": name,
                                "g": int(cells[2] or 0),
                                "a": int(cells[3] or 0),
                                "pts": int(cells[4] or 0),
                                "pim": int(cells[5] or 0),
                            })
                        except (ValueError, IndexError):
                            pass

    return result


def clean_game(raw_game, slug, our_team):
    """Parse a raw schedule row into a clean game dict."""
    raw = raw_game.get("raw", [])
    if len(raw) < 2:
        return None

    home_raw = raw[0] if len(raw) > 0 else ""
    away_raw = raw[1] if len(raw) > 1 else ""
    date_raw = raw[2] if len(raw) > 2 else ""
    time_raw = raw[3] if len(raw) > 3 else ""
    status_raw = raw[4] if len(raw) > 4 else ""

    home_team, home_score = parse_team_score(home_raw)
    away_team, away_score = parse_team_score(away_raw)

    # Determine which side we're on
    our_lower = our_team.lower() if our_team else ""
    is_home = home_team.lower() == our_lower
    is_away = away_team.lower() == our_lower

    if not is_home and not is_away:
        # Fuzzy match
        is_home = any(t in home_team.lower() for t in OUR_TEAM_NAMES)
        is_away = any(t in away_team.lower() for t in OUR_TEAM_NAMES)

    our_score = home_score if is_home else away_score
    opp_score = away_score if is_home else home_score
    opponent = away_team if is_home else home_team

    # Determine result
    status = status_raw.lower().strip()
    result_type = None
    result = None
    is_playoff = False

    if status in ("data pending", ""):
        result_type = "pending"
    elif status == "forfeit":
        result_type = "forfeit"
        result = "W" if is_home else "L"  # crude guess
    else:
        if "so" in status:
            result_type = "SO"
        elif "ot" in status:
            result_type = "OT"
        else:
            result_type = "REG"

        if our_score is not None and opp_score is not None:
            if our_score > opp_score:
                result = "W"
            elif our_score < opp_score:
                result = "L"
            else:
                result = "T"

    full_date = infer_date(date_raw, slug)

    return {
        "gameid": raw_game.get("gameid"),
        "date": full_date,
        "date_raw": date_raw,
        "time": time_raw,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "our_score": our_score,
        "opp_score": opp_score,
        "opponent": opponent,
        "is_home": is_home,
        "result": result,
        "result_type": result_type,
        "is_playoff": is_playoff,
        "status": status_raw,
    }


def find_our_standing(standings, our_team, teamid):
    """Find our team's rank and total teams in the standings."""
    if not standings:
        return None, None
    total = len(standings)
    for s in standings:
        t_name = (s.get("TEAM") or s.get("TEAM NAME") or "").lower()
        t_id = s.get("teamid")
        if t_id == str(teamid) or any(name in t_name for name in OUR_TEAM_NAMES):
            return s.get("rank"), total
    return None, total


def load_boxscores_from_raw(slug, games, our_team):
    """Load and parse boxscores from raw HTML files."""
    raw_dir = os.path.join(RAW_DIR, slug)
    boxscores = {}
    for game in games:
        gid = game.get("gameid")
        if not gid:
            continue
        path = os.path.join(raw_dir, f"boxscore_{gid}.html")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            boxscores[gid] = parse_boxscore_html(html, gid, our_team)
    return boxscores


def process_season(raw):
    """Convert raw season data into clean format."""
    slug = raw["slug"]
    season = raw["season"]
    teamid = raw.get("teamid")
    our_team = raw.get("team_name") or ""

    # Record
    rec_raw = raw.get("record", {}).get("record_raw", {})
    record = {
        "gp":  int(rec_raw.get("GP", 0) or 0),
        "w":   int(rec_raw.get("W", 0) or 0),
        "l":   int(rec_raw.get("L", 0) or 0),
        "otl": int(rec_raw.get("OTL", 0) or 0),
        "sol": int(rec_raw.get("SOL", 0) or 0),
        "pts": int(rec_raw.get("PTS", 0) or 0),
        "gf":  int(rec_raw.get("GF", 0) or 0),
        "ga":  int(rec_raw.get("GA", 0) or 0),
    }

    # Clean schedule
    clean_games = []
    for g in raw.get("schedule", []):
        cg = clean_game(g, slug, our_team)
        if cg:
            clean_games.append(cg)

    # Playoffs: PointStreak doesn't explicitly flag playoff games in the schedule.
    # Rather than use an unreliable heuristic (all teams face each other multiple times
    # in the regular season), we leave is_playoff=False for all games.
    # The user can visually identify playoffs by date — they're the last April games.

    # Load boxscores from raw HTML
    boxscores = load_boxscores_from_raw(slug, clean_games, our_team)

    # Standings
    raw_standings = raw.get("standings", [])
    our_rank, total_teams = find_our_standing(raw_standings, our_team, teamid)

    # Clean standings
    clean_standings = []
    for s in raw_standings:
        clean_standings.append({
            "rank": s.get("rank"),
            "team": s.get("TEAM") or s.get("TEAM NAME") or "",
            "teamid": s.get("teamid"),
            "gp": s.get("GP"),
            "w": s.get("W"),
            "l": s.get("L"),
            "otl": s.get("OTL"),
            "sol": s.get("SOL"),
            "pts": s.get("PTS"),
            "gf": s.get("GF"),
            "ga": s.get("GA"),
        })

    # Roster - skaters
    skaters = []
    for p in raw.get("roster", {}).get("skaters", []):
        name = normalize_name(p.get("NAME") or p.get("name") or "")
        if not name:
            continue
        try:
            skaters.append({
                "number": p.get("#", ""),
                "name": name,
                "gp": int(p.get("GP", 0) or 0),
                "g":  int(p.get("G", 0) or 0),
                "a":  int(p.get("A", 0) or 0),
                "pts": int(p.get("PTS", 0) or 0),
                "pim": int(p.get("PIM", 0) or 0),
                "pp":  int(p.get("PP", 0) or 0),
                "sh":  int(p.get("SH", 0) or 0),
                "gwg": int(p.get("GWG", 0) or 0),
            })
        except (ValueError, TypeError):
            pass

    # Roster - goalies
    goalies = []
    for g in raw.get("roster", {}).get("goalies", []):
        name = normalize_name(g.get("NAME") or "")
        if not name:
            continue
        try:
            goalies.append({
                "number": g.get("#", ""),
                "name": name,
                "gp": int(g.get("GP", 0) or 0),
                "min": g.get("MIN", ""),
                "w":  int(g.get("W", 0) or 0),
                "l":  int(g.get("L", 0) or 0),
                "ga": int(g.get("GA", 0) or 0),
                "gaa": float(g.get("GAA", 0) or 0),
                "sv": int(g.get("SV", 0) or 0),
                "sv_pct": float(g.get("SV%", 0) or 0),
                "so": int(g.get("SO", 0) or 0),
            })
        except (ValueError, TypeError):
            pass

    # Leaders
    leaders = raw.get("record", {}).get("leaders") or raw.get("leaders") or {}

    return {
        "season": season,
        "slug": slug,
        "team_name": our_team,
        "teamid": teamid,
        "divisionid": raw.get("divisionid"),
        "seasonid": raw.get("seasonid"),
        "source_urls": raw.get("source_urls", {}),
        "record": record,
        "our_rank": our_rank,
        "total_teams": total_teams,
        "leaders": leaders,
        "schedule": clean_games,
        "boxscores": boxscores,
        "standings": clean_standings,
        "skaters": skaters,
        "goalies": goalies,
    }


def aggregate_career_stats(all_seasons):
    """Aggregate career stats for all players across all seasons."""
    player_map = {}  # lower_name -> career dict

    for season in all_seasons:
        slug = season["slug"]
        sname = season["season"]
        for p in season.get("skaters", []):
            name = p["name"]
            key = name.lower()
            if key not in player_map:
                player_map[key] = {
                    "name": name,
                    "seasons": [],
                    "career_gp": 0,
                    "career_g": 0,
                    "career_a": 0,
                    "career_pts": 0,
                    "career_pim": 0,
                    "seasons_count": 0,
                }
            player_map[key]["seasons"].append({
                "season": sname,
                "slug": slug,
                "team_name": season.get("team_name"),
                **p,
            })
            player_map[key]["career_gp"] += p["gp"]
            player_map[key]["career_g"] += p["g"]
            player_map[key]["career_a"] += p["a"]
            player_map[key]["career_pts"] += p["pts"]
            player_map[key]["career_pim"] += p["pim"]
            player_map[key]["seasons_count"] += 1

    return sorted(player_map.values(), key=lambda x: x["career_pts"], reverse=True)


def detect_name_transition(all_seasons):
    """Detect the first season where team name changed to Parking Lot Beers."""
    last = None
    for s in all_seasons:
        name = (s.get("team_name") or "").strip().lower()
        if last and last != name and "vinegar" in last and "parking" in name:
            return s["season"]
        if name:
            last = name
    return None


def load_gamesheet_season(path):
    """
    Load a GameSheet-sourced season JSON (e.g. summer_2026.json).
    These are already in clean format — just normalise player names.
    """
    with open(path) as f:
        s = json.load(f)

    # Normalize player names
    for p in s.get("skaters", []):
        p["name"] = normalize_name(p.get("name") or "") or p.get("name", "")
    for g in s.get("goalies", []):
        g["name"] = normalize_name(g.get("name") or "") or g.get("name", "")

    # Remove keys the app doesn't need
    s.pop("all_division_games", None)
    s.pop("boxscores", None)
    return s


def main():
    with open(os.path.join(DATA_DIR, "all_seasons.json")) as f:
        raw_seasons = json.load(f)

    print("Processing all seasons...")
    all_seasons = []
    for raw in raw_seasons:
        print(f"  Processing {raw['season']}...")
        clean = process_season(raw)
        all_seasons.append(clean)

    # Append any GameSheet seasons (not in PointStreak archive)
    gs_files = ["summer_2026.json"]
    for fname in gs_files:
        fpath = os.path.join(DATA_DIR, fname)
        if os.path.exists(fpath):
            print(f"  Loading GameSheet season: {fname}")
            gs_season = load_gamesheet_season(fpath)
            all_seasons.append(gs_season)

    career_stats = aggregate_career_stats(all_seasons)
    transition_season = detect_name_transition(all_seasons)

    # Build the final app data — strip boxscores from main payload (too large)
    for s in all_seasons:
        s.pop("boxscores", None)

    app_data = {
        "seasons": all_seasons,
        "career_stats": career_stats,
        "name_transition_season": transition_season,
        "total_games": sum(len(s["schedule"]) for s in all_seasons),
        "archive_note": "Raw HTML archived from PointStreak before 5/31/2026 decommission",
    }

    out_path = os.path.join(DATA_DIR, "app_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(app_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Wrote {out_path}")
    print(f"   Seasons: {len(all_seasons)}")
    print(f"   Total games: {app_data['total_games']}")
    print(f"   Career players: {len(career_stats)}")
    print(f"   Name transition: {transition_season}")
    print("\nTop 5 all-time scorers:")
    for i, p in enumerate(career_stats[:5], 1):
        print(f"  {i}. {p['name']}: {p['career_pts']} PTS ({p['career_g']}G + {p['career_a']}A in {p['career_gp']} GP, {p['seasons_count']} seasons)")


if __name__ == "__main__":
    main()
