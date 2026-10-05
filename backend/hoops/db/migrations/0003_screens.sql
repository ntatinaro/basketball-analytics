-- Precomputed season tables for the screens (architecture doc, section 7), refreshed
-- whenever a game goes final, plus fuzzy search.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX players_name_trgm_idx ON players USING gin (display_name gin_trgm_ops);
CREATE INDEX teams_name_trgm_idx ON teams USING gin (display_name gin_trgm_ops);

-- One row per player, team, season, and season type. Games failing the player-level
-- quality check are left out, so missing player rows never drag averages down.
CREATE MATERIALIZED VIEW player_season_stats AS
SELECT g.league, g.season, g.season_type, p.player_id, p.team_id,
       count(*) FILTER (WHERE NOT p.did_not_play AND p.minutes > 0)            AS games,
       count(*) FILTER (WHERE p.starter AND NOT p.did_not_play)                AS starts,
       coalesce(sum(p.minutes), 0) AS minutes, coalesce(sum(p.pts), 0) AS pts,
       coalesce(sum(p.fgm), 0) AS fgm, coalesce(sum(p.fga), 0) AS fga,
       coalesce(sum(p.fg3m), 0) AS fg3m, coalesce(sum(p.fg3a), 0) AS fg3a,
       coalesce(sum(p.ftm), 0) AS ftm, coalesce(sum(p.fta), 0) AS fta,
       coalesce(sum(p.oreb), 0) AS oreb, coalesce(sum(p.dreb), 0) AS dreb,
       coalesce(sum(p.reb), 0) AS reb, coalesce(sum(p.ast), 0) AS ast,
       coalesce(sum(p.stl), 0) AS stl, coalesce(sum(p.blk), 0) AS blk,
       coalesce(sum(p.tov), 0) AS tov, coalesce(sum(p.pf), 0) AS pf,
       max(g.start_time) AS last_game
FROM player_game_stats p
JOIN games g USING (game_id)
WHERE g.status = 'final' AND g.player_quality_ok
GROUP BY g.league, g.season, g.season_type, p.player_id, p.team_id
HAVING count(*) FILTER (WHERE NOT p.did_not_play AND p.minutes > 0) > 0;
CREATE UNIQUE INDEX player_season_stats_key
    ON player_season_stats (season, season_type, player_id, team_id);

-- One row per team, season, and season type: record from every final game, box totals
-- (own and opponents') from games passing the team-level check.
CREATE MATERIALIZED VIEW team_season_stats AS
WITH sides AS (
    SELECT g.league, g.season, g.season_type, g.game_id, g.team_quality_ok,
           g.home_team_id AS team_id, g.away_team_id AS opponent_id,
           g.home_score AS pts_for, g.away_score AS pts_against
    FROM games g WHERE g.status = 'final'
    UNION ALL
    SELECT g.league, g.season, g.season_type, g.game_id, g.team_quality_ok,
           g.away_team_id, g.home_team_id, g.away_score, g.home_score
    FROM games g WHERE g.status = 'final'
)
SELECT s.league, s.season, s.season_type, s.team_id,
       count(*) AS games,
       count(*) FILTER (WHERE s.pts_for > s.pts_against) AS wins,
       count(*) FILTER (WHERE s.pts_for < s.pts_against) AS losses,
       count(t.game_id) AS box_games,
       sum(t.pts) AS pts, sum(t.fgm) AS fgm, sum(t.fga) AS fga, sum(t.fg3m) AS fg3m,
       sum(t.fg3a) AS fg3a, sum(t.ftm) AS ftm, sum(t.fta) AS fta, sum(t.oreb) AS oreb,
       sum(t.dreb) AS dreb, sum(t.ast) AS ast, sum(t.stl) AS stl, sum(t.blk) AS blk,
       sum(t.tov) AS tov, sum(t.pf) AS pf, sum(t.possessions) AS possessions,
       sum(o.pts) AS opp_pts, sum(o.fgm) AS opp_fgm, sum(o.fga) AS opp_fga,
       sum(o.fg3m) AS opp_fg3m, sum(o.fg3a) AS opp_fg3a, sum(o.ftm) AS opp_ftm,
       sum(o.fta) AS opp_fta, sum(o.oreb) AS opp_oreb, sum(o.dreb) AS opp_dreb,
       sum(o.ast) AS opp_ast, sum(o.stl) AS opp_stl, sum(o.blk) AS opp_blk,
       sum(o.tov) AS opp_tov, sum(o.possessions) AS opp_possessions
FROM sides s
LEFT JOIN team_game_stats t ON t.game_id = s.game_id AND t.team_id = s.team_id
LEFT JOIN team_game_stats o ON o.game_id = s.game_id AND o.team_id = s.opponent_id
GROUP BY s.league, s.season, s.season_type, s.team_id;
CREATE UNIQUE INDEX team_season_stats_key ON team_season_stats (season, season_type, team_id);
