#!/usr/bin/env python3
"""
Thursday updater for Summer 2026 season.
Run weekly (e.g. Thursday evening) to pull latest scores, standings, and stats
from GameSheet and refresh data/summer_2026.json and data/app_data.json.

Usage:
    python3 update.py
"""

import json, os, re, sys
from datetime import datetime, date
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

GS_SEASON        = "14815"
GS_DIVISION      = "79347"
GS_BASE          = f"https://gamesheetstats.com/seasons/{GS_SEASON}"
GS_SEASON_START  = "2026-05-01"   # used to fetch full season scores history

OUR_TEAM    = "parking lot beers"
TEAMS       = ["Alaskan Bull Worms", "Brown Baggers", "Buff Stuff",
               "Green Belly Hot Sauce", "Parking Lot Beers", "Short Bench"]


# ── helpers ──────────────────────────────────────────────────────────────────

def load_page(page, url, wait=5000):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Scroll to force lazy-load all content
    prev = 0
    for _ in range(12):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        h = page.evaluate("document.body.scrollHeight")
        if h == prev:
            break
        prev = h
    page.wait_for_timeout(wait)
    return page.locator("body").inner_text()


def parse_schedule(text):
    """Parse GameSheet schedule text into a list of game dicts."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    games = []
    i = 0
    while i < len(lines):
        line = lines[i]
        date_m = re.match(r"^(\w+ \d{1,2}, \d{4})$", line)
        if not date_m:
            i += 1
            continue

        game_date = date_m.group(1)
        i += 1
        time_str  = lines[i] if i < len(lines) else ""; i += 1
        game_type = lines[i] if i < len(lines) else ""; i += 1
        if i < len(lines) and "VIEW GAME" in lines[i].upper(): i += 1
        ot_so_hint = "REG"
        if i < len(lines) and "FINAL" in lines[i].upper():
            if "OT" in lines[i].upper(): ot_so_hint = "OT"
            elif "SO" in lines[i].upper(): ot_so_hint = "SO"
            i += 1

        home_team = lines[i].strip() if i < len(lines) else ""; i += 1
        if i < len(lines) and "B2" in lines[i]: i += 1

        home_score = None
        if i < len(lines) and re.match(r"^\d+$", lines[i]):
            home_score = int(lines[i]); i += 1

        away_team = lines[i].strip() if i < len(lines) else ""; i += 1
        if i < len(lines) and "B2" in lines[i]: i += 1

        away_score = None
        if i < len(lines) and re.match(r"^\d+$", lines[i]):
            away_score = int(lines[i]); i += 1

        gm_num = ""
        if i < len(lines) and lines[i].startswith("GM#"):
            gm_num = lines[i].replace("GM#: ", "").strip(); i += 1

        rink = ""
        if i < len(lines) and "Ice Centre" in lines[i]:
            rink = lines[i]; i += 1

        date_iso = game_date
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                date_iso = datetime.strptime(game_date, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass

        is_home    = home_team.lower() == OUR_TEAM
        is_away    = away_team.lower() == OUR_TEAM
        is_our     = is_home or is_away
        opp        = away_team if is_home else home_team
        our_score  = home_score if is_home else away_score
        opp_score  = away_score if is_home else home_score

        result = None
        rtype  = "pending"
        if home_score is not None and away_score is not None:
            rtype = ot_so_hint
            if is_home:
                result = "W" if home_score > away_score else "L" if home_score < away_score else "T"
            elif is_away:
                result = "W" if away_score > home_score else "L" if away_score < home_score else "T"

        games.append({
            "date": date_iso, "date_raw": game_date, "time": time_str,
            "game_type": game_type, "home_team": home_team, "away_team": away_team,
            "home_score": home_score, "away_score": away_score,
            "our_score": our_score, "opp_score": opp_score,
            "gm_num": gm_num, "rink": rink,
            "is_home": is_home, "is_our_game": is_our, "opponent": opp,
            "result": result, "result_type": rtype,
            "is_playoff": "playoff" in game_type.lower(), "status": game_type,
        })

    return games


def parse_scores(text):
    """Parse the GameSheet /scores page (COMPLETED games).

    The scores page layout differs from the schedule page:
      - Visitor team is listed FIRST, home team SECOND (schedule is home-first)
      - The score is a single combined token "VIS-HOME" (e.g. "5-3")
      - OT/SO appears as its own line AFTER the score
    Layout per game:
        <date> / FINAL / <visitor> / B2.. / <V-H score> / [OT|SO] /
        <home> / B2.. / <location> / <type> / <game #>
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    games = []
    i = 0
    date_re  = re.compile(r"^(\w+ \d{1,2}, \d{4})$")
    score_re = re.compile(r"^(\d+)-(\d+)$")
    while i < len(lines):
        m = date_re.match(lines[i])
        if not m or i + 1 >= len(lines) or "FINAL" not in lines[i + 1].upper():
            i += 1
            continue

        game_date = m.group(1)
        j = i + 2
        visitor = lines[j].strip() if j < len(lines) else ""; j += 1
        if j < len(lines) and "B2" in lines[j]: j += 1

        vis_score = home_score = None
        rtype = "REG"
        if j < len(lines):
            sm = score_re.match(lines[j])
            if sm:
                vis_score, home_score = int(sm.group(1)), int(sm.group(2)); j += 1
        if j < len(lines) and lines[j].upper() in ("OT", "SO"):
            rtype = lines[j].upper(); j += 1

        home = lines[j].strip() if j < len(lines) else ""; j += 1
        if j < len(lines) and "B2" in lines[j]: j += 1

        rink = ""
        if j < len(lines) and "Ice Centre" in lines[j]:
            rink = lines[j]; j += 1
        game_type = ""
        if j < len(lines) and "Season" in lines[j]:
            game_type = lines[j]; j += 1
        gm_num = ""
        if j < len(lines) and re.match(r"^\d+$", lines[j]):
            gm_num = lines[j]; j += 1

        date_iso = game_date
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                date_iso = datetime.strptime(game_date, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass

        # Visitor = away, home = home
        away_team, away_score = visitor, vis_score
        home_team = home
        is_home = home_team.lower() == OUR_TEAM
        is_away = away_team.lower() == OUR_TEAM
        is_our  = is_home or is_away
        our_score = home_score if is_home else away_score
        opp_score = away_score if is_home else home_score

        result = None
        if home_score is not None and away_score is not None and is_our:
            result = "W" if our_score > opp_score else "L" if our_score < opp_score else "T"

        games.append({
            "date": date_iso, "date_raw": game_date, "time": "",
            "game_type": game_type, "home_team": home_team, "away_team": away_team,
            "home_score": home_score, "away_score": away_score,
            "our_score": our_score, "opp_score": opp_score,
            "gm_num": gm_num, "rink": rink,
            "is_home": is_home, "is_our_game": is_our,
            "opponent": away_team if is_home else home_team,
            "result": result, "result_type": rtype if result else "pending",
            "is_playoff": "playoff" in game_type.lower(), "status": "FINAL",
        })
        i = j

    return games


def parse_standings(text):
    """Parse GameSheet standings page into a list of team dicts.

    Each team is a tab-delimited row (sometimes with a leading empty field):
        [TEAM] GP W L T OTW OTL SOW SOL PTS PCT RW ROW GF GA DIFF STK PIM ...
    The table is already sorted by rank, so rank = row order.
    """
    standings = []
    for raw in text.split("\n"):
        if "\t" not in raw:
            continue
        parts = [p.strip() for p in raw.split("\t") if p.strip() != ""]
        if not parts or parts[0] not in TEAMS:
            continue

        def n(idx, default="0"):
            return parts[idx] if idx < len(parts) else default

        standings.append({
            "rank":  len(standings) + 1,
            "team":  parts[0],
            "teamid": None,
            "gp":  n(1), "w": n(2), "l": n(3), "t": n(4),
            "otw": n(5), "otl": n(6), "sow": n(7), "sol": n(8),
            "pts": n(9), "gf": n(13), "ga": n(14),
        })

    return standings


def derive_record(plb_games):
    """Calculate W-L-OTL-SOL record from PLB games."""
    w = l = otl = sol = gf = ga = gp = 0
    for g in plb_games:
        if g.get("result") is None:
            continue
        gp += 1
        gf += g.get("our_score") or 0
        ga += g.get("opp_score") or 0
        r, rt = g["result"], g.get("result_type", "REG")
        if r == "W":
            if rt == "OT":   otl += 0; w += 1  # we won in OT
            elif rt == "SO": sol += 0; w += 1
            else:            w += 1
        elif r == "L":
            if rt == "OT":   otl += 1
            elif rt == "SO": sol += 1
            else:            l += 1
    pts = w * 2 + otl + sol
    return {"gp": gp, "w": w, "l": l, "otl": otl, "sol": sol,
            "pts": pts, "gf": gf, "ga": ga}


def parse_players(text):
    """Parse the GameSheet /players page into PLB skater dicts.

    Each player spans several lines following the tab-delimited header row:
        <rank> / <NAME> / <tab jersey [tab pos]> / <team> / <tab-stats line>
    The stats line's numeric fields are, in order: GP G A PTS PIM (PPG, ...).
    Names are upper-cased on GameSheet; we title-case them so brand-new
    players display consistently with the title-case PointStreak history
    (returning players merge by lower-cased key in aggregate_career_stats).
    """
    FLAGS = {"R", "+", "S", "A", "C", "X", "I"}
    lines = [l.rstrip() for l in text.split("\n")]

    # Header row: tab-delimited line containing PLAYER and PTS
    start = None
    for i, l in enumerate(lines):
        if "PLAYER" in l.upper() and "PTS" in l.upper() and "\t" in l:
            start = i + 1
            break
    if start is None:
        return []

    # Rank lines (bare integers) mark the start of each player block
    rank_idx, end = [], len(lines)
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s == "EXPORT" or s.startswith("Powered by"):
            end = i
            break
        if re.match(r"^\d+$", s):
            rank_idx.append(i)

    players = []
    for k, ri in enumerate(rank_idx):
        nxt = rank_idx[k + 1] if k + 1 < len(rank_idx) else end
        block = lines[ri + 1:nxt]

        name, team, best = "", "", []
        for b in block:
            bs = b.strip()
            if not bs or b.startswith("\t"):
                continue
            if bs in TEAMS:
                team = bs
                continue
            if not name and bs not in FLAGS and len(bs) > 1:
                name = bs
        # Stats line = the block line with the most numeric tab fields
        for b in block:
            nums = [t.strip() for t in b.split("\t") if re.match(r"^-?\d+\.?\d*$", t.strip())]
            if len(nums) > len(best):
                best = nums

        if team.lower() == OUR_TEAM and name and len(best) >= 4:
            players.append({
                "number": "", "name": name.title(),
                "gp": int(float(best[0])), "g": int(float(best[1])),
                "a": int(float(best[2])), "pts": int(float(best[3])),
                "pim": int(float(best[4])) if len(best) >= 5 else 0,
                "pp": 0, "sh": 0, "gwg": 0,
            })

    return players


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"=== GameSheet updater — {date.today()} ===")

    season_path = os.path.join(DATA_DIR, "summer_2026.json")
    if not os.path.exists(season_path):
        print(f"ERROR: {season_path} not found. Run scrape.py first.")
        sys.exit(1)

    with open(season_path) as f:
        season = json.load(f)

    print("Launching browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})

        print("  Fetching scores (completed games)...")
        scores_text = load_page(page, f"{GS_BASE}/scores?filter[division]={GS_DIVISION}&filter[start_time_from]={GS_SEASON_START}")
        all_scored  = parse_scores(scores_text)
        plb_scored  = [g for g in all_scored if g.get("is_our_game") and g.get("result")]
        print(f"    {len(all_scored)} division completed games, {len(plb_scored)} PLB completed games")

        print("  Fetching schedule (upcoming games)...")
        sched_text = load_page(page, f"{GS_BASE}/schedule?filter[division]={GS_DIVISION}")
        all_games  = parse_schedule(sched_text)
        plb_games  = [g for g in all_games if g.get("is_our_game")]
        print(f"    {len(all_games)} division games, {len(plb_games)} PLB upcoming games")

        print("  Fetching standings...")
        stand_text = load_page(page, f"{GS_BASE}/standings?filter[division]={GS_DIVISION}")
        standings = parse_standings(stand_text)
        print(f"    {len(standings)} teams in standings")

        print("  Fetching player stats...")
        plb_skaters = []
        for stats_path in ["players", "scoring-leaders"]:
            try:
                player_text = load_page(page, f"{GS_BASE}/{stats_path}?filter[division]={GS_DIVISION}", wait=3000)
                if "404" not in player_text:
                    plb_skaters = parse_players(player_text)
                    if plb_skaters:
                        print(f"    {len(plb_skaters)} PLB skaters from /{stats_path}")
                        break
            except Exception as e:
                print(f"    /{stats_path} failed: {e}")

        browser.close()

    # Merge into a complete game list:
    #   1. Start from cached games (never lose history)
    #   2. Overlay completed games from /scores (authoritative — includes full season)
    #   3. Add any new upcoming games from /schedule not already present
    # Key by date + sorted team names so home/away orientation and differing
    # game-number schemes between the /scores and /schedule pages still collide.
    def game_key(g):
        teams = sorted([(g.get("home_team") or "").lower(),
                        (g.get("away_team") or "").lower()])
        return f"{g['date']}|{teams[0]}|{teams[1]}"

    old_games  = season.get("schedule", [])
    merged_map = {game_key(g): g for g in old_games}

    for g in plb_scored:                          # scores page wins for completed games
        merged_map[game_key(g)] = g

    for g in plb_games:                           # schedule page adds new upcoming games
        k = game_key(g)
        if k not in merged_map:
            merged_map[k] = g

    merged = sorted(merged_map.values(), key=lambda g: g["date"])
    print(f"    Merge: {len(old_games)} cached + {len(plb_scored)} scored + {len(plb_games)} upcoming → {len(merged)} total")

    # Update season data
    record = derive_record(merged)
    season["schedule"]           = merged
    season["all_division_games"] = all_games
    season["record"]             = record
    season["standings"]          = standings
    season["skaters"]            = plb_skaters
    season["last_updated"]       = str(date.today())

    # Derive our standing from standings
    for i, t in enumerate(standings):
        if t["team"].lower() == OUR_TEAM:
            season["our_rank"]    = t["rank"]
            season["total_teams"] = len(standings)
            break

    with open(season_path, "w") as f:
        json.dump(season, f, indent=2)
    print(f"\n✅ Saved {season_path}")

    # Regenerate app_data.json
    print("Regenerating app_data.json...")
    import subprocess
    result = subprocess.run(["python3", os.path.join(BASE_DIR, "process.py")], capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ app_data.json updated")
    else:
        print(f"⚠  process.py failed: {result.stderr[:200]}")

    # Summary
    played   = [g for g in merged if g.get("result")]
    upcoming = [g for g in merged if not g.get("result")]
    print(f"\n=== SUMMARY ===")
    print(f"Record:    {record['w']}-{record['l']}-{record['otl']}-{record['sol']}  ({record['pts']} PTS)")
    print(f"Games:     {len(played)} played, {len(upcoming)} upcoming")
    if standings:
        our = next((t for t in standings if t["team"].lower() == OUR_TEAM), None)
        if our:
            print(f"Standing:  {our['rank']}/{len(standings)}")
    if plb_skaters:
        top = sorted(plb_skaters, key=lambda x: x["pts"], reverse=True)[:3]
        print("Top scorers:", ", ".join(f"{p['name']} ({p['pts']})" for p in top))
    if upcoming:
        next_g = upcoming[0]
        print(f"Next game: {next_g['date']} {next_g['time']} {'vs' if next_g['is_home'] else 'at'} {next_g['opponent']}")


if __name__ == "__main__":
    main()
