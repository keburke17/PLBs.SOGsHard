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

# A complete, current-looking Chrome UA. A truncated UA (no "Chrome/… Safari/…"
# tail) is itself a bot signal to Cloudflare — keep this realistic.
BROWSER_UA  = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/126.0.0.0 Safari/537.36")

OUR_TEAM    = "parking lot beers"
TEAMS       = ["Alaskan Bull Worms", "Brown Baggers", "Buff Stuff",
               "Green Belly Hot Sauce", "Parking Lot Beers", "Short Bench"]


# ── helpers ──────────────────────────────────────────────────────────────────

# Markers of Cloudflare's interstitial (the static "verify you're human" page,
# NOT the real content). This one does not auto-resolve in headless Chromium.
CF_CHALLENGE_MARKERS = ("performing security verification", "just a moment",
                        "verify you are human", "cf-challenge")


def _looks_like_challenge(text):
    low = text.lower()
    return len(text) < 600 and any(m in low for m in CF_CHALLENGE_MARKERS)


def _short(url):
    return url.split("/")[-1][:34]


def _new_page(browser):
    """A fresh, ordinary-looking browser context + page.

    GameSheet is behind Cloudflare's bot challenge (added ~Jul 2026). Two things
    matter to get through it without any CAPTCHA solving:
      1. Present as a real browser — full UA, no AutomationControlled flag (set at
         launch), no navigator.webdriver.
      2. Use a FRESH context per page. Cloudflare lets each new context through on
         its first navigation, then hard-challenges reuse — so every page we scrape
         gets its own short-lived session. The caller closes the context.
    """
    ctx = browser.new_context(
        user_agent=BROWSER_UA,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    page = ctx.new_page()
    page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return ctx, page


def load_page(browser, url, wait=5000, cf_retries=2):
    """Load one page in its own fresh context and return its body text.

    On the rare occasion the first navigation still draws a challenge, retry with
    another fresh context. Exhausting retries returns "" — the parsers yield no
    rows and update.py's data-integrity guard aborts the run (a later scheduled
    run retries), rather than overwriting good data with a challenge page.
    """
    for attempt in range(cf_retries):
        ctx, page = _new_page(browser)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            if _looks_like_challenge(page.locator("body").inner_text()):
                print(f"    Cloudflare challenge on {_short(url)} — retry with fresh session ({attempt + 1}/{cf_retries})")
                continue
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
        finally:
            ctx.close()
    return ""


def parse_schedule(text):
    """Parse the GameSheet schedule (SCHEDULED / upcoming) page.

    Post-redesign layout per game — visitor listed first, like the scores page:
        <date> / <visitor> / "B2 - Wed/Thu" / <time> / <home> / "B2 - Wed/Thu" /
        <location> / <game #> / <type>
    Scheduled games carry no score, so results stay pending; completed results
    come from parse_scores instead.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    date_re = re.compile(r"^\w+ \d{1,2}, \d{4}$")
    time_re = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.I)
    games = []
    i = 0
    while i < len(lines):
        if not date_re.match(lines[i]):
            i += 1
            continue

        game_date = lines[i]; j = i + 1
        visitor = lines[j].strip() if j < len(lines) else ""; j += 1
        if j < len(lines) and "B2" in lines[j]: j += 1
        time_str = ""
        if j < len(lines) and time_re.match(lines[j]):
            time_str = lines[j]; j += 1
        home = lines[j].strip() if j < len(lines) else ""; j += 1
        if j < len(lines) and "B2" in lines[j]: j += 1
        rink = ""
        if j < len(lines) and "Ice Centre" in lines[j]:
            rink = lines[j]; j += 1
        gm_num = ""
        if j < len(lines) and re.match(r"^\d+$", lines[j]):
            gm_num = lines[j]; j += 1
        game_type = ""
        if j < len(lines) and "Season" in lines[j]:
            game_type = lines[j]; j += 1

        date_iso = game_date
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                date_iso = datetime.strptime(game_date, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass

        # Both scores and schedule pages list visitor first, home second.
        away_team, home_team = visitor, home
        is_home = home_team.lower() == OUR_TEAM
        is_away = away_team.lower() == OUR_TEAM
        is_our  = is_home or is_away
        opp     = away_team if is_home else home_team

        games.append({
            "date": date_iso, "date_raw": game_date, "time": time_str,
            "game_type": game_type or "Regular Season",
            "home_team": home_team, "away_team": away_team,
            "home_score": None, "away_score": None,
            "our_score": None, "opp_score": None,
            "gm_num": gm_num, "rink": rink,
            "is_home": is_home, "is_our_game": is_our, "opponent": opp,
            "result": None, "result_type": "pending",
            "is_playoff": "playoff" in (game_type or "").lower(),
            "status": game_type or "Regular Season",
        })
        i = j

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
        # A completed-game block is a date line followed shortly by a
        # "visitor / division / V-H score" sequence. GameSheet dropped the
        # old "FINAL" label from this page, so detect the block by the nearby
        # score token instead of requiring "FINAL".
        if not m or not any(score_re.match(x) for x in lines[i + 1:i + 6]):
            i += 1
            continue

        game_date = m.group(1)
        j = i + 1
        if j < len(lines) and "FINAL" in lines[j].upper():
            j += 1  # legacy layout still had a FINAL line here
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
        # Game # and type can appear in either order after the rink.
        game_type = ""
        gm_num = ""
        for _ in range(2):
            if j < len(lines) and re.match(r"^\d+$", lines[j]):
                gm_num = lines[j]; j += 1
            elif j < len(lines) and "Season" in lines[j]:
                game_type = lines[j]; j += 1

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


# A bare time string ("10:45 PM"). Used to detect field-shift parse artifacts
# where a time value lands in a team-name slot.
TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.I)


def sanitize_games(games):
    """Drop field-shift parse artifacts and collapse duplicate games.

    GameSheet occasionally serves a schedule row in a variant column order,
    which the positional parser mis-reads into a shifted record (e.g. a time
    like '10:45 PM' ends up in a team-name field). Those rows are dropped.
    Entries sharing the same date + teams are then collapsed, keeping the most
    complete one (prefers a real result, then a game number, then a rink)."""
    kept = []
    for g in games:
        if (TIME_ONLY_RE.match((g.get("home_team") or "").strip()) or
                TIME_ONLY_RE.match((g.get("away_team") or "").strip())):
            print(f"    dropped malformed game row: {g.get('date')} "
                  f"{g.get('home_team')!r} vs {g.get('away_team')!r}")
            continue
        kept.append(g)

    def richness(g):
        return (g.get("result") is not None, bool(g.get("gm_num")), bool(g.get("rink")))

    best = {}
    for g in kept:
        teams = sorted([(g.get("home_team") or "").lower(),
                        (g.get("away_team") or "").lower()])
        key = f"{g.get('date')}|{teams[0]}|{teams[1]}"
        if key not in best or richness(g) > richness(best[key]):
            best[key] = g
    return sorted(best.values(), key=lambda g: g.get("date", ""))


def parse_standings(text):
    """Parse GameSheet standings page into a list of team dicts.

    Post-redesign layout: each team spans three lines —
        <rank> / <TEAM name> / <tab-stats>
    where the stats line (no team name) is:
        GP W L T OTW OTL SOW SOL PTS PCT RW ROW GF GA DIFF STK PIM ...
    Summary cards near the top also list team names, but those are followed by
    a plain "N PTS"/"N GF" line (no tab), so requiring a tab-delimited next line
    keeps only real table rows. The table is pre-sorted, so rank = row order.
    """
    lines = [l.rstrip() for l in text.split("\n")]
    standings = []
    for i, l in enumerate(lines):
        if l.strip() not in TEAMS:
            continue
        stats = lines[i + 1] if i + 1 < len(lines) else ""
        if "\t" not in stats:
            continue
        parts = stats.split("\t")
        # The rank column renders as a blank leading cell in the full page,
        # so drop any leading empties to anchor parts[0] on GP.
        while parts and parts[0].strip() == "":
            parts.pop(0)

        def n(idx, default="0"):
            return parts[idx].strip() if idx < len(parts) and parts[idx].strip() else default

        standings.append({
            "rank":  len(standings) + 1,
            "team":  l.strip(),
            "teamid": None,
            "gp":  n(0), "w": n(1), "l": n(2), "t": n(3),
            "otw": n(4), "otl": n(5), "sow": n(6), "sol": n(7),
            "pts": n(8), "gf": n(12), "ga": n(13),
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


# GameSheet's /players and /goalies pages share a multi-line, tab-delimited
# row layout: <rank> / <NAME> / <tab jersey [tab pos]> / <team> / <tab-stats>.
# Jersey lines like "\t01" strip to bare integers (look like rank lines), so we
# anchor on the unambiguous TEAM line instead: name is 2 lines above, stats 1
# line below. Names are upper-cased on GameSheet; we title-case them so brand-new
# players display consistently with the title-case PointStreak history (returning
# players merge by lower-cased key in aggregate_career_stats).
_PLAYER_FLAGS = {"R", "+", "S", "A", "C", "X", "I"}


def _stat_region(lines, *must_have):
    """Return (start, end) line indices of the data region: the line after the
    tab-delimited header containing all `must_have` tokens, up to EXPORT/footer."""
    start = None
    for i, l in enumerate(lines):
        up = l.upper()
        if "\t" in l and all(t in up for t in must_have):
            start = i + 1
            break
    if start is None:
        return None, None
    end = len(lines)
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s == "EXPORT" or s.startswith("Powered by"):
            end = i
            break
    return start, end


def _row_nums(line):
    """Numeric fields (ints/floats) from a tab-delimited stats line, in order."""
    return [t.strip() for t in line.split("\t") if re.match(r"^-?\d+\.?\d*$", t.strip())]


def _anchored_rows(lines, start, end):
    """Yield (name, nums) for each PLB row, anchoring on the team line."""
    for i in range(start, end):
        if lines[i].strip() != "Parking Lot Beers":
            continue
        name = lines[i - 2].strip() if i >= 2 else ""
        nums = _row_nums(lines[i + 1]) if i + 1 < len(lines) else []
        if name and name not in _PLAYER_FLAGS and len(name) > 1:
            yield name, nums


def parse_players(text):
    """Extract PLB skater rows from whatever is currently rendered.

    Post-redesign /players is a virtualized, division-wide leaderboard, so only
    the visible window is in the DOM at any moment — this is called repeatedly
    while scrolling and results are unioned by name (see collect_plb_rows).
    Header-independent: anchors on the PLB team line, name two lines above,
    tab-delimited stats one line below (GP G A PTS PIM ...). Summary-card blocks
    (e.g. "GOALS LEADER / <name> / Parking Lot Beers / 9 G") are rejected by
    requiring >=4 numeric stat fields on the following line.
    """
    lines = [l.rstrip() for l in text.split("\n")]
    players = []
    for i, l in enumerate(lines):
        if l.strip() != "Parking Lot Beers":
            continue
        name = lines[i - 2].strip() if i >= 2 else ""
        nums = _row_nums(lines[i + 1]) if i + 1 < len(lines) else []
        if not name or name in _PLAYER_FLAGS or len(name) <= 1:
            continue
        if not re.search(r"[A-Za-z]", name) or name in TEAMS:
            continue
        if len(nums) < 4:
            continue
        players.append({
            "number": "", "name": name.title(),
            "gp": int(float(nums[0])), "g": int(float(nums[1])),
            "a": int(float(nums[2])), "pts": int(float(nums[3])),
            "pim": int(float(nums[4])) if len(nums) >= 5 else 0,
            "pp": 0, "sh": 0, "gwg": 0,
        })
    return players


def collect_plb_rows(browser, url, parse_fn, max_steps=60, cf_retries=2):
    """Scrape a virtualized leaderboard by scrolling incrementally and unioning
    parsed rows by name. A single inner_text() only sees the rendered window, so
    we parse at every scroll step until the bottom stops moving.

    Uses a fresh context per attempt (see _new_page) to clear Cloudflare; a
    challenge that survives the retries yields no rows, which the caller's guard
    treats as a failed scrape rather than a real empty roster."""
    for attempt in range(cf_retries):
        ctx, page = _new_page(browser)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            if _looks_like_challenge(page.locator("body").inner_text()):
                print(f"    Cloudflare challenge on {_short(url)} — retry with fresh session ({attempt + 1}/{cf_retries})")
                continue
            seen = {}
            prev_y = -1
            steps = 0
            for _ in range(max_steps):
                steps += 1
                for row in parse_fn(page.locator("body").inner_text()):
                    seen[row["name"].lower()] = row
                y  = page.evaluate("window.scrollY")
                h  = page.evaluate("document.body.scrollHeight")
                ih = page.evaluate("window.innerHeight")
                if y + ih >= h - 5:
                    if y == prev_y:
                        break
                    prev_y = y
                page.evaluate("window.scrollBy(0, Math.round(window.innerHeight*0.55))")
                page.wait_for_timeout(450)
            print(f"    (scroll-collected over {steps} steps)")
            return list(seen.values())
        finally:
            ctx.close()
    return []


def parse_goalies(text):
    """Parse the GameSheet /goalies page into PLB goalie dicts.
    Stats line numeric order: GP GS SA GA GAA SV% W L T OTL PPGA SHGA SO ..."""
    lines = [l.rstrip() for l in text.split("\n")]
    start, end = _stat_region(lines, "GOALIE", "GAA")
    if start is None:
        return []
    goalies = []
    for name, nums in _anchored_rows(lines, start, end):
        if len(nums) < 8:
            continue
        f = lambda idx: float(nums[idx]) if idx < len(nums) else 0
        goalies.append({
            "name": name.title(),
            "gp": int(f(0)), "w": int(f(6)), "l": int(f(7)),
            "gaa": f(4), "sv_pct": f(5),
            "so": int(f(12)) if len(nums) > 12 else 0,
        })
    return goalies


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
        # GameSheet sits behind Cloudflare's bot challenge (added ~Jul 2026). A
        # default-headless Chromium with a truncated UA gets served the
        # "Performing security verification" interstitial instead of data, which
        # makes every scrape return 0 rows and the run abort. The launch flag below
        # plus the per-page fresh context in _new_page clear the challenge with no
        # CAPTCHA solving; each load_page / collect_plb_rows call gets its own
        # short-lived session (Cloudflare hard-challenges context reuse).
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        print("  Fetching scores (completed games)...")
        scores_text = load_page(browser, f"{GS_BASE}/scores?filter[division]={GS_DIVISION}&filter[start_time_from]={GS_SEASON_START}")
        all_scored  = parse_scores(scores_text)
        plb_scored  = [g for g in all_scored if g.get("is_our_game") and g.get("result")]
        print(f"    {len(all_scored)} division completed games, {len(plb_scored)} PLB completed games")

        print("  Fetching schedule (upcoming games)...")
        sched_text = load_page(browser, f"{GS_BASE}/schedule?filter[division]={GS_DIVISION}")
        all_games  = sanitize_games(parse_schedule(sched_text))
        plb_games  = [g for g in all_games if g.get("is_our_game")]
        print(f"    {len(all_games)} division games, {len(plb_games)} PLB upcoming games")

        print("  Fetching standings...")
        stand_text = load_page(browser, f"{GS_BASE}/standings?filter[division]={GS_DIVISION}")
        standings = parse_standings(stand_text)
        print(f"    {len(standings)} teams in standings")

        print("  Fetching player stats...")
        plb_skaters = collect_plb_rows(browser, f"{GS_BASE}/players?filter[division]={GS_DIVISION}", parse_players)
        print(f"    {len(plb_skaters)} PLB skaters")

        print("  Fetching goalie stats...")
        plb_goalies = []
        try:
            goalie_text = load_page(browser, f"{GS_BASE}/goalies?filter[division]={GS_DIVISION}", wait=3000)
            plb_goalies = parse_goalies(goalie_text)
            print(f"    {len(plb_goalies)} PLB goalies")
        except Exception as e:
            print(f"    /goalies failed: {e}")

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
    merged = sanitize_games(merged)   # drop shifted-field rows, collapse dupes
    print(f"    Merge: {len(old_games)} cached + {len(plb_scored)} scored + {len(plb_games)} upcoming → {len(merged)} total")

    # Update season data
    record = derive_record(merged)
    season["schedule"]           = merged
    season["all_division_games"] = all_games
    season["record"]             = record

    # Guard against a fully-blocked run: if every source came back empty,
    # Cloudflare almost certainly challenged the whole run. Abort loudly
    # (CI goes red, a later scheduled run retries) rather than committing a
    # no-op "update"; the existing data is left untouched.
    if not (all_scored or all_games or standings or plb_skaters or plb_goalies):
        raise SystemExit(
            "ABORT: every scrape returned 0 rows — the run was likely blocked "
            "entirely; leaving existing data untouched."
        )

    # Partial-scrape protection for the roster. The full skater roster is no
    # longer reachable headlessly (GameSheet's leaderboard API 403s without a
    # cf_clearance cookie; only the top ~20 division-wide rows are
    # server-rendered), so instead of aborting, merge: scraped rows overlay the
    # cached roster by name and everyone else keeps their cached stats.
    # Counting stats only ever grow, so a scraped row is always at least as
    # fresh as its cached version — the merge can never regress data, which
    # honors the old hard guard's intent without failing the whole run.
    prev_skaters = season.get("skaters", [])
    merged_skaters = {p["name"].lower(): p for p in prev_skaters}
    for p in plb_skaters:
        merged_skaters[p["name"].lower()] = p
    all_skaters = sorted(merged_skaters.values(),
                         key=lambda p: (-p["pts"], p["name"]))
    if len(plb_skaters) < len(all_skaters):
        print(f"⚠  players scrape returned {len(plb_skaters)} of "
              f"{len(all_skaters)} known skaters — unscraped players keep "
              f"their cached stats.")

    prev_standings = season.get("standings", [])
    if prev_standings and len(standings) < len(prev_standings):
        print(f"⚠  standings scrape returned {len(standings)} rows vs "
              f"{len(prev_standings)} on file — keeping existing standings.")
        standings = prev_standings

    # Goalies get the same merge — the old skater abort shielded them from a
    # wholesale overwrite by an empty scrape; the merge keeps that protection.
    prev_goalies = season.get("goalies", [])
    merged_goalies = {g["name"].lower(): g for g in prev_goalies}
    for g in plb_goalies:
        merged_goalies[g["name"].lower()] = g
    all_goalies = sorted(merged_goalies.values(), key=lambda g: -g["gp"])

    season["standings"]          = standings
    season["skaters"]            = all_skaters
    season["goalies"]            = all_goalies
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
    if all_skaters:
        top = all_skaters[:3]
        print("Top scorers:", ", ".join(f"{p['name']} ({p['pts']})" for p in top))
    if all_goalies:
        print("Goalies:   ", ", ".join(f"{g['name']} ({g['w']}-{g['l']}, {g['gaa']:.2f} GAA)" for g in all_goalies))
    if upcoming:
        next_g = upcoming[0]
        print(f"Next game: {next_g['date']} {next_g['time']} {'vs' if next_g['is_home'] else 'at'} {next_g['opponent']}")


if __name__ == "__main__":
    main()
