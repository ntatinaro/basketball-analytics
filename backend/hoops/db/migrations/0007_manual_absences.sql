-- Absences the owner enters by hand in the admin panel (V1.1). College injury reports are
-- not available, so from V4 these stand in for them; the NBA uses ESPN's reports.
CREATE TABLE manual_absences (
    absence_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league      text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    player_id   bigint NOT NULL REFERENCES players,
    team_id     bigint NOT NULL REFERENCES teams,
    status      text NOT NULL DEFAULT 'Out' CHECK (status IN ('Out', 'Doubtful', 'Questionable')),
    starts_on   date NOT NULL,
    ends_on     date,                 -- empty: until further notice
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);
CREATE INDEX manual_absences_active_idx ON manual_absences (league, starts_on, ends_on);
