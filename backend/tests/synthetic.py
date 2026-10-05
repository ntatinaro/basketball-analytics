"""Simulated seasons with known team strengths, for testing the models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from hoops.leagues import League
from hoops.models.data import add_game_fields


def simulate_season(n_teams: int = 30, games_per_pair: int = 3, seed: int = 0,
                    home_edge: float = 1.5, noise: float = 11.0, season: int = 2025,
                    start: datetime = datetime(2024, 10, 22, 23, 0, tzinfo=UTC)):
    """Returns (games frame, true offense, true defense). Efficiency per 100 possessions
    is 112 + off[team] + dfn[opponent] +/- home edge + noise."""
    rng = np.random.default_rng(seed)
    off = rng.normal(0, 3.0, n_teams)
    dfn = rng.normal(0, 3.0, n_teams)
    off -= off.mean()
    dfn -= dfn.mean()
    team_ids = list(range(1, n_teams + 1))
    rows = []
    game_id = 1
    pairs = [(i, j) for i in range(n_teams) for j in range(n_teams) if i != j]
    schedule = [p for p in pairs for _ in range(max(1, games_per_pair // 2))]
    rng.shuffle(schedule)
    for k, (h, a) in enumerate(schedule):
        poss = rng.normal(99, 3)
        effs = {
            "h": 112 + off[h] + dfn[a] + home_edge + rng.normal(0, noise),
            "a": 112 + off[a] + dfn[h] - home_edge + rng.normal(0, noise),
        }
        row = {
            "game_id": game_id, "season": season, "season_type": "regular",
            "start_time": start + timedelta(hours=6 * (k // 8)), "neutral_site": False,
            "home_team_id": team_ids[h], "away_team_id": team_ids[a], "periods_played": 4,
            "pbp_quality_ok": True,
        }
        for side, eff in effs.items():
            pts = int(round(eff * poss / 100))
            fg3a = int(rng.normal(35, 4))
            fg3m = int(round(fg3a * 0.36))
            fta = int(rng.normal(22, 4))
            ftm = int(round(fta * 0.78))
            fgm = max((pts - ftm - fg3m) // 2, fg3m)
            tov = 13
            oreb = 10
            fga = int(round(poss + oreb - tov - 0.44 * fta))
            row.update({
                f"{side}_pts": pts, f"{side}_fgm": fgm, f"{side}_fga": fga,
                f"{side}_fg3m": fg3m, f"{side}_fg3a": fg3a, f"{side}_ftm": ftm,
                f"{side}_fta": fta, f"{side}_oreb": oreb, f"{side}_dreb": 33,
                f"{side}_ast": 25, f"{side}_stl": 7, f"{side}_blk": 5, f"{side}_tov": tov,
                f"{side}_pf": 19, f"{side}_possessions": poss,
                f"{side}_pts_excl_garbage": pts, f"{side}_possessions_excl_garbage": poss,
            })
        rows.append(row)
        game_id += 1
    games = add_game_fields(pd.DataFrame(rows), League.NBA)
    return games, team_ids, off, dfn
