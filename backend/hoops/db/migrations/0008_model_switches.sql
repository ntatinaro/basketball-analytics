-- Shadow models (V1.1): every time a challenger replaces the champion at a monthly
-- checkpoint, with the side-by-side record that justified it. Shown on the report card.
CREATE TABLE model_switches (
    switch_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    league              text NOT NULL CHECK (league IN ('nba', 'ncaam')),
    model_name          text NOT NULL,
    from_version_id     bigint NOT NULL REFERENCES model_versions,
    to_version_id       bigint NOT NULL REFERENCES model_versions,
    switched_at         timestamptz NOT NULL DEFAULT now(),
    games               integer NOT NULL,
    champion_log_loss   double precision NOT NULL,
    challenger_log_loss double precision NOT NULL,
    t_stat              double precision NOT NULL
);
CREATE INDEX model_switches_league_idx ON model_switches (league, model_name, switched_at);
