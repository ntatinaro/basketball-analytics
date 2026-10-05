-- Walk-forward predictions of the champion model on past seasons, for the report card's
-- backtest section. Unlike `predictions`, these were made after the fact by replaying
-- each season with only earlier games, so they are kept separate.
CREATE TABLE backtest_predictions (
    model_version_id  bigint NOT NULL REFERENCES model_versions,
    game_id           bigint NOT NULL REFERENCES games,
    season            int NOT NULL,
    home_win_prob     double precision NOT NULL,
    margin_home       double precision NOT NULL,
    total             double precision NOT NULL,
    market_home_prob  double precision,
    PRIMARY KEY (model_version_id, game_id)
);
