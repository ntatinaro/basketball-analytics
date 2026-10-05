"""Loads modeling data from Postgres into pandas frames.

Only games that count (regular season, play-in, playoffs) and pass the team-level quality
check are used for team models. Player models additionally require the player check.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg

from hoops.leagues import COUNTED_SEASON_TYPES, RULES, League

EASTERN = ZoneInfo("America/New_York")
BOX = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk",
       "tov", "pf", "possessions", "pts_excl_garbage", "possessions_excl_garbage"]


def _frame(conn: psycopg.Connection, sql: str, params: tuple) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def games_frame(conn: psycopg.Connection, league: League, seasons: list[int],
                season_types: tuple[str, ...] | None = None) -> pd.DataFrame:
    """One row per finished game with both teams' box scores (`h_*` home, `a_*` away)."""
    types = sorted(season_types or COUNTED_SEASON_TYPES)
    home_cols = ", ".join(f"h.{c} AS h_{c}" for c in BOX)
    away_cols = ", ".join(f"a.{c} AS a_{c}" for c in BOX)
    frame = _frame(conn, f"""
        SELECT g.game_id, g.season, g.season_type, g.start_time, g.neutral_site,
               g.home_team_id, g.away_team_id, g.periods_played, g.pbp_quality_ok,
               {home_cols}, {away_cols}
        FROM games g
        JOIN team_game_stats h ON h.game_id = g.game_id AND h.team_id = g.home_team_id
        JOIN team_game_stats a ON a.game_id = g.game_id AND a.team_id = g.away_team_id
        WHERE g.league = %s AND g.season = ANY(%s) AND g.status = 'final'
          AND g.team_quality_ok AND g.season_type = ANY(%s)
        ORDER BY g.start_time, g.game_id
    """, (str(league), seasons, types))
    return add_game_fields(frame, league)


def add_game_fields(frame: pd.DataFrame, league: League) -> pd.DataFrame:
    if frame.empty:
        return frame
    rules = RULES[league]
    frame = frame.copy()
    frame["start_time"] = pd.to_datetime(frame["start_time"], utc=True)
    frame["game_date"] = frame["start_time"].dt.tz_convert(EASTERN).dt.date
    for c in BOX:
        for side in ("h", "a"):
            frame[f"{side}_{c}"] = pd.to_numeric(frame[f"{side}_{c}"], errors="coerce")
    frame["poss"] = (frame["h_possessions"] + frame["a_possessions"]) / 2
    periods = pd.to_numeric(frame["periods_played"], errors="coerce").fillna(
        rules.periods).clip(lower=rules.periods)
    minutes = rules.periods * rules.period_minutes + (periods - rules.periods) * (
        rules.overtime_minutes)
    frame["pace"] = frame["poss"] * rules.periods * rules.period_minutes / minutes
    frame["margin"] = frame["h_pts"] - frame["a_pts"]
    frame["total"] = frame["h_pts"] + frame["a_pts"]
    frame["home_won"] = (frame["margin"] > 0).astype(int)
    frame["home_sign"] = np.where(frame["neutral_site"].astype(bool), 0.0, 1.0)
    for col in ("poss", "pace", "margin", "total"):
        frame[col] = frame[col].astype(float)
    return frame


def player_games_frame(conn: psycopg.Connection, league: League,
                       seasons: list[int]) -> pd.DataFrame:
    """One row per player per game played, with the team's possessions for rate stats."""
    frame = _frame(conn, """
        SELECT g.game_id, g.season, g.start_time, p.player_id, p.team_id, p.minutes, p.pts,
               p.fgm, p.fga, p.fg3m, p.fg3a, p.ftm, p.fta, p.oreb, p.dreb, p.ast, p.stl,
               p.blk, p.tov, p.pf, t.possessions AS team_possessions
        FROM player_game_stats p
        JOIN games g USING (game_id)
        JOIN team_game_stats t ON t.game_id = p.game_id AND t.team_id = p.team_id
        WHERE g.league = %s AND g.season = ANY(%s) AND g.status = 'final'
          AND g.team_quality_ok AND g.player_quality_ok AND g.season_type = ANY(%s)
          AND NOT p.did_not_play AND p.minutes > 0
    """, (str(league), seasons, sorted(COUNTED_SEASON_TYPES)))
    if not frame.empty:
        frame["start_time"] = pd.to_datetime(frame["start_time"], utc=True)
        numeric = frame.columns.drop(["game_id", "season", "start_time", "player_id", "team_id"])
        frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce").fillna(0)
    return frame


def lines_frame(conn: psycopg.Connection, league: League, seasons: list[int]) -> pd.DataFrame:
    """The best available pregame line per game: captured by us before tip-off, then ESPN's
    labeled close, then lines of unknown timing."""
    return _frame(conn, """
        SELECT DISTINCT ON (b.game_id) b.game_id, b.line_kind, b.provider, b.spread_home,
               b.total, b.home_moneyline, b.away_moneyline
        FROM betting_lines b JOIN games g USING (game_id)
        WHERE g.league = %s AND g.season = ANY(%s)
        ORDER BY b.game_id,
                 CASE b.line_kind WHEN 'captured_pregame' THEN 0 WHEN 'espn_close' THEN 1
                                  ELSE 2 END,
                 (b.home_moneyline IS NULL), b.provider
    """, (str(league), seasons))
