"""Live model steps for the worker: refit ratings, predict, lock, and grade.

Plugs into the worker through the GameHooks protocol (hoops.jobs.tasks).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg

from hoops.evaluation import metrics
from hoops.leagues import COUNTED_SEASON_TYPES, League
from hoops.models import data
from hoops.models.config import DEFAULT, ModelSettings
from hoops.models.context import GameContext, absence_cost, schedule_context
from hoops.models.explainer import explain, fit_sensitivities
from hoops.models.live_projections import Projector
from hoops.models.player_values import roster_strength, season_player_values
from hoops.models.predictor import Calibration, Prediction, predict
from hoops.models.ratings import (
    Priors,
    SubRatings,
    TeamRatings,
    fit_sub_ratings,
    fit_team_ratings,
    observations,
    season_priors,
)

log = logging.getLogger(__name__)
MODEL_NAME = "game_predictor"
UPCOMING_DAYS = 7
VALUE_BLEND_MINUTES = 800.0     # current-season minutes that outweigh last season's value


@dataclass
class Champion:
    model_version_id: int
    members: list[ModelSettings]
    calibrations: list[Calibration]


def load_champion(conn: psycopg.Connection, league: League) -> Champion:
    row = conn.execute(
        "SELECT model_version_id, settings FROM model_versions WHERE league = %s"
        " AND model_name = %s AND role = 'champion'", (str(league), MODEL_NAME),
    ).fetchone()
    if row is None:
        # No rolling exams yet: use the default settings, registered as the champion.
        (vid,) = conn.execute(
            "INSERT INTO model_versions (league, model_name, version, settings, role)"
            " VALUES (%s, %s, 'default', %s, 'champion')"
            " ON CONFLICT (league, model_name, version) DO UPDATE SET role = 'champion'"
            " RETURNING model_version_id",
            (str(league), MODEL_NAME, json.dumps({"members": [DEFAULT.to_json()]})),
        ).fetchone()
        return Champion(vid, [DEFAULT], [Calibration()])
    vid, settings = row
    members = [ModelSettings(**m) for m in settings.get("members", [DEFAULT.to_json()])]
    cals = [Calibration(**c) for c in settings.get("calibrations", [])] or [Calibration()] * len(
        members)
    return Champion(vid, members, cals)


@dataclass
class LiveState:
    season: int
    team_ids: list[int]
    names: dict[int, str]
    abbreviations: dict[int, str]
    ratings: list[TeamRatings]           # one per champion member
    sub: SubRatings
    sensitivity: dict[str, float]
    values: pd.DataFrame                 # player values for absences
    as_of: datetime


class ModelHooks:
    def __init__(self, league: League, *, rehearsal: bool = False) -> None:
        self.league = league
        self.rehearsal = rehearsal     # preseason games get (rehearsal) predictions too
        self._priors: dict[tuple[int, str], tuple[Priors, pd.DataFrame]] = {}
        self._state: LiveState | None = None
        self.projector = Projector(league)

    # -- state ------------------------------------------------------------------------

    def current_season(self, conn: psycopg.Connection, now: datetime) -> int:
        row = conn.execute(
            "SELECT season FROM games WHERE league = %s AND start_time <= %s + interval '10 days'"
            " ORDER BY start_time DESC LIMIT 1", (str(self.league), now),
        ).fetchone()
        return row[0] if row else now.year + (1 if now.month >= 8 else 0)

    def priors(self, conn, season: int, settings: ModelSettings) -> tuple[Priors, pd.DataFrame]:
        """Starting ratings for `season` and last season's player values (cached until the
        overnight job clears them)."""
        key = (season, settings.version)
        if key in self._priors:
            return self._priors[key]
        prev = data.games_frame(conn, self.league, [season - 1])
        team_ids = self.team_ids(conn)
        if prev.empty:
            result = (Priors(), pd.DataFrame(columns=["player_id", "mpg", "value"]))
        else:
            end = prev["start_time"].max().to_pydatetime() + timedelta(days=1)
            final = fit_team_ratings(prev, team_ids, Priors(), settings, end)
            values = season_player_values(
                data.player_games_frame(conn, self.league, [season - 1]),
                dict(zip(final.team_ids, final.net, strict=True)))
            roster = pd.DataFrame(conn.execute(
                "SELECT team_id, player_id FROM roster_entries WHERE season = %s"
                " AND team_id = ANY(%s)", (season, team_ids)).fetchall(),
                columns=["team_id", "player_id"])
            roster_net = roster_strength(roster, values) if len(roster) else {}
            result = (season_priors(final, roster_net, settings), values)
        self._priors[key] = result
        return result

    def season_types(self) -> list[str]:
        return sorted(COUNTED_SEASON_TYPES | ({"pre"} if self.rehearsal else set()))

    def team_ids(self, conn) -> list[int]:
        return [r[0] for r in conn.execute(
            "SELECT team_id FROM teams WHERE league = %s ORDER BY team_id", (str(self.league),))]

    def refit(self, conn: psycopg.Connection, now: datetime | None = None) -> LiveState:
        """Fits current ratings from every finished game of the season and stores them."""
        now = now or datetime.now(UTC)
        self.projector.invalidate()
        champion = load_champion(conn, self.league)
        season = self.current_season(conn, now)
        games = data.games_frame(conn, self.league, [season])
        games = games[games["start_time"] < now] if len(games) else games
        team_ids = self.team_ids(conn)
        ratings, values = [], None
        for member in champion.members:
            priors, values = self.priors(conn, season, member)
            ratings.append(fit_team_ratings(games, team_ids, priors, member, now))
        sub = fit_sub_ratings(games, team_ids, champion.members[0], now)
        prev_games = data.games_frame(conn, self.league, [season - 1])
        both = pd.concat([prev_games, games]) if len(games) else prev_games
        sensitivity = fit_sensitivities(observations(both, champion.members[0])) if len(
            both) else fit_sensitivities(pd.DataFrame())
        names = dict(conn.execute("SELECT team_id, display_name FROM teams WHERE league = %s",
                                  (str(self.league),)).fetchall())
        abbreviations = dict(conn.execute(
            "SELECT team_id, abbreviation FROM teams WHERE league = %s",
            (str(self.league),)).fetchall())
        current_values = self._current_values(conn, season, ratings[0], values)
        self._state = LiveState(season, team_ids, names, abbreviations, ratings, sub,
                                sensitivity, current_values, now)
        self._store_ratings(conn, champion, season, now)
        return self._state

    def _current_values(self, conn, season: int, ratings: TeamRatings,
                        previous: pd.DataFrame) -> pd.DataFrame:
        """Blends this season's player values with last season's, by minutes played."""
        current = season_player_values(data.player_games_frame(conn, self.league, [season]),
                                       dict(zip(ratings.team_ids, ratings.net, strict=True)))
        merged = pd.merge(previous[["player_id", "mpg", "value"]] if len(previous) else
                          pd.DataFrame(columns=["player_id", "mpg", "value"]),
                          current[["player_id", "minutes", "mpg", "value"]], on="player_id",
                          how="outer", suffixes=("_prev", "_cur"))
        minutes = merged["minutes"].fillna(0.0)
        w = minutes / (minutes + VALUE_BLEND_MINUTES)
        prev_value = merged["value_prev"].fillna(merged["value_cur"])
        merged["value"] = (w * merged["value_cur"].fillna(prev_value) + (1 - w) * prev_value)
        merged["mpg"] = np.where(minutes > 60, merged["mpg_cur"], merged["mpg_prev"])
        return merged[["player_id", "mpg", "value"]].dropna(subset=["value"])

    def _store_ratings(self, conn, champion: Champion, season: int, now: datetime) -> None:
        state = self._state
        frames = [r.frame() for r in state.ratings]
        avg = frames[0].copy()
        for col in ("overall", "offense", "defense", "overall_se", "offense_se", "defense_se",
                    "pace"):
            avg[col] = np.mean([f[col].to_numpy() for f in frames], axis=0)
        sub_table = state.sub.table(state.ratings[0])
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO team_ratings (team_id, season, as_of, model_version_id,
                    games_played, overall, offense, defense, overall_se, offense_se,
                    defense_se, pace)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                [(int(r.team_id), season, now, champion.model_version_id, int(r.games_played),
                  float(r.overall), float(r.offense), float(r.defense), float(r.overall_se),
                  float(r.offense_se), float(r.defense_se), float(r.pace))
                 for r in avg.itertuples(index=False)],
            )
            cur.executemany(
                """
                INSERT INTO team_sub_ratings (team_id, as_of, model_version_id, metric, value,
                                              percentile)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                [(int(r.team_id), now, champion.model_version_id, r.metric, float(r.value),
                  float(r.percentile)) for r in sub_table.itertuples(index=False)],
            )

    def state(self, conn, now: datetime) -> LiveState:
        if self._state is None or self._state.as_of.date() != now.date():
            return self.refit(conn, now)
        return self._state

    # -- predictions ------------------------------------------------------------------

    def predict_game(self, conn: psycopg.Connection, game_id: int, now: datetime, *,
                     lock: bool = False) -> int | None:
        """Writes a prediction for one game. Unlocked predictions are skipped when nothing
        changed since the last one. Returns the prediction ID written, if any."""
        state = self.state(conn, now)
        champion = load_champion(conn, self.league)
        game = conn.execute(
            "SELECT season, season_type, home_team_id, away_team_id, neutral_site, start_time"
            " FROM games WHERE game_id = %s", (game_id,)).fetchone()
        season, season_type, home, away, neutral, start = game
        if home not in state.team_ids or away not in state.team_ids:
            return None
        context, absent = self._context(conn, game_id, season, home, away, start, state)
        preds: list[Prediction] = []
        for member, cal, ratings in zip(champion.members, champion.calibrations, state.ratings,
                                        strict=True):
            preds.append(predict(ratings, home, away, neutral=neutral, context=context,
                                 calibration=cal, use_rest_travel=member.use_rest_travel,
                                 absence_weight=member.absence_weight))
        prob = float(np.mean([p.home_win_prob for p in preds]))
        margin = float(np.mean([p.margin_home for p in preds]))
        r0 = state.ratings[0]
        h, a = r0.index(home), r0.index(away)
        poss = r0.pace_mean + (r0.pace[h] + r0.pace[a]) / 2
        mismatches = explain(state.sub, home, away, state.names, state.sensitivity, poss)
        inputs = {
            "ratings_as_of": state.as_of.isoformat(),
            "home": {"overall": float(r0.net[h]), "offense": float(r0.intercept + r0.off[h]),
                     "defense": float(r0.intercept + r0.dfn[h])},
            "away": {"overall": float(r0.net[a]), "offense": float(r0.intercept + r0.off[a]),
                     "defense": float(r0.intercept + r0.dfn[a])},
            "base_margin": float(np.mean([p.base_margin for p in preds])),
            "absence_shift": float(np.mean([p.absence_shift for p in preds])),
            "absences": absent,
            "rest_days": {"home": context.home_rest_days, "away": context.away_rest_days},
            "explainer": [{"text": m.text, "points": m.points, "metric": m.metric,
                           "beneficiary": m.beneficiary} for m in mismatches],
        }
        self._project(conn, game_id, season, season_type, home, away, preds, context, absent,
                      lock)
        digest = hashlib.sha1(json.dumps(
            {k: inputs[k] for k in ("home", "away", "absences", "rest_days")},
            sort_keys=True, default=str).encode()).hexdigest()
        if not lock:
            latest = conn.execute(
                "SELECT inputs_hash FROM predictions WHERE game_id = %s AND NOT is_shadow"
                " ORDER BY created_at DESC LIMIT 1", (game_id,)).fetchone()
            if latest and latest[0] == digest:
                return None
        rehearsal = season_type not in COUNTED_SEASON_TYPES
        (pid,) = conn.execute(
            """
            INSERT INTO predictions (game_id, model_version_id, home_win_prob, margin_home,
                total, margin_low, margin_high, is_locked, is_rehearsal, inputs, inputs_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING prediction_id
            """,
            (game_id, champion.model_version_id, prob, margin,
             float(np.mean([p.total for p in preds])),
             float(np.mean([p.margin_low for p in preds])),
             float(np.mean([p.margin_high for p in preds])), lock, rehearsal,
             json.dumps(inputs, default=str), digest),
        ).fetchone()
        return pid

    def _project(self, conn, game_id, season, season_type, home, away,
                 preds: list[Prediction], context: GameContext, absent: dict, lock: bool) -> None:
        """Player projections for the game. A failure here never blocks the prediction."""
        out = {e["player_id"] for side in absent.values() for e in side
               if e.get("status") == "Out"}
        try:
            self.projector.project_game(
                conn, game_id, season=season, season_type=season_type, home=home, away=away,
                home_points=float(np.mean([p.home_points for p in preds])),
                away_points=float(np.mean([p.away_points for p in preds])),
                home_b2b=context.home_rest_days <= 1, away_b2b=context.away_rest_days <= 1,
                out=out, lock=lock)
        except Exception:  # noqa: BLE001
            log.exception("player projections failed for game %s", game_id)

    def _context(self, conn, game_id, season, home, away, start, state: LiveState):
        sched = pd.DataFrame(conn.execute(
            "SELECT game_id, start_time, home_team_id, away_team_id FROM games"
            " WHERE league = %s AND season = %s AND season_type = ANY(%s)"
            " AND status <> 'canceled' AND start_time <= %s",
            (str(self.league), season, sorted(COUNTED_SEASON_TYPES | {"pre"}), start),
        ).fetchall(), columns=["game_id", "start_time", "home_team_id", "away_team_id"])
        rest = (4, 4, 0.0, 0.0)
        if len(sched):
            sched["game_date"] = pd.to_datetime(sched["start_time"], utc=True).dt.tz_convert(
                data.EASTERN).dt.date
            rest = schedule_context(sched, state.abbreviations).get(game_id, rest)
        out = self._out_players(conn, start)
        values = state.values.set_index("player_id")
        absent: dict[str, list] = {"home": [], "away": []}
        costs = []
        for side, team in (("home", home), ("away", away)):
            players = []
            for player_id, name, status in out.get(team, []):
                value = values["value"].get(player_id)
                mpg = values["mpg"].get(player_id)
                entry = {"player_id": player_id, "name": name, "status": status}
                if status == "Out" and value is not None and mpg is not None and mpg == mpg:
                    players.append((float(value), float(mpg)))
                    entry["value"], entry["mpg"] = float(value), float(mpg)
                absent[side].append(entry)
            costs.append(absence_cost(players))
        ctx = GameContext(home_rest_days=rest[0], away_rest_days=rest[1],
                          home_travel_miles=rest[2], away_travel_miles=rest[3],
                          home_absence=costs[0], away_absence=costs[1])
        return ctx, absent

    def _out_players(self, conn, before: datetime) -> dict[int, list[tuple]]:
        """Players on the most recent injury report before `before`, by team."""
        rows = conn.execute(
            """
            SELECT i.team_id, i.player_id, p.display_name, i.status
            FROM injury_snapshots i JOIN players p USING (player_id)
            WHERE i.snapshot_time = (SELECT max(snapshot_time) FROM injury_snapshots s
                                     JOIN players q USING (player_id)
                                     WHERE q.league = %s AND s.snapshot_time <= %s)
              AND p.league = %s
            """,
            (str(self.league), before, str(self.league)),
        ).fetchall()
        out: dict[int, list[tuple]] = {}
        for team, player, name, status in rows:
            out.setdefault(team, []).append((player, name, status))
        return out

    def refresh(self, conn: psycopg.Connection, league: League | None = None,
                now: datetime | None = None) -> int:
        """Predicts every unlocked game in the next week (skipping unchanged ones)."""
        now = now or datetime.now(UTC)
        rows = conn.execute(
            """
            SELECT g.game_id FROM games g
            WHERE g.league = %s AND g.status = 'scheduled' AND g.season_type = ANY(%s)
              AND g.start_time BETWEEN %s AND %s
              AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.game_id = g.game_id
                              AND p.is_locked AND NOT p.is_shadow)
            ORDER BY g.start_time
            """,
            (str(self.league), self.season_types(), now, now + timedelta(days=UPCOMING_DAYS)),
        ).fetchall()
        written = 0
        for (game_id,) in rows:
            written += self.predict_game(conn, game_id, now) is not None
        return written

    # -- GameHooks --------------------------------------------------------------------

    def lock(self, conn: psycopg.Connection, game_id: int, now: datetime) -> None:
        self.predict_game(conn, game_id, now, lock=True)

    def after_final(self, conn: psycopg.Connection, game_id: int) -> None:
        self.grade(conn, game_id)
        try:
            self.projector.grade(conn, game_id)
        except Exception:  # noqa: BLE001
            log.exception("grading player projections failed for game %s", game_id)
        self.refit(conn)
        self.refresh(conn)

    def after_injuries(self, conn: psycopg.Connection, league: League) -> None:
        self.refresh(conn)

    def overnight(self, conn: psycopg.Connection, league: League) -> None:
        self._priors.clear()
        self.refit(conn)
        self.refresh(conn)

    # -- grading ----------------------------------------------------------------------

    def grade(self, conn: psycopg.Connection, game_id: int) -> int:
        """Grades the game's locked predictions (public, shadow, and rehearsal)."""
        game = conn.execute("SELECT home_score, away_score, status FROM games"
                            " WHERE game_id = %s", (game_id,)).fetchone()
        if game is None or game[2] != "final" or game[0] is None:
            return 0
        home, away, _ = game
        line = data.lines_frame(conn, self.league, [r[0] for r in conn.execute(
            "SELECT season FROM games WHERE game_id = %s", (game_id,))])
        line = line[line["game_id"] == game_id]
        market = None
        if len(line):
            r = line.iloc[0]
            market = metrics.market_home_probability(r.home_moneyline, r.away_moneyline,
                                                     r.spread_home)
        rows = conn.execute(
            "SELECT prediction_id, home_win_prob, margin_home, total FROM predictions"
            " WHERE game_id = %s AND is_locked", (game_id,)).fetchall()
        for pid, prob, margin, total in rows:
            won = home > away
            conn.execute(
                """
                INSERT INTO prediction_grades (prediction_id, home_won, actual_margin,
                    actual_total, log_loss, brier, margin_error, total_error, market_home_prob)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (prediction_id) DO UPDATE SET home_won = EXCLUDED.home_won,
                    actual_margin = EXCLUDED.actual_margin, actual_total = EXCLUDED.actual_total,
                    log_loss = EXCLUDED.log_loss, brier = EXCLUDED.brier,
                    margin_error = EXCLUDED.margin_error, total_error = EXCLUDED.total_error,
                    market_home_prob = EXCLUDED.market_home_prob, graded_at = now()
                """,
                (pid, won, home - away, home + away,
                 metrics.single_game_log_loss(prob, won), (prob - won) ** 2,
                 (home - away) - margin, (home + away) - total, market),
            )
        return len(rows)
