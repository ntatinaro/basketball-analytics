// Thin client for the read-only API. Types describe only the fields the screens use.

export type League = "nba" | "ncaam";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") query.set(k, String(v));
  const url = `/api/${path}${query.toString() ? `?${query}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export interface Meta {
  league: League;
  current_season: number;
  seasons: number[];
  conferences: string[];
  capabilities: string[];
  rehearsal: boolean;
  last_update: string | null;
  delayed: boolean;
}

export interface TeamRef {
  id: number;
  abbreviation: string;
  name: string;
  short_name?: string | null;
  location?: string | null;
  logo?: string | null;
  conference?: string | null;
  conference_abbr?: string | null;
  record?: { wins: number; losses: number } | null;
}

export interface PredictionView {
  source: "live" | "backtest";
  locked: boolean;
  rehearsal: boolean;
  home_win_prob: number;
  margin_home: number;
  total: number;
  margin_low: number | null;
  margin_high: number | null;
  updated_after_lock: (Omit<PredictionView, "updated_after_lock" | "grade" | "source" | "locked" | "rehearsal"> & { note: string }) | null;
  grade: { correct: boolean; actual_margin: number; log_loss: number | null } | null;
}

export interface GameCard {
  id: number;
  start_time: string;
  status: "scheduled" | "live" | "final" | "postponed" | "canceled";
  season: number;
  season_type: string;
  neutral_site: boolean;
  home: TeamRef;
  away: TeamRef;
  home_score: number | null;
  away_score: number | null;
  prediction: PredictionView | null;
  players_out: { home: number; away: number };
}

export interface Absence {
  player_id: number;
  name: string;
  status: string;
  value?: number;
  mpg?: number;
}

export interface BoxPlayer {
  player_id: number;
  name: string;
  position: string | null;
  starter: boolean;
  did_not_play: boolean;
  dnp_reason: string | null;
  minutes: number | null;
  pts: number | null; fgm: number | null; fga: number | null; fg3m: number | null; fg3a: number | null;
  ftm: number | null; fta: number | null; oreb: number | null; dreb: number | null; reb: number | null;
  ast: number | null; stl: number | null; blk: number | null; tov: number | null; pf: number | null;
  plus_minus: number | null;
}

export interface GameDetail extends GameCard {
  preview: {
    explainer: { text: string; points: number; metric: string; beneficiary: number }[];
    absences: { home: Absence[]; away: Absence[] };
    base_margin: number | null;
    absence_shift: number | null;
    ratings: { home: Record<string, number> | null; away: Record<string, number> | null };
    rest_days: { home: number; away: number } | null;
  };
  box_score: Record<"home" | "away", { team: Record<string, number> | null; players: BoxPlayer[] }> | null;
  default_tab: "preview" | "live" | "box";
}

export interface TeamRow extends TeamRef {
  rank: number | null;
  rating: number | null;
  rating_se: number | null;
  offense: number | null;
  defense: number | null;
  pace: number | null;
  games_played: number | null;
  low_confidence: boolean;
  wins: number;
  losses: number;
  ppg: number | null;
  opp_ppg: number | null;
}

export interface PlayerRow {
  player_id: number;
  name: string;
  position: string | null;
  headshot: string | null;
  team: string | null;
  team_id: number;
  teams: string[];
  is_total: boolean;
  traded: boolean;
  impact: number | null;
  games: number; starts: number; mpg: number | null; ppg: number | null; rpg: number | null;
  orpg: number | null; drpg: number | null; apg: number | null; spg: number | null; bpg: number | null;
  topg: number | null; fpg: number | null; fg3m_pg: number | null;
  fg_pct: number | null; fg3_pct: number | null; ft_pct: number | null;
  qualified: { games: boolean; fg: boolean; fg3: boolean; ft: boolean };
}

export interface ScheduleRow {
  game_id: number;
  start_time: string;
  status: string;
  home: boolean;
  opponent: TeamRef;
  win_prob: number | null;
  predicted_margin: number | null;
  prediction_source: "live" | "backtest" | null;
  result: { won: boolean; score: string; model_right: boolean | null } | null;
}

export interface PerGame { [k: string]: number | null }

export interface TeamDetail {
  season: number;
  team: TeamRow;
  switcher: { id: number; name: string; abbreviation: string }[];
  league_size: number;
  overview: {
    sub_ratings: { metric: string; label: string; value: number; percentile: number }[];
    trend: { as_of: string; games_played: number; overall: number; overall_se: number }[];
    season_odds: null;
  };
  team_stats: { games: number; team: PerGame; opponents: PerGame; pace: number | null } | null;
  roster: PlayerRow[];
  schedule: ScheduleRow[];
}

export interface GameLogRow {
  game_id: number; date: string; season_type: string; opponent: string; home: boolean;
  result: string | null; did_not_play: boolean; dnp_reason: string | null; starter: boolean;
  minutes: number | null; pts: number | null; fgm: number | null; fga: number | null;
  fg3m: number | null; fg3a: number | null; ftm: number | null; fta: number | null;
  oreb: number | null; dreb: number | null; reb: number | null; ast: number | null;
  stl: number | null; blk: number | null; tov: number | null; pf: number | null;
  plus_minus: number | null;
}

export interface PlayerDetail {
  season: number;
  seasons: number[];
  player: {
    id: number; name: string; position: string | null; height_inches: number | null;
    weight_pounds: number | null; birth_date: string | null; class_year: string | null;
    headshot: string | null; team: string | null; team_id: number | null;
  };
  season_stats: PlayerRow[];
  recent_form: GameLogRow[];
  projection: Record<string, { expected: number; low: number; high: number }> | null;
  similar: { player_id: number; name: string; team: string | null; position: string | null; ppg: number; rpg: number; apg: number }[];
  game_log: GameLogRow[];
}

export interface Summary { games: number; log_loss: number; brier: number; accuracy: number }
export interface CalibrationBin { low: number; high: number; games: number; predicted: number; actual: number }
export interface MarketCompare {
  model: Summary;
  market: Summary;
  target: { accuracy_gap: number; log_loss_gap: number; within_target: boolean };
  note?: string;
}

export interface ReportCard {
  season: number;
  live: {
    games: number;
    model?: Summary;
    calibration?: CalibrationBin[];
    by_month?: (Summary & { month: string })[];
    market?: MarketCompare;
    predictions: {
      game_id: number; date: string; home: string; away: string; score: string;
      home_win_prob: number; home_won: boolean; correct: boolean; log_loss: number;
    }[];
  };
  backtests: {
    season: number;
    model: Summary;
    early_season: Summary;
    rest_of_season: Summary;
    calibration: CalibrationBin[];
    market?: MarketCompare;
  }[];
}

export interface SearchResult { id: number; name: string; abbreviation?: string; team?: string | null; headshot?: string | null; logo?: string | null; position?: string | null }
