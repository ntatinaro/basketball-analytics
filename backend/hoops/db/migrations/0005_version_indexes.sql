-- The API checks for new data with one query on every request (hoops.api.deps). These
-- indexes keep its max() lookups cheap as the tables grow.
CREATE INDEX predictions_created_idx ON predictions (created_at);
CREATE INDEX prediction_grades_graded_idx ON prediction_grades (graded_at);
CREATE INDEX team_ratings_as_of_idx ON team_ratings (as_of);
