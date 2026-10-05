"""Rebuilds stored rating history for whole seasons (trend charts, past seasons).

Live refits store ratings as the season goes; this command fills in seasons that were
backfilled, replaying each one day by day with the champion's settings.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg

from hoops.db.refresh import mark_data_changed
from hoops.evaluation.backtest import load_season
from hoops.leagues import League
from hoops.models.live import load_champion
from hoops.models.player_values import roster_strength, season_player_values
from hoops.models.ratings import (
    TeamRatings,
    fit_sub_ratings,
    fit_team_ratings,
    season_priors,
)

log = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")


def rebuild(conn: psycopg.Connection, league: League, seasons: list[int]) -> int:
    champion = load_champion(conn, league)
    settings = champion.members[0]
    final: TeamRatings | None = None
    values = pd.DataFrame(columns=["player_id", "team_id", "minutes", "games", "mpg", "value"])
    written = 0
    for season in sorted(seasons):
        sd = load_season(conn, league, season)
        if sd.games.empty:
            continue
        roster_net = roster_strength(sd.opening_roster(), values) if len(values) else {}
        priors = season_priors(final, roster_net, settings)
        rows = []
        # A team's history ends with its last game: refits after that (while others play
        # on) only age its games and pull it back toward the preseason starting rating.
        last_day = pd.concat([
            sd.games.groupby("home_team_id")["game_date"].max(),
            sd.games.groupby("away_team_id")["game_date"].max(),
        ]).groupby(level=0).max().to_dict()
        for day in sorted(sd.games["game_date"].unique()):
            as_of = datetime.combine(day + timedelta(days=1), time(4, 0), EASTERN)
            past = sd.games[sd.games["start_time"] < as_of]
            r = fit_team_ratings(past, sd.team_ids, priors, settings, as_of)
            for row in r.frame().itertuples(index=False):
                if day > last_day.get(int(row.team_id), day):
                    continue
                rows.append((int(row.team_id), season, as_of, champion.model_version_id,
                             int(row.games_played), float(row.overall), float(row.offense),
                             float(row.defense), float(row.overall_se), float(row.offense_se),
                             float(row.defense_se), float(row.pace)))
            final = r
        sub = fit_sub_ratings(sd.games, sd.team_ids, settings, final.as_of).table(final)
        with conn.transaction():
            conn.execute("DELETE FROM team_ratings WHERE season = %s AND model_version_id = %s"
                         " AND team_id = ANY(%s)",
                         (season, champion.model_version_id, sd.team_ids))
            conn.execute("DELETE FROM team_sub_ratings WHERE model_version_id = %s"
                         " AND as_of = %s", (champion.model_version_id, final.as_of))
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO team_ratings (team_id, season, as_of, model_version_id,
                        games_played, overall, offense, defense, overall_se, offense_se,
                        defense_se, pace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
                cur.executemany(
                    "INSERT INTO team_sub_ratings (team_id, as_of, model_version_id, metric,"
                    " value, percentile) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(int(r.team_id), final.as_of, champion.model_version_id, r.metric,
                      float(r.value), float(r.percentile)) for r in sub.itertuples(index=False)],
                )
            values = season_player_values(sd.player_games,
                                          dict(zip(final.team_ids, final.net, strict=True)))
            _store_values(conn, season, champion.model_version_id, values)
        written += len(rows)
        log.info("%s %d: %d rating rows", league, season, len(rows))
    mark_data_changed(conn)
    return written


def _store_values(conn, season: int, model_version_id: int, values: pd.DataFrame) -> None:
    conn.execute("DELETE FROM player_values WHERE season = %s AND model_version_id = %s",
                 (season, model_version_id))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO player_values (player_id, season, model_version_id, minutes, value)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(int(v.player_id), season, model_version_id, float(v.minutes), float(v.value))
             for v in values.itertuples(index=False)],
        )
