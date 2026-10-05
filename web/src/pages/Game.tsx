import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Absence, type BoxPlayer, type GameDetail, type GameProjections } from "../api";
import { ProjectionsTab } from "../components/Projections";
import { favoredText, fixed, longDate, pct, rangeText, signed, tipTime } from "../format";
import { Empty, ErrorNote, Loading, ProbBar, Tabs, TeamLogo, useLeague } from "../components/ui";

type Tab = "preview" | "box" | "projections";

export function GamePage() {
  const league = useLeague();
  const { id } = useParams();
  const game = useQuery({
    queryKey: ["game", league, id],
    queryFn: () => api<GameDetail>(`${league}/games/${id}`),
    refetchInterval: 60_000,
  });
  const [tab, setTab] = useState<Tab | null>(null);
  const projections = useQuery({
    queryKey: ["projections", league, id],
    queryFn: () => api<GameProjections>(`${league}/games/${id}/projections`),
    enabled: league === "nba",
    refetchInterval: 60_000,
  });
  if (game.isLoading) return <main className="page"><Loading what="Loading game" /></main>;
  if (game.error || !game.data) return <main className="page"><ErrorNote error={game.error} /></main>;
  const g = game.data;
  const tabs: [Tab, string][] = [["preview", "Preview"]];
  if (g.box_score) tabs.push(["box", "Box score"]);
  if (projections.data?.available) tabs.push(["projections", "Projections"]);
  // A live game opens on its running box score until the Live tab arrives (V2).
  const opensOnBox = (g.default_tab === "box" || g.default_tab === "live") && !!g.box_score;
  const active: Tab = tab ?? (opensOnBox ? "box" : "preview");
  const scored = g.status === "final" || g.status === "live";

  return (
    <main className="page">
      <div className="card">
        <div className="small muted">
          {longDate(new Date(g.start_time))} · {g.status === "final" ? "Final" : g.status === "live" ? "Live" : tipTime(g.start_time)}
          {g.season_type === "post" ? " · Playoffs" : g.season_type === "play_in" ? " · Play-in" : ""}
        </div>
        {[["away", g.away, g.away_score] as const, ["home", g.home, g.home_score] as const].map(([side, team, score]) => (
          <Link key={side} to={`/${league}/teams/${team.id}`} className="team-line">
            <TeamLogo src={team.logo} alt="" />
            <span className="name">
              {team.name}
              {team.record && <span className="muted small"> {team.record.wins}-{team.record.losses}</span>}
            </span>
            <span className="score num">{scored ? score : ""}</span>
          </Link>
        ))}
      </div>
      <Tabs tabs={tabs} value={active} onChange={setTab} />
      {active === "preview" ? <Preview game={g} />
        : active === "projections" ? <ProjectionsTab game={g} data={projections.data} />
        : <BoxScore game={g} />}
    </main>
  );
}

function Preview({ game }: { game: GameDetail }) {
  const p = game.prediction;
  if (!p) return <Empty>No prediction for this game yet. Predictions appear once both teams are rated.</Empty>;
  const homeName = game.home.short_name ?? game.home.name;
  const awayName = game.away.short_name ?? game.away.name;
  const moved = game.preview.absence_shift ?? 0;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card">
        <h3>{p.locked ? "Locked prediction" : "Prediction"}{p.source === "backtest" ? " (backtest)" : ""}</h3>
        <ProbBar homeProb={p.home_win_prob} label={`${game.home.name} win chance ${pct(p.home_win_prob)}`} />
        <div className="row spread num">
          <span>{game.away.abbreviation} {pct(1 - p.home_win_prob)}</span>
          <span>{game.home.abbreviation} {pct(p.home_win_prob)}</span>
        </div>
        <div style={{ fontSize: 18, fontWeight: 700 }}>{favoredText(homeName, awayName, p.home_win_prob, p.margin_home)}</div>
        <div className="muted">
          Expected total {Math.round(p.total)}
          {p.margin_low !== null && p.margin_high !== null && <> · likely margin {rangeText(p.home_win_prob, p.margin_low, p.margin_high)}</>}
        </div>
        {Math.abs(moved) >= 0.5 && (
          <div className="small">Absences moved this prediction by {signed(moved)} points toward {moved > 0 ? homeName : awayName}.</div>
        )}
        {p.updated_after_lock && (
          <div className="small muted">
            Updated after lock (not graded): {favoredText(homeName, awayName, p.updated_after_lock.home_win_prob, p.updated_after_lock.margin_home)}
          </div>
        )}
        {p.grade && (
          <span className={`pill ${p.grade.correct ? "good" : "bad"}`}>
            {p.grade.correct ? "✓ Model was right" : "✗ Model was wrong"} · actual margin {signed(p.grade.actual_margin, 0)} for {game.home.abbreviation}
          </span>
        )}
      </section>

      {game.preview.explainer.length > 0 && (
        <section className="card">
          <h3>Why</h3>
          <ul className="stack" style={{ margin: 0, paddingLeft: 18 }}>
            {game.preview.explainer.map((e) => <li key={e.text}>{e.text}</li>)}
          </ul>
        </section>
      )}

      {(game.preview.ratings.home || game.preview.ratings.away) && (
        <section className="card">
          <h3>Ratings going in</h3>
          <div className="table-wrap auto-height">
            <table>
              <thead><tr><th scope="col" className="left">Team</th><th scope="col" title="Net points per 100 possessions vs. an average team, neutral court">Rating</th><th scope="col">Offense</th><th scope="col">Defense</th></tr></thead>
              <tbody>
                {(["away", "home"] as const).map((side) => {
                  const r = game.preview.ratings[side];
                  const team = side === "home" ? game.home : game.away;
                  return r ? (
                    <tr key={side}>
                      <td className="left">{team.abbreviation}</td>
                      <td>{signed(r.overall)}</td><td>{fixed(r.offense)}</td><td>{fixed(r.defense)}</td>
                    </tr>
                  ) : null;
                })}
              </tbody>
            </table>
          </div>
          <div className="small muted">Offense: points scored per 100 possessions. Defense: points allowed per 100 (lower is better).</div>
        </section>
      )}

      <section className="card">
        <h3>Who's out</h3>
        <div className="grid-2">
          {(["away", "home"] as const).map((side) => (
            <AbsenceList key={side} team={side === "home" ? game.home.name : game.away.name} players={game.preview.absences[side]} />
          ))}
        </div>
        <div className="small muted">Only players listed as Out change the prediction.</div>
      </section>
    </div>
  );
}

function AbsenceList({ team, players }: { team: string; players: Absence[] }) {
  return (
    <div>
      <div style={{ fontWeight: 600 }}>{team}</div>
      {players.length === 0 ? <div className="muted small">No one listed</div> : (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {players.map((p) => (
            <li key={p.player_id}>
              {p.name} <span className={`pill ${p.status === "Out" ? "bad" : ""}`}>{p.status}</span>
              {p.mpg !== undefined && <span className="muted small"> {p.mpg.toFixed(0)} min/game</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const BOX_COLUMNS: [keyof BoxPlayer, string][] = [
  ["minutes", "Min"], ["pts", "Pts"], ["reb", "Reb"], ["ast", "Ast"], ["stl", "Stl"], ["blk", "Blk"],
  ["tov", "TO"], ["pf", "PF"], ["plus_minus", "+/-"],
];

function BoxScore({ game }: { game: GameDetail }) {
  const league = useLeague();
  if (!game.box_score) return <Empty>No box score yet.</Empty>;
  return (
    <div className="stack" style={{ gap: 16 }}>
      {(["away", "home"] as const).map((side) => {
        const team = side === "home" ? game.home : game.away;
        const players = game.box_score![side].players;
        return (
          <section key={side} className="stack">
            <h2>{team.name}</h2>
            <div className="table-wrap auto-height">
              <table aria-label={`${team.name} box score`}>
                <thead>
                  <tr>
                    <th scope="col" className="left">Player</th>
                    {BOX_COLUMNS.slice(0, 2).map(([, l]) => <th key={l}>{l}</th>)}
                    <th scope="col">FG</th><th scope="col">3PT</th><th scope="col">FT</th>
                    {BOX_COLUMNS.slice(2).map(([, l]) => <th key={l}>{l}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {players.map((p) => (
                    <tr key={p.player_id}>
                      <td className="left">
                        <Link className="link" to={`/${league}/players/${p.player_id}`}>{p.name}</Link>
                        {p.starter && <span className="muted small"> {p.position}</span>}
                      </td>
                      {p.did_not_play ? (
                        <td colSpan={BOX_COLUMNS.length + 3} className="left muted">{p.dnp_reason ?? "Did not play"}</td>
                      ) : (
                        <>
                          {BOX_COLUMNS.slice(0, 2).map(([k]) => <td key={k}>{p[k] ?? "–"}</td>)}
                          <td>{p.fgm}-{p.fga}</td><td>{p.fg3m}-{p.fg3a}</td><td>{p.ftm}-{p.fta}</td>
                          {BOX_COLUMNS.slice(2).map(([k]) => <td key={k}>{k === "plus_minus" ? signed(p[k] as number, 0) : p[k] ?? "–"}</td>)}
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}
