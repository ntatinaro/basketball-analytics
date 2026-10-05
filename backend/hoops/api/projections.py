"""Player projections for a game (NBA, from V1.1): the locked set (the one graded after
the game) or, before the lock, the latest one, next to what each player actually did."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from hoops.api.deps import cached, get_conn, parse_league, rehearsal
from hoops.leagues import League
from hoops.models.projections import HEADLINE, PROJECTED

router = APIRouter()


def projection_set(conn: psycopg.Connection, game_id: int) -> dict | None:
    """The locked set if there is one, otherwise the latest."""
    return conn.execute(
        """
        SELECT projection_set_id, is_locked, is_rehearsal, created_at,
               EXISTS (SELECT 1 FROM projection_sets n WHERE n.game_id = s.game_id
                       AND n.created_at > s.created_at AND s.is_locked) AS updated_after_lock
        FROM projection_sets s
        WHERE s.game_id = %s AND (%s OR NOT s.is_rehearsal)
        ORDER BY s.is_locked DESC, s.created_at DESC, s.projection_set_id DESC LIMIT 1
        """, (game_id, rehearsal())).fetchone()


@router.get("/api/{league}/games/{game_id}/projections")
def game_projections(league: str, game_id: int, conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    if lg is not League.NBA:
        raise HTTPException(404, "Player projections are NBA only.")

    def build():
        game = conn.execute("SELECT home_team_id, away_team_id, status FROM games"
                            " WHERE league = %s AND game_id = %s", (str(lg), game_id)).fetchone()
        if game is None:
            raise HTTPException(404, "Game not found.")
        chosen = projection_set(conn, game_id)
        out = {"game_id": game_id, "available": chosen is not None, "stats": PROJECTED,
               "headline": HEADLINE, "teams": {"home": [], "away": []}}
        if chosen is None:
            return out
        out.update(locked=chosen["is_locked"], rehearsal=chosen["is_rehearsal"],
                   created_at=chosen["created_at"],
                   updated_after_lock=chosen["updated_after_lock"])
        rows = conn.execute(
            """
            SELECT pp.player_id, pp.team_id, pp.stats, p.display_name AS name, p.position,
                   p.headshot_url AS headshot, s.did_not_play, s.minutes, s.pts, s.fgm, s.fga,
                   s.fg3m, s.fg3a, s.ftm, s.fta, s.oreb, s.dreb, s.reb, s.ast, s.stl, s.blk,
                   s.tov, s.pf
            FROM player_projections pp JOIN players p USING (player_id)
            LEFT JOIN player_game_stats s ON s.game_id = %s AND s.player_id = pp.player_id
            WHERE pp.projection_set_id = %s
            """, (game_id, chosen["projection_set_id"])).fetchall()
        played = game["status"] in ("live", "final")
        for r in sorted(rows, key=lambda r: -r["stats"]["minutes"][0]):
            side = "home" if r["team_id"] == game["home_team_id"] else "away"
            actual = None
            if played and r["did_not_play"] is not None:
                actual = None if r["did_not_play"] else {s: r[s] for s in PROJECTED}
            out["teams"][side].append({
                "player_id": r["player_id"], "name": r["name"], "position": r["position"],
                "headshot": r["headshot"],
                "projection": {s: {"expected": v[0], "low": v[1], "high": v[2]}
                               for s, v in r["stats"].items()},
                "actual": actual,
                "did_not_play": bool(r["did_not_play"]) if played else None,
            })
        return out

    return cached(conn, lg, ("projections", game_id), build)
