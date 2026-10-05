# Basketball Analytics App: Feature Spec

Last updated: 2026-10-05 (updated at the end of the architecture phase)
Companion document: `technical-architecture.md`

## 1. Context for a new session

This document records the product design. The design phase is complete, and the architecture phase updated it with its decisions (features 10, 13, and 14, seasons loaded, shadow models, admin model management, accuracy target). The next phase is build, then testing and deploy. Do not skip phases.

**What the app is.** A basketball prediction and analytics engine for fans who watch for fun. It is built on the NBA first, then extended to NCAA Division I men's basketball for March Madness.

**Who it is for.** The owner and a group of sports-loving friends. They watch games together and argue about them. They do not bet and do not play fantasy basketball.

**Decisions.**

| Decision | Detail |
| --- | --- |
| Sport and order | NBA first, NCAA Division I men's second. The NCAA version should be ready before the tournament in March. |
| Purpose | Analytics and predictions. The owner wants to refine the models on NBA data, then apply them to NCAA data, where data is thinner but predictions are more valuable. |
| Structure | A shared core that runs in both leagues, plus league-specific add-ons. If college lacks the data for a feature, the feature is NBA only. |
| Platform | Responsive website. Works on phones and desktops. |
| Access | Open link, no sign-in. Authentication can be added later if needed. The owner gets an admin login for admin actions only. |
| Social layer | None. The app is read-only analytics. |
| Data budget | Free data by default. Paying for data needs a strong case. |
| Hosting | The owner's existing VPS. Everything runs there; no home machine. |
| College scope | Division I only. Games against non-Division I opponents are ignored everywhere: ratings, predictions, and player stats. |
| Women's basketball | Out of scope. |
| Betting | No betting features. Betting lines are used only as a benchmark on the report card. They never appear on game cards. |
| Realtime | Near-realtime during games: live win probability and live insights. |
| Look | Dark mode by default. Times in the viewer's local time zone. Team logos and player headshots shown. |
| Name | Not chosen yet. |
| Rejected ideas | "Switch to this game" alerts, fantasy tools, a standalone prop-bet tracker, a standalone injury feature, a standalone schedule-strength feature, a standalone upset finder, natural-language stat questions, percentile color coding, advanced stat filters. |
| Cut | Feature 15 (roster turnover priors) and feature 18 (bracket builder) in design; feature 13 (defensive matchups) in architecture, because NBA.com data is out of scope. See section 5.4. |
| Data source | ESPN only. NBA.com (`nba_api`) is out of scope. |
| Seasons loaded | 2021-22 onward for both leagues (after the bubble seasons). |
| Naming | Do not use the term "luck index". Use "shot-making over expected". Releases are called versions: V1, V1.1, V2, and so on. |

**Still open.** See section 9.

## 2. Design rules

1. **The core runs on its own.** Add-ons feed in as optional inputs. The same game predictor works with or without them.
2. **Small samples are expected.** Every rating starts from a prior estimate, is pulled toward the average until there is enough evidence, and shows an uncertainty range.
3. **Every prediction is locked 30 minutes before tip-off and graded afterwards.** Only the locked prediction is graded. A card may show a newer number after a late change, labeled "updated after lock, not graded". Accuracy is public inside the app.
4. **Each add-on must earn its place.** The NBA predictor is run with and without each add-on to measure what it contributes. A change to any model ships only if it improves accuracy in the rolling exams on past seasons (architecture doc, section 9.2).
5. **Accuracy target.** Over a season, the game predictor should be within 1.5 percentage points of the betting market's accuracy and within 0.01 of its log loss, and clearly better than a naive baseline. The app launches regardless; the report card shows the gap.
6. **"Correct" is measured, not asserted.** Ratings and predictions are judged by how well they predict future games, how they compare with the betting market, and whether they broadly agree with established public ratings.
7. **Data freshness is visible.** When a data source is late or down, screens say so ("Data delayed, last updated 7:42 PM") instead of quietly showing old numbers.

## 3. Feature availability at a glance

15 features: 8 core, 5 NBA add-ons, 2 NCAA add-ons. Features 13, 15, and 18 were cut.

| # | Feature | Group | NBA | NCAA | First version |
| --- | --- | --- | --- | --- | --- |
| 1 | Team ratings (overall plus sub-ratings) | Core | Full | Full, wider uncertainty | V1 (NBA), V4 (NCAA) |
| 2 | Game predictor | Core | Full, absences from ESPN injury statuses | Full, absences entered by hand | V1 (NBA), V4 (NCAA) |
| 3 | Matchup explainer | Core | Full | Full | V1 (NBA), V4 (NCAA) |
| 4 | Live win probability | Core | Ours and ESPN's side by side | ESPN's in V4, ours added in V5 | V2 (NBA) |
| 5 | Live analyst feed | Core | Full | Where live play-by-play is reliable | V2 (NBA), V4 (NCAA) |
| 6 | Season simulator | Core | Playoff, seed, and title odds | Conference title and tournament-bid odds | V3 (NBA), V4 (NCAA) |
| 7 | Player profiles | Core | Full | Season-level only | V1 (NBA), V4 (NCAA) |
| 8 | Model report card | Core | Full | Full | V1 (NBA), V4 (NCAA) |
| 9 | Single-game player projections | NBA add-on | Yes | No | V1.1 |
| 10 | Shot quality and shot-making over expected | NBA add-on | Yes (ESPN shot location and type; no defender distance) | No | V3 |
| 11 | Player impact ratings | NBA add-on | Yes | No | V3 |
| 12 | Lineup analysis | NBA add-on | Yes | No | V3 |
| 14 | Play-style profiles | NBA add-on | Yes (ESPN-based) | No | V3 |
| 16 | Tournament field projection | NCAA add-on | No | Yes | V5 |
| 17 | Bracket simulator (includes likely upsets) | NCAA add-on | No | Yes | V5 |

## 4. Use cases

**Primary actor:** a fan who watches basketball with friends. There is one secondary actor, the owner, who also maintains the models.

### UC-1: Decide what to expect from tonight's games

- **Goal:** know who is favored in each game and why.
- **Trigger:** the fan opens the app on a game day.
- **Flow:**
  1. The Tonight screen lists every game with win probability, expected margin, expected total, and a likely range.
  2. The fan taps a game and reads the matchup explainer.
  3. For NBA games, the fan sees who is ruled out and how that moved the prediction.
- **Outcome:** the fan has a prediction and a reason for it before tip-off.
- **Features used:** 1, 2, 3, and for the NBA 9 (from V1.1).

### UC-2: Follow a game live

- **Goal:** understand what is happening in a game beyond the score.
- **Trigger:** a game the fan is watching tips off.
- **Flow:**
  1. The fan keeps the live game page open as a second screen.
  2. The win probability chart updates every few seconds, showing our line and ESPN's.
  3. Insight cards appear in the analyst feed, such as a scoring run or a lineup that is struggling. A card that is still developing, like a run, updates in place.
  4. (Later version) The fan shares a card or the chart to the group chat.
- **Outcome:** the fan has live context to talk about with friends.
- **Features used:** 4, 5.

### UC-3: Review a finished game

- **Goal:** see whether the result matched expectations and whether it was deserved.
- **Trigger:** a game ends.
- **Flow:**
  1. The game page shows the result next to the locked prediction, the full win probability chart, and the box score.
  2. Player projections are shown next to what each player actually did (from V1.1).
  3. For NBA games, shot-making over expected shows whether the winner created better shots or shot unusually well (from V3).
  4. The model report card updates with the graded prediction.
- **Outcome:** the fan knows what the result says about each team.
- **Features used:** 2, 4, 8, and for the NBA 9 and 10.

### UC-4: Settle an argument about a team or player

- **Goal:** compare teams or players with evidence.
- **Trigger:** a debate between friends.
- **Flow:**
  1. The fan opens the Teams or Players table, sorts by the stat in question, and filters by position or conference.
  2. The fan opens team and player pages to see ratings, rosters, game logs, and trends, for this season or a past one.
  3. For the NBA, the fan checks player impact ratings, lineups, and play style (from V3).
  4. (Later version) The fan opens the Compare screen with two teams or two players side by side.
- **Outcome:** the fan has specific numbers to share.
- **Features used:** 1, 7, and for the NBA 11, 12, and 14.

### UC-5: Track a season

- **Goal:** know a team's chances of making the playoffs or the tournament.
- **Trigger:** the fan checks in during the season.
- **Flow:**
  1. The Season screen shows projected record and odds for each team. The odds are recalculated every time a game finishes.
  2. A chart shows how a team's odds have moved over the season.
- **Outcome:** the fan knows where each team stands.
- **Features used:** 1, 6, and for the NCAA 16.

### UC-6: Fill out a March Madness bracket

- **Goal:** fill out a bracket for a pool using the model's odds.
- **Trigger:** the tournament bracket is announced.
- **Flow:**
  1. The bracket simulator shows every team's odds of reaching each round.
  2. The likely upsets view lists lower seeds with a better chance than their seed suggests.
  3. The fan fills out their bracket on the pool's own site, using these odds. The app does not build brackets.
  4. During the tournament, live win probability runs for each game and the odds are re-run after every round.
- **Outcome:** the fan submits a bracket informed by the model and follows it through the tournament.
- **Features used:** 2, 4, 17.

### UC-7: Check whether the model can be trusted (owner)

- **Goal:** know how accurate the model is and what to improve.
- **Trigger:** weekly review, or before applying the model to NCAA games.
- **Flow:**
  1. The owner opens the model report card.
  2. The owner compares the model's accuracy with the betting market.
  3. The owner compares versions of the model with and without each add-on, and checks early-season accuracy against the rest of the season.
- **Outcome:** the owner knows which changes helped and whether the model is ready for March.
- **Features used:** 8.

## 5. Feature descriptions

### 5.1 Shared core (both leagues)

These features need only box scores, play-by-play, and a live score feed.

#### 1. Team ratings

Every team gets an overall rating, an offensive and a defensive rating, and a set of sub-ratings for specific skills. All are adjusted for opponent strength and home court.

- **Offense:** points scored per 100 of the team's possessions.
- **Defense:** points allowed per 100 of the opponent's possessions.
- **Overall:** offense minus defense. Example: a team that scores 118 and allows 110 is +8.
- **What the numbers mean:** the overall rating is measured against an average team on a neutral court. An average team is an imaginary team whose offense and defense equal the current season's league average, so the league as a whole averages 0. A neutral court means neither team has home-court advantage; the model measures home advantage from the data. "+6" means "expected to beat a league-average team by about 6 points with no home edge."
- **Display:** the rating with a likely range, for example "+6.2 (likely +3 to +9)", and an "early season, low confidence" tag until about 15 games. Offense and defense are shown both raw (118.2 scored) and relative to the league (+3.1).
- **Sub-ratings:** shooting efficiency, three-point shooting, ball security, forcing turnovers and steals, offensive rebounding, defensive rebounding, getting to the free-throw line, interior defense, and pace. Each is shown as a league percentile.
- **How it works:**
  - Ratings are recalculated every time a game finishes, from all of the season's games so far, with recent games counting more. They change throughout the season.
  - Garbage time is removed: a lead of 25 or more points in the last 6 minutes, or 15 or more in the last 3 (fourth quarter in the NBA, second half in college).
  - Mostly-luck components, such as opponent 3-point percentage and opponent free-throw percentage, are pulled toward normal instead of trusted fully.
  - The league average is recalculated daily, so ratings are always relative to the current season.
- **Starting point (preseason):**
  - V1: each player is valued from last season's box scores, and each team's starting rating is rebuilt from its current roster, weighted by expected minutes. This handles offseason trades and signings. If that is not available, the fallback is last season's rating pulled about one third of the way back toward average.
  - V3: player impact ratings (feature 11) replace the box-score player values.
  - NCAA: the same player-based starting point, with freshmen and players without Division I history at a default. Once teams have played a dozen or more games, real results dominate.
  - The past-season backtest decides whether each starting-point method is kept.
- **League difference:** NCAA ratings carry wider uncertainty, because teams play about 30 games. The opponent adjustment matters most there, because college schedules are very uneven. (Schedule strength is part of this feature, not a separate one.)

#### 2. Game predictor

Every scheduled game gets a win probability, an expected margin, and an expected total score.

- **What the user sees:** a card per game, such as "Boston 68% · favored by 5 · total 224 · range −7 to +17". When a player is ruled out, the card shows the prediction and how much the absence moved it.
- **How it works:** it combines the two teams' ratings with pace, home court, rest days, travel, and which players are available. The prediction is locked 30 minutes before tip-off.
- **Absences (NBA):** absences come from ESPN's injury statuses. Only players listed as "Out" change the prediction. "Questionable" and "Day-to-day" players are shown on the card but do not change it. Until V3, the size of the adjustment comes from the box-score player values; from V3, from player impact ratings.
- **Absences (NCAA):** entered by hand by the owner in the admin page, and adjusted by the player's share of minutes and production.
- **Which games count:** regular season, play-in, playoffs, and NBA Cup games. Preseason and the All-Star game are excluded from ratings and predictions.
- (Injury handling is part of this feature, not a separate one.)

#### 3. Matchup explainer

This feature explains in plain language why the model favors one team.

- **What the user sees:** a side-by-side comparison of the sub-ratings. The two or three biggest mismatches are highlighted, with template text such as "Boston's offensive rebounding (91st percentile) vs. Miami's defensive rebounding (12th)".
- **How it works:** it compares each team's strength in a category with the opponent's weakness in the same category, and ranks the gaps by how many points they are worth.
- **League difference:** none in the core. The NBA version can add shot quality and matchup notes from V3.

#### 4. Live win probability

Each team's chance of winning is updated throughout a live game.

- **What the user sees:** a chart of win probability across the game, refreshed every few seconds, with the biggest swings marked. Two lines are shown side by side: ours and ESPN's.
- **Why both:** ESPN's line is a benchmark. Ours starts from our own pregame prediction, so the chart stays consistent with the game card. Ours also reflects our absence adjustments, keeps working if ESPN's feed disappears, and can be graded.
- **How it works:** ours starts from the pregame prediction and updates from the score, time remaining, and possession. It is a separate model from the game predictor and takes over at tip-off.
- **League difference:** the NCAA model is trained separately, because the college game has two halves, a longer shot clock, and different foul rules. NCAA games show ESPN's line from V4 and ours from V5.

#### 5. Live analyst feed

A running stream of insights appears during a game, covering things the box score does not show.

- **What the user sees:** short cards such as "15-2 run over the last four minutes", "this lineup has been outscored by 14 in six minutes", or "9 of 12 on threes, far above their season rate".
- **How it works:** rules and models scan the play-by-play for scoring runs, unusual shooting, foul trouble, and large win probability swings. Text comes from templates.
- **Keeping it readable:** only significant events become cards, and the feed keeps the most significant cards per game. A developing event, such as a run, updates its existing card instead of creating new ones (8-0, then 12-0).
- **League difference:** the NBA feed adds lineup and shot quality insights from V3. The NCAA feed is limited to games with reliable live play-by-play.

#### 6. Season simulator

The remaining schedule is played out thousands of times using the game predictor.

- **What the user sees:** each team's projected record and odds, and a chart of how a team's odds have moved over the season.
- **How it works:** each simulation draws game results from the predicted probabilities, and also varies the team ratings within their uncertainty ranges. The odds are recalculated every time a game finishes, so they change continuously.
- **League difference:** the NBA version gives playoff, seeding, and title odds, using the real play-in and tiebreaker rules. The NCAA version gives conference regular-season title odds and tournament-bid odds. Conference tournament odds are not included.

#### 7. Player profiles

Every player has a page with season stats, trends, and a rest-of-season projection.

- **What the user sees:** per-game production, efficiency, role (minutes and share of the team's shots), a recent form chart, a rest-of-season projection with a range, and the most similar players by playing style. See section 6 for the page layout.
- **How it works:** per-minute production and minutes are projected separately, then combined. A player with little history starts from what similar players have done.
- **League difference:** NBA pages include the add-on features below. NCAA pages are season-level only, adjusted for opponent strength.

#### 8. Model report card

This page shows how accurate the model has been.

- **What the user sees:** a record of every graded prediction, accuracy by month, a calibration chart showing whether 70% predictions win about 70% of the time, and a comparison with the betting market.
- **Backtests:** results from past seasons are shown alongside this season's results, labeled separately, so the page is useful from opening night. Accuracy is also split into early season and rest of season, to show how much new rosters hurt and when the model settles.
- **How it works:** every prediction is locked before tip-off and graded after the game.
- **Why it matters:** it shows whether the model is ready to trust in March, and which add-ons improve accuracy.

### 5.2 NBA-only features

These features depend on data or sample sizes that only the NBA has.

#### 9. Single-game player projections

Each player's stat line for a game is predicted as a range.

- **What the user sees:** every box-score stat is projected: minutes, points, rebounds (offensive and defensive), assists, steals, blocks, turnovers, 3-pointers, field goals and free throws made and attempted, and fouls. Minutes, points, rebounds, assists, and 3-pointers are shown by default ("24 to 34 points, most likely 29"); the rest are under "full projection". Plus/minus is not projected.
- **Projected vs. actual:** after a game, each player's projection is shown next to what they actually did, on the game page and in the player's game log.
- **How it works:** projected minutes are multiplied by per-minute rates, then adjusted for the opponent's defense, pace, rest, and absent teammates.
- **Why NBA only:** college players have too few games for a reliable single-game prediction.

#### 10. Shot quality and shot-making over expected

This feature measures how good a team's or player's shots are, and compares that with the actual results.

- **What the user sees:** expected and actual points per shot, with a label for who is shooting above or below expectation and likely to drift back.
- **How it works:** each field goal attempt gets an expected value from its location and shot type (for example pull-up jumper or driving layup), both from ESPN play-by-play. Defender distance is not available (NBA.com is out of scope). Free throws are not included, since they are not contested.
- **Why NBA only:** the shot detail and sample sizes are reliable only for the NBA.

#### 11. Player impact ratings

Each player is rated by how much the team's scoring margin changes per 100 possessions when he is on the floor.

- **What the user sees:** an offensive and a defensive impact number for every player, with an uncertainty range shown prominently, and a league ranking. Players need about 500 minutes to appear in the rankings.
- **How it works:** a regression over every stretch of every game separates each player's effect from his teammates and opponents. Results are pulled toward a box-score-based estimate.
- **Why NBA only:** it needs thousands of possessions per player and reliable substitution data.
- **Also used by:** team ratings (preseason starting point) and the game predictor (absences), from V3.

#### 12. Lineup analysis

This feature shows which player combinations work.

- **What the user sees:** each team's most-used five-man groups with their scoring margin per 100 possessions, the best two- and three-player pairings, and on/off splits.
- **How it works:** substitutions in the play-by-play show who was on the floor for every possession. Lineups with few possessions are labeled "small sample" and blended with player impact ratings.
- **Why NBA only:** college lineup data is incomplete and the samples are too small.

#### 14. Play-style profiles

Player pages gain a play-style profile.

- **What the user sees:** a profile built from ESPN shot locations and types: rim vs. midrange vs. three, drives vs. pull-ups, assisted vs. unassisted, each with a league percentile.
- **Why not tracking data:** tracking stats exist only on NBA.com, which is out of scope.
- **Why NBA only:** the samples are too small in college.

### 5.3 NCAA-only features

#### 16. Tournament field projection

From January until the bracket is announced, the app projects which teams will make the tournament and their seeds.

- **What the user sees:** projected seeds in the familiar bracketology format, "last four in" and "first four out", and each bubble team's chance of getting in. Updated daily.
- **How it works:** a model trained on past selections uses each team's record, quality of wins, and rating. The committee's NET ranking and quadrant records are used if they can be read reliably from the NCAA's site; otherwise they are approximated with our own ratings.
- **Why NCAA only:** NBA playoff spots are decided by record, which the season simulator already covers.

#### 17. Bracket simulator

Once the bracket is set, the tournament is simulated thousands of times.

- **What the user sees:** every team's odds of reaching each round and winning the title, and a win probability for every possible matchup. The First Four games are included. A "likely upsets" view lists games where the lower seed's chance is at least 10 percentage points above the historical rate for that seed matchup.
- **How it works:** it runs the game predictor at neutral sites and varies team ratings within their uncertainty ranges. Odds are re-run after every round.
- **Why NCAA only:** it is built for the single-elimination format.

### 5.4 Cut features

- **15. Roster turnover priors.** Cut as a separate feature. The idea that a team is its players weighted by minutes is kept as the starting point of team ratings (feature 1). Recruiting rankings and coaching changes are not used.
- **18. Bracket builder.** Cut. Fans use the bracket simulator's odds and fill out brackets on their pool's own site.
- **13. Defensive matchups.** Cut in the architecture phase. The data exists only on NBA.com, which is out of scope, and ESPN data cannot replace it.

## 6. Screens

**Navigation.** On phones, a bottom tab bar: Tonight · Teams · Players · Season · Report card. A March tab appears for the NCAA during tournament season. An NBA/NCAA switch sits at the top. Dark mode by default.

**Tables everywhere** follow the conventions of current stat sites: tap a column header to sort, tap again to reverse. Player names and column headers stay fixed in place while scrolling sideways on a phone. No advanced filters and no percentile colors.

**Season picker.** Teams, Players, the team page, and the player page have a season picker. Both leagues go back to 2021-22, the first season after the bubble seasons. Past seasons show that season's final ratings, rosters, and stats. A player traded mid-season has one row per team plus a combined total.

A clickable wireframe of the Teams, team page, and Players screens was reviewed and approved during design: https://claude.ai/artifact/DmxRAwZpkDbB6aiLAyhvuZ

| Screen | Contents | Features | Version |
| --- | --- | --- | --- |
| Tonight | Date arrows to move between days. Game cards grouped as Live, Upcoming, and Final. Upcoming: teams, records, tip time, win probability, favored by, total, range, and a flag when players are out. Live: score, clock, both win probabilities. Final: score, our pregame pick, and whether it was right. NCAA adds a conference filter and a team search. | 2, 4 | V1 (live cards from V2) |
| Game page | One page that changes with the game's state. Tabs: **Preview** (prediction, what moved it, matchup explainer, absences), **Live** (win probability chart with both lines, analyst feed), **Box score** (running, then final; shot-making over expected for the NBA), **Projections** (NBA: projections before the game, projected vs. actual during and after). Opens on Preview before tip-off, Live during, Box score after. | 2, 3, 4, 5, 9, 10 | V1 (Preview, Box score, final result); Live in V2; Projections in V1.1 |
| Teams | All teams ranked by rating, with columns for rating with range, offense, defense, record, points per game, opponent points per game, pace, and conference. Team search. Scope: League, East, West (NBA); Division I, Conference (NCAA). | 1 | V1 |
| Team page | Header: name, record, rating with range, league rank, and a quick team switcher (previous, next, dropdown). Tabs: **Overview** (ratings, sub-ratings profile, rating trend, season odds from V3), **Team Stats** (team and opponent per-game stats), **Roster** (every player; sorted by impact rating for the NBA (points per game until impact ratings arrive in V3) and points per game for the NCAA; every column sortable; position filter), **Schedule** (past games with our prediction vs. the result, upcoming games with predictions), **Lineups** (NBA, V3). | 1, 6, 7, 11, 12 | V1 |
| Players | The roster table for every player. Scope: Team, League (NBA); Team, Conference, Division I (NCAA). Player search, position filter, and a "Qualified only" switch using the official minimums. Default sort: impact rating (NBA, from V3; points per game before that) or points per game (NCAA). | 7, 11 | V1 |
| Player page | Header: name, team, position, height, age or class year, and a key stat line. Tabs: **Overview** (season averages, recent form chart, rest-of-season projection, similar players by playing style), **Game log** (one row per game; projected vs. actual from V1.1), **Impact** (NBA, V3), **Shooting** (NBA, V3), **Play style** (NBA, V3). No teammate switcher. | 7, 9, 10, 11, 14 | V1 |
| Season | Projected record and odds for every team, recalculated every time a game finishes, with a chart of how a team's odds moved. | 6 | V3 (NBA), V4 (NCAA) |
| Report card | Graded predictions, accuracy by month, calibration, comparison with the market, past-season backtests, early vs. late season split. | 8 | V1 |
| March (NCAA) | Field projection, bracket odds, likely upsets. | 16, 17 | V5 |
| Admin | Owner login, view only at first. NCAA absences entry. Data health (last successful data pull, job history, data quality reports). Model management: model versions and settings, champion and challengers, rolling exam results, live accuracy against the target, training history. | 2, 8 | V1.1 (actions such as starting a backtest or promoting a challenger come later) |
| Compare | Two teams or two players side by side, with the better value in each row highlighted. | 1, 7 | Later |

**Search.** Separate searches for teams (on Teams) and players (on Players). Results appear as you type. Search tolerates typos, nicknames, abbreviations, and city names.

**Leaderboard qualifier.** "Qualified only" uses the league's official minimums. NBA: 70% of the team's games for per-game stats; 300 field goals, 82 three-pointers, or 125 free throws made for shooting percentages. NCAA minimums to be confirmed in the architecture phase.

**Updates.** When a game finishes, its score, box score, ratings, and season odds update within minutes. Official stat corrections are pulled in overnight.

## 7. Versions

Scope of work by version. No dates; V1 targets the start of the NBA regular season.

| Version | Scope |
| --- | --- |
| **V1** | ESPN data pipeline on the VPS. Team ratings with sub-ratings, ranges, and the roster-based starting point. Game predictor with ESPN injury statuses, locked before tip-off. Matchup explainer. Tonight. Game page (Preview, Box score, final result vs. prediction). Teams, team page, Players, player page (Overview, Game log). Season picker. Report card with past-season backtests. |
| **V1.1** | Single-game player projections (feature 9), projected vs. actual on the game page and player game log, Admin page (view only, with model management), shadow models (challengers predict every game quietly; a challenger replaces the champion at fixed checkpoints only if clearly better). |
| **V2** | Live: our win probability and ESPN's side by side, analyst feed, running box score, live cards on Tonight. |
| **V3** | NBA add-ons: player impact ratings (and roster-aware ratings and absences built on them), shot quality, lineups, play-style profiles. Season simulator. |
| **V4** | NCAA core for Division I: data, ratings, predictions, all core screens, ESPN's live win probability line, season simulator, graded daily. |
| **V5** | March: tournament field projection, bracket simulator with likely upsets, our NCAA live win probability. |
| **Later** | Compare screen, share links with preview images, admin actions, sign-in if needed, backups. |

## 8. Data constraints

From checks during design and architecture. Details are in the architecture doc, sections 4.2 and 4.3.

- **ESPN is the only data source.** It covers both leagues: scoreboard, box scores, play-by-play with shot locations, injuries, betting lines, and ESPN's own win probability (college from about 2017-18).
- **Seasons:** both leagues load 2021-22 onward. In a sample, NBA and college team-level data had no failures in those seasons. College player box scores (about 6% of games) and play-by-play (about 10%) are sometimes incomplete; a three-part quality gate keeps those games out of only the parts they would affect.
- **ESPN quirks** handled by design: one date per scoreboard request, play order taken from the list (not sequence numbers), stale running scores on some plays, free throws worth 0 in older data, missing shot coordinates.
- **NBA.com is out of scope.** This removes feature 13, tracking data in 14, and defender distance in 10.

## 9. Open questions

1. App name.
2. Live feed delay behind the broadcast (measure before V2).
3. Backups (revisit after launch).
4. For V5: load older tournament selection history for the field projection model only?
