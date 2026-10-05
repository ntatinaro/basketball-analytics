"""Tonight and the game page (features 2 and 3)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from hoops.api.deps import cached, get_conn, parse_league, season_types, teams_for_season
from hoops.api.predictions import predictions_for

router = APIRouter()


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(400, f"Unknown time zone '{tz}'.") from exc


def _records(conn, season: int) -> dict[int, dict]:
    return {r["team_id"]: {"wins": r["wins"], "losses": r["losses"]} for r in conn.execute(
        "SELECT team_id, wins, losses FROM team_season_stats WHERE season = %s"
        " AND season_type = 'regular'", (season,))}


def _game_rows(conn, where: str, params: tuple) -> list[dict]:
    return conn.execute(
        f"""
        SELECT g.game_id, g.league, g.season, g.season_type, g.start_time, g.status,
               g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.neutral_site,
               g.periods_played
        FROM games g WHERE {where} ORDER BY g.start_time, g.game_id
        """,
        params,
    ).fetchall()


def _card(game: dict, teams: dict, records: dict, prediction: dict | None) -> dict:
    def side(team_id: int) -> dict:
        return teams.get(team_id, {"id": team_id}) | {"record": records.get(team_id)}

    outs = {"home": 0, "away": 0}
    if prediction and prediction.get("inputs"):
        for key in outs:
            outs[key] = sum(1 for p in prediction["inputs"].get("absences", {}).get(key, [])
                            if p.get("status") == "Out")
    pred = None
    if prediction:
        pred = {k: v for k, v in prediction.items() if k != "inputs"}
    return {
        "id": game["game_id"], "start_time": game["start_time"], "status": game["status"],
        "season": game["season"], "season_type": game["season_type"],
        "neutral_site": game["neutral_site"],
        "home": side(game["home_team_id"]), "away": side(game["away_team_id"]),
        "home_score": game["home_score"], "away_score": game["away_score"],
        "prediction": pred, "players_out": outs,
    }


@router.get("/api/{league}/games")
def games_on_date(league: str, date_: date = Query(alias="date"), tz: str = "America/New_York",
                  conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    zone = _zone(tz)
    start = datetime.combine(date_, time(0, 0), zone)

    def build():
        rows = _game_rows(conn, "g.league = %s AND g.start_time >= %s AND g.start_time < %s"
                          " AND g.season_type = ANY(%s) AND g.status <> 'canceled'",
                          (str(lg), start, start + timedelta(days=1), season_types()))
        if not rows:
            return {"date": date_, "games": []}
        season = rows[0]["season"]
        teams = teams_for_season(conn, lg, season)
        records = _records(conn, season)
        preds = predictions_for(conn, [r["game_id"] for r in rows])
        return {"date": date_, "games": [_card(r, teams, records, preds.get(r["game_id"]))
                                         for r in rows]}

    return cached(conn, lg, ("games", date_, tz), build)


@router.get("/api/{league}/games/{game_id}")
def game_detail(league: str, game_id: int, conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)

    def build():
        rows = _game_rows(conn, "g.league = %s AND g.game_id = %s", (str(lg), game_id))
        if not rows:
            raise HTTPException(404, "Game not found.")
        game = rows[0]
        teams = teams_for_season(conn, lg, game["season"])
        prediction = predictions_for(conn, [game_id]).get(game_id)
        card = _card(game, teams, _records(conn, game["season"]), prediction)
        inputs = (prediction or {}).get("inputs") or {}
        card["preview"] = {
            "explainer": inputs.get("explainer", []),
            "absences": inputs.get("absences", {"home": [], "away": []}),
            "base_margin": inputs.get("base_margin"),
            "absence_shift": inputs.get("absence_shift"),
            "ratings": {"home": inputs.get("home"), "away": inputs.get("away")},
            "rest_days": inputs.get("rest_days"),
        }
        card["box_score"] = _box_score(conn, game) if game["status"] in ("live", "final") else None
        card["default_tab"] = {"scheduled": "preview", "live": "live"}.get(game["status"], "box")
        return card

    return cached(conn, lg, ("game", game_id), build)


def _box_score(conn, game: dict) -> dict:
    teams = conn.execute(
        """
        SELECT team_id, pts, fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf
        FROM team_game_stats WHERE game_id = %s
        """, (game["game_id"],)).fetchall()
    players = conn.execute(
        """
        SELECT s.team_id, s.player_id, p.display_name AS name, p.position, s.starter,
               s.did_not_play, s.dnp_reason, s.minutes, s.pts, s.fgm, s.fga, s.fg3m, s.fg3a,
               s.ftm, s.fta, s.oreb, s.dreb, s.reb, s.ast, s.stl, s.blk, s.tov, s.pf,
               s.plus_minus
        FROM player_game_stats s JOIN players p USING (player_id)
        WHERE s.game_id = %s
        ORDER BY s.team_id, s.starter DESC, s.minutes DESC NULLS LAST
        """, (game["game_id"],)).fetchall()
    out = {}
    for key, team_id in (("home", game["home_team_id"]), ("away", game["away_team_id"])):
        out[key] = {
            "team": next((t for t in teams if t["team_id"] == team_id), None),
            "players": [p for p in players if p["team_id"] == team_id],
        }
    return out
