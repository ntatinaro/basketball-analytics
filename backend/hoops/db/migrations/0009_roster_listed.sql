-- The last day the roster sync listed a player on a team. With the game dates it tells which
-- of a traded player's entries is current, even before he plays for his new team.
ALTER TABLE roster_entries ADD COLUMN listed_on date;
