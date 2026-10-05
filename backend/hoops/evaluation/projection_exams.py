"""Rolling exams for single-game player projections (same protocol as the game model).

Every past game is projected walk-forward: only games before its date are used, the game's
predicted team points and total come from the game model's own backtest predictions, and
the players who actually played stand in for the injury report (past reports are not
available). Candidates are compared on the tuning seasons only; the winner is then
examined on the next season.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pandas as pd
import psycopg

from hoops.evaluation.backtest import SeasonData, load_season
from hoops.leagues import League
from hoops.models.live import MODEL_NAME as GAME_MODEL
from hoops.models.projections import (
    MODEL_NAME,
    History,
    ProjectionSettings,
    Ranges,
    actuals,
    candidates,
    evaluate,
    fit_ranges,
    naive,
    project,
    season_prior,
)

log = logging.getLogger(__name__)
TIE = 0.002     # score differences smaller than this count as ties


def positions(conn: psycopg.Connection, league: League) -> dict[int, str]:
    return {pid: pos for pid, pos in conn.execute(
        "SELECT player_id, position FROM players WHERE league = %s", (str(league),))}


def backtest_points(conn: psycopg.Connection, league: League) -> dict[int, tuple[float, float]]:
    """game_id -> (home points, away points) predicted by the game model's champion."""
    rows = conn.execute(
        """
        SELECT b.game_id, b.margin_home, b.total FROM backtest_predictions b
        JOIN model_versions m USING (model_version_id)
        WHERE m.league = %s AND m.model_name = %s AND m.role = 'champion'
        """, (str(league), GAME_MODEL)).fetchall()
    return {g: ((t + m) / 2, (t - m) / 2) for g, m, t in rows}


def season_inputs(sd: SeasonData, prev: SeasonData | None, pos: dict[int, str],
                  points: dict[int, tuple[float, float]], half_life: float) -> pd.DataFrame:
    """Walk-forward inputs for every player who played in a game with a predicted score."""
    prior = season_prior(prev.player_games, prev.games, pos) if prev else season_prior(
        sd.player_games, sd.games, pos)
    history = History(prior, pos, half_life)
    rows: list[dict] = []
    players = sd.player_games
    for _day, day_games in sd.games.groupby("game_date", sort=True):
        ids = set(day_games["game_id"])
        day_players = players[players["game_id"].isin(ids)]
        for g in day_games.itertuples(index=False):
            if g.game_id not in points:
                continue
            home_pts, away_pts = points[g.game_id]
            rest = sd.context.get(g.game_id, (3, 3, 0, 0))
            for team, pts, margin, b2b in ((g.home_team_id, home_pts, home_pts - away_pts,
                                            rest[0] <= 1),
                                           (g.away_team_id, away_pts, away_pts - home_pts,
                                            rest[1] <= 1)):
                who = day_players[(day_players["game_id"] == g.game_id)
                                  & (day_players["team_id"] == team)]["player_id"].tolist()
                rows += history.inputs(g.game_id, team, who, team_points=pts,
                                       game_total=home_pts + away_pts, margin=margin,
                                       back_to_back=b2b)
        history.update(day_players, day_games)
    return pd.DataFrame(rows)


class Lab:
    def __init__(self, conn: psycopg.Connection, league: League, seasons: list[int]) -> None:
        self.conn, self.league, self.seasons = conn, league, seasons
        self.data: dict[int, SeasonData] = {}
        self.inputs: dict[tuple[float, int], pd.DataFrame] = {}

    def load(self) -> None:
        self.pos = positions(self.conn, self.league)
        self.points = backtest_points(self.conn, self.league)
        for s in self.seasons:
            self.data[s] = load_season(self.conn, self.league, s)
            log.info("%s %d: %d player games", self.league, s, len(self.data[s].player_games))

    def season_inputs(self, half_life: float, season: int) -> pd.DataFrame:
        key = (half_life, season)
        if key not in self.inputs:
            self.inputs[key] = season_inputs(self.data[season], self.data.get(season - 1),
                                             self.pos, self.points, half_life)
        return self.inputs[key]

    def frames(self, settings: ProjectionSettings, seasons: list[int]):
        inputs = pd.concat([self.season_inputs(settings.half_life_games, s) for s in seasons],
                           ignore_index=True)
        actual = actuals(pd.concat([self.data[s].player_games for s in seasons]))
        return project(inputs, settings), naive(inputs), actual


@dataclass
class Choice:
    settings: ProjectionSettings
    tuning: dict
    ranges: Ranges


def choose(lab: Lab, pool: list[ProjectionSettings], tuning: list[int]
           ) -> tuple[Choice, list[tuple[float, ProjectionSettings]]]:
    table = []
    for s in pool:
        proj, base, actual = lab.frames(s, tuning)
        table.append((evaluate(proj, actual, base)["score"], s))
    table.sort(key=lambda t: t[0])
    best = table[0][0]
    tied = [s for score, s in table if score - best < TIE]
    winner = min(tied, key=lambda s: (s.complexity, [x for x, y in table if y == s][0]))
    proj, base, actual = lab.frames(winner, tuning)
    ranges = fit_ranges(proj, actual)
    return Choice(winner, evaluate(proj, actual, base, ranges), ranges), table


def run_projection_exams(conn: psycopg.Connection, league: League, warmup: int,
                         scored: list[int], n_candidates: int = 30) -> dict:
    lab = Lab(conn, league, [warmup, *scored])
    lab.load()
    if not lab.points:
        raise RuntimeError("no game-model backtest predictions; run the game exams first")
    pool = candidates(n_candidates)
    report: dict = {"league": str(league), "candidates": len(pool), "rounds": []}
    version_ids = {s.version: _model_version(conn, league, s.version, s.to_json())
                   for s in pool}
    for round_no, exam_season in enumerate(scored[1:], start=1):
        tuning = scored[:round_no]
        choice, table = choose(lab, pool, tuning)
        proj, base, actual = lab.frames(choice.settings, [exam_season])
        exam = evaluate(proj, actual, base, choice.ranges)
        _record_round(conn, league, round_no, tuning, exam_season, table, choice, exam,
                      version_ids)
        report["rounds"].append({"round": round_no, "tuning_seasons": tuning,
                                 "exam_season": exam_season,
                                 "winner": choice.settings.to_json(),
                                 "tuning": choice.tuning, "exam": exam})
        log.info("round %d: winner %s, exam score %.3f", round_no, choice.settings.version,
                 exam["score"])
    champion, _ = choose(lab, pool, scored)
    champion_id = _record_champion(conn, league, champion, scored)
    report["champion"] = {"settings": champion.settings.to_json(), "tuning": champion.tuning,
                          "ranges": champion.ranges.to_json(),
                          "model_version_id": champion_id}
    return report


def _model_version(conn, league: League, version: str, settings: dict, role: str = "candidate"
                   ) -> int:
    return conn.execute(
        """
        INSERT INTO model_versions (league, model_name, version, settings, role)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (league, model_name, version) DO UPDATE SET settings = EXCLUDED.settings
        RETURNING model_version_id
        """,
        (str(league), MODEL_NAME, version, json.dumps(settings), role),
    ).fetchone()[0]


def _record_round(conn, league, round_no, tuning, exam_season, table, choice, exam,
                  version_ids) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM exam_results WHERE league = %s AND model_name = %s"
                     " AND exam_round = %s", (str(league), MODEL_NAME, round_no))
        for score, s in table:
            win = s.version == choice.settings.version
            conn.execute(
                """
                INSERT INTO exam_results (league, model_name, exam_round, tuning_seasons,
                    exam_season, model_version_id, is_winner, tuning_metrics, exam_metrics)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (str(league), MODEL_NAME, round_no, tuning, exam_season,
                 version_ids[s.version], win, json.dumps({"score": score}),
                 json.dumps(exam) if win else None),
            )


def _record_champion(conn, league, champion: Choice, scored) -> int:
    settings = {"settings": champion.settings.to_json(), "ranges": champion.ranges.to_json(),
                "tuned_on": scored}
    version = "champion-" + champion.settings.version.split("-")[1]
    with conn.transaction():
        conn.execute("UPDATE model_versions SET role = 'retired' WHERE league = %s"
                     " AND model_name = %s AND role = 'champion'", (str(league), MODEL_NAME))
        champion_id = _model_version(conn, league, version, settings)
        conn.execute("UPDATE model_versions SET role = 'champion' WHERE model_version_id = %s",
                     (champion_id,))
        conn.execute(
            "INSERT INTO training_runs (model_version_id, finished_at, status, metrics)"
            " VALUES (%s, now(), 'succeeded', %s)",
            (champion_id, json.dumps({"tuning": champion.tuning, "seasons": scored})),
        )
    return champion_id


def format_report(report: dict) -> str:
    lines = [f"Projection exams: {report['league']} ({report['candidates']} candidates)", ""]
    for r in report["rounds"]:
        e = r["exam"]
        lines.append(f"Round {r['round']}: tuned on {r['tuning_seasons']}, examined on"
                     f" {r['exam_season']} ({e['players']} player games)")
        lines.append(f"  score {e['score']:.3f} (1.0 = season-average baseline; lower is"
                     f" better)")
        for s in ("minutes", "pts", "reb", "ast", "fg3m"):
            lines.append(f"  {s:8s} error {e['mae'][s]:.2f} (baseline {e['baseline_mae'][s]:.2f})"
                         f"  80% range covers {100 * e['coverage_80'][s]:.0f}%")
        lines.append("")
    c = report["champion"]
    lines.append(f"Champion: {c['settings']}  tuning score {c['tuning']['score']:.3f}")
    return "\n".join(lines)

