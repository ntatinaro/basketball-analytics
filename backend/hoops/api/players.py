"""Players table and player page (feature 7)."""

from __future__ import annotations

import math

import numpy as np
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from hoops.api.deps import (
    cached,
    get_conn,
    parse_league,
    rehearsal,
    resolve_season,
    teams_for_season,
)
from hoops.leagues import League
from hoops.models.projections import HEADLINE

router = APIRouter()

# Leaderboard minimums. NBA: official rules (nba.com/stats/help/statminimums), prorated to
# games played so far. NCAA: the NCAA's published per-game minimums (to be re-confirmed
# against the current NCAA statistics policy before V4).
MINIMUMS = {
    League.NBA: {"games_share": 0.70, "season_games": 82, "fgm": 300, "fg3m": 82, "ftm": 125},
    League.NCAAM: {"games_share": 0.75, "fgm_per_game": 5.0, "fg3m_per_game": 2.5,
                   "ftm_per_game": 2.5},
}
STAT_COLUMNS = ("minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb",
                "reb", "ast", "stl", "blk", "tov", "pf")


def _rates(row: dict) -> dict:
    g = row["games"] or 0

    def per_game(key: str) -> float | None:
        return round(float(row[key]) / g, 1) if g else None

    def pct(made: str, att: str) -> float | None:
        return round(100 * float(row[made]) / float(row[att]), 1) if row[att] else None

    return {
        "games": g, "starts": row["starts"], "mpg": per_game("minutes"), "ppg": per_game("pts"),
        "rpg": per_game("reb"), "orpg": per_game("oreb"), "drpg": per_game("dreb"),
        "apg": per_game("ast"), "spg": per_game("stl"), "bpg": per_game("blk"),
        "topg": per_game("tov"), "fpg": per_game("pf"), "fg3m_pg": per_game("fg3m"),
        "fg_pct": pct("fgm", "fga"), "fg3_pct": pct("fg3m", "fg3a"), "ft_pct": pct("ftm", "fta"),
        "totals": {k: float(row[k]) for k in STAT_COLUMNS},
    }


def _qualified(league: League, rates: dict, team_games: int) -> dict:
    rules = MINIMUMS[league]
    totals, g = rates["totals"], rates["games"]
    games_ok = g >= math.ceil(rules["games_share"] * team_games) if team_games else False
    if league is League.NBA:
        frac = team_games / rules["season_games"] if team_games else 0
        return {"games": games_ok, "fg": totals["fgm"] >= rules["fgm"] * frac,
                "fg3": totals["fg3m"] >= rules["fg3m"] * frac,
                "ft": totals["ftm"] >= rules["ftm"] * frac}
    return {"games": games_ok,
            "fg": games_ok and totals["fgm"] >= rules["fgm_per_game"] * team_games,
            "fg3": games_ok and totals["fg3m"] >= rules["fg3m_per_game"] * team_games,
            "ft": games_ok and totals["ftm"] >= rules["ftm_per_game"] * team_games}


def player_rows(conn, league: League, season: int, *, team_id: int | None = None,
                conference: str | None = None, season_type: str = "regular") -> list[dict]:
    """Players for a season. Traded players get one row per team plus a combined row
    (`is_total`), the way Basketball-Reference shows them."""
    teams = teams_for_season(conn, league, season)
    team_games = {r["team_id"]: r["games"] for r in conn.execute(
        "SELECT team_id, games FROM team_season_stats WHERE season = %s AND season_type = %s"
        " AND league = %s", (season, season_type, str(league)))}
    rows = conn.execute(
        """
        SELECT s.*, p.display_name AS name, p.position, p.headshot_url
        FROM player_season_stats s JOIN players p USING (player_id)
        WHERE s.league = %s AND s.season = %s AND s.season_type = %s
        ORDER BY s.player_id, s.last_game
        """,
        (str(league), season, season_type),
    ).fetchall()
    by_player: dict[int, list[dict]] = {}
    for r in rows:
        by_player.setdefault(r["player_id"], []).append(r)

    out = []
    for player_id, stints in by_player.items():
        current_team = stints[-1]["team_id"]
        entries = []
        for s in stints:
            entries.append((s, [s["team_id"]], False))
        if len(stints) > 1:
            combined = {k: sum(float(s[k]) for s in stints) for k in STAT_COLUMNS}
            combined |= {"games": sum(s["games"] for s in stints),
                         "starts": sum(s["starts"] for s in stints)}
            entries.append((combined, [s["team_id"] for s in stints], True))
        for stats, team_ids, is_total in entries:
            if team_id is not None and (is_total or team_ids[0] != team_id):
                continue
            if conference and teams.get(current_team, {}).get("conference_abbr") != conference \
                    and teams.get(current_team, {}).get("conference") != conference:
                continue
            rates = _rates(stats)
            games_ref = max(team_games.get(t, 0) for t in team_ids)
            out.append({
                "player_id": player_id, "name": stints[0]["name"],
                "position": stints[0]["position"], "headshot": stints[0]["headshot_url"],
                "team": teams.get(current_team if is_total else team_ids[0], {}).get(
                    "abbreviation"),
                "team_id": current_team if is_total else team_ids[0],
                "teams": [teams.get(t, {}).get("abbreviation") for t in team_ids],
                "is_total": is_total, "traded": len(stints) > 1,
                "impact": None,        # player impact ratings arrive in V3
                **{k: v for k, v in rates.items() if k != "totals"},
                "qualified": _qualified(league, rates, games_ref),
            })
    return out


@router.get("/api/{league}/players")
def players(league: str, season: int | None = None,
            scope: str = Query("league", pattern="^(league|team|conference)$"),
            team: int | None = None, conf: str | None = None,
            conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    s = resolve_season(conn, lg, season)
    if scope == "team" and team is None:
        raise HTTPException(400, "Choose a team for the team scope.")
    if scope == "conference" and not conf:
        raise HTTPException(400, "Choose a conference for the conference scope.")

    def build():
        rows = player_rows(conn, lg, s, team_id=team if scope == "team" else None,
                           conference=conf if scope == "conference" else None)
        if scope != "team":
            # League and conference tables show each traded player once (their total).
            rows = [r for r in rows if r["is_total"] or not r["traded"]]
        return {"season": s, "scope": scope, "players": rows}

    return cached(conn, lg, ("players", s, scope, team, conf), build)


@router.get("/api/{league}/players/{player_id}")
def player_detail(league: str, player_id: int, season: int | None = None,
                  conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)

    def build():
        info = conn.execute(
            "SELECT player_id, display_name, position, height_inches, weight_pounds,"
            " birth_date, class_year, headshot_url FROM players WHERE player_id = %s"
            " AND league = %s", (player_id, str(lg))).fetchone()
        if info is None:
            raise HTTPException(404, "Player not found.")
        seasons = [r["season"] for r in conn.execute(
            "SELECT DISTINCT season FROM player_season_stats WHERE player_id = %s"
            " ORDER BY season DESC", (player_id,))]
        s = season or (seasons[0] if seasons else resolve_season(conn, lg, None))
        rows = [r for r in player_rows(conn, lg, s) if r["player_id"] == player_id]
        summary = next((r for r in rows if r["is_total"]), rows[0] if rows else None)
        log = _game_log(conn, player_id, s)
        return {
            "season": s, "seasons": seasons,
            "player": {"id": info["player_id"], "name": info["display_name"],
                       "position": info["position"], "height_inches": info["height_inches"],
                       "weight_pounds": info["weight_pounds"], "birth_date": info["birth_date"],
                       "class_year": info["class_year"], "headshot": info["headshot_url"],
                       "team": summary["team"] if summary else None,
                       "team_id": summary["team_id"] if summary else None},
            "season_stats": rows,
            "recent_form": log[-10:],
            "projection": _projection(log),
            "similar": _similar(conn, lg, s, player_id),
            "game_log": log,
        }

    return cached(conn, lg, ("player", player_id, season), build)


def _game_log(conn, player_id: int, season: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT g.game_id, g.start_time, g.season_type, s.team_id, g.home_team_id,
               g.away_team_id, g.home_score, g.away_score, s.starter, s.did_not_play,
               s.dnp_reason, s.minutes, s.pts, s.fgm, s.fga, s.fg3m, s.fg3a, s.ftm, s.fta,
               s.oreb, s.dreb, s.reb, s.ast, s.stl, s.blk, s.tov, s.pf, s.plus_minus,
               ht.abbreviation AS home_abbr, at.abbreviation AS away_abbr,
               pr.stats AS projected
        FROM player_game_stats s JOIN games g USING (game_id)
        JOIN teams ht ON ht.team_id = g.home_team_id JOIN teams at ON at.team_id = g.away_team_id
        LEFT JOIN LATERAL (       -- the locked projection (the graded one), else the latest
            SELECT pp.stats FROM projection_sets ps JOIN player_projections pp
                USING (projection_set_id)
            WHERE ps.game_id = g.game_id AND pp.player_id = s.player_id
              AND (%s OR NOT ps.is_rehearsal)
            ORDER BY ps.is_locked DESC, ps.created_at DESC LIMIT 1) pr ON true
        WHERE s.player_id = %s AND g.season = %s AND g.status = 'final'
        ORDER BY g.start_time
        """,
        (rehearsal(), player_id, season),
    ).fetchall()
    out = []
    for r in rows:
        home = r["team_id"] == r["home_team_id"]
        us, them = ((r["home_score"], r["away_score"]) if home
                    else (r["away_score"], r["home_score"]))
        out.append({
            "game_id": r["game_id"], "date": r["start_time"], "season_type": r["season_type"],
            "opponent": r["away_abbr"] if home else r["home_abbr"], "home": home,
            "result": f"{'W' if us > them else 'L'} {us}-{them}" if us is not None else None,
            "did_not_play": r["did_not_play"], "dnp_reason": r["dnp_reason"],
            "starter": r["starter"],
            **{k: r[k] for k in ("minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
                                 "oreb", "dreb", "reb", "ast", "stl", "blk", "tov", "pf",
                                 "plus_minus")},
            # Projected expected values for the headline stats (V1.1), next to the actuals.
            "projection": ({s: r["projected"][s][0] for s in HEADLINE if s in r["projected"]}
                           if r["projected"] else None),
        })
    return out


def _projection(log: list[dict]) -> dict | None:
    """Rest-of-season per-game projection: the season average blended with recent form,
    pulled toward it while games are few, with an 80% range."""
    played = [g for g in log if not g["did_not_play"] and (g["minutes"] or 0) > 0
              and g["season_type"] == "regular"]
    if len(played) < 3:
        return None
    out = {}
    for key in ("minutes", "pts", "reb", "ast", "fg3m"):
        values = np.array([float(g[key] or 0) for g in played])
        recent = values[-10:]
        mean = 0.7 * values.mean() + 0.3 * recent.mean()
        spread = values.std(ddof=1) if len(values) > 1 else 0.0
        half = 1.2816 * spread * math.sqrt(1 / len(values) + 1 / 20)
        out[key] = {"expected": round(mean, 1), "low": round(max(mean - half, 0), 1),
                    "high": round(mean + half, 1)}
    return out


def _similar(conn, league: League, season: int, player_id: int, k: int = 5) -> list[dict]:
    """Players with the most similar playing style this season: per-36 production, shot
    mix, and position, compared after standardizing each feature."""
    rows = player_rows(conn, league, season)
    rows = [r for r in rows if r["is_total"] or not r["traded"]]
    max_minutes = max((r["games"] * (r["mpg"] or 0) for r in rows), default=0)
    floor = min(500.0, 0.25 * max_minutes)
    pool = [r for r in rows
            if r["games"] * (r["mpg"] or 0) >= floor or r["player_id"] == player_id]
    target = next((r for r in pool if r["player_id"] == player_id), None)
    if target is None or len(pool) < k + 1:
        return []

    def features(r: dict) -> list[float]:
        per36 = 36 / max(r["mpg"] or 1, 1)
        pos = (r["position"] or "")[:1]
        return [r["ppg"] * per36, r["rpg"] * per36, r["apg"] * per36, r["spg"] * per36,
                r["bpg"] * per36, r["topg"] * per36, (r["fg3m_pg"] or 0) * per36,
                (r["fg3_pct"] or 0) / 100, (r["ft_pct"] or 0) / 100,
                float(pos == "G"), float(pos == "F"), float(pos == "C")]

    x = np.array([features(r) for r in pool], dtype=float)
    x = (x - x.mean(axis=0)) / np.where(x.std(axis=0) > 0, x.std(axis=0), 1)
    i = next(i for i, r in enumerate(pool) if r["player_id"] == player_id)
    dist = np.sqrt(((x - x[i]) ** 2).sum(axis=1))
    order = [j for j in np.argsort(dist) if j != i][:k]
    return [{"player_id": pool[j]["player_id"], "name": pool[j]["name"],
             "team": pool[j]["team"], "position": pool[j]["position"],
             "ppg": pool[j]["ppg"], "rpg": pool[j]["rpg"], "apg": pool[j]["apg"],
             "distance": round(float(dist[j]), 2)} for j in order]
