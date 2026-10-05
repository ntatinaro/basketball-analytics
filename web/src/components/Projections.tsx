import { useState } from "react";
import { Link } from "react-router-dom";
import type { GameDetail, GameProjections, ProjectedPlayer } from "../api";
import { Empty, useLeague } from "./ui";

const LABELS: Record<string, string> = {
  minutes: "Min", pts: "Pts", reb: "Reb", ast: "Ast", fg3m: "3PM", fgm: "FGM", fga: "FGA",
  fg3a: "3PA", ftm: "FTM", fta: "FTA", oreb: "OReb", dreb: "DReb", stl: "Stl", blk: "Blk",
  tov: "TO", pf: "PF",
};
const FULL_ORDER = ["minutes", "pts", "reb", "ast", "fg3m", "fgm", "fga", "fg3a", "ftm", "fta", "oreb", "dreb", "stl", "blk", "tov", "pf"];

/** Single-game player projections: "24 to 34, most likely 29", and next to it, once the
 *  game has started, what the player actually did. */
export function ProjectionsTab({ game, data }: { game: GameDetail; data: GameProjections | undefined }) {
  const [full, setFull] = useState(false);
  if (!data || !data.available) {
    return <Empty>Player projections appear with the game's prediction, usually a few days before tip-off.</Empty>;
  }
  const stats = full ? FULL_ORDER.filter((s) => data.stats.includes(s)) : data.headline;
  const started = game.status === "live" || game.status === "final";
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row spread small muted">
        <span>
          {data.locked ? "Locked 30 minutes before tip-off" : "Latest projection, locks 30 minutes before tip-off"}
          {data.updated_after_lock ? " · updated after lock, not graded" : ""}
          {started ? " · actual first, projection below" : " · most likely, with the likely range (8 in 10 games)"}
        </span>
        <label className="check">
          <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Full projection
        </label>
      </div>
      {([["away", game.away.name], ["home", game.home.name]] as const).map(([side, name]) => (
        <section className="card" key={side}>
          <h3>{name}</h3>
          <div className="table-wrap auto-height">
            <table aria-label={`${name} projections`}>
              <thead>
                <tr>
                  <th scope="col" className="left">Player</th>
                  {stats.map((s) => <th scope="col" key={s}>{LABELS[s] ?? s}</th>)}
                </tr>
              </thead>
              <tbody>
                {data.teams[side].map((p) => <Row key={p.player_id} p={p} stats={stats} started={started} />)}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <p className="small muted">
        Projected minutes times each player's per-minute rates, adjusted for the opponent, pace, rest, and absent
        teammates. Plus/minus is not projected.
      </p>
    </div>
  );
}

function Row({ p, stats, started }: { p: ProjectedPlayer; stats: string[]; started: boolean }) {
  const league = useLeague();
  return (
    <tr>
      <td className="left">
        <Link className="link" to={`/${league}/players/${p.player_id}`}>{p.name}</Link>
        {started && p.did_not_play && <span className="muted small"> · did not play</span>}
      </td>
      {stats.map((s) => {
        const r = p.projection[s];
        if (!r) return <td key={s}>–</td>;
        const range = `${Math.round(r.low)}–${Math.round(r.high)}`;
        const actual = p.actual?.[s];
        if (started && actual !== undefined && actual !== null) {
          const inside = actual >= Math.round(r.low) && actual <= Math.round(r.high);
          return (
            <td key={s} className="num" title={`Projected ${Math.round(r.expected)} (${range})`}>
              <div style={{ fontWeight: 700 }}>{s === "minutes" ? Math.round(actual) : actual}</div>
              <div className="small muted">{Math.round(r.expected)} · {range}{inside ? "" : " ✗"}</div>
            </td>
          );
        }
        return (
          <td key={s} className="num">
            <div style={{ fontWeight: 700 }}>{Math.round(r.expected)}</div>
            <div className="small muted">{range}</div>
          </td>
        );
      })}
    </tr>
  );
}
