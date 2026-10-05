-- Single-game player projections (V1.1). Like predictions, they are append-only: each
-- refresh that changes anything adds a new set, and the set locked 30 minutes before
-- tip-off is the one graded after the game.

CREATE TABLE projection_sets (
    projection_set_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game_id           bigint NOT NULL REFERENCES games,
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    created_at        timestamptz NOT NULL DEFAULT now(),
    is_locked         boolean NOT NULL DEFAULT false,
    is_rehearsal      boolean NOT NULL DEFAULT false,
    inputs_hash       text NOT NULL
);
CREATE INDEX projection_sets_game_idx ON projection_sets (game_id, created_at DESC);
CREATE UNIQUE INDEX projection_sets_one_lock_idx
    ON projection_sets (game_id) WHERE is_locked AND NOT is_rehearsal;

-- stats: {"pts": [expected, low, high], ...} for every projected stat
CREATE TABLE player_projections (
    projection_set_id bigint NOT NULL REFERENCES projection_sets,
    player_id         bigint NOT NULL REFERENCES players,
    team_id           bigint NOT NULL REFERENCES teams,
    stats             jsonb NOT NULL,
    PRIMARY KEY (projection_set_id, player_id)
);

CREATE FUNCTION projections_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'projections are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER projection_sets_append_only
    BEFORE UPDATE OR DELETE ON projection_sets
    FOR EACH ROW EXECUTE FUNCTION projections_append_only();
CREATE TRIGGER projection_sets_no_truncate
    BEFORE TRUNCATE ON projection_sets
    FOR EACH STATEMENT EXECUTE FUNCTION projections_append_only();
CREATE TRIGGER player_projections_append_only
    BEFORE UPDATE OR DELETE ON player_projections
    FOR EACH ROW EXECUTE FUNCTION projections_append_only();
CREATE TRIGGER player_projections_no_truncate
    BEFORE TRUNCATE ON player_projections
    FOR EACH STATEMENT EXECUTE FUNCTION projections_append_only();

-- metrics: {"mae": {stat: error}, "in_range": {stat: share of players inside the range}}
CREATE TABLE projection_grades (
    projection_set_id bigint PRIMARY KEY REFERENCES projection_sets,
    graded_at         timestamptz NOT NULL DEFAULT now(),
    players           integer NOT NULL,
    metrics           jsonb NOT NULL
);
