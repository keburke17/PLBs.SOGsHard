#!/usr/bin/env python3
"""
Updater for the live GameSheet season (currently Winter 2026-27).
Run after game nights to pull latest scores, standings, and stats from GameSheet
and refresh data/<SEASON_FILE> and data/app_data.json.

Usage:
    python3 update.py
"""

import json, os, re, sys
from datetime import datetime, date
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# ── season rollover: change these five together ──────────────────────────────
# SEASON_FILE lives here rather than inline in main() so a rollover can't half
# happen. Pointing GS_SEASON at a new season while still writing the old season's
# file would scrape the NEW season straight over the ARCHIVED one; the
# gs_season_id check in main() is the backstop if these ever drift apart anyway.
# Winter 2026-27 (rolled over 2026-10-01). Summer 2026 was 14815 / 79347 / 512204.
GS_SEASON        = "15870"
GS_DIVISION      = "86566"
GS_TEAM          = "560174"
GS_SEASON_START  = "2026-09-24"   # season's first day (informational)
SEASON_FILE      = "winter_26_27.json"

# Standings points per result. Winter 2026-27 is a "3-point" season (regulation
# win 3, OT/SO win 2, OT/SO loss 1); Summer 2026 was a flat win = 2. Only used for
# the fallback record in derive_record() — when the standings scrape has our row,
# GameSheet's own PTS wins (see main), so a wrong guess here can't stick.
# A level final score (2-2, GM#48 on 2026-09-30) shows as a tie worth 0 on GameSheet.
PTS_REG_WIN, PTS_OT_WIN, PTS_OT_LOSS, PTS_TIE = 3, 2, 1, 0

GS_BASE          = f"https://gamesheetstats.com/seasons/{GS_SEASON}"

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


# Shared row-field matchers for the positional completed/scheduled games parsers.
# A bare time string ("10:45 PM") also flags field-shift parse artifacts, where a
# time value lands in a team-name slot (see sanitize_games).
DATE_RE      = re.compile(r"^(\w+ \d{1,2}, \d{4})$")
TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.I)


def _is_game_type(line):
    """True if `line` is the trailing "Type" column ("Regular Season", "Playoff", …).

    Matched by exclusion rather than a keyword whitelist: both parsers used to
    test `"Season" in line`, which recognised "Regular Season" but silently
    dropped "Playoff" — every postseason game came through with game_type "" and
    is_playoff False. The Type cell is the last field in a row, so anything there
    that isn't a date, a time or a bare number is the type, and a label we've
    never seen before still gets picked up.
    """
    return bool(line) and not (DATE_RE.match(line) or TIME_ONLY_RE.match(line)
                               or line.isdigit())


def _iso_date(raw):
    """'Sep 30, 2026' / 'September 30, 2026' / 'WED, SEP 30, 2026' -> ISO date."""
    raw = re.sub(r"^[A-Za-z]{3},\s*", "", raw.strip()).title()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


_TEAM_BY_LOWER = {t.lower(): t for t in TEAMS}


def _canon_team(name):
    """Map a team name to its canonical casing. The narrow "card" layout renders
    names upper-cased via CSS ("PARKING LOT BEERS"); everything downstream keys
    on the title-case names."""
    name = (name or "").strip()
    return _TEAM_BY_LOWER.get(name.lower(), name)


# ── /games "card" layout (narrow viewport) ───────────────────────────────────
# Since the Sep 2026 redesign /scores and /schedule both redirect to
# /games?filter[status]=completed|scheduled. At desktop width (which is what
# _new_page's 1280px viewport gets) that page is the same table the old pages
# had, so the positional parsers below still apply. Below the `md` breakpoint it
# renders stacked cards instead, all upper-cased:
#     WED, SEP 30, 2026                 <- day header (printed twice)
#     GM#: 51 / ICE CENTRE - BLUE / <visitor> / B2 - WED/THU / <home> / B2 - WED/THU
#     then  4 / FINAL / 7               (completed: visitor score, status, home score)
#     or    10:45 PM / SEP 30           (scheduled)
# Cards carry no game-type column, so playoff games can't be told apart here.
# parse_scores / parse_schedule fall back to this when the table layout yields
# nothing, so a viewport or breakpoint change degrades instead of breaking.
CARD_DAY_RE = re.compile(r"^[A-Za-z]{3}, [A-Za-z]{3,9} \d{1,2}, \d{4}$")
CARD_GM_RE  = re.compile(r"^GM#:\s*(\S+)$", re.I)
FINAL_RE    = re.compile(r"^FINAL\b[\s/\-(]*(OT|SO)?", re.I)


def _game_record(date_raw, visitor, home, vis_score=None, home_score=None,
                 rtype="REG", time_str="", gm_num="", rink="", game_type=""):
    """Build one game dict (visitor = away). Scores None -> a pending game."""
    away_team, home_team = _canon_team(visitor), _canon_team(home)
    is_home = home_team.lower() == OUR_TEAM
    is_our  = is_home or away_team.lower() == OUR_TEAM
    done    = vis_score is not None and home_score is not None
    our_score = (home_score if is_home else vis_score) if done else None
    opp_score = (vis_score if is_home else home_score) if done else None
    result = None
    if done and is_our:
        result = "W" if our_score > opp_score else "L" if our_score < opp_score else "T"
    if done:
        result_type = rtype if result else "pending"
    else:
        result_type = "pending"
    return {
        "date": _iso_date(date_raw), "date_raw": date_raw, "time": time_str,
        "game_type": game_type or ("" if done else "Regular Season"),
        "home_team": home_team, "away_team": away_team,
        "home_score": home_score if done else None,
        "away_score": vis_score if done else None,
        "our_score": our_score, "opp_score": opp_score,
        "gm_num": gm_num, "rink": rink,
        "is_home": is_home, "is_our_game": is_our,
        "opponent": away_team if is_home else home_team,
        "result": result, "result_type": result_type,
        "is_playoff": "playoff" in (game_type or "").lower(),
        "status": "FINAL" if done else (game_type or "Regular Season"),
    }


def parse_game_cards(text):
    """Parse the narrow-viewport card layout of /games (see comment above).
    Returns completed and scheduled games together; callers filter."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    games, day = [], ""
    i = 0
    while i < len(lines):
        if CARD_DAY_RE.match(lines[i]):
            day = lines[i]; i += 1
            continue
        m = CARD_GM_RE.match(lines[i])
        if not m or not day or i + 8 >= len(lines):
            i += 1
            continue
        rink, visitor, home = lines[i + 1], lines[i + 2], lines[i + 4]
        if "B2" not in lines[i + 3].upper() or "B2" not in lines[i + 5].upper():
            i += 1
            continue
        a, b, c = lines[i + 6], lines[i + 7], lines[i + 8]
        fm = FINAL_RE.match(b)
        if a.isdigit() and fm and c.isdigit():
            games.append(_game_record(day, visitor, home, int(a), int(c),
                                      rtype=(fm.group(1) or "REG").upper(),
                                      gm_num=m.group(1), rink=rink.title(),
                                      game_type="Regular Season"))
            i += 9
        elif TIME_ONLY_RE.match(a):
            games.append(_game_record(day, visitor, home, time_str=a,
                                      gm_num=m.group(1), rink=rink.title()))
            i += 8
        else:
            i += 1      # in-progress or unknown card shape: skip, don't guess
    return games


def parse_schedule(text):
    """Parse upcoming games from /games?filter[status]=scheduled (table layout).

    Layout per game — visitor listed first, like the completed view:
        <date> / <visitor> / "B2 - Wed/Thu" / <time> / <home> / "B2 - Wed/Thu" /
        <location> / <game #> / <type>
    Scheduled games carry no score, so results stay pending; completed results
    come from parse_scores instead. A block with no time in the 4th slot is not
    an upcoming game (the team schedule page mixes in completed rows, which show
    "L 4 - 7" there) and is skipped. Falls back to the card layout when the
    table yields nothing.
    """
    games = _parse_schedule_table(text)
    if not games:
        games = [g for g in parse_game_cards(text) if g["home_score"] is None]
    return games


def _parse_schedule_table(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    games = []
    i = 0
    while i < len(lines):
        if not DATE_RE.match(lines[i]):
            i += 1
            continue

        game_date = lines[i]; j = i + 1
        visitor = lines[j].strip() if j < len(lines) else ""; j += 1
        if j < len(lines) and "B2" in lines[j]: j += 1
        if not (j < len(lines) and TIME_ONLY_RE.match(lines[j])):
            i += 1          # not an upcoming-game block (see docstring)
            continue
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
        if j < len(lines) and _is_game_type(lines[j]):
            game_type = lines[j]; j += 1

        games.append(_game_record(game_date, visitor, home, time_str=time_str,
                                  gm_num=gm_num, rink=rink, game_type=game_type))
        i = j

    return games


def parse_scores(text):
    """Parse completed games from /games?filter[status]=completed (table layout).

      - Visitor team is listed FIRST, home team SECOND
      - The score is a single combined token "VIS-HOME" (e.g. "5-3")
      - OT/SO appears as its own line AFTER the score (not yet re-confirmed on
        the Sep 2026 /games page — no OT/SO game had been played when it was
        checked; a level score with no marker is recorded as a tie)
    Layout per game:
        <date> / <visitor> / B2.. / <V-H score> / [OT|SO] /
        <home> / B2.. / <location> / <game #> / <type>
    Falls back to the card layout when the table yields nothing.
    """
    games = _parse_scores_table(text)
    if not games:
        games = [g for g in parse_game_cards(text) if g["home_score"] is not None]
    return games


def _parse_scores_table(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    games = []
    i = 0
    score_re = re.compile(r"^(\d+)-(\d+)$")
    while i < len(lines):
        m = DATE_RE.match(lines[i])
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
            elif j < len(lines) and _is_game_type(lines[j]):
                game_type = lines[j]; j += 1

        if vis_score is not None:
            games.append(_game_record(game_date, visitor, home, vis_score, home_score,
                                      rtype=rtype, gm_num=gm_num, rink=rink,
                                      game_type=game_type))
        i = j

    return games


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
    pts = 0
    for g in plb_games:
        if g.get("result") is None:
            continue
        gp += 1
        gf += g.get("our_score") or 0
        ga += g.get("opp_score") or 0
        r, rt = g["result"], g.get("result_type", "REG")
        if r == "W":
            w += 1
            pts += PTS_OT_WIN if rt in ("OT", "SO") else PTS_REG_WIN
        elif r == "L":
            if rt == "OT":   otl += 1; pts += PTS_OT_LOSS
            elif rt == "SO": sol += 1; pts += PTS_OT_LOSS
            else:            l += 1
        elif r == "T":
            pts += PTS_TIE
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
    # Leading-dot decimals count too: SV% renders as ".857", and dropping it shifted
    # every goalie column after it (W read as SV%, L as W, T as L).
    return [t.strip() for t in line.split("\t")
            if re.match(r"^-?(\d+\.?\d*|\.\d+)$", t.strip())]


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


# ── per-game lineups (the authoritative skater source) ───────────────────────
#
# The division-wide /players leaderboard only server-renders its top ~20 rows and
# fetches the rest through a Cloudflare-gated XHR, so PLB players outside that
# window could never be refreshed — their cached stats froze (a 6-GP player stayed
# at 6 GP all season). Per-game lineups fix that at the source: every completed
# game's page server-renders BOTH teams' dressed rosters with G/A/PTS/PIM, so the
# full-season table can be summed from them, and GP is simply how many lineups a
# player appears in.
#
# The team's own /schedule page (not the division games list) is used to enumerate
# games: the division list only returned roughly the last 18 games in Summer 2026,
# which silently drops the early season.
#
# Since the Sep 2026 redesign that page IGNORES filter[status]=completed and lists
# the whole season, upcoming games included. Only rows showing a result
# ("L 4 - 7") are completed, so ids are filtered by those rows' dates — otherwise
# every unplayed game would be fetched, fail with "no lineup rows", and the
# completeness guard would never let the aggregate through.
#
# NOTE (verified 2026-08-10): the team page's own Stats tab looks like a shortcut
# but is NOT division-scoped — it aggregates each player across every team they
# appear on in the season, so it reports GP far above the team's games played.
# Do not use it; per-game lineups are the only division-correct source.

# Fetched same-origin from an already-loaded page rather than by navigating to each
# game: one navigation gets a Cloudflare-cleared context, and the subsequent
# fetches ride its cookies. This is both far lighter on the site (12 XHRs vs 12
# full page loads) and much less likely to trip bot protection.
_LINEUPS_JS = """
async ([ids, teamName, teams, delayMs]) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = {}, errors = [];

  // Each table is rendered twice (mobile + desktop layouts) and both teams'
  // tables share ancestors, so identify a table's team by walking up to the
  // nearest ancestor that mentions exactly ONE known team name.
  const teamOf = (table) => {
    let n = table;
    for (let d = 0; d < 8 && n; d++) {
      n = n.parentElement;
      if (!n) break;
      const txt = n.textContent || '';
      const hits = teams.filter(t => txt.includes(t));
      if (hits.length === 1) return hits[0];
    }
    return null;
  };

  for (const id of ids) {
    try {
      const r = await fetch(`/seasons/${SEASON}/games/${id}?tab=lineups`,
                            {credentials: 'include'});
      if (r.status !== 200) throw new Error('status ' + r.status);
      const html = await r.text();
      if (/Just a moment|security verification/i.test(html)) throw new Error('challenged');
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const game = {skaters: [], goalies: []};
      const seen = new Set();
      for (const t of doc.querySelectorAll('table')) {
        if (teamOf(t) !== teamName) continue;
        const hdr = [...t.querySelectorAll('thead th, thead td')].map(x => x.textContent.trim());
        const isSk = hdr.includes('Player'), isG = hdr.includes('Goalie');
        if (!isSk && !isG) continue;
        for (const tr of t.querySelectorAll('tbody tr')) {
          const c = [...tr.querySelectorAll('td')].map(x => x.textContent.trim());
          if (c.length < 4) continue;
          const key = (isSk ? 'S' : 'G') + '|' + c[1];
          if (seen.has(key)) continue;   // drop the duplicate layout's copy
          seen.add(key);
          if (isSk) game.skaters.push({num: c[0], name: c[1], pos: c[2],
                                       g: +c[3] || 0, a: +c[4] || 0,
                                       pts: +c[5] || 0, pim: +c[6] || 0});
          else      game.goalies.push({num: c[0], name: c[1],
                                       sv: +c[2] || 0, sa: +c[3] || 0, ga: +c[4] || 0});
        }
      }
      if (!game.skaters.length) throw new Error('no lineup rows');
      out[id] = game;
    } catch (e) {
      errors.push(id + ': ' + e.message);
    }
    await sleep(delayMs);
  }
  return {games: out, errors};
}
""".replace("${SEASON}", GS_SEASON)


# A completed row on the team schedule shows the result where an upcoming row
# shows the puck-drop time: "L 4 - 7", "W 5 - 3", possibly with an OT/SO tag.
TEAM_RESULT_RE = re.compile(r"^(?:[A-Z]{1,3}\s+)?\d+\s*-\s*\d+(?:\s+\S+)?$")


def team_completed_dates(text):
    """ISO dates of the completed games on the team schedule page."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    dates = set()
    for i, l in enumerate(lines):
        if (DATE_RE.match(l) and i + 3 < len(lines) and "B2" in lines[i + 2]
                and TEAM_RESULT_RE.match(lines[i + 3])
                and not TIME_ONLY_RE.match(lines[i + 3])):
            dates.add(_iso_date(l))
    return dates


def collect_boxscores(browser, cached=None, delay_ms=900):
    """Scrape each completed team game's lineup.

    Returns (boxscores, complete, team_text) where `boxscores` maps game id ->
    dressed roster, `complete` is True only when every completed game listed on
    the team schedule has a parsed lineup, and `team_text` is the page text (the
    full-season PLB schedule, which main() mines for upcoming games). Aggregated
    totals are only trustworthy when complete — a missing game would silently
    undercount, so the caller must not overwrite good data unless this is True.
    """
    cached = dict(cached or {})
    ctx, page = _new_page(browser)
    try:
        url = (f"{GS_BASE}/teams/{GS_TEAM}/schedule"
               f"?filter[division]={GS_DIVISION}&filter[status]=completed")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        team_text = page.locator("body").inner_text()
        if _looks_like_challenge(team_text):
            print("    Cloudflare challenge on team schedule — skipping boxscores")
            return cached, False, ""
        try:        # the list is filled in by the client; give it time to appear
            page.wait_for_selector('a[href*="/games/"]', timeout=10000)
        except Exception:
            pass
        team_text = page.locator("body").inner_text()

        # Each game link's own text is that row's date ("Sep 30, 2026").
        links = page.evaluate("""() =>
            [...document.querySelectorAll('a[href*="/games/"]')].map(a => {
                const m = a.getAttribute('href').match(/\\/games\\/(\\d+)/);
                return m ? [m[1], (a.textContent || '').trim()] : null;
            }).filter(Boolean)""")
        done_dates = team_completed_dates(team_text)
        game_ids = []
        for gid, label in links:
            if DATE_RE.match(label) and _iso_date(label) in done_dates \
                    and gid not in game_ids:
                game_ids.append(gid)
        if not game_ids or len(game_ids) != len(done_dates):
            # Two completed games on one date would also land here (ids can't be
            # told apart by date) — refuse rather than fetch the wrong set.
            print(f"    team schedule: {len(done_dates)} completed date(s) but "
                  f"{len(game_ids)} game id(s) — skipping boxscores")
            return cached, False, team_text

        todo = [g for g in game_ids if g not in cached]
        print(f"    {len(game_ids)} completed games ({len(todo)} new to fetch)")
        if todo:
            res = page.evaluate(_LINEUPS_JS, [todo, "Parking Lot Beers", TEAMS, delay_ms])
            cached.update(res.get("games") or {})
            for err in (res.get("errors") or []):
                print(f"    lineup fetch failed — {err}")

        complete = all(g in cached for g in game_ids)
        if not complete:
            missing = [g for g in game_ids if g not in cached]
            print(f"⚠  missing lineups for {len(missing)} game(s): {', '.join(missing)}")
        return cached, complete, team_text
    finally:
        ctx.close()


def aggregate_boxscore_skaters(boxscores):
    """Sum per-game lineups into full-season skater rows.

    GP is the number of lineups a player appears in, which is what the frozen
    leaderboard could never tell us. Names are title-cased to match the
    PointStreak history's convention (see parse_players)."""
    agg = {}
    for game in boxscores.values():
        for s in game.get("skaters", []):
            key = s["name"].lower()
            row = agg.setdefault(key, {
                "number": "", "name": s["name"].title(),
                "gp": 0, "g": 0, "a": 0, "pts": 0, "pim": 0,
                "pp": 0, "sh": 0, "gwg": 0,
            })
            row["gp"]  += 1
            row["g"]   += s.get("g", 0)
            row["a"]   += s.get("a", 0)
            row["pts"] += s.get("pts", 0)
            row["pim"] += s.get("pim", 0)
            if s.get("num"):
                row["number"] = s["num"]
    return sorted(agg.values(), key=lambda p: (-p["pts"], p["name"]))


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"=== GameSheet updater — {date.today()} ===")

    season_path = os.path.join(DATA_DIR, SEASON_FILE)
    if not os.path.exists(season_path):
        print(f"ERROR: {season_path} not found. Run scrape.py first.")
        sys.exit(1)

    with open(season_path) as f:
        season = json.load(f)

    # Backstop for a half-finished season rollover: if GS_SEASON was pointed at a
    # new season but SEASON_FILE still names the old one, this run would scrape
    # the new season over an archived one. Refuse rather than destroy history.
    file_season = str(season.get("gs_season_id") or "")
    if file_season and file_season != GS_SEASON:
        raise SystemExit(
            f"ABORT: {SEASON_FILE} holds season {file_season} but GS_SEASON is "
            f"{GS_SEASON}. Point SEASON_FILE at the new season's file (see the "
            f"rollover constants at the top of this file) before running."
        )

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
        scores_text = load_page(browser, f"{GS_BASE}/games?filter[division]={GS_DIVISION}&filter[status]=completed")
        all_scored  = parse_scores(scores_text)
        plb_scored  = [g for g in all_scored if g.get("is_our_game") and g.get("result")]
        print(f"    {len(all_scored)} division completed games, {len(plb_scored)} PLB completed games")

        print("  Fetching schedule (upcoming games)...")
        sched_text = load_page(browser, f"{GS_BASE}/games?filter[division]={GS_DIVISION}&filter[status]=scheduled")
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

        print("  Fetching per-game lineups (authoritative skater stats)...")
        boxscores, box_complete, team_text = season.get("boxscores") or {}, False, ""
        try:
            boxscores, box_complete, team_text = collect_boxscores(browser, season.get("boxscores"))
        except Exception as e:
            print(f"    boxscore collection failed: {e}")
        # The division list only shows the next few weeks; the team page lists
        # PLB's whole season, so take the upcoming games it adds.
        seen_up = {(g["date"], g["gm_num"]) for g in plb_games}
        team_up = [g for g in sanitize_games(parse_schedule(team_text))
                   if g.get("is_our_game") and (g["date"], g["gm_num"]) not in seen_up]
        if team_up:
            print(f"    +{len(team_up)} upcoming PLB games from the team schedule")
            plb_games += team_up
        box_skaters = aggregate_boxscore_skaters(boxscores) if box_complete else []
        print(f"    {len(boxscores)} games cached, {len(box_skaters)} skaters aggregated")

        browser.close()

    # Merge into a complete game list:
    #   1. Start from cached games (never lose history)
    #   2. Overlay completed games from the completed-games view (authoritative)
    #   3. Add any new upcoming games from the scheduled view / team schedule
    # Key by date + sorted team names so home/away orientation and differing
    # game-number schemes between the two views still collide.
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
    if not (all_scored or all_games or standings or plb_skaters or plb_goalies
            or box_skaters):
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
    if box_skaters:
        # Preferred path: every completed game's lineup was read, so these totals
        # are complete for the whole roster — including players the leaderboard
        # never renders, whose GP used to freeze. Cached-only players are still
        # kept (never shrink the roster), though a player absent from all lineups
        # is a strong hint of a name mismatch worth a look.
        for p in box_skaters:
            merged_skaters[p["name"].lower()] = p
        stale = [n for n in {p["name"].lower() for p in prev_skaters}
                 if n not in {p["name"].lower() for p in box_skaters}]
        if stale:
            print(f"⚠  {len(stale)} cached skater(s) appear in no lineup, keeping "
                  f"cached stats: {', '.join(sorted(stale))}")
        print(f"✅ skaters aggregated from {len(boxscores)} game lineups")
    else:
        # Fallback: the leaderboard merge. Only the top ~20 division-wide rows
        # render, so this refreshes whoever it can and leaves everyone else on
        # their cached (possibly stale) numbers rather than clobbering them.
        for p in plb_skaters:
            merged_skaters[p["name"].lower()] = p
        if len(plb_skaters) < len(merged_skaters):
            print(f"⚠  no complete lineup set; players scrape returned "
                  f"{len(plb_skaters)} of {len(merged_skaters)} known skaters — "
                  f"unscraped players keep their cached stats.")
    all_skaters = sorted(merged_skaters.values(),
                         key=lambda p: (-p["pts"], p["name"]))

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
    if boxscores:
        # Cached so later runs only fetch newly-played games. process.py strips
        # this key, so it never reaches app_data.json.
        season["boxscores"]      = boxscores
    season["goalies"]            = all_goalies
    season["last_updated"]       = str(date.today())

    # Derive our standing from standings
    for i, t in enumerate(standings):
        if t["team"].lower() == OUR_TEAM:
            season["our_rank"]    = t["rank"]
            season["total_teams"] = len(standings)
            # GameSheet's own PTS is authoritative for the season's points system
            # (it changes between seasons); only trust it when it covers the same
            # games our record was derived from.
            if str(t.get("gp")) == str(record["gp"]) and str(t.get("pts", "")).isdigit():
                record["pts"] = int(t["pts"])
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
