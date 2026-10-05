"""Data quality gate (architecture doc, section 8).

Three groups of checks, each gating only what depends on it:

- team:   team box score present and consistent with the final score
          -> a failing game is excluded from ratings, predictions, and backtests
- player: player points add up to team points
          -> a failing game is excluded from player averages and player values
- pbp:    plays present, scoring plays add up to the final score, clock order sane
          -> a failing game falls back to whole-game stats (no garbage-time removal)
             and is excluded from win probability training

Lineup tracking (five players on the floor) is recorded as a pbp issue named
`lineups_inconsistent` but does not fail the pbp group; it only matters for V3 lineups.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from hoops.espn.parse import GameSummary, Play


@dataclass(frozen=True)
class Issue:
    group: str          # 'team', 'player', or 'pbp'
    name: str
    detail: str


@dataclass(frozen=True)
class QualityResult:
    team_ok: bool
    player_ok: bool
    pbp_ok: bool
    issues: tuple[Issue, ...]


LINEUP_CHECK = "lineups_inconsistent"


def check_game(summary: GameSummary) -> QualityResult:
    game = summary.game
    issues: list[Issue] = []
    final = {game.home.espn_id: game.home_score, game.away.espn_id: game.away_score}

    # Team-level
    boxes = {b.team_espn_id: b for b in summary.team_box}
    if game.home_score is None or game.away_score is None:
        issues.append(Issue("team", "final_score_missing", "no final score"))
    if set(boxes) != set(final) or any(b.fga == 0 for b in boxes.values()):
        issues.append(Issue("team", "team_box_missing", f"team box for {sorted(boxes)}"))
    else:
        for team_id, box in boxes.items():
            if final[team_id] is not None and box.points_from_shots != final[team_id]:
                issues.append(Issue(
                    "team", "team_box_points_mismatch",
                    f"team {team_id}: shots add to {box.points_from_shots}, final {final[team_id]}",
                ))

    # Player-level
    by_team: dict[str, int] = defaultdict(int)
    for p in summary.player_box:
        by_team[p.team_espn_id] += p.pts or 0
    if not summary.player_box:
        issues.append(Issue("player", "player_box_missing", "no player rows"))
    else:
        for team_id, points in final.items():
            if points is not None and by_team.get(team_id, 0) != points:
                issues.append(Issue(
                    "player", "player_points_mismatch",
                    f"team {team_id}: players add to {by_team.get(team_id, 0)}, final {points}",
                ))

    # Play-by-play
    plays = summary.plays
    if not plays:
        issues.append(Issue("pbp", "pbp_missing", "no plays"))
    else:
        last = plays[-1]
        if (last.home_score, last.away_score) != (game.home_score, game.away_score):
            issues.append(Issue(
                "pbp", "pbp_score_mismatch",
                f"scoring plays add to {last.home_score}-{last.away_score},"
                f" final {game.home_score}-{game.away_score}",
            ))
        backwards = sum(
            1 for a, b in zip(plays, plays[1:], strict=False)
            if a.period == b.period and b.clock_seconds > a.clock_seconds + 0.01
        )
        if backwards:
            issues.append(Issue("pbp", "clock_runs_backward", f"{backwards} times"))
        lineup_problem = lineup_issue(summary)
        if lineup_problem:
            issues.append(Issue("pbp", LINEUP_CHECK, lineup_problem))

    def ok(group: str) -> bool:
        return not any(i.group == group and i.name != LINEUP_CHECK for i in issues)

    return QualityResult(ok("team"), ok("player"), ok("pbp"), tuple(issues))


def lineup_issue(summary: GameSummary) -> str | None:
    """Replays substitutions from the starters. Returns a description of the first problem,
    or None if lineups track cleanly (or there are no substitutions to track)."""
    on: dict[str, set[str]] = defaultdict(set)
    for p in summary.player_box:
        if p.starter:
            on[p.team_espn_id].add(p.player_espn_id)
    subs = [p for p in summary.plays if p.is_substitution]
    if not subs:
        return None
    if len(on) != 2 or any(len(v) != 5 for v in on.values()):
        return "starters are not five per team"
    bad = sum(_apply_substitution(on, play) for play in subs)
    if bad:
        return f"{bad} substitutions inconsistent with who was on the floor"
    if any(len(v) != 5 for v in on.values()):
        return "lineups do not end with five players per team"
    return None


def _apply_substitution(on: dict[str, set[str]], play: Play) -> int:
    """Applies one substitution and returns 1 if it contradicts who was on the floor.

    NBA: 'X enters the game for Y' (participants: in, out).
    College: separate 'X subbing in' and 'X subbing out' events (one participant each).
    """
    team = play.team_espn_id
    ids = play.participant_espn_ids
    text = play.text.lower()
    if team not in on or not ids:
        return 1
    if "enters the game for" in text and len(ids) >= 2:
        incoming, outgoing = ids[0], ids[1]
        error = outgoing not in on[team] or incoming in on[team]
        on[team].discard(outgoing)
        on[team].add(incoming)
        return int(error)
    if "subbing in" in text:
        error = ids[0] in on[team]
        on[team].add(ids[0])
        return int(error)
    if "subbing out" in text:
        error = ids[0] not in on[team]
        on[team].discard(ids[0])
        return int(error)
    return 1
