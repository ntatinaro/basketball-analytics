-- ESPN's odds endpoint lists in-game odds as their own provider ("DraftKings - Live Odds"),
-- with the last in-game line as its "close". Those were stored as closing lines; the parser
-- now skips them, and this removes the ones already stored.
DELETE FROM betting_lines WHERE provider ILIKE '%live%';
