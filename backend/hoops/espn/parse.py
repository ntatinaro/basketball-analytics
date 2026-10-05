"""Parses ESPN responses into plain data objects carrying ESPN IDs.

Every quirk listed in the architecture doc (section 4.2) is handled here:

- Play order comes from the order of the `plays` list; `sequenceNumber` is not chronological.
- ESPN's running score on each play is unreliable (stale on some plays), so scores are
  rebuilt by adding up scoring plays.
- In older seasons made free throws have `scoreValue` 0; they count as 1 point.
- Missing shot coordinates use large negative sentinels and are stored as missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SEASON_TYPES = {1: "pre", 2: "regular", 3: "post", 5: "play_in"}
_COORD_SENTINEL = -1_000_000
_ATHLETE_ID_IN_URL = re.compile(r"/id/(\d+)")


@dataclass(frozen=True)
class TeamRef:
    espn_id: str
    abbreviation: str
    display_name: str
    short_name: str | None = None
    location: str | None = None
    name: str | None = None
    logo_url: str | None = None
    color: str | None = None


@dataclass
class Game:
    espn_id: str
    season: int
    season_type: str
    start_time: datetime
    home: TeamRef
    away: TeamRef
    neutral_site: bool
    conference_game: bool | None
    status: str
    home_score: int | None
    away_score: int | None
    periods_played: int | None
    notes: list[str] = field(default_factory=list)


@dataclass
class TeamBox:
    team_espn_id: str
    pts: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    oreb: int
    dreb: int
    ast: int
    stl: int
    blk: int
    tov: int
    pf: int

    @property
    def possessions(self) -> float:
        """Box-score possession estimate for this team's offense."""
        return self.fga - self.oreb + self.tov + 0.44 * self.fta

    @property
    def points_from_shots(self) -> int:
        return 2 * self.fgm + self.fg3m + self.ftm


@dataclass
class PlayerBox:
    player_espn_id: str
    team_espn_id: str
    display_name: str
    short_name: str | None
    position: str | None
    jersey: str | None
    headshot_url: str | None
    starter: bool
    did_not_play: bool
    dnp_reason: str | None
    ejected: bool
    minutes: float | None = None
    pts: int | None = None
    fgm: int | None = None
    fga: int | None = None
    fg3m: int | None = None
    fg3a: int | None = None
    ftm: int | None = None
    fta: int | None = None
    oreb: int | None = None
    dreb: int | None = None
    reb: int | None = None
    ast: int | None = None
    stl: int | None = None
    blk: int | None = None
    tov: int | None = None
    pf: int | None = None
    plus_minus: int | None = None


@dataclass
class Play:
    sequence: int                 # position in game order, from 0
    espn_play_id: str
    period: int
    clock_seconds: float
    team_espn_id: str | None
    type_id: str | None
    type_text: str
    text: str
    scoring_play: bool
    points: int                   # 1 for made free throws even when ESPN says 0
    shooting_play: bool
    home_score: int               # rebuilt from scoring plays
    away_score: int
    x: float | None
    y: float | None
    participant_espn_ids: list[str]
    wallclock: datetime | None
    is_garbage_time: bool = False

    @property
    def is_free_throw(self) -> bool:
        return "free throw" in self.type_text.lower()

    @property
    def is_field_goal_attempt(self) -> bool:
        return self.shooting_play and not self.is_free_throw

    @property
    def is_offensive_rebound(self) -> bool:
        return self.type_text.lower() == "offensive rebound"

    @property
    def is_turnover(self) -> bool:
        return "turnover" in self.type_text.lower()

    @property
    def is_substitution(self) -> bool:
        return "substitution" in self.type_text.lower()


@dataclass
class BettingLine:
    provider: str
    spread_home: float | None
    total: float | None
    home_moneyline: int | None
    away_moneyline: int | None


@dataclass
class GameSummary:
    game: Game
    team_box: list[TeamBox]
    player_box: list[PlayerBox]
    plays: list[Play]
    lines: list[BettingLine]
    espn_win_prob: list[tuple[str, float]]   # (espn play id, home win probability)


@dataclass
class Injury:
    player_espn_id: str
    player_name: str
    team_espn_id: str | None
    status: str
    detail: str | None
    espn_updated: datetime | None


@dataclass
class RosterPlayer:
    player_espn_id: str
    display_name: str
    short_name: str | None
    position: str | None
    jersey: str | None
    height_inches: int | None
    weight_pounds: int | None
    birth_date: datetime | None
    class_year: str | None
    headshot_url: str | None


# -- small helpers --------------------------------------------------------------------


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lstrip("+")
    if text in ("", "-", "--"):
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


def made_attempted(value: str | None) -> tuple[int | None, int | None]:
    if not value or "-" not in value:
        return None, None
    made, attempted = value.split("-", 1)
    return to_int(made), to_int(attempted)


def parse_clock(display: str | None) -> float:
    """'11:34' -> 694.0, '45.3' -> 45.3."""
    if not display:
        return 0.0
    if ":" in display:
        minutes, seconds = display.split(":", 1)
        return int(minutes) * 60 + float(seconds)
    return float(display)


def _status(status: dict[str, Any]) -> str:
    kind = status.get("type") or {}
    name = kind.get("name", "")
    if "POSTPONED" in name:
        return "postponed"
    if "CANCELED" in name or "CANCELLED" in name or "FORFEIT" in name:
        return "canceled"
    state = kind.get("state")
    if state == "post":
        return "final" if kind.get("completed", True) else "canceled"
    if state == "in":
        return "live"
    return "scheduled"


def team_ref(team: dict[str, Any]) -> TeamRef:
    logo = team.get("logo")
    if not logo and team.get("logos"):
        logo = team["logos"][0].get("href")
    return TeamRef(
        espn_id=str(team["id"]),
        abbreviation=team.get("abbreviation") or "",
        display_name=team.get("displayName") or team.get("name") or "",
        short_name=team.get("shortDisplayName"),
        location=team.get("location"),
        name=team.get("name"),
        logo_url=logo,
        color=team.get("color"),
    )


def _game(event_id: str, season: dict[str, Any], competition: dict[str, Any],
          status: dict[str, Any]) -> Game:
    sides = {c["homeAway"]: c for c in competition["competitors"]}
    state = _status(status)
    scored = state in ("live", "final")
    return Game(
        espn_id=str(event_id),
        season=int(season["year"]),
        season_type=SEASON_TYPES.get(int(season.get("type") or 0), "other"),
        start_time=parse_time(competition.get("date") or competition.get("startDate")),
        home=team_ref(sides["home"]["team"]),
        away=team_ref(sides["away"]["team"]),
        neutral_site=bool(competition.get("neutralSite", False)),
        conference_game=competition.get("conferenceCompetition"),
        status=state,
        home_score=to_int(sides["home"].get("score")) if scored else None,
        away_score=to_int(sides["away"].get("score")) if scored else None,
        periods_played=to_int(status.get("period")),
        notes=[n.get("headline", "") for n in competition.get("notes") or []],
    )


# -- scoreboard -----------------------------------------------------------------------


def parse_scoreboard(body: dict[str, Any]) -> list[Game]:
    games = []
    for event in body.get("events", []):
        competition = event["competitions"][0]
        status = competition.get("status") or event.get("status") or {}
        games.append(_game(event["id"], event["season"], competition, status))
    return games


# -- summary --------------------------------------------------------------------------


def parse_summary(body: dict[str, Any]) -> GameSummary:
    header = body["header"]
    competition = header["competitions"][0]
    game = _game(header["id"], header["season"], competition, competition.get("status") or {})
    points = {str(c["team"]["id"]): to_int(c.get("score")) or 0
              for c in competition["competitors"]}
    box = body.get("boxscore") or {}
    plays = _plays(body.get("plays") or [], game.home.espn_id, game.away.espn_id)
    if plays:
        game.periods_played = max(game.periods_played or 0, max(p.period for p in plays))
    return GameSummary(
        game=game,
        team_box=[_team_box(t, points) for t in box.get("teams", []) if t.get("statistics")],
        player_box=_player_box(box.get("players", [])),
        plays=plays,
        lines=_lines(body.get("pickcenter") or []),
        espn_win_prob=[
            (str(w["playId"]), float(w["homeWinPercentage"]))
            for w in body.get("winprobability") or []
            if w.get("playId") is not None and w.get("homeWinPercentage") is not None
        ],
    )


def _team_box(team: dict[str, Any], points: dict[str, int]) -> TeamBox:
    stats = {s["name"]: s.get("displayValue") for s in team["statistics"]}
    fgm, fga = made_attempted(stats.get("fieldGoalsMade-fieldGoalsAttempted"))
    fg3m, fg3a = made_attempted(
        stats.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
    )
    ftm, fta = made_attempted(stats.get("freeThrowsMade-freeThrowsAttempted"))
    tov = to_int(stats.get("totalTurnovers"))
    if tov is None:
        tov = to_int(stats.get("turnovers"))
    team_id = str(team["team"]["id"])
    return TeamBox(
        team_espn_id=team_id,
        pts=points.get(team_id, 0),
        fgm=fgm or 0, fga=fga or 0, fg3m=fg3m or 0, fg3a=fg3a or 0, ftm=ftm or 0, fta=fta or 0,
        oreb=to_int(stats.get("offensiveRebounds")) or 0,
        dreb=to_int(stats.get("defensiveRebounds")) or 0,
        ast=to_int(stats.get("assists")) or 0,
        stl=to_int(stats.get("steals")) or 0,
        blk=to_int(stats.get("blocks")) or 0,
        tov=tov or 0,
        pf=to_int(stats.get("fouls")) or 0,
    )


_PLAYER_STATS = {
    "points": "pts", "rebounds": "reb", "assists": "ast", "turnovers": "tov", "steals": "stl",
    "blocks": "blk", "offensiveRebounds": "oreb", "defensiveRebounds": "dreb", "fouls": "pf",
    "plusMinus": "plus_minus",
}
_PLAYER_SPLITS = {
    "fieldGoalsMade-fieldGoalsAttempted": ("fgm", "fga"),
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
    "freeThrowsMade-freeThrowsAttempted": ("ftm", "fta"),
}


def _player_box(teams: list[dict[str, Any]]) -> list[PlayerBox]:
    rows: list[PlayerBox] = []
    for team in teams:
        team_id = str(team["team"]["id"])
        for group in team.get("statistics") or []:
            keys = group.get("keys") or []
            for entry in group.get("athletes") or []:
                athlete = entry.get("athlete") or {}
                if not athlete.get("id"):
                    continue
                stats = entry.get("stats") or []
                row = PlayerBox(
                    player_espn_id=str(athlete["id"]),
                    team_espn_id=team_id,
                    display_name=athlete.get("displayName") or "",
                    short_name=athlete.get("shortName"),
                    position=(athlete.get("position") or {}).get("abbreviation"),
                    jersey=athlete.get("jersey"),
                    headshot_url=(athlete.get("headshot") or {}).get("href"),
                    starter=bool(entry.get("starter")),
                    did_not_play=bool(entry.get("didNotPlay")) or not stats,
                    dnp_reason=entry.get("reason"),
                    ejected=bool(entry.get("ejected")),
                )
                for key, value in zip(keys, stats, strict=False):
                    if key == "minutes":
                        minutes = to_int(value)
                        row.minutes = None if minutes is None else float(minutes)
                    elif key in _PLAYER_STATS:
                        setattr(row, _PLAYER_STATS[key], to_int(value))
                    elif key in _PLAYER_SPLITS:
                        made, attempted = made_attempted(value)
                        setattr(row, _PLAYER_SPLITS[key][0], made)
                        setattr(row, _PLAYER_SPLITS[key][1], attempted)
                rows.append(row)
    return rows


def _coordinate(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if value < _COORD_SENTINEL else value


def _plays(raw: list[dict[str, Any]], home_id: str, away_id: str) -> list[Play]:
    plays: list[Play] = []
    home = away = 0
    for i, p in enumerate(raw):
        type_text = (p.get("type") or {}).get("text") or ""
        team_id = str(p["team"]["id"]) if p.get("team") else None
        scoring = bool(p.get("scoringPlay"))
        points = to_int(p.get("scoreValue")) or 0
        if scoring and points == 0 and "free throw" in type_text.lower():
            points = 1
        if scoring:
            if team_id == home_id:
                home += points
            elif team_id == away_id:
                away += points
        coordinate = p.get("coordinate") or {}
        plays.append(Play(
            sequence=i,
            espn_play_id=str(p.get("id") or p.get("sequenceNumber") or i),
            period=int((p.get("period") or {}).get("number") or 0),
            clock_seconds=parse_clock((p.get("clock") or {}).get("displayValue")),
            team_espn_id=team_id,
            type_id=str((p.get("type") or {}).get("id")) if p.get("type") else None,
            type_text=type_text,
            text=p.get("text") or "",
            scoring_play=scoring,
            points=points if scoring else 0,
            shooting_play=bool(p.get("shootingPlay")),
            home_score=home,
            away_score=away,
            x=_coordinate(coordinate.get("x")),
            y=_coordinate(coordinate.get("y")),
            participant_espn_ids=[
                str(x["athlete"]["id"]) for x in p.get("participants") or []
                if (x.get("athlete") or {}).get("id")
            ],
            wallclock=parse_time(p.get("wallclock")),
        ))
    return plays


def _lines(pickcenter: list[dict[str, Any]]) -> list[BettingLine]:
    lines = []
    for entry in pickcenter:
        home = entry.get("homeTeamOdds") or {}
        away = entry.get("awayTeamOdds") or {}
        line = BettingLine(
            provider=(entry.get("provider") or {}).get("name") or "unknown",
            spread_home=entry.get("spread"),
            total=entry.get("overUnder"),
            home_moneyline=to_int(home.get("moneyLine")),
            away_moneyline=to_int(away.get("moneyLine")),
        )
        if any(v is not None for v in (line.spread_home, line.total, line.home_moneyline)):
            lines.append(line)
    return lines


# -- other endpoints ------------------------------------------------------------------


def parse_close_lines(body: dict[str, Any]) -> list[BettingLine]:
    """Labeled closing lines from the core odds endpoint, where ESPN provides them."""
    lines = []
    for item in body.get("items") or []:
        home_close = ((item.get("homeTeamOdds") or {}).get("close") or {})
        away_close = ((item.get("awayTeamOdds") or {}).get("close") or {})
        if not home_close and not item.get("close"):
            continue
        spread = to_float((home_close.get("pointSpread") or {}).get("american"))
        total = to_float(((item.get("close") or {}).get("total") or {}).get("american"))
        lines.append(BettingLine(
            provider=(item.get("provider") or {}).get("name") or "unknown",
            spread_home=spread if spread is not None else item.get("spread"),
            total=total if total is not None else item.get("overUnder"),
            home_moneyline=to_int((home_close.get("moneyLine") or {}).get("american")),
            away_moneyline=to_int((away_close.get("moneyLine") or {}).get("american")),
        ))
    return lines


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower().lstrip("+")
    if text in ("", "-", "--"):
        return None
    if text in ("ev", "even"):
        return 100.0
    text = text.lstrip("ou")
    try:
        return float(text)
    except ValueError:
        return None


def parse_teams(body: dict[str, Any]) -> list[TeamRef]:
    leagues = (body.get("sports") or [{}])[0].get("leagues") or [{}]
    return [team_ref(t["team"]) for t in leagues[0].get("teams") or []]


def parse_standings_conferences(body: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """Team ESPN ID -> (conference name, abbreviation), from the standings tree."""
    out: dict[str, tuple[str, str | None]] = {}

    def walk(node: dict[str, Any]) -> None:
        entries = (node.get("standings") or {}).get("entries") or []
        for e in entries:
            out[str(e["team"]["id"])] = (node.get("name") or "", node.get("abbreviation"))
        for child in node.get("children") or []:
            walk(child)

    for child in body.get("children") or []:
        walk(child)
    return out


def parse_injuries(body: dict[str, Any]) -> list[Injury]:
    out = []
    for team in body.get("injuries") or []:
        team_id = str(team["id"]) if team.get("id") else None
        for injury in team.get("injuries") or []:
            athlete = injury.get("athlete") or {}
            athlete_id = athlete.get("id")
            if not athlete_id:
                for link in athlete.get("links") or []:
                    match = _ATHLETE_ID_IN_URL.search(link.get("href") or "")
                    if match:
                        athlete_id = match.group(1)
                        break
            if not athlete_id:
                continue
            out.append(Injury(
                player_espn_id=str(athlete_id),
                player_name=athlete.get("displayName") or "",
                team_espn_id=team_id,
                status=injury.get("status") or "",
                detail=(injury.get("details") or {}).get("type"),
                espn_updated=parse_time(injury.get("date")),
            ))
    return out


def parse_roster(body: dict[str, Any]) -> list[RosterPlayer]:
    out = []
    for a in body.get("athletes") or []:
        if not a.get("id"):
            continue
        out.append(RosterPlayer(
            player_espn_id=str(a["id"]),
            display_name=a.get("displayName") or "",
            short_name=a.get("shortName"),
            position=(a.get("position") or {}).get("abbreviation"),
            jersey=a.get("jersey"),
            height_inches=to_int(a.get("height")),
            weight_pounds=to_int(a.get("weight")),
            birth_date=parse_time(a.get("dateOfBirth")),
            class_year=(a.get("experience") or {}).get("displayValue"),
            headshot_url=(a.get("headshot") or {}).get("href"),
        ))
    return out
