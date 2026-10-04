# Basketball Analytics App: Feature Spec

Last updated: 2026-10-04
Companion document: `basketball-analytics-technical-architecture.md`

## 1. Context for a new session

This document is a handoff. It records what has been decided so far so that a new Claude session can continue the design and start the build without the earlier conversation.

**What the app is.** A basketball prediction and analytics engine for fans who watch for fun. It is built on the NBA first, then extended to NCAA Division I men's basketball for March Madness.

**Who it is for.** The owner and a group of sports-loving friends. They watch games together and argue about them. They do not bet and do not play fantasy basketball.

**Decisions already made.**

| Decision | Detail |
| --- | --- |
| Sport and order | NBA first, NCAA second. The NCAA version should be ready before the tournament in March 2027. |
| Purpose | Analytics and predictions. The owner wants to refine the models on NBA data, then apply them to NCAA data, where data is thinner but predictions are more valuable. |
| Structure | A shared core that runs in both leagues, plus league-specific add-ons. Features are not limited to what both leagues support: if college lacks the data, the feature is NBA only. |
| Betting | No betting features. Betting-market closing lines are pulled only as a benchmark for model accuracy. |
| Realtime | Near-realtime during games is wanted (live win probability and live insights). |
| Rejected ideas | "Switch to this game" alerts, fantasy tools, a standalone prop-bet tracker, a standalone injury feature, a standalone schedule-strength feature, a standalone upset finder. |
| Naming | Do not use the term "luck index". Use "shot-making over expected". |

**Still open.** See section 7.

## 2. Design rules

1. **The core runs on its own.** Add-ons feed in as optional inputs. The same game predictor works with or without them.
2. **Small samples are expected.** Every rating starts from a prior estimate, is pulled toward the average until there is enough evidence, and shows an uncertainty range.
3. **Every prediction is stored before tip-off and graded afterwards.** Accuracy is public inside the app.
4. **Each add-on must earn its place.** The NBA predictor is run with and without each add-on to measure what it contributes. This also shows what the NCAA version loses without it.

## 3. Feature availability at a glance

18 features: 8 core, 6 NBA add-ons, 4 NCAA add-ons.

| # | Feature | Group | NBA | NCAA |
| --- | --- | --- | --- | --- |
| 1 | Team ratings (overall plus sub-ratings) | Core | Full | Full, wider uncertainty |
| 2 | Game predictor | Core | Full, absences handled automatically | Full, absences entered by hand |
| 3 | Matchup explainer | Core | Full | Full |
| 4 | Live win probability | Core | Full | Full |
| 5 | Live analyst feed | Core | Full | Reduced, where live play-by-play is reliable |
| 6 | Season simulator | Core | Playoff and title odds | Conference and tournament-bid odds |
| 7 | Player profiles | Core | Full | Season-level only |
| 8 | Model report card | Core | Full | Full |
| 9 | Single-game player projections | NBA add-on | Yes | No |
| 10 | Shot quality and shot-making over expected | NBA add-on | Yes | No |
| 11 | Player impact ratings | NBA add-on | Yes | Rough version possible later |
| 12 | Lineup analysis | NBA add-on | Yes | Rough version possible later |
| 13 | Defensive matchups | NBA add-on | Yes | No |
| 14 | Tracking profiles | NBA add-on | Yes | No |
| 15 | Roster turnover priors | NCAA add-on | No | Yes |
| 16 | Tournament field projection | NCAA add-on | No | Yes |
| 17 | Bracket simulator (includes likely upsets) | NCAA add-on | No | Yes |
| 18 | Bracket builder | NCAA add-on | No | Yes |

## 4. Use cases

**Primary actor:** a fan who watches basketball with friends. There is one secondary actor, the owner, who also maintains the models.

### UC-1: Decide what to expect from tonight's games

- **Goal:** know who is favored in each game and why.
- **Trigger:** the fan opens the app on a game day.
- **Flow:**
  1. The Tonight screen lists every game with win probability, expected margin, and expected total.
  2. The fan taps a game and reads the matchup explainer.
  3. For NBA games, the fan sees who is ruled out and how that moved the prediction.
- **Outcome:** the fan has a prediction and a reason for it before tip-off.
- **Features used:** 1, 2, 3, and for the NBA 9 and 11.

### UC-2: Follow a game live

- **Goal:** understand what is happening in a game beyond the score.
- **Trigger:** a game the fan is watching tips off.
- **Flow:**
  1. The fan keeps the live game page open as a second screen.
  2. The win probability chart updates every few seconds.
  3. Insight cards arrive in the analyst feed, such as a scoring run or a lineup that is struggling.
  4. The fan shares a card or the chart to the group chat.
- **Outcome:** the fan has live context to talk about with friends.
- **Features used:** 4, 5.

### UC-3: Review a finished game

- **Goal:** see whether the result matched expectations and whether it was deserved.
- **Trigger:** a game ends.
- **Flow:**
  1. The game page shows the result next to the pregame prediction and the full win probability chart.
  2. For NBA games, shot-making over expected shows whether the winner created better shots or shot unusually well.
  3. The model report card updates with the graded prediction.
- **Outcome:** the fan knows what the result says about each team.
- **Features used:** 2, 4, 8, and for the NBA 10.

### UC-4: Settle an argument about a team or player

- **Goal:** compare teams or players with evidence.
- **Trigger:** a debate between friends.
- **Flow:**
  1. The fan opens the ratings table or two team pages and compares overall ratings and sub-ratings.
  2. The fan opens player profiles and compares production, efficiency, and role.
  3. For the NBA, the fan checks player impact ratings, lineups, defensive matchups, and tracking profiles.
- **Outcome:** the fan has specific numbers to share.
- **Features used:** 1, 7, and for the NBA 11 to 14.

### UC-5: Track a season

- **Goal:** know a team's chances of making the playoffs or the tournament.
- **Trigger:** the fan checks in during the season.
- **Flow:**
  1. The season simulator shows projected record and odds for each team.
  2. A chart shows how the odds have moved over the season.
- **Outcome:** the fan knows where each team stands.
- **Features used:** 1, 6, and for the NCAA 15 and 16.

### UC-6: Fill out a March Madness bracket

- **Goal:** build a bracket for a pool using the model.
- **Trigger:** the tournament bracket is announced.
- **Flow:**
  1. The bracket simulator shows every team's odds of reaching each round.
  2. The likely upsets view lists lower seeds with a better chance than their seed suggests.
  3. The bracket builder pre-fills a bracket. The fan overrides picks and sees the effect on expected score.
  4. During the tournament, live win probability runs for each game and the odds are re-run after every round.
- **Outcome:** the fan submits a bracket and follows it through the tournament.
- **Features used:** 2, 4, 17, 18.

### UC-7: Check whether the model can be trusted (owner)

- **Goal:** know how accurate the model is and what to improve.
- **Trigger:** weekly review, or before applying the model to NCAA games.
- **Flow:**
  1. The owner opens the model report card.
  2. The owner compares the model's accuracy with betting-market closing lines.
  3. The owner compares versions of the model with and without each add-on.
- **Outcome:** the owner knows which changes helped and whether the model is ready for March.
- **Features used:** 8.

## 5. Feature descriptions

### 5.1 Shared core (both leagues)

These features need only box scores, play-by-play, and a live score feed.

#### 1. Team ratings

Every team gets an overall rating, an offensive and a defensive rating, and a set of sub-ratings for specific skills. All are adjusted for opponent strength and home court.

- **Overall, offense, and defense:** points scored and allowed per 100 possessions. The overall rating is the difference between the two.
- **Sub-ratings:** shooting efficiency, three-point shooting, ball security, forcing turnovers and steals, offensive rebounding, defensive rebounding, getting to the free-throw line, interior defense, and pace. Each is shown as a league percentile.
- **What the user sees:** a sortable league table, and a team page with the sub-ratings as a profile, the trend over the last 10 games, and an uncertainty range.
- **How it works:** ratings start from a preseason estimate and update after every game. Early results are pulled toward the league average until enough games are played. The sub-ratings explain the overall rating and feed the matchup explainer.
- **League difference:** NCAA ratings carry wider uncertainty, because teams play about 30 games. The opponent adjustment matters most there, because college schedules are very uneven. (Schedule strength is part of this feature, not a separate one.)

#### 2. Game predictor

Every scheduled game gets a win probability, an expected margin, and an expected total score.

- **What the user sees:** a card per game, such as "Boston 68%, favored by 5, total 224", with a likely range around the margin. When a player is ruled out, the card shows the new prediction and how much it moved.
- **How it works:** it combines the two teams' ratings with pace, home court, rest days, travel, and which players are available.
- **League difference:** the NBA version updates automatically for absent players, using the league injury report and player impact ratings, and also uses shot quality. College has no reliable injury feed or player impact ratings, so absences are entered by hand and adjusted by the player's share of minutes and production. (Injury handling is part of this feature, not a separate one.)

#### 3. Matchup explainer

This feature explains in plain language why the model favors one team.

- **What the user sees:** a side-by-side comparison of the sub-ratings. The two or three biggest mismatches are highlighted, such as one team's offensive rebounding against the other's weak defensive rebounding.
- **How it works:** it compares each team's strength in a category with the opponent's weakness in the same category, and ranks the gaps by how many points they are worth.
- **League difference:** none in the core. The NBA version can add shot quality and defensive matchup notes.

#### 4. Live win probability

Each team's chance of winning is updated throughout a live game. It is a separate model from the game predictor: the game predictor gives the pregame number, and this model takes over at tip-off.

- **What the user sees:** a chart of win probability across the game, refreshed every few seconds, with the biggest swings marked.
- **How it works:** it starts from the pregame prediction and updates from the score, time remaining, and possession. It needs only a live score feed.
- **League difference:** the NCAA version is trained separately, because the college game has two halves, a longer shot clock, and different foul rules.

#### 5. Live analyst feed

A running stream of insights appears during a game, covering things the box score does not show.

- **What the user sees:** short cards such as "15-2 run over the last four minutes", "this lineup has been outscored by 14 in six minutes", or "9 of 12 on threes, far above their season rate".
- **How it works:** rules and models scan the play-by-play for scoring runs, unusual shooting, foul trouble, and large win probability swings.
- **League difference:** the NBA feed includes lineup and shot quality insights. The NCAA feed is limited to games with reliable live play-by-play.

#### 6. Season simulator

The remaining schedule is played out thousands of times using the game predictor.

- **What the user sees:** each team's projected record and odds, updated daily, with a chart of how the odds have moved.
- **How it works:** each simulation draws game results from the predicted probabilities, and also varies the team ratings within their uncertainty ranges.
- **League difference:** the NBA version gives playoff, seeding, and title odds. The NCAA version gives conference title and tournament-bid odds.

#### 7. Player profiles

Every player has a page with season stats, trends, and a rest-of-season projection.

- **What the user sees:** per-game and per-minute production, efficiency, role (minutes and share of the team's shots), a recent form chart, and the most similar players.
- **How it works:** per-minute production and minutes are projected separately, then combined. A player with little history starts from what similar players have done.
- **League difference:** NBA pages include the add-on features below. NCAA pages are season-level only, adjusted for opponent strength.

#### 8. Model report card

This page shows how accurate the model has been.

- **What the user sees:** a record of every prediction, accuracy by month, and a calibration chart showing whether 70% predictions win about 70% of the time. A second chart compares the model with betting-market closing lines.
- **How it works:** every prediction is stored before tip-off and graded after the game.
- **Why it matters:** it shows whether the model is ready to trust in March, and which add-ons improve accuracy.

### 5.2 NBA-only features

These features depend on data or sample sizes that only the NBA has.

#### 9. Single-game player projections

Each player's stat line for tonight is predicted as a range.

- **What the user sees:** projected minutes, points, rebounds, and assists for every player in tonight's games, such as "24 to 34 points, most likely 29".
- **How it works:** projected minutes are multiplied by per-minute rates, then adjusted for the opponent's defense, pace, rest, and absent teammates.
- **Why NBA only:** college players have too few games for a reliable single-game prediction.

#### 10. Shot quality and shot-making over expected

This feature measures how good a team's or player's shots are, and compares that with the actual results.

- **What the user sees:** expected and actual points per shot, with a label for who is shooting above or below expectation and likely to drift back.
- **How it works:** each shot type gets an expected value from its location and how closely it was defended.
- **Why NBA only:** defender distance comes from the NBA's camera tracking.

#### 11. Player impact ratings

Each player is rated by how much the team's scoring margin changes per 100 possessions when he is on the floor.

- **What the user sees:** an offensive and a defensive impact number for every player, with an uncertainty range, and a league ranking.
- **How it works:** a regression over every stretch of every game separates each player's effect from his teammates and opponents. Results are pulled toward a box-score-based estimate.
- **Why NBA only:** it needs thousands of possessions per player and rosters that stay stable.
- **Also used by:** the game predictor, to adjust for absent players.

#### 12. Lineup analysis

This feature shows which player combinations work.

- **What the user sees:** each team's most-used five-man groups with their scoring margin per 100 possessions, the best two- and three-player pairings, and on/off splits.
- **How it works:** substitutions in the play-by-play show who was on the floor for every possession. Small samples are blended with player impact ratings.
- **Why NBA only:** college lineup data is incomplete and the samples are too small.

#### 13. Defensive matchups

This feature shows who guards whom.

- **What the user sees:** for any scorer, the defenders he has faced and how he shot against each, plus tonight's likely matchup.
- **How it works:** it uses the NBA's matchup data, which counts possessions for each defender and scorer pairing. Small samples are labeled.
- **Why NBA only:** matchup data comes from camera tracking.

#### 14. Tracking profiles

Player pages gain a play-style profile built from tracking stats.

- **What the user sees:** drives, touches, time with the ball, passes that lead to shots, distance run, deflections, and contested shots, with a league percentile for each.
- **How it works:** season-level tracking summaries are refreshed after each game. They are not available live.
- **Why NBA only:** college arenas do not have a public tracking feed.

### 5.3 NCAA-only features

These features deal with problems specific to college basketball: new rosters every year and a single-elimination tournament.

#### 15. Roster turnover priors

Each team gets a preseason rating before it has played a game.

- **What the user sees:** a preseason rating and a "how much is new" indicator showing the share of last season's minutes that returned.
- **How it works:** it combines last season's rating, returning minutes, incoming transfers, recruiting rankings, and coaching changes. The estimate fades as real games are played.
- **Why NCAA only:** college rosters change far more each year than NBA rosters. The NBA core uses a simpler version.

#### 16. Tournament field projection

From January until the bracket is announced, the app projects which teams will make the tournament and their seeds.

- **What the user sees:** a projected bracket, updated daily, and a list of teams on the edge with each one's chance of getting in.
- **How it works:** a model trained on past selections uses each team's record, quality of wins, and rating.
- **Why NCAA only:** NBA playoff spots are decided by record, which the season simulator already covers.

#### 17. Bracket simulator

Once the bracket is set, the tournament is simulated thousands of times.

- **What the user sees:** every team's odds of reaching each round and winning the title, and a win probability for every possible matchup. A "likely upsets" view lists games where a lower seed's chance is well above the usual rate for that seed pairing.
- **How it works:** it runs the game predictor at neutral sites and varies team ratings within their uncertainty ranges. Odds are re-run after every round.
- **Why NCAA only:** it is built for the single-elimination format.

#### 18. Bracket builder

This feature helps a user fill out a bracket.

- **What the user sees:** a bracket pre-filled with the most likely picks, a riskier alternative with more upsets, and the expected score of each under standard pool scoring.
- **How it works:** picks are chosen to maximize expected points from the bracket simulator's odds. The user can override any pick and see the effect.
- **Why NCAA only:** bracket pools are specific to the tournament.

## 6. Screens implied by the features

| Screen | Contents | Features |
| --- | --- | --- |
| Tonight | Every game today with prediction cards | 2 |
| Game page (pregame) | Prediction, matchup explainer, absences, player projections | 2, 3, 9 |
| Game page (live) | Win probability chart, analyst feed | 4, 5 |
| Game page (final) | Result against prediction, shot-making over expected | 2, 4, 10 |
| Ratings | League table with overall and sub-ratings | 1 |
| Team page | Ratings profile, trend, lineups, season odds | 1, 6, 12 |
| Player page | Profile, projection, impact, matchups, tracking | 7, 11, 13, 14 |
| Season | Simulator odds for every team | 6 |
| Report card | Accuracy, calibration, comparison with the market | 8 |
| March (NCAA) | Field projection, bracket odds, likely upsets, bracket builder | 15 to 18 |

## 7. Open questions

1. Is the app a website, a mobile app, or both?
2. Do friends need accounts, or is it a shared link with no sign-in?
3. Is there any social layer (for example, friends making picks against the model)? It was discussed and neither accepted nor rejected.
4. Is NCAA women's basketball in scope later?
5. What budget, if any, is there for paid data (odds history, college play-by-play volume)?
6. How should absences be entered by hand for NCAA games: by the owner only, or by any user?
