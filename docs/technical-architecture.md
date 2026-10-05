# Basketball Analytics App: Technical Architecture

Last updated: 2026-10-05 (end of architecture phase, pending owner sign-off)
Companion document: `feature-spec.md` (feature numbers below refer to it)

## 1. Context for a new session

This document is the architecture for the app described in the feature spec. Nothing has been built yet.

- **Project phases:** design (complete), architecture (this document), then build, testing, and deploy. Do not skip phases. Build starts only after the owner signs off on this document.
- **Next step:** build V1 (section 16). Deploy the data pipeline to the VPS early enough to run a dress rehearsal on NBA preseason games (section 14).
- **Target:** V1 launches at the start of the NBA regular season. The docs track scope by version, not by date.
- **Decisions in this document were made with the owner** during the design and architecture phases. Where a choice is still open, it is listed in section 18.

## 2. Requirements and constraints

| Requirement or constraint | Consequence |
| --- | --- |
| Two leagues, one engine | League-specific rules sit behind a common interface. Models are parameterized by league and fit separately per league. |
| Core runs without add-ons | Add-on outputs are optional model inputs. A capability matrix says which features each league supports. |
| Near-realtime during games (V2) | A live poller, live state in Postgres, and a push channel to browsers. |
| Small samples | Shrinkage toward priors, with uncertainty ranges on every rating. |
| Predictions graded honestly | Predictions are locked 30 minutes before tip-off and are append-only. Only locked predictions are graded. |
| Small audience (a friend group) | One server. Favor simple operations over scale. |
| Runs on the owner's existing VPS | 2 CPU cores, 7.8 GB RAM (about 6.5 GB free; other services already run there), 136 GB free disk, Debian 13, Podman (no Docker). The app should stay under about 3 to 4 GB of RAM at peak, and heavy jobs run at low CPU priority. |
| Free data by default | ESPN's public site API is the only data source. |
| NCAA is Division I only | Games against non-Division I opponents are dropped at ingestion. |
| Open link, no sign-in | Public read-only routes. One admin login (V1.1). |
| Unofficial data source | Keep every raw response, validate every game, and show "data delayed" when updates stop. |
| Language | Python only for the backend, worker, and models. TypeScript for the website. |

## 3. Technology stack (approved)

| Layer | Choice | Reason |
| --- | --- | --- |
| Backend language | Python 3.13 | Debian 13's version. The modeling libraries are Python. |
| Data and models | pandas, numpy, scipy, scikit-learn | Ratings, simulations, and win probability are small statistical models. LightGBM may be added later if a candidate needs it. |
| API | FastAPI with uvicorn | Async, typed, supports server-sent events for V2 |
| Database | Postgres 17 | All data fits comfortably (estimated 5 to 8 GB for 5 seasons of both leagues). `pg_trgm` for fuzzy search. `LISTEN/NOTIFY` for live updates. |
| Background work | One Python worker process with a built-in scheduler (APScheduler) | Daily jobs, injury checks, prediction locks, and game-final processing in one place |
| Website | React + TypeScript, built with Vite into static files | No server-side rendering needed. Static files use almost no server memory. |
| Website libraries | TanStack Table (sorting), TanStack Virtual (virtual scrolling), Recharts (charts) | Sortable tables that stay fast with thousands of rows |
| Internal web server | Caddy, inside the app stack, on `127.0.0.1:8080` | Serves the website files and forwards `/api` to the API |
| Public HTTPS | The owner's existing Caddy | Handles `hoops.<owner's domain>` and forwards to the app stack |
| Containers | Podman with Quadlet (systemd units) | Services start on boot and restart on failure. Built into Debian 13. |
| CI | GitHub Actions | Lint and tests on every push |

Not used, by decision: Redis, Parquet/DuckDB, Next.js, Go, Docker, `nba_api`.

## 4. Data source: ESPN

### 4.1 Endpoints

`{league}` is `nba` or `mens-college-basketball`.

| Purpose | Endpoint |
| --- | --- |
| Games for one date | `site.api.espn.com/apis/site/v2/sports/basketball/{league}/scoreboard?dates=YYYYMMDD` (college also needs `&groups=50&limit=500`) |
| One game: box score, plays, injuries, lines, win probability | `site.web.api.espn.com/apis/site/v2/sports/basketball/{league}/summary?event={id}` |
| Division I team list | `site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?groups=50&limit=500` |
| Open and close betting lines | `sports.core.api.espn.com/v2/sports/basketball/leagues/{league}/events/{id}/competitions/{id}/odds` |
| Injuries (league-wide) | ESPN injuries endpoint for each league; confirm the exact path during build |

### 4.2 Verified behavior (checks run 2026-10-04 and 2026-10-05)

| Topic | Finding |
| --- | --- |
| Coverage | Box scores, play-by-play with shot coordinates, injuries, betting lines, and ESPN's win probability for both leagues. ESPN's college win probability exists from about 2017-18. |
| History | NBA team box scores are complete from 2016-17 (we use 2021-22 onward). College box scores and play-by-play go back to at least 2005-06. |
| Date ranges | Not supported: one scoreboard request per date. |
| Play order | The order of the `plays` array is game order. `sequenceNumber` is not chronological. The game clock never runs backward within a period in sampled data. |
| Running score field | Unreliable on some plays: a non-scoring play can carry a stale score, and plays after a late-inserted play can carry stale scores. The final score is correct. Rebuild the running score from scoring plays. |
| Free throws | In older seasons, made free throws have `scoreValue` 0. Treat a made free throw as 1 point. |
| Shot coordinates | Missing values use large negative sentinels. Treat them as missing. |
| Substitutions | NBA: "X enters the game for Y", consistent in sampled games from 2018-19 on. College: separate "subbing in" and "subbing out" events; present in recent seasons, absent in some older ones. |
| Division I | 362 Division I teams. Non-Division I opponents are common in November and easy to identify. |
| Betting lines | For a recent game, the summary line equalled the core API's labeled closing line. Game summaries have no lines at all for NBA 2023-24 and 2024-25 (and about a fifth of 2025-26); the core odds endpoint has labeled closing lines for those games, so the backfill fills them from there. 2021-22 and 2022-23 lines come from the summaries, without open/close labels. From launch, we record lines ourselves near tip-off. |
| NBA.com | Out of scope, by decision. |

### 4.3 Data quality sample (2026-10-05)

About 25 random completed games per season, per league. Rough rates; the build runs the checks on every game.

| League and seasons | Team-level checks | Player box | Play-by-play |
| --- | --- | --- | --- |
| NBA 2016-17, 2017-18 | 8% and 16% fail (team box missing) | Same games | Fine |
| NBA 2018-19 to 2025-26 | 0 of 200 fail | 0 of 200 | 1 of 200 |
| College 2016-17 to 2025-26 | 0 of 250 fail | About 6% of games have player rows missing points | About 10% missing or incomplete |

**Decision:** both leagues load seasons from **2021-22** onward (after the bubble seasons), five seasons in total.

### 4.4 Ingestion rules

1. Save every raw response to disk before parsing, keyed by endpoint, parameters, and fetch time.
2. Rate-limit ESPN requests (about 1 to 2 per second at most) and retry with exponential backoff.
3. Match records by ESPN IDs, so every job can be re-run without duplicates.
4. For the NCAA, drop games where either team is not Division I.
5. Order plays by their position in the `plays` array.
6. Run the quality checks (section 8) on every game as it is stored.
7. Raw files for finished games are kept indefinitely (estimated about 2 GB). From V2, live polling files are deleted after 7 days.

## 5. System components

```
                    ESPN (only data source)
                            |
               +------------v-------------+
               |  WORKER (low priority)   |
               |  scheduler + jobs        |
               +--+--------------+--------+
       raw files  |              |  clean data, ratings, predictions
                  v              v
          /data/raw (disk)   POSTGRES --NOTIFY--+   (V2: live updates)
                                 |              |
                                 v              v
                            API (FastAPI) -- server-sent events (V2)
                                 |
                  App Caddy on 127.0.0.1:8080 (website files + /api)
                                 |
                 Owner's existing Caddy (HTTPS, hoops.<domain>)
                                 |
                              Browsers
```

**Services (one Quadlet unit each):** `postgres`, `api`, `worker`, `web` (the app's internal Caddy).

**Worker jobs:**

| Job | When | Does |
| --- | --- | --- |
| Schedule sync | Every morning, then every 3 hours | Scoreboards for the past 2 and next 7 days: new games, time changes, postponements |
| Injury sync | Every 15 minutes on game days | ESPN injury statuses. A change in "Out" players writes a new, unlocked prediction. |
| Reference sync | Daily, 4:05 AM Eastern (before overnight corrections) | Teams, conferences, rosters and player details |
| Game watcher | Every minute while any game is near tip-off or live | 30 minutes before tip-off: lock the prediction and record the betting line. On final: store the box score and plays, run quality checks, grade every finished game, then refit ratings and refresh upcoming predictions once, and refresh the precomputed stat tables. Each game is handled on its own: locking needs nothing from ESPN, and a game that fails to load is retried at most three times, then left to the overnight job. From V3 also re-run the season simulator. |
| Overnight corrections | Nightly, 4:15 AM Eastern | Re-pull the previous day's games and any final that failed to load, grade any locked prediction still ungraded, refit, prune superseded raw responses older than 7 days |
| Live poller (V2) | Every ~10 seconds per live game | Live state, both win probability lines, insight cards, NOTIFY to the API |
| Backfill, backtests, rolling exams | Run by hand | Load past seasons, evaluate candidate models |

**Cross-cutting rules:**
- Every job run is logged in `job_runs` (start, end, status, error, and per-game errors in its details).
- If the worker loses its database connection it exits, and systemd restarts it with a fresh one. The API exposes the last successful update; the website shows "Data delayed, last updated …" when it is stale.
- Times are stored in UTC and shown in the viewer's local time zone.
- Heavy jobs run at low CPU priority so the website stays responsive.

**Memory budget at peak:** Postgres about 1 GB, API about 0.3 GB, worker 1 to 2 GB while fitting models, internal Caddy about 50 MB. Total about 2.5 to 3.5 GB.

## 6. League abstraction

One ESPN client, parameterized by league, behind a small interface. League rules (periods, period length, overtime length, shot clock, garbage-time window) and a capability matrix live in one module.

| Capability | NBA | NCAA |
| --- | --- | --- |
| Box scores, play-by-play | Yes | Yes (player rows and plays occasionally incomplete) |
| Live play-by-play | Yes | Partial |
| ESPN live win probability | Yes | Yes, from about 2017-18 |
| Injury feed | ESPN statuses | No (manual entry, V1.1 admin) |
| Substitutions reliable enough for lineups | Yes | Recent seasons only; lineups not planned |
| Tracking, defender distance, matchups | No (NBA.com out of scope) | No |
| Tournament bracket and seeds | No | Yes |

Features check the matrix and hide themselves when a capability is missing.

## 7. Data model

Postgres. ESPN IDs are stored directly on teams, players, and games (no crosswalk table; ESPN is the only source).

| Group | Tables | Notes |
| --- | --- | --- |
| Reference | `teams`, `team_seasons`, `players`, `roster_entries` | `team_seasons` holds each team's conference per season (college realignment). Logo and headshot links from ESPN. |
| Game data | `games`, `team_game_stats`, `player_game_stats`, `plays`, `injury_snapshots`, `betting_lines` | `games` holds the Division I flag, quality status per check, and exclusion reason. `betting_lines` records the line kind: captured by us before tip-off, ESPN labeled close, or timing uncertain. |
| Model outputs | `player_values`, `team_ratings`, `team_sub_ratings`, `predictions`, `prediction_grades` | Full rating history for trend charts. `predictions` is append-only (a database trigger rejects updates and deletes) with a locked flag. Every output records its model version. |
| Model management | `model_versions`, `training_runs`, `exam_results` | Each model version's settings and role (champion, challenger from V1.1), each training run, and each rolling exam result per candidate. Feeds the admin panel. |
| Precomputed for screens | `team_season_stats`, `player_season_stats` | Postgres materialized views, refreshed (without blocking readers) whenever a game goes final. Traded players get one row per team plus a combined row. `screen_refreshes` records the last refresh so the API cache knows when to rebuild. |
| Operations | `job_runs`, `data_quality_issues` | Data freshness, job history, quality reports |
| Backtests | `backtest_predictions` | The champion's walk-forward predictions for past seasons. Feeds the report card's backtest section and fills predictions on past game pages. |

| Player projections (V1.1) | `projection_sets`, `player_projections`, `projection_grades` | Append-only like `predictions`: a new set when anything changes, locked with the prediction 30 minutes before tip-off; the locked set is graded after the game (mean error per stat, share inside the range). |

**Added in later versions:** admin login, `manual_absences`, shadow predictions (V1.1); `live_states`, `live_win_prob` (ours and ESPN's), `insights` (V2); `player_impact`, `lineup_stats`, `shot_quality`, `play_style`, `sim_runs`, `sim_results` (V3); `field_projection`, `brackets`, `bracket_sim_results` (V5).

## 8. Data quality gate

Every game is checked when stored. There are three groups, and each one gates only what depends on it:

| Group | Checks | If a game fails |
| --- | --- | --- |
| Team-level | Team box score present; team box points (2 × FGM + 3PM + FTM) equal the final score | Excluded from ratings, predictions, and backtests. Still shown on the site. |
| Player box | Player points add up to team points | Excluded from player averages and player values. Team data still used. |
| Play-by-play | Plays present; scoring plays add up to the final score; game clock never runs backward within a period; (where substitutions exist) five players on the floor per team | Garbage-time removal falls back to the whole game. Excluded from win probability training and lineup data. |

Each season gets a quality report per group. If more than 2% of a season's games fail a group, the owner is told before that data is used. College player box and play-by-play are expected to exceed 2%; the gate above handles that.

## 9. Models

Every model has a version string, and outputs record the version that produced them.

### 9.1 V1 models

| Model | Method |
| --- | --- |
| Player box-score values | Learn from past seasons how box-score rates per 100 possessions (shooting efficiency, assists, rebounds, turnovers, steals, blocks) relate to team scoring margin. Score every player with those weights, shrunk toward average by minutes played. Rookies and players without history get a modest below-average default. |
| Team starting rating | Sum of current players' values weighted by expected minutes share. Fallback: last season's rating pulled about one third toward average. |
| Team ratings | Each game gives two observations (each offense against the other defense). Points per 100 possessions = league average + offense strength − opposing defense strength + home court. Fit by ridge regression, refit after every final. The starting rating acts as pseudo-games that fade as real games accumulate. Recent games weighted more. Home-court advantage estimated per season. Garbage time removed: a lead of 25+ points in the last 6 minutes, or 15+ in the last 3 (fourth quarter in the NBA, second half in college). Opponent 3-point and free-throw percentage regressed toward normal before computing points allowed. Ranges from the fit's uncertainty. |
| Sub-ratings | The same opponent-adjusted fit, run per metric |
| Game predictor | Expected possessions from both teams' pace; expected points from offense against defense; adjustments for home court, rest, travel distance, and "Out" players (box-score values until V3). Win probability from the expected margin and the spread of real results around predictions (fitted; about 12 points in the NBA), widened when ratings are uncertain. Locked 30 minutes before tip-off. |
| Matchup explainer | Rule: compare each offense skill with the opposing defense skill, convert the gap to points, show the top two or three. |

### 9.2 Evaluation protocol

- **Walk-forward:** every prediction in a backtest uses only games before that day.
- **Seasons:** 2021-22 is a warm-up season (it provides player values and starting ratings). 2022-23 through 2025-26 are scored.
- **Rolling exams:**

  | Round | Candidates compete on | Winner examined on |
  | --- | --- | --- |
  | 1 | 2022-23 | 2023-24 |
  | 2 | 2022-23 to 2023-24 | 2024-25 |
  | 3 | 2022-23 to 2024-25 | 2025-26 |

- **Candidates:** each round runs about 20 to 50 candidate methods (recency weighting, starting-rating strength, luck discounting, home-court handling, and similar settings). The winner is chosen using the tuning seasons only, never the exam. Ties within noise go to the simpler method. A blend of the top three is tested as an extra candidate and used if it wins.
- **Score:** the average across the three exams. A change is kept only if it improves that average.
- **Final model:** tuned on all four scored seasons and used live. The live season is the fresh exam.
- **Metrics:** log loss, Brier score, accuracy, calibration, mean absolute error on margin and total.
- **Benchmarks:** the naive baseline (home team favored at its historical win rate), the model without the roster starting point, and the betting market (bookmaker margin removed).
- **Accuracy target:** over a season, within 1.5 percentage points of the market's accuracy and within 0.01 of its log loss, and clearly better than the naive baseline. The app launches regardless; the report card and admin panel show the gap.
- **Absences in backtests:** past injury reports are not available, so backtests treat a rotation player who did not play as "Out". Live predictions use the injury report.
- **Sanity check:** final-season ratings broadly agree with public ratings such as ESPN's BPI.
- **Shadow models (V1.1):** the champion makes public predictions; a few challengers predict every game quietly and are graded. At fixed checkpoints (for example monthly), a challenger replaces the champion only if it has been clearly better. Switches are noted on the report card.

### 9.3 Later versions

| Version | Model | Method |
| --- | --- | --- |
| V1.1 | Single-game player projections | Minutes model times per-minute rates, shrunk toward average for small samples, adjusted for opponent, pace, rest, and absent teammates. As built: minutes are recency weighted from last season's minutes per game; each team's available players share 241 minutes, absent teammates' minutes going to them and surplus minutes coming off the deep bench first; starters lose minutes when a blowout is likely. Live, each player's minutes are weighted by his chance of playing (his appearance rate, starting from a guess based on last season's minutes), since who plays is not known before the game. Scoring stats scale with the team's predicted points, other stats with the predicted pace. 10th to 90th percentile ranges are fitted on past seasons and calibrated so that, shown as whole numbers, they hold 80% of results. Rolling exams pick the settings; projections beat players' season averages by about 5.5% (minutes about 13%, points about 5%). |
| V2 | Live win probability | Trained on past play-by-play: score difference, time remaining, possession, pregame win probability, foul and bonus state. Calibrated. One model per league. |
| V2 | Insight detectors | Rules with significance thresholds; developing events update their card in place |
| V3 | Player impact | Regularized adjusted plus-minus over stints, with a box-score prior. Replaces box-score values in starting ratings and absences. |
| V3 | Shot quality | Expected points by court zone and shot type from ESPN play-by-play; free throws excluded |
| V3 | Lineups, play style | Stints aggregated by lineup and pairings; play-style percentiles from shot locations and types |
| V3, V4 | Season simulator | 10,000 Monte Carlo runs, re-run after every final. NBA play-in and tiebreakers; NCAA conference regular-season titles and bids. |
| V5 | Field projection, bracket simulator | Selection and seeding model trained on past selections (older selection history may be loaded for this model only); bracket simulation at neutral sites with likely upsets |

## 10. Live pipeline (V2)

1. The worker starts a poller for each game shortly before tip-off.
2. Every ~10 seconds it fetches the summary, takes plays after the last processed position, and normalizes them.
3. Live state (score, clock, possession, fouls, players on the floor, running box score) is written to `live_states`.
4. Our live win probability scores the new state. ESPN's win probability for the same moment is stored next to it.
5. Insight detectors run and create or update cards.
6. The worker sends a Postgres NOTIFY; the API forwards it to subscribed browsers over server-sent events.
7. At final, the poller stops and the game-final job runs. The overnight job re-pulls the official data.

The feed delay behind the broadcast is to be measured on a real game before V2 is built.

## 11. API

Read-only JSON under `/api`. `{league}` is `nba` or `ncaam`. Team and player endpoints take `season`.

| Endpoint | Feeds | Version |
| --- | --- | --- |
| `GET /api/{league}/meta` | Seasons, conferences, last successful data update | V1 |
| `GET /api/{league}/games?date=&tz=` | Tonight | V1 |
| `GET /api/{league}/games/{id}` | Game page | V1 |
| `GET /api/{league}/teams?season=` | Teams table | V1 |
| `GET /api/{league}/teams/{id}?season=` | Team page | V1 |
| `GET /api/{league}/players?season=&scope=&team=&conf=` | Players table | V1 |
| `GET /api/{league}/players/{id}?season=` | Player page | V1 |
| `GET /api/{league}/search/teams?q=`, `/search/players?q=` | Typo-tolerant search (`pg_trgm`) | V1 |
| `GET /api/{league}/report-card?season=` | Report card, including backtests | V1 |
| `GET /api/nba/games/{id}/projections` | Player projections, projected vs. actual | V1.1 |
| `/api/admin/*` | Admin panel (login, data health, model management, NCAA absences) | V1.1 |
| `GET /api/{league}/games/{id}/live` | Server-sent events: state, win probability (ours and ESPN's), insight cards | V2 |
| `GET /api/{league}/season/odds` | Season simulator | V3 |
| `GET /api/ncaam/march/*` | Field projection, bracket odds, likely upsets | V5 |

- Tables are sent whole for the chosen scope (up to about 5,000 college players, about 100 KB compressed) and sorted and filtered in the browser.
- Responses are cached in memory and invalidated when data changes (a game going final, an injury update).

## 12. Website

- React + TypeScript single-page app, built with Vite into static files.
- Screens and behavior as in feature spec section 6, including the approved wireframe.
- Tables: TanStack Table for sorting, TanStack Virtual so only visible rows are drawn. Sticky name column and header on phones.
- Dark mode by default. Times in the viewer's local time zone. Team logos and player headshots from ESPN image links.
- **Performance budget** (mid-range phone): first load under 2 seconds on 4G; sorting or switching scope under 100 ms; smooth scrolling on the full Division I players table.

## 13. Deployment and operations

- **Code** lives in the owner's GitHub repository.
- **Deploy:** the owner runs `./deploy.sh` on the VPS. It pulls the latest code, builds the images with Podman, applies database migrations, and restarts the services. Deploy never touches the owner's existing services.
- **Secrets and settings:** a `.env` file on the VPS, never committed.
- **Services:** Quadlet units for `postgres`, `api`, `worker`, and `web`. Only `web` is reachable, on `127.0.0.1:8080`.
- **Public access:** the owner's existing Caddy (probably in a container) serves `hoops.<owner's domain>` with HTTPS and forwards to the app. If that Caddy runs in a container, it reaches the app through a shared Podman network or the host address rather than `127.0.0.1`; confirm with `podman ps` at deploy time. The owner adds a DNS record for the subdomain.
- **Admin panel (V1.1):** view only. Shows models (versions, settings, champion and challengers), rolling exam results, live accuracy against the target, training and job history, and data quality reports. Training runs and exam results are recorded from V1, so the history is complete. Actions (start a backtest, promote a challenger, re-run a job) come later.
- **Monitoring:** the "data delayed" note on the site, and the admin panel from V1.1. No email or phone alerts at launch.
- **Backups:** none at launch, by decision. To be revisited. Note: locked prediction history cannot be recreated from ESPN.

## 14. Testing strategy

| Layer | What is tested | How |
| --- | --- | --- |
| Parsing | ESPN responses parse correctly, including every known quirk (section 4.2) | Saved real ESPN responses as test fixtures |
| Rules | Division I filter, garbage time, 30-minute lock, "Out only" injury rule, which games count | Small hand-made cases |
| Math | Ratings recover known strengths from simulated seasons; probabilities and ranges are sensible | Simulated data |
| Database | Predictions cannot be edited or deleted; re-running jobs never duplicates | Real Postgres in tests |
| Full data check | Quality gate on every game of every loaded season; the 2% report | After the backfill |
| Models | Rolling exam results against the accuracy target; sanity check against public ratings | Backtest reports |
| API | Correct data and response times for every endpoint | Automated tests |
| Website | Simple Playwright click-through tests at phone and desktop sizes, plus the performance budget. The owner tests manually beyond that. | Playwright |
| Dress rehearsal | The full system on real NBA preseason games on the VPS: schedule sync, lock, game final, ratings update, report card, and a simulated ESPN outage | VPS, before opening night |

GitHub Actions runs lint and tests on every push.

## 15. Repository layout

```
/backend
  pyproject.toml
  hoops/
    leagues.py        league rules and capability matrix
    espn/             client, raw store, parsing
    quality/          data quality checks
    db/               migrations, queries
    models/           player_values, ratings, sub_ratings, predictor, explainer
    evaluation/       walk-forward backtests, rolling exams, metrics
    jobs/             scheduler and job definitions
    api/              FastAPI app and routers
  tests/
/web                  React + Vite + TypeScript
/deploy               Quadlet units, Containerfiles, internal Caddyfile, deploy.sh, .env.example
/.github/workflows    CI
```

## 16. Versions

Scope by version, from feature spec section 7.

| Version | Scope | Done when |
| --- | --- | --- |
| V1 | ESPN pipeline on the VPS; backfill 2021-22 onward for the NBA; quality gate; player values, team ratings, sub-ratings, game predictor, matchup explainer; rolling exams; screens: Tonight, game page (Preview, Box score, final result), Teams, team page, Players, player page (Overview, Game log), Report card; season picker | Friends can open the app on opening night; rolling exam results exist; dress rehearsal passed |
| V1.1 | Player projections, projected vs. actual, admin panel (view only, with model management), shadow models | Projections graded for real games; admin panel shows training history |
| V2 | Live poller, our and ESPN's win probability, analyst feed, running box score, server-sent events | Live game page works on a game night |
| V3 | Player impact, shot quality, lineups, play style, season simulator | Each add-on has a rolling exam result |
| V4 | NCAA Division I backfill from 2021-22, ratings, predictor, core screens, ESPN live win probability line, season simulator | College predictions graded daily well before March |
| V5 | Field projection, bracket simulator with likely upsets, our NCAA live win probability | Ready when the bracket is announced |
| Later | Compare screen, share links, admin actions, sign-in if needed, backups | |

## 17. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| ESPN changes or removes its unofficial endpoints | Data stops | Raw responses stored; parsing isolated in one module; "data delayed" note; daily job failures visible in the admin panel |
| ESPN rate-limits or blocks the VPS | Updates slow or stop | Polite request pacing; cached raw responses; backfill run once |
| Data quirks beyond those found | Wrong numbers | Quality gate on every game; per-season reports |
| Old betting lines are not closing lines | Backtest market comparison less precise | Label line kind; grade against our own captured lines from launch |
| Only four scored seasons | Model choices are less certain | Few tuning settings, rolling exams, ties go to the simpler method, live season as the fresh exam |
| VPS has 2 cores shared with other services | Slow site during heavy jobs | Low CPU priority for heavy jobs; precomputed tables; in-memory API cache |
| No backups at launch | A disk failure loses locked prediction history | Revisit backups soon after launch |
| Open link | The app could spread beyond the friend group | Do not publicize it; add sign-in if needed |
| Live feed delay (V2) | Insights arrive after the broadcast | Measure before V2; set expectations in the interface |

## 18. Open questions

1. App name (owner, any time).
2. The exact ESPN injuries endpoint path (check during build).
3. Live feed delay behind the broadcast (measure before V2).
4. Whether the existing Caddy runs in a container, and how it reaches the app (check at deploy).
5. Backups (revisit after launch).
6. For V5: load older tournament selection history for the field projection model only?
7. NCAA "Qualified only" minimums (75% of games; 5 field goals, 2.5 threes, 2.5 free throws made per game) to be reconfirmed against the official NCAA rules before V4.

## 19. Sources

- ESPN site API behavior: checked directly on 2026-10-04 and 2026-10-05 (section 4.2 and 4.3).
- ESPN unofficial API guides: https://zuplo.com/blog/2024/10/01/espn-hidden-api-guide and https://sportsapis.dev/espn-api
- ESPN college scoreboard parameters: https://github.com/pseudo-r/public-espn-api
- NBA league leader minimums: https://www.nba.com/stats/help/statminimums
