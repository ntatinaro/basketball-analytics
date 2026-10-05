-- Core schema for V1 (architecture doc, section 7).
-- ESPN is the only data source, so ESPN IDs are stored directly on teams, players, and games.
-- Seasons are named by the year they end in (2025-26 = 2026). Times are UTC.

-- Reference ---------------------------------------------------------------------------

CREATE TABLE teams (
    team_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league         text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    espn_id        text NOT NULL,
    abbreviation   text NOT NULL,
    display_name   text NOT NULL,
    short_name     text,
    location       text,
    name           text,
    logo_url       text,
    color          text,
    is_division_1  boolean NOT NULL DEFAULT true,   -- always true for the NBA
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league, espn_id)
);

-- A team's conference can change between seasons (college realignment).
CREATE TABLE team_seasons (
    team_id            bigint NOT NULL REFERENCES teams,
    season             int NOT NULL,
    conference_name    text,
    conference_abbr    text,
    PRIMARY KEY (team_id, season)
);

CREATE TABLE players (
    player_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league         text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    espn_id        text NOT NULL,
    display_name   text NOT NULL,
    short_name     text,
    position       text,
    height_inches  int,
    weight_pounds  int,
    birth_date     date,
    class_year     text,             -- college only
    headshot_url   text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league, espn_id)
);

CREATE TABLE roster_entries (
    player_id   bigint NOT NULL REFERENCES players,
    team_id     bigint NOT NULL REFERENCES teams,
    season      int NOT NULL,
    jersey      text,
    first_game  date,
    last_game   date,
    PRIMARY KEY (player_id, team_id, season)
);

-- Game data ---------------------------------------------------------------------------

CREATE TABLE games (
    game_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league           text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    espn_id          text NOT NULL,
    season           int NOT NULL,
    season_type      text NOT NULL CHECK (season_type IN
                         ('pre', 'regular', 'play_in', 'post', 'all_star', 'other')),
    start_time       timestamptz NOT NULL,
    home_team_id     bigint NOT NULL REFERENCES teams,
    away_team_id     bigint NOT NULL REFERENCES teams,
    neutral_site     boolean NOT NULL DEFAULT false,
    conference_game  boolean,
    status           text NOT NULL CHECK (status IN
                         ('scheduled', 'live', 'final', 'postponed', 'canceled')),
    home_score       int,
    away_score       int,
    periods_played   int,
    -- Quality gate results per check group (section 8). NULL = not checked yet.
    team_quality_ok    boolean,
    player_quality_ok  boolean,
    pbp_quality_ok     boolean,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league, espn_id)
);
CREATE INDEX games_league_season_start_idx ON games (league, season, start_time);
CREATE INDEX games_start_idx ON games (start_time);

CREATE TABLE team_game_stats (
    game_id      bigint NOT NULL REFERENCES games ON DELETE CASCADE,
    team_id      bigint NOT NULL REFERENCES teams,
    opponent_id  bigint NOT NULL REFERENCES teams,
    is_home      boolean NOT NULL,
    pts int NOT NULL, fgm int NOT NULL, fga int NOT NULL, fg3m int NOT NULL, fg3a int NOT NULL,
    ftm int NOT NULL, fta int NOT NULL, oreb int NOT NULL, dreb int NOT NULL, ast int NOT NULL,
    stl int NOT NULL, blk int NOT NULL, tov int NOT NULL, pf int NOT NULL,
    possessions  double precision NOT NULL,
    -- The same totals with garbage-time plays removed, when play-by-play allows it.
    pts_excl_garbage          int,
    possessions_excl_garbage  double precision,
    PRIMARY KEY (game_id, team_id)
);
CREATE INDEX team_game_stats_team_idx ON team_game_stats (team_id);

CREATE TABLE player_game_stats (
    game_id       bigint NOT NULL REFERENCES games ON DELETE CASCADE,
    player_id     bigint NOT NULL REFERENCES players,
    team_id       bigint NOT NULL REFERENCES teams,
    starter       boolean NOT NULL,
    did_not_play  boolean NOT NULL,
    dnp_reason    text,
    ejected       boolean NOT NULL DEFAULT false,
    minutes double precision, pts int, fgm int, fga int, fg3m int, fg3a int, ftm int, fta int,
    oreb int, dreb int, reb int, ast int, stl int, blk int, tov int, pf int, plus_minus int,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX player_game_stats_player_idx ON player_game_stats (player_id);

-- One row per play-by-play event. `sequence` is the position in game order (from 0),
-- taken from the order of ESPN's list, not ESPN's sequence numbers.
-- Scores are rebuilt from scoring plays; ESPN's running score field is unreliable.
CREATE TABLE plays (
    game_id          bigint NOT NULL REFERENCES games ON DELETE CASCADE,
    sequence         int NOT NULL,
    espn_play_id     text NOT NULL,
    period           int NOT NULL,
    clock_seconds    double precision NOT NULL,
    team_id          bigint REFERENCES teams,
    type_id          text,
    type_text        text,
    text             text,
    scoring_play     boolean NOT NULL,
    points           int NOT NULL,
    shooting_play    boolean NOT NULL,
    home_score       int NOT NULL,
    away_score       int NOT NULL,
    x                double precision,
    y                double precision,
    player_ids       bigint[] NOT NULL DEFAULT '{}',
    wallclock        timestamptz,
    is_garbage_time  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (game_id, sequence)
);

CREATE TABLE injury_snapshots (
    snapshot_time  timestamptz NOT NULL,
    player_id      bigint NOT NULL REFERENCES players,
    team_id        bigint REFERENCES teams,
    status         text NOT NULL,           -- ESPN status, e.g. 'Out', 'Questionable'
    detail         text,
    espn_updated   timestamptz,
    PRIMARY KEY (snapshot_time, player_id)
);
CREATE INDEX injury_snapshots_player_idx ON injury_snapshots (player_id, snapshot_time);

-- Betting lines are a benchmark only; they never appear on game cards.
CREATE TABLE betting_lines (
    game_id         bigint NOT NULL REFERENCES games ON DELETE CASCADE,
    provider        text NOT NULL,
    line_kind       text NOT NULL CHECK (line_kind IN
                        ('captured_pregame', 'espn_close', 'timing_unknown')),
    spread_home     double precision,      -- negative = home team favored
    total           double precision,
    home_moneyline  int,
    away_moneyline  int,
    captured_at     timestamptz NOT NULL,
    PRIMARY KEY (game_id, provider, line_kind)
);

-- Model management --------------------------------------------------------------------

CREATE TABLE model_versions (
    model_version_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league            text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    model_name        text NOT NULL,         -- e.g. 'team_ratings', 'game_predictor'
    version           text NOT NULL,
    settings          jsonb NOT NULL DEFAULT '{}',
    role              text NOT NULL DEFAULT 'candidate' CHECK (role IN
                          ('candidate', 'champion', 'challenger', 'retired')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (league, model_name, version)
);
-- At most one champion per league and model.
CREATE UNIQUE INDEX model_versions_one_champion_idx
    ON model_versions (league, model_name) WHERE role = 'champion';

CREATE TABLE training_runs (
    training_run_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text NOT NULL DEFAULT 'running' CHECK (status IN
                          ('running', 'succeeded', 'failed')),
    data_through      timestamptz,
    metrics           jsonb NOT NULL DEFAULT '{}',
    error             text
);

CREATE TABLE exam_results (
    exam_result_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league            text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    model_name        text NOT NULL,
    exam_round        int NOT NULL,
    tuning_seasons    int[] NOT NULL,
    exam_season       int NOT NULL,
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    is_winner         boolean NOT NULL DEFAULT false,
    tuning_metrics    jsonb NOT NULL DEFAULT '{}',
    exam_metrics      jsonb,                 -- filled for the round's winner
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Model outputs -----------------------------------------------------------------------

CREATE TABLE player_values (
    player_id         bigint NOT NULL REFERENCES players,
    season            int NOT NULL,
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    minutes           double precision NOT NULL,
    value             double precision NOT NULL,   -- points per 100 possessions vs. average
    value_se          double precision,
    PRIMARY KEY (player_id, season, model_version_id)
);

-- Full history: one row per team per refit, so trend charts work.
CREATE TABLE team_ratings (
    team_id           bigint NOT NULL REFERENCES teams,
    season            int NOT NULL,
    as_of             timestamptz NOT NULL,   -- uses games that finished before this time
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    games_played      int NOT NULL,
    overall           double precision NOT NULL,
    offense           double precision NOT NULL,
    defense           double precision NOT NULL,
    overall_se        double precision,
    offense_se        double precision,
    defense_se        double precision,
    pace              double precision,
    PRIMARY KEY (team_id, as_of, model_version_id)
);
CREATE INDEX team_ratings_season_idx ON team_ratings (season, as_of);

CREATE TABLE team_sub_ratings (
    team_id           bigint NOT NULL REFERENCES teams,
    as_of             timestamptz NOT NULL,
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    metric            text NOT NULL,
    value             double precision NOT NULL,
    percentile        double precision,
    PRIMARY KEY (team_id, as_of, model_version_id, metric)
);

-- Append-only. Locking a prediction inserts a new row with is_locked = true.
-- Shadow (challenger) and rehearsal predictions are never shown in public grading.
CREATE TABLE predictions (
    prediction_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game_id           bigint NOT NULL REFERENCES games,
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    created_at        timestamptz NOT NULL DEFAULT now(),
    home_win_prob     double precision NOT NULL CHECK (home_win_prob BETWEEN 0 AND 1),
    margin_home       double precision NOT NULL,
    total             double precision NOT NULL,
    margin_low        double precision,
    margin_high       double precision,
    is_locked         boolean NOT NULL DEFAULT false,
    is_shadow         boolean NOT NULL DEFAULT false,
    is_rehearsal      boolean NOT NULL DEFAULT false,
    inputs            jsonb NOT NULL DEFAULT '{}',   -- ratings, absences, adjustments used
    inputs_hash       text NOT NULL
);
CREATE INDEX predictions_game_idx ON predictions (game_id, created_at);
-- Only one public locked prediction per game.
CREATE UNIQUE INDEX predictions_one_lock_idx
    ON predictions (game_id) WHERE is_locked AND NOT is_shadow AND NOT is_rehearsal;

CREATE FUNCTION predictions_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'predictions are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER predictions_append_only
    BEFORE UPDATE OR DELETE ON predictions
    FOR EACH ROW EXECUTE FUNCTION predictions_append_only();

CREATE TRIGGER predictions_no_truncate
    BEFORE TRUNCATE ON predictions
    FOR EACH STATEMENT EXECUTE FUNCTION predictions_append_only();

CREATE TABLE prediction_grades (
    prediction_id     bigint PRIMARY KEY REFERENCES predictions,
    graded_at         timestamptz NOT NULL DEFAULT now(),
    home_won          boolean NOT NULL,
    actual_margin     int NOT NULL,
    actual_total      int NOT NULL,
    log_loss          double precision NOT NULL,
    brier             double precision NOT NULL,
    margin_error      double precision NOT NULL,
    total_error       double precision NOT NULL,
    market_home_prob  double precision
);

-- Operations --------------------------------------------------------------------------

CREATE TABLE job_runs (
    job_run_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_name     text NOT NULL,
    league       text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'running' CHECK (status IN
                     ('running', 'succeeded', 'failed')),
    details      jsonb NOT NULL DEFAULT '{}',
    error        text
);
CREATE INDEX job_runs_name_started_idx ON job_runs (job_name, started_at DESC);

CREATE TABLE data_quality_issues (
    game_id      bigint NOT NULL REFERENCES games ON DELETE CASCADE,
    check_group  text NOT NULL CHECK (check_group IN ('team', 'player', 'pbp')),
    check_name   text NOT NULL,
    detail       text,
    found_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, check_name)
);
