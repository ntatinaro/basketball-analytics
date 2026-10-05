"""Player box-score values (architecture doc, section 9.1).

A V1 stand-in for player impact ratings (V3). Each player gets a value in points per
100 possessions relative to an average player:

1. Box production per 100 on-court possessions, scored with Hollinger's Game Score weights.
2. Scaled so that minutes-weighted team sums best match team net ratings across the league.
3. A team adjustment so each team's players add up exactly to its net rating; this spreads
   credit for defense and other things the box score misses across the roster by minutes.
4. Pulled toward a below-average default by minutes played, so small samples don't look
   like stardom.

Team net rating is approximately the sum over players of value x (minutes per game / 48).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SHRINK_MINUTES = 400.0        # minutes of evidence that count as much as the default
DEFAULT_VALUE = -2.0          # roughly replacement level
NEWCOMER_VALUE = -1.5         # rookies and players without last-season data
NEWCOMER_MPG = 10.0
MAX_MPG = 36.0


def game_score(df: pd.DataFrame) -> pd.Series:
    return (df["pts"] + 0.4 * df["fgm"] - 0.7 * df["fga"] - 0.4 * (df["fta"] - df["ftm"])
            + 0.7 * df["oreb"] + 0.3 * df["dreb"] + df["stl"] + 0.7 * df["ast"]
            + 0.7 * df["blk"] - 0.4 * df["pf"] - df["tov"])


def season_player_values(player_games: pd.DataFrame, team_net: dict[int, float]) -> pd.DataFrame:
    """Values for one season. `team_net` is each team's opponent-adjusted net rating."""
    if player_games.empty:
        return pd.DataFrame(columns=["player_id", "team_id", "minutes", "games", "mpg", "value"])
    pg = player_games.copy()
    pg["gmsc"] = game_score(pg)
    pg["on_court_poss"] = pg["team_possessions"] * pg["minutes"] / 48.0

    # Per player and team (traded players have one row per team).
    by_pt = pg.groupby(["player_id", "team_id"]).agg(
        minutes=("minutes", "sum"), games=("game_id", "nunique"), gmsc=("gmsc", "sum"),
        poss=("on_court_poss", "sum"),
    ).reset_index()
    team_games = pg.groupby("team_id")["game_id"].nunique()
    by_pt["share"] = by_pt["minutes"] / by_pt["team_id"].map(team_games) / 48.0   # sums to ~5
    rate = 100 * by_pt["gmsc"] / by_pt["poss"].clip(lower=1.0)
    league_avg = np.average(rate, weights=by_pt["minutes"])
    by_pt["dev"] = rate - league_avg

    # Scale: regress team net on the minutes-weighted sum of deviations.
    team_dev = (by_pt["share"] * by_pt["dev"]).groupby(by_pt["team_id"]).sum()
    nets = team_dev.index.map(lambda t: team_net.get(t, 0.0)).to_numpy(dtype=float)
    denom = float((team_dev.to_numpy() ** 2).sum())
    scale = float((team_dev.to_numpy() * nets).sum() / denom) if denom > 0 else 0.0
    by_pt["raw"] = scale * by_pt["dev"]

    # Team adjustment, spread by minutes so each team's players sum to its net rating.
    shares = by_pt.groupby("team_id")["share"].sum()
    raw_sum = (by_pt["share"] * by_pt["raw"]).groupby(by_pt["team_id"]).sum()
    adjust = {t: (team_net.get(t, 0.0) - raw_sum[t]) / shares[t] for t in shares.index}
    by_pt["value"] = by_pt["raw"] + by_pt["team_id"].map(adjust)

    # Combine teams per player (minutes-weighted), then shrink toward the default.
    by_pt["weighted"] = by_pt["value"] * by_pt["minutes"]
    per_player = by_pt.groupby("player_id").agg(
        minutes=("minutes", "sum"), games=("games", "sum"), weighted=("weighted", "sum"),
    )
    main_team = by_pt.sort_values("minutes").groupby("player_id")["team_id"].last()
    value = per_player["weighted"] / per_player["minutes"]
    m = per_player["minutes"]
    out = pd.DataFrame({
        "player_id": per_player.index,
        "team_id": main_team.reindex(per_player.index).to_numpy(),
        "minutes": m.to_numpy(),
        "games": per_player["games"].to_numpy(),
        "mpg": (m / per_player["games"]).to_numpy(),
        "value": ((m * value + SHRINK_MINUTES * DEFAULT_VALUE) / (m + SHRINK_MINUTES)).to_numpy(),
    })
    return out.reset_index(drop=True)


def roster_strength(roster: pd.DataFrame, previous: pd.DataFrame) -> dict[int, float]:
    """Each team's net rating implied by its current roster and last season's values.

    `roster` has columns team_id, player_id. Expected minutes come from last season's
    minutes per game (capped), with newcomers at a default, scaled so each team plays 240
    minutes a game.
    """
    prev = previous.set_index("player_id")
    out: dict[int, float] = {}
    for team_id, players in roster.groupby("team_id"):
        ids = players["player_id"].to_numpy()
        known = prev.reindex(ids)
        mpg = known["mpg"].fillna(NEWCOMER_MPG).clip(upper=MAX_MPG).to_numpy()
        value = known["value"].fillna(NEWCOMER_VALUE).to_numpy()
        # Keep the top 13 by expected minutes, then scale minutes to 240 per game.
        order = np.argsort(-mpg)[:13]
        mpg, value = mpg[order], value[order]
        total = mpg.sum()
        if total <= 0:
            continue
        mpg = mpg * 240.0 / total
        out[int(team_id)] = float((value * mpg / 48.0).sum())
    return out
