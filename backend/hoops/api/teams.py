"""Teams table and team page (features 1, 6, 7)."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from hoops.api.deps import (
    cached,
    get_conn,
    latest_ratings,
    parse_league,
    resolve_season,
    season_types,
    teams_for_season,
)
from hoops.api.players import player_rows
from hoops.api.predictions import predictions_for
from hoops.models.ratings import SUB_RATINGS

router = APIRouter()


def _per_game(row: dict, games: int) -> dict:
    def avg(key: str) -> float | None:
        return round(float(row[key]) / games, 1) if row.get(key) is not None and games else None

    def pct(made: str, att: str) -> float | None:
        return round(100 * float(row[made]) / float(row[att]), 1) if row.get(att) else None

    return {
        "pts": avg("pts"), "reb": _sum_avg(row, ("oreb", "dreb"), games),
        "oreb": avg("oreb"), "dreb": avg("dreb"), "ast": avg("ast"), "stl": avg("stl"),
        "blk": avg("blk"), "tov": avg("tov"), "fgm": avg("fgm"), "fga": avg("fga"),
        "fg3m": avg("fg3m"), "fg3a": avg("fg3a"), "ftm": avg("ftm"), "fta": avg("fta"),
        "fg_pct": pct("fgm", "fga"), "fg3_pct": pct("fg3m", "fg3a"), "ft_pct": pct("ftm", "fta"),
    }


def _sum_avg(row: dict, keys: tuple[str, ...], games: int) -> float | None:
    if not games or any(row.get(k) is None for k in keys):
        return None
    return round(sum(float(row[k]) for k in keys) / games, 1)


def team_table(conn, league, season: int) -> list[dict]:
    teams = teams_for_season(conn, league, season)
    ratings = latest_ratings(conn, league, season)
    stats = {r["team_id"]: r for r in conn.execute(
        "SELECT * FROM team_season_stats WHERE season = %s AND season_type = 'regular'"
        " AND league = %s", (season, str(league)))}
    ranked = sorted(ratings, key=lambda t: -ratings[t]["overall"])
    rows = []
    for team_id, team in teams.items():
        if team_id not in ratings and team_id not in stats:
            continue
        r = ratings.get(team_id, {})
        s = stats.get(team_id, {})
        box_games = s.get("box_games") or 0
        own = _per_game(s, box_games) if s else {}
        rows.append(team | {
            "rank": ranked.index(team_id) + 1 if team_id in ratings else None,
            "rating": r.get("overall"), "rating_se": r.get("overall_se"),
            "offense": r.get("offense"), "defense": r.get("defense"), "pace": r.get("pace"),
            "games_played": r.get("games_played"),
            "low_confidence": (r.get("games_played") or 0) < 15,
            "wins": s.get("wins", 0), "losses": s.get("losses", 0),
            "ppg": own.get("pts"),
            "opp_ppg": round(float(s["opp_pts"]) / box_games, 1) if box_games else None,
        })
    rows.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0))
    return rows


@router.get("/api/{league}/teams")
def teams(league: str, season: int | None = None, conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    s = resolve_season(conn, lg, season)
    return cached(conn, lg, ("teams", s), lambda: {"season": s, "teams": team_table(conn, lg, s)})


@router.get("/api/{league}/teams/{team_id}")
def team_detail(league: str, team_id: int, season: int | None = None,
                conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    s = resolve_season(conn, lg, season)

    def build():
        table = team_table(conn, lg, s)
        row = next((t for t in table if t["id"] == team_id), None)
        if row is None:
            raise HTTPException(404, "Team not found for that season.")
        return {
            "season": s,
            "team": row,
            "switcher": [{"id": t["id"], "name": t["name"], "abbreviation": t["abbreviation"]}
                         for t in sorted(table, key=lambda t: t["name"])],
            "league_size": len(table),
            "overview": {
                "sub_ratings": _sub_ratings(conn, team_id, s),
                "trend": _trend(conn, team_id, s),
                "season_odds": None,        # season simulator arrives in V3
            },
            "team_stats": _team_stats(conn, team_id, s),
            "roster": player_rows(conn, lg, s, team_id=team_id),
            "schedule": _schedule(conn, lg, team_id, s),
        }

    return cached(conn, lg, ("team", team_id, s), build)


def _sub_ratings(conn, team_id: int, season: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (sr.metric) sr.metric, sr.value, sr.percentile
        FROM team_sub_ratings sr
        WHERE sr.team_id = %s AND sr.as_of <= (
            SELECT max(as_of) FROM team_ratings WHERE team_id = %s AND season = %s)
          AND sr.as_of >= (SELECT min(as_of) FROM team_ratings WHERE team_id = %s
                           AND season = %s)
        ORDER BY sr.metric, sr.as_of DESC
        """,
        (team_id, team_id, season, team_id, season),
    ).fetchall()
    by_metric = {r["metric"]: r for r in rows}
    return [{"metric": m, "label": label, "value": by_metric[m]["value"],
             "percentile": by_metric[m]["percentile"]}
            for m, (label, _, _) in SUB_RATINGS.items() if m in by_metric]


def _trend(conn, team_id: int, season: int) -> list[dict]:
    """The team's rating after each game day of the season (latest model version)."""
    return conn.execute(
        """
        SELECT r.as_of, r.games_played, r.overall, r.overall_se
        FROM team_ratings r JOIN model_versions m USING (model_version_id)
        WHERE r.team_id = %s AND r.season = %s AND m.role = 'champion'
        ORDER BY r.as_of
        """,
        (team_id, season),
    ).fetchall()


def _team_stats(conn, team_id: int, season: int) -> dict | None:
    s = conn.execute("SELECT * FROM team_season_stats WHERE team_id = %s AND season = %s"
                     " AND season_type = 'regular'", (team_id, season)).fetchone()
    if not s or not s["box_games"]:
        return None
    opp = {k[4:]: v for k, v in s.items() if k.startswith("opp_")}
    games = s["box_games"]
    return {"games": games, "team": _per_game(s, games), "opponents": _per_game(opp, games),
            "pace": round(float(s["possessions"]) / games, 1) if s["possessions"] else None}


def _schedule(conn, league, team_id: int, season: int) -> list[dict]:
    games = conn.execute(
        """
        SELECT game_id, start_time, status, season_type, home_team_id, away_team_id,
               home_score, away_score
        FROM games WHERE league = %s AND season = %s AND season_type = ANY(%s)
          AND status <> 'canceled' AND (home_team_id = %s OR away_team_id = %s)
        ORDER BY start_time
        """,
        (str(league), season, season_types(), team_id, team_id),
    ).fetchall()
    teams = teams_for_season(conn, league, season)
    preds = predictions_for(conn, [g["game_id"] for g in games])
    out = []
    for g in games:
        home = g["home_team_id"] == team_id
        opponent = teams.get(g["away_team_id"] if home else g["home_team_id"], {})
        p = preds.get(g["game_id"])
        prob = None if not p else (p["home_win_prob"] if home else 1 - p["home_win_prob"])
        margin = None if not p else (p["margin_home"] if home else -p["margin_home"])
        result = None
        if g["status"] == "final" and g["home_score"] is not None:
            us, them = ((g["home_score"], g["away_score"]) if home
                        else (g["away_score"], g["home_score"]))
            result = {"won": us > them, "score": f"{us}-{them}",
                      "model_right": None if prob is None else (prob >= 0.5) == (us > them)}
        out.append({"game_id": g["game_id"], "start_time": g["start_time"],
                    "status": g["status"], "home": home, "opponent": opponent,
                    "win_prob": prob, "predicted_margin": margin,
                    "prediction_source": p["source"] if p else None, "result": result})
    return out
