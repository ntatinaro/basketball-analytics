"""Rolling exams (architecture doc, section 9.2).

Each round, candidate methods compete on the tuning seasons only. The winner (or a blend
of the top three, if that wins) is then examined on the next season, which no candidate
has been tuned on. The final champion is chosen the same way on all scored seasons.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
import psycopg

from hoops.evaluation import metrics
from hoops.evaluation.backtest import (
    SeasonData,
    fit_calibration,
    load_season,
    run_chain,
    score,
)
from hoops.leagues import League
from hoops.models import data
from hoops.models.config import ModelSettings, candidates
from hoops.models.predictor import Calibration

log = logging.getLogger(__name__)
MODEL_NAME = "game_predictor"
TIE_MARGIN = 0.0015          # log-loss differences smaller than this count as a tie
BLEND_SIZE = 3


def rating_key(s: ModelSettings) -> tuple:
    """Settings that change the ratings themselves (the rest only change scoring)."""
    return (s.half_life_days, s.prior_games, s.regress_to_mean, s.roster_weight,
            s.luck_discount, s.remove_garbage_time)


@dataclass
class Choice:
    """A round's pick: one candidate, or a blend of several."""

    members: list[ModelSettings]
    calibrations: list[Calibration]
    tuning: dict
    is_blend: bool = False

    @property
    def label(self) -> str:
        return " + ".join(m.version for m in self.members)


@dataclass
class Lab:
    """Holds season data and cached walk-forward runs for one league."""

    conn: psycopg.Connection
    league: League
    seasons: list[int]                               # warm-up season first
    data: dict[int, SeasonData] = field(default_factory=dict)
    runs: dict[tuple, dict] = field(default_factory=dict)
    market: dict[int, float] = field(default_factory=dict)

    def load(self) -> None:
        for s in self.seasons:
            self.data[s] = load_season(self.conn, self.league, s)
            log.info("%s %d: %d games", self.league, s, len(self.data[s].games))
        lines = data.lines_frame(self.conn, self.league, self.seasons)
        for row in lines.itertuples(index=False):
            p = metrics.market_home_probability(row.home_moneyline, row.away_moneyline,
                                                row.spread_home)
            if p is not None:
                self.market[row.game_id] = p

    def chain(self, settings: ModelSettings) -> dict:
        key = rating_key(settings)
        if key not in self.runs:
            self.runs[key] = run_chain([self.data[s] for s in self.seasons], settings)
        return self.runs[key]

    def predictions(self, settings: ModelSettings, seasons: list[int]) -> pd.DataFrame:
        runs = self.chain(settings)
        return pd.concat([runs[s].predictions for s in seasons], ignore_index=True)


def evaluate(lab: Lab, settings: ModelSettings, seasons: list[int],
             cal: Calibration | None = None) -> tuple[pd.DataFrame, Calibration]:
    preds = lab.predictions(settings, seasons)
    cal = cal or fit_calibration(preds, settings)
    return score(preds, settings, cal), cal


def choose(lab: Lab, pool: list[ModelSettings], tuning: list[int]) -> tuple[Choice, list]:
    """Picks the best candidate on tuning seasons; ties go to the simpler method; a blend
    of the top three wins only if it beats the best single candidate by more than a tie."""
    results = []
    for s in pool:
        scored, cal = evaluate(lab, s, tuning)
        results.append((metrics.log_loss(scored["prob"], scored["home_won"]), s, cal, scored))
    results.sort(key=lambda r: r[0])
    best_loss = results[0][0]
    tied = [r for r in results if r[0] <= best_loss + TIE_MARGIN]
    winner = min(tied, key=lambda r: (r[1].complexity, r[0]))
    single = Choice([winner[1]], [winner[2]], _summary(winner[3]))

    top = results[:BLEND_SIZE]
    blend_prob = np.mean([r[3]["prob"].to_numpy() for r in top], axis=0)
    blend_loss = metrics.log_loss(blend_prob, top[0][3]["home_won"])
    table = [(loss, s) for loss, s, _, _ in results]
    if blend_loss < winner[0] - TIE_MARGIN:
        blended = _blend_frame([r[3] for r in top])
        return Choice([r[1] for r in top], [r[2] for r in top], _summary(blended),
                      is_blend=True), table
    return single, table


def predict_choice(lab: Lab, choice: Choice, seasons: list[int]) -> pd.DataFrame:
    frames = [evaluate(lab, s, seasons, cal)[0]
              for s, cal in zip(choice.members, choice.calibrations, strict=True)]
    return _blend_frame(frames)


def _blend_frame(frames: list[pd.DataFrame]) -> pd.DataFrame:
    out = frames[0].copy()
    if len(frames) > 1:
        for col in ("prob", "margin_pred", "total_pred"):
            out[col] = np.mean([f[col].to_numpy() for f in frames], axis=0)
    return out


def _summary(scored: pd.DataFrame) -> dict:
    return metrics.summary(scored["prob"], scored["home_won"], scored["margin_pred"],
                           scored["margin"], scored["total_pred"], scored["total"])


def benchmarks(lab: Lab, exam: pd.DataFrame, tuning_preds: pd.DataFrame,
               no_roster: pd.DataFrame) -> dict:
    home_rate = float(tuning_preds["home_won"].mean())
    naive = metrics.summary(np.full(len(exam), home_rate), exam["home_won"])
    with_market = exam[exam["game_id"].isin(lab.market)]
    market_prob = with_market["game_id"].map(lab.market)
    has_market = len(with_market) > 0
    return {
        "naive": naive,
        "without_roster_start": metrics.summary(no_roster["prob"], no_roster["home_won"]),
        "market": metrics.summary(market_prob, with_market["home_won"]) if has_market else None,
        "model_on_market_games": (metrics.summary(with_market["prob"], with_market["home_won"])
                                  if has_market else None),
    }


def target_check(model_on_market: dict | None, market: dict | None) -> dict | None:
    """The owner's accuracy target: within 1.5 points of market accuracy and 0.01 of its
    log loss. None when no game in the exam has a betting line."""
    if model_on_market is None or market is None:
        return None
    acc_gap = market["accuracy"] - model_on_market["accuracy"]
    loss_gap = model_on_market["log_loss"] - market["log_loss"]
    return {"accuracy_gap": acc_gap, "log_loss_gap": loss_gap,
            "within_target": bool(acc_gap <= 0.015 and loss_gap <= 0.01)}


# -- running and recording ------------------------------------------------------------


def run_exams(conn: psycopg.Connection, league: League, warmup: int, scored: list[int],
              n_candidates: int = 40) -> dict:
    lab = Lab(conn, league, [warmup, *scored])
    lab.load()
    pool = candidates(n_candidates)
    report: dict = {"league": str(league), "candidates": len(pool), "rounds": []}
    version_ids = {s.version: _model_version(conn, league, s) for s in pool}

    for round_no, exam_season in enumerate(scored[1:], start=1):
        tuning = scored[:round_no]
        choice, table = choose(lab, pool, tuning)
        exam = predict_choice(lab, choice, [exam_season])
        tuning_preds = lab.predictions(choice.members[0], tuning)
        no_roster_settings = replace(choice.members[0], roster_weight=0.0)
        no_roster, _ = evaluate(lab, no_roster_settings, [exam_season],
                                fit_calibration(lab.predictions(no_roster_settings, tuning),
                                                no_roster_settings))
        bench = benchmarks(lab, exam, tuning_preds, no_roster)
        exam_metrics = _summary(exam) | {"benchmarks": bench, "target": target_check(
            bench["model_on_market_games"], bench["market"])}
        _record_round(conn, league, round_no, tuning, exam_season, table, choice, exam_metrics,
                      version_ids)
        report["rounds"].append({
            "round": round_no, "tuning_seasons": tuning, "exam_season": exam_season,
            "winner": choice.label, "blend": choice.is_blend,
            "winner_settings": [m.to_json() for m in choice.members],
            "tuning": choice.tuning, "exam": exam_metrics,
        })
        log.info("round %d: winner %s, exam log loss %.4f (market %s)", round_no,
                 choice.label, exam_metrics["log_loss"],
                 f"{bench['market']['log_loss']:.4f}" if bench["market"] else "none")

    champion, _ = choose(lab, pool, scored)
    champion_preds = predict_choice(lab, champion, scored)
    report["champion"] = {
        "members": [m.to_json() for m in champion.members], "blend": champion.is_blend,
        "calibrations": [c.to_json() for c in champion.calibrations],
        "tuning": champion.tuning,
    }
    champion_id = _record_champion(conn, league, champion, scored, version_ids)
    _store_backtest(conn, champion_id, champion_preds, lab.market)
    report["champion_model_version_id"] = champion_id
    return report


def _model_version(conn, league: League, settings: ModelSettings) -> int:
    return conn.execute(
        """
        INSERT INTO model_versions (league, model_name, version, settings)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (league, model_name, version) DO UPDATE SET settings = EXCLUDED.settings
        RETURNING model_version_id
        """,
        (str(league), MODEL_NAME, settings.version, json.dumps(settings.to_json())),
    ).fetchone()[0]


def _record_round(conn, league, round_no, tuning, exam_season, table, choice, exam_metrics,
                  version_ids) -> None:
    winners = {m.version for m in choice.members}
    with conn.transaction():
        conn.execute("DELETE FROM exam_results WHERE league = %s AND model_name = %s"
                     " AND exam_round = %s", (str(league), MODEL_NAME, round_no))
        for loss, s in table:
            win = s.version in winners
            conn.execute(
                """
                INSERT INTO exam_results (league, model_name, exam_round, tuning_seasons,
                    exam_season, model_version_id, is_winner, tuning_metrics, exam_metrics)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (str(league), MODEL_NAME, round_no, tuning, exam_season, version_ids[s.version],
                 win, json.dumps({"log_loss": loss}),
                 json.dumps(exam_metrics | {"blend": choice.is_blend}) if win else None),
            )


def _record_champion(conn, league, champion: Choice, scored, version_ids) -> int:
    """Stores the champion as its own model version holding member settings and
    calibrations, and marks it as the only champion."""
    settings = {
        "members": [m.to_json() for m in champion.members],
        "calibrations": [c.to_json() for c in champion.calibrations],
        "blend": champion.is_blend, "tuned_on": scored,
    }
    version = "champion-" + "-".join(m.version.split("-")[1] for m in champion.members)
    with conn.transaction():
        conn.execute("UPDATE model_versions SET role = 'retired' WHERE league = %s"
                     " AND model_name = %s AND role = 'champion'", (str(league), MODEL_NAME))
        (champion_id,) = conn.execute(
            """
            INSERT INTO model_versions (league, model_name, version, settings, role)
            VALUES (%s, %s, %s, %s, 'champion')
            ON CONFLICT (league, model_name, version) DO UPDATE
                SET settings = EXCLUDED.settings, role = 'champion'
            RETURNING model_version_id
            """,
            (str(league), MODEL_NAME, version, json.dumps(settings)),
        ).fetchone()
        conn.execute(
            "INSERT INTO training_runs (model_version_id, finished_at, status, metrics)"
            " VALUES (%s, now(), 'succeeded', %s)",
            (champion_id, json.dumps({"tuning": champion.tuning, "seasons": scored})),
        )
    return champion_id


def _store_backtest(conn, model_version_id: int, preds: pd.DataFrame,
                    market: dict[int, float]) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM backtest_predictions WHERE model_version_id = %s",
                     (model_version_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO backtest_predictions (model_version_id, game_id, season,
                    home_win_prob, margin_home, total, market_home_prob)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [(model_version_id, int(r.game_id), int(r.season), float(r.prob),
                  float(r.margin_pred), float(r.total_pred), market.get(int(r.game_id)))
                 for r in preds.itertuples(index=False)],
            )
