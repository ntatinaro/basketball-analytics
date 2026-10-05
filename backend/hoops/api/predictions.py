"""Picks the prediction to show for each game.

Rules (feature spec, design rule 3): the locked prediction is the one that counts. A newer
prediction made after the lock (for example after a late scratch) is shown too, labeled
"updated after lock, not graded". Before the lock, the latest prediction is shown. Past
seasons without live predictions show the champion's walk-forward backtest prediction.
"""

from __future__ import annotations

import psycopg

from hoops.api.deps import rehearsal


def _public(row: dict) -> dict:
    return {
        "home_win_prob": row["home_win_prob"],
        "margin_home": row["margin_home"],
        "total": row["total"],
        "margin_low": row.get("margin_low"),
        "margin_high": row.get("margin_high"),
        "created_at": row.get("created_at"),
    }


def predictions_for(conn: psycopg.Connection, game_ids: list[int]) -> dict[int, dict]:
    if not game_ids:
        return {}
    rows = conn.execute(
        """
        SELECT p.prediction_id, p.game_id, p.created_at, p.home_win_prob, p.margin_home,
               p.total, p.margin_low, p.margin_high, p.is_locked, p.is_rehearsal, p.inputs,
               g.home_won, g.actual_margin, g.log_loss, g.market_home_prob
        FROM predictions p
        LEFT JOIN prediction_grades g USING (prediction_id)
        WHERE p.game_id = ANY(%s) AND NOT p.is_shadow AND (%s OR NOT p.is_rehearsal)
        ORDER BY p.game_id, p.created_at
        """,
        (game_ids, rehearsal()),
    ).fetchall()
    by_game: dict[int, list[dict]] = {}
    for r in rows:
        by_game.setdefault(r["game_id"], []).append(r)

    out: dict[int, dict] = {}
    for game_id, preds in by_game.items():
        locked = next((p for p in reversed(preds) if p["is_locked"]), None)
        latest = preds[-1]
        shown = locked or latest
        info = {
            "source": "live",
            "locked": locked is not None,
            "rehearsal": bool(shown["is_rehearsal"]),
            **_public(shown),
            "inputs": shown["inputs"],
            "updated_after_lock": None,
            "grade": None,
        }
        if locked and latest["created_at"] > locked["created_at"] and not latest["is_locked"]:
            info["updated_after_lock"] = _public(latest) | {
                "note": "updated after lock, not graded"}
        if locked and locked["home_won"] is not None:
            info["grade"] = {
                "correct": (locked["home_win_prob"] >= 0.5) == locked["home_won"],
                "actual_margin": locked["actual_margin"],
                "log_loss": locked["log_loss"],
            }
        out[game_id] = info

    missing = [g for g in game_ids if g not in out]
    if missing:
        for r in conn.execute(
            """
            SELECT DISTINCT ON (b.game_id) b.game_id, b.home_win_prob, b.margin_home, b.total,
                   g.home_score, g.away_score
            FROM backtest_predictions b
            JOIN model_versions m USING (model_version_id)
            JOIN games g USING (game_id)
            WHERE b.game_id = ANY(%s)
            -- the champion's backtest, else the newest (rows outlive a model switch)
            ORDER BY b.game_id, (m.role = 'champion') DESC, m.created_at DESC
            """,
            (missing,),
        ).fetchall():
            grade = None
            if r["home_score"] is not None:
                home_won = r["home_score"] > r["away_score"]
                grade = {"correct": (r["home_win_prob"] >= 0.5) == home_won,
                         "actual_margin": r["home_score"] - r["away_score"], "log_loss": None}
            out[r["game_id"]] = {
                "source": "backtest", "locked": False, "rehearsal": False,
                **_public(r), "inputs": None, "updated_after_lock": None, "grade": grade,
            }
    return out
