# Basketball Analytics App: Build Plan

Last updated: 2026-10-05
Companion documents: `../basketball-analytics-feature-spec.md` (features, screens, versions) and `../basketball-analytics-technical-architecture.md` (stack, data, models, deployment). Section numbers below refer to the architecture doc unless they say "spec".

## How to use this plan

- Work is grouped by version (V1, V1.1, V2, V3, V4, V5, Later), then into sprints. A sprint is a unit of work with one goal and a clear "done when", not a fixed length of time. No dates, by decision.
- Each version follows the project phases: build, then testing, then deploy. The last sprint of each version covers its testing and deploy.
- Check items off (`- [x]`) as they are completed.
- A sprint starts only when the sprints it depends on are done.

**Every sprint is done only when:**

- [ ] Its code is on GitHub and CI (lint and tests) passes.
- [ ] New logic has tests (architecture section 14).
- [ ] The docs are updated if anything in them changed.
- [ ] The owner has reviewed the sprint's result.

**Timing constraint for V1:** NBA preseason ends around October 16. The dress rehearsal (Sprint 1.7) needs the pipeline and models running on the VPS while preseason games are still being played, so Sprints 1.1 to 1.4 come first and Sprint 1.3 deploys early.

---

## V1: NBA core (opening night)

### Sprint 1.1: Foundations

Goal: an empty but working project skeleton, locally and in CI.

Depends on: architecture sign-off.

- [ ] Create the repository layout (section 15): `/backend`, `/web`, `/deploy`, `/.github/workflows`.
- [ ] Backend package `hoops` with `pyproject.toml`, Python 3.13, lint (ruff) and test (pytest) setup.
- [ ] `leagues.py`: league rules (periods, period length, overtime, shot clock, garbage-time windows) and the capability matrix (section 6).
- [ ] Settings loaded from environment variables; `deploy/.env.example` listing every setting.
- [ ] Database migration runner and the first migration: reference tables, game data tables, operations tables (section 7).
- [ ] Database trigger that rejects updates and deletes on `predictions`, with a test.
- [ ] Local development setup: Postgres in a Podman container, one command to start it.
- [ ] GitHub Actions workflow: lint and tests on every push, with a Postgres service for database tests.

Done when: CI is green on an empty skeleton, and migrations create every V1 table on a fresh database.

### Sprint 1.2: ESPN ingestion and NBA backfill

Goal: five NBA seasons (2021-22 to 2025-26) loaded, checked, and queryable.

Depends on: Sprint 1.1.

- [ ] ESPN client: rate limiting, retries with exponential backoff, every response saved to the raw store before parsing (section 4.4).
- [ ] Raw store on disk, keyed by endpoint, parameters, and fetch time; re-processing from raw files without re-downloading.
- [ ] Parsers for scoreboard and summary: games, team box scores, player box scores, plays, injuries, betting lines, ESPN win probability.
- [ ] Handle every known quirk (section 4.2), each with a test built from a saved real ESPN response:
  - [ ] Play order from the `plays` list, not `sequenceNumber`.
  - [ ] Running score rebuilt from scoring plays (stale score fields).
  - [ ] Made free throws with `scoreValue` 0 count as 1 point.
  - [ ] Missing shot coordinates (negative sentinels) stored as missing.
  - [ ] One scoreboard request per date.
- [ ] Division I team list and the non-Division I filter (built now, used in V4).
- [ ] Store by ESPN ID so every load can be re-run without duplicates.
- [ ] Data quality gate (section 8): team-level, player box, and play-by-play checks on every game; `data_quality_issues` records.
- [ ] Per-season quality report, with the 2% rule per check group.
- [ ] Garbage-time tagging from play-by-play: lead of 25+ in the last 6 minutes, or 15+ in the last 3.
- [ ] Backfill command; run it for NBA 2021-22 to 2025-26.
- [ ] Review the quality report with the owner before the data is used.

Done when: five NBA seasons are in Postgres, every game has quality results, and the owner has seen the quality report.

### Sprint 1.3: Worker, deployment, and early VPS deploy

Goal: the data pipeline running on the VPS, updating itself, before preseason ends.

Depends on: Sprint 1.2.

- [ ] Worker process with the scheduler (section 5):
  - [ ] Schedule sync (morning, then every 3 hours; past 2 and next 7 days).
  - [ ] Injury sync (every 15 minutes on game days).
  - [ ] Game watcher (every minute near and during games): records the betting line 30 minutes before tip-off; on final, stores the game and runs quality checks. Model steps are added in Sprint 1.4.
  - [ ] Overnight corrections job.
- [ ] `job_runs` logging for every job (start, end, status, error).
- [ ] Heavy jobs run at low CPU priority.
- [ ] Containerfiles for the API, worker, and internal web server.
- [ ] Quadlet units for `postgres`, `api`, `worker`, `web`; only `web` listens, on `127.0.0.1:8080`.
- [ ] Internal Caddyfile: serve website files, forward `/api` to the API.
- [ ] `deploy.sh`: pull code, build images with Podman, run migrations, restart services. Never touches other services on the VPS.
- [ ] With the owner: confirm how the existing Caddy runs (`podman ps`), add the `hoops.<domain>` block, and add the DNS record.
- [ ] First deploy to the VPS; run the NBA backfill there (or copy the database).
- [ ] Confirm jobs run on schedule on real preseason games.

Done when: the VPS updates games, injuries, and lines on its own, and `job_runs` shows the history.

### Sprint 1.4: V1 models and rolling exams

Goal: ratings and predictions that have been tested on past seasons.

Depends on: Sprint 1.2. Can run in parallel with Sprint 1.3.

- [ ] Model management tables: `model_versions`, `training_runs`, `exam_results`. Every training run and exam is recorded from now on.
- [ ] Player box-score values, shrunk by minutes, with a default for players without history (section 9.1).
- [ ] Team starting rating from current rosters and expected minutes; fallback to last season pulled one third toward average.
- [ ] Team ratings: opponent-adjusted ridge fit, home court per season, recency weighting, starting rating as fading pseudo-games, garbage time removed, opponent 3-point and free-throw luck discounted, ranges.
- [ ] Sub-ratings with league percentiles.
- [ ] Game predictor: possessions, points, home court, rest, travel distance, "Out" players; win probability, margin, total, and range.
- [ ] Matchup explainer rule and template text.
- [ ] Walk-forward backtest harness (no future data).
- [ ] Metrics: log loss, Brier score, accuracy, calibration, margin and total error.
- [ ] Benchmarks: naive baseline, model without the roster starting point, betting market with the bookmaker margin removed.
- [ ] Rolling exams (section 9.2): 2021-22 warm-up; three rounds; 20 to 50 candidates per round; winner chosen on tuning seasons only; ties to the simpler method; top-three blend as a candidate.
- [ ] Report the results against the accuracy target (within 1.5 points of market accuracy and 0.01 of market log loss; clearly better than the naive baseline).
- [ ] Sanity check: final-season ratings against public ratings such as ESPN's BPI.
- [ ] Tune the final model on all four scored seasons; record it as the champion.
- [ ] Hook the models into the worker: on final, refit ratings, grade the locked prediction, refresh upcoming predictions; lock predictions 30 minutes before tip-off.
- [ ] Review the exam results with the owner.

Done when: rolling exam results exist and have been reviewed, and the worker produces, locks, and grades predictions automatically.

### Sprint 1.5: API

Goal: every V1 screen has an endpoint returning correct data quickly.

Depends on: Sprint 1.4.

- [ ] Precomputed tables `team_season_stats` and `player_season_stats` (traded players: one row per team plus a combined row), refreshed on every final.
- [ ] FastAPI app with the V1 endpoints (section 11): meta, games by date and time zone, game detail, teams, team detail, players (by scope), player detail, report card.
- [ ] Locked and latest prediction on game endpoints; "updated after lock, not graded" when they differ.
- [ ] Absences on game endpoints ("Out" changes the prediction; "Questionable" and "Day-to-day" are shown only).
- [ ] Typo-tolerant team and player search with `pg_trgm`.
- [ ] Season parameter on team and player endpoints (2021-22 onward).
- [ ] Official NBA leaderboard minimums for "Qualified only" (spec section 6).
- [ ] Last successful data update in `meta`, for the "data delayed" note.
- [ ] In-memory caching, invalidated when data changes.
- [ ] Report card data: graded predictions, accuracy by month, calibration, market comparison, backtests, early vs. late season split.
- [ ] API tests for every endpoint, including response time.

Done when: every V1 endpoint passes its tests against the real backfilled data.

### Sprint 1.6: Website

Goal: every V1 screen working on phones and desktops.

Depends on: Sprint 1.5 (can start against sample data during Sprint 1.5).

- [ ] React + TypeScript + Vite project; build into static files served by the internal Caddy.
- [ ] Shell: bottom tab bar (Tonight, Teams, Players, Season placeholder hidden until V3, Report card), NBA/NCAA switch (NCAA hidden until V4), dark mode by default, local times.
- [ ] "Data delayed, last updated …" note when data is stale.
- [ ] Tonight: date arrows; Live, Upcoming, Final groups; prediction cards with range and absences flag; final cards with pick result.
- [ ] Game page: Preview (prediction, what moved it, matchup explainer, absences), Box score, final result next to the locked prediction; opens on the right tab for the game's state.
- [ ] Teams: ranked table with team search and League / East / West scope.
- [ ] Team page: header with rating, range, rank, and team switcher; tabs Overview, Team Stats, Roster, Schedule.
- [ ] Players: table with player search, scope, position filter, "Qualified only".
- [ ] Player page: header and stat line; tabs Overview and Game log.
- [ ] Report card screen.
- [ ] Season picker on Teams, Players, team page, and player page.
- [ ] Tables: TanStack Table sorting, TanStack Virtual rows, sticky name column and header.
- [ ] Team logos and player headshots from ESPN.
- [ ] Simple Playwright click-through tests at phone and desktop sizes.

Done when: every V1 screen works against the real API, and the Playwright tests pass.

### Sprint 1.7: V1 testing, dress rehearsal, and launch

Goal: V1 proven on real games and live on opening night.

Depends on: Sprints 1.3 to 1.6.

- [ ] Deploy the full V1 to the VPS.
- [ ] Dress rehearsal on NBA preseason games, with preseason games temporarily treated as predictable in a rehearsal mode (excluded from public grading): schedule sync, prediction lock 30 minutes before tip-off, game final, ratings refit, grading, report card update.
- [ ] Simulate an ESPN outage and confirm the "data delayed" note appears and the system recovers.
- [ ] Check the performance budget on a mid-range phone (first load under 2 seconds on 4G; sort or scope switch under 100 ms; smooth scrolling).
- [ ] Owner manual testing; fix what it finds.
- [ ] Switch rehearsal mode off; clear rehearsal predictions from public views.
- [ ] Final checks before opening night: preseason ratings built from current rosters, opening-night predictions present, locks scheduled.
- [ ] Tell the friends.

Done when: opening-night predictions are locked and graded automatically, and the site is live at `hoops.<domain>`.

---

## V1.1: Projections, admin panel, shadow models

### Sprint 1.1.1: Single-game player projections

Depends on: V1.

- [ ] Minutes model (role, absences, blowout risk from the predicted margin).
- [ ] Per-minute rates shrunk toward average; adjusted for opponent, pace, rest, and absent teammates.
- [ ] Every box-score stat projected with a range; plus/minus excluded.
- [ ] Rolling exam for projections; record in model management tables.
- [ ] `player_projections` table; worker produces projections with each prediction.
- [ ] API: `GET /api/nba/games/{id}/projections`.
- [ ] Website: game page Projections tab (projections before the game, projected vs. actual during and after); projected vs. actual in the player game log; five stats by default, the rest under "full projection".

Done when: projections are graded on real games and shown next to actuals.

### Sprint 1.1.2: Admin panel and shadow models

Depends on: V1.

- [ ] Admin login (single owner account; password from `.env`).
- [ ] Data health: last successful pull per job, job history, failures.
- [ ] Data quality: per-season reports and flagged games.
- [ ] Model management (view only): model versions and settings, champion and challengers, rolling exam results per round and candidate, live accuracy against the target, training history.
- [ ] NCAA absences entry (used from V4).
- [ ] Shadow models: challengers predict every game quietly and are graded; monthly checkpoint rule (a challenger replaces the champion only if clearly better); switches noted on the report card.

Done when: the owner can see training history, exam results, live accuracy against the target, and job health in the admin panel.

### Sprint 1.1.3: V1.1 testing and deploy

- [ ] Tests for projections, admin login, and shadow grading.
- [ ] Playwright click-through of the Projections tab and admin panel.
- [ ] Deploy; owner manual testing.

---

## V2: Live

### Sprint 2.1: Live data

Depends on: V1.

- [ ] Measure ESPN's live feed delay on a real game (play timestamps against fetch times).
- [ ] Live poller per game (about every 10 seconds), processing only new plays by list position.
- [ ] `live_states`: score, clock, possession, fouls, players on the floor, running box score.
- [ ] Store ESPN's live win probability for each moment.
- [ ] Postgres NOTIFY on every update.
- [ ] Raw live polling files deleted after 7 days.

### Sprint 2.2: Live win probability and insights

Depends on: Sprint 2.1.

- [ ] Train our live win probability model on past play-by-play (score difference, time left, possession, pregame probability, fouls and bonus); calibrate; rolling exam.
- [ ] Insight detectors: scoring runs, unusual shooting (against season rates), struggling lineups, foul trouble, large win probability swings.
- [ ] Significance thresholds; developing events update their card in place; keep the most significant cards per game.
- [ ] Template text for each card type.

### Sprint 2.3: Live website and V2 testing

Depends on: Sprint 2.2.

- [ ] Server-sent events endpoint forwarding NOTIFY messages.
- [ ] Game page Live tab: win probability chart with both lines and biggest swings marked; analyst feed.
- [ ] Running box score; live cards on Tonight (score, clock, both probabilities).
- [ ] Test on a real game night; deploy; owner manual testing.

Done when: the live game page works through a full game night.

---

## V3: NBA add-ons and season simulator

### Sprint 3.1: Player impact

Depends on: V1. Uses 2021-22 onward (substitutions verified clean).

- [ ] Stints from substitutions; five players per team check.
- [ ] Regularized adjusted plus-minus with a box-score prior and recency weights; ranges.
- [ ] Replace box-score values with impact ratings in team starting ratings and absence adjustments; keep only if the rolling exams improve.
- [ ] Player page Impact tab (offense, defense, ranges, rank, on/off splits); impact column in rosters and Players (default sort for the NBA); 500-minute minimum for rankings.

### Sprint 3.2: Shot quality, lineups, and play style

Depends on: Sprint 3.1 for lineups.

- [ ] Shot quality: expected points by court zone and shot type from ESPN play-by-play (free throws excluded); shot-making over expected for teams and players.
- [ ] Player page Shooting tab (shot chart, zone table); shot-making over expected on the final game page.
- [ ] Shot quality as a predictor input; keep only if the rolling exams improve.
- [ ] Lineups: five-man groups, two- and three-player pairings, on/off; small samples labeled and blended with impact ratings. Team page Lineups tab.
- [ ] Play style: ESPN-based profile (rim, midrange, three; drives vs. pull-ups; assisted vs. unassisted) with league percentiles. Player page Play style tab.
- [ ] Lineup and shot quality insight cards in the live feed.

### Sprint 3.3: Season simulator and V3 testing

- [ ] Season simulator: 10,000 runs, rating uncertainty, NBA play-in and tiebreaker rules; re-run after every final; history stored.
- [ ] Season screen (odds table and odds-over-time chart); season odds on the team page Overview.
- [ ] Add-on contribution report (with and without each add-on) in the report card and admin panel.
- [ ] Tests, deploy, owner manual testing.

Done when: each add-on has a rolling exam result, and season odds update after every final.

---

## V4: NCAA core (Division I)

### Sprint 4.1: NCAA data

Depends on: V1 (pipeline), V1.1 (admin absences entry).

- [ ] Backfill Division I seasons 2021-22 to 2025-26 with the non-Division I filter.
- [ ] Conferences per season (`team_seasons`).
- [ ] Quality report; review with the owner (player box and play-by-play are expected to exceed 2%, handled by the gate).
- [ ] College substitution format ("subbing in" and "subbing out").
- [ ] Worker jobs extended to the NCAA (busy Saturdays: scoreboard for all games).

### Sprint 4.2: NCAA models

Depends on: Sprint 4.1.

- [ ] Fit player values, starting ratings (freshmen and newcomers at a default), team ratings, and the predictor separately for college (two halves, second-half garbage time).
- [ ] Hand-entered absences adjust predictions by the player's share of minutes and production.
- [ ] Rolling exams for college; review against the accuracy target.
- [ ] Season simulator: conference regular-season titles and tournament bids.

### Sprint 4.3: NCAA website and V4 testing

Depends on: Sprint 4.2.

- [ ] NBA/NCAA switch enabled.
- [ ] Conference scope on Teams and Players; Division I scope; college roster sorted by points per game.
- [ ] Tonight: conference filter and team search.
- [ ] NCAA leaderboard minimums for "Qualified only" (confirm the official values).
- [ ] ESPN's live win probability line on college live games; live analyst feed where play-by-play is reliable.
- [ ] Tests, deploy, owner manual testing.

Done when: every Division I game is predicted, locked, and graded daily.

---

## V5: March

### Sprint 5.1: Field projection

Depends on: V4.

- [ ] Decide with the owner whether to load older tournament selection history for this model only.
- [ ] Read the NET ranking and quadrant records from the NCAA's site; fall back to our own ratings if reading fails.
- [ ] Selection and seeding model; rolling exam on past selections.
- [ ] Daily projected seeds, last four in, first four out, bubble chances.

### Sprint 5.2: Bracket simulator and our NCAA live win probability

- [ ] Bracket structure (including the First Four) once announced.
- [ ] Bracket simulator at neutral sites with rating uncertainty; re-run after every round.
- [ ] Likely upsets: lower seed's chance at least 10 percentage points above the historical rate for that seed matchup.
- [ ] Our NCAA live win probability model (trained separately), shown next to ESPN's.

### Sprint 5.3: March screen and V5 testing

- [ ] March tab (NCAA, in season): field projection, round-by-round odds, likely upsets.
- [ ] Tests, deploy before Selection Sunday, owner manual testing.

Done when: bracket odds are live when the bracket is announced.

---

## Later

Not scheduled. Each becomes its own sprint when picked up.

- [ ] Compare screen (two teams or two players side by side).
- [ ] Share links with preview images (cards, charts, game pages).
- [ ] Admin actions: start a backtest, promote a challenger, re-run a failed job.
- [ ] Sign-in, if the open link becomes a problem.
- [ ] Backups, including an off-VPS copy of the locked prediction history.
