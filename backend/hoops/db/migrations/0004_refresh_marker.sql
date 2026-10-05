-- When the precomputed screen tables were last refreshed. The API's response cache checks
-- this, so a refresh is visible immediately.
CREATE TABLE screen_refreshes (
    id            int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    refreshed_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO screen_refreshes DEFAULT VALUES;
