import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type GameLogRow, type PlayerDetail } from "../api";
import { age, fixed, height, seasonLabel, shortDate, signed } from "../format";
import { FormChart } from "../components/Charts";
import { Empty, ErrorNote, Headshot, Loading, SeasonPicker, Segmented, Tabs, useLeague, useSeasonParam } from "../components/ui";

type Tab = "overview" | "log";
type FormStat = "pts" | "reb" | "ast" | "minutes" | "fg3m";
const FORM_STATS: [FormStat, string][] = [["pts", "Points"], ["reb", "Rebounds"], ["ast", "Assists"], ["fg3m", "3PM"], ["minutes", "Minutes"]];

export function PlayerPage() {
  const league = useLeague();
  const { id } = useParams();
  const [season, setSeason] = useSeasonParam();
  const [tab, setTab] = useState<Tab>("overview");
  const player = useQuery({
    queryKey: ["player", league, id, season],
    queryFn: () => api<PlayerDetail>(`${league}/players/${id}`, { season }),
  });
  if (player.isLoading) return <main className="page"><Loading what="Loading player" /></main>;
  if (player.error || !player.data) return <main className="page"><ErrorNote error={player.error} /></main>;
  const d = player.data;
  const p = d.player;
  const line = d.season_stats.find((r) => r.is_total) ?? d.season_stats[0];
  const years = age(p.birth_date);

  return (
    <main className="page">
      <header className="row" style={{ gap: 14 }}>
        <Headshot src={p.headshot} alt="" large />
        <div className="stack" style={{ gap: 4, minWidth: 0 }}>
          <h1>{p.name}</h1>
          <div className="muted">
            {p.position ?? "–"}
            {p.team_id && <> · <Link className="link" to={`/${league}/teams/${p.team_id}`}>{p.team}</Link></>}
            {" · "}{height(p.height_inches)}
            {league === "ncaam" ? (p.class_year ? ` · ${p.class_year}` : "") : years ? ` · age ${years}` : ""}
          </div>
          {line && (
            <div style={{ fontWeight: 700 }} className="num">
              {fixed(line.ppg)} PPG · {fixed(line.rpg)} RPG · {fixed(line.apg)} APG
              <span className="muted small"> · {seasonLabel(d.season)}</span>
            </div>
          )}
        </div>
        <div style={{ marginLeft: "auto" }}>
          <SeasonPicker value={season} onChange={setSeason} />
        </div>
      </header>
      <Tabs tabs={[["overview", "Overview"], ["log", "Game log"]]} value={tab} onChange={setTab} />
      {tab === "overview" ? <Overview d={d} /> : <GameLog rows={d.game_log} />}
    </main>
  );
}

function Overview({ d }: { d: PlayerDetail }) {
  const league = useLeague();
  const [stat, setStat] = useState<FormStat>("pts");
  const label = FORM_STATS.find(([k]) => k === stat)![1];
  const played = d.recent_form.filter((g) => !g.did_not_play);
  const total = d.season_stats.find((r) => r.is_total) ?? d.season_stats[0];
  const averages: Record<FormStat, number | null> = {
    pts: total?.ppg ?? null, reb: total?.rpg ?? null, ast: total?.apg ?? null,
    minutes: total?.mpg ?? null, fg3m: total?.fg3m_pg ?? null,
  };
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card">
        <h3>Season averages</h3>
        {d.season_stats.length === 0 ? <Empty>No games this season.</Empty> : (
          <div className="table-wrap auto-height">
            <table aria-label="Season averages">
              <thead><tr><th scope="col" className="left">Team</th><th scope="col">GP</th><th scope="col">MPG</th><th scope="col">PPG</th><th scope="col">RPG</th><th scope="col">APG</th><th scope="col">SPG</th><th scope="col">BPG</th><th scope="col">FG%</th><th scope="col">3P%</th><th scope="col">FT%</th></tr></thead>
              <tbody>
                {d.season_stats.map((r) => (
                  <tr key={`${r.team_id}-${r.is_total}`}>
                    <td className="left">{r.is_total ? "Total" : r.team}</td>
                    <td>{r.games}</td><td>{fixed(r.mpg)}</td><td>{fixed(r.ppg)}</td><td>{fixed(r.rpg)}</td><td>{fixed(r.apg)}</td>
                    <td>{fixed(r.spg)}</td><td>{fixed(r.bpg)}</td><td>{fixed(r.fg_pct)}</td><td>{fixed(r.fg3_pct)}</td><td>{fixed(r.ft_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="grid-2">
        <section className="card">
          <div className="row spread">
            <h3>Recent form</h3>
            <select aria-label="Stat" id="form-stat" value={stat} onChange={(e) => setStat(e.target.value as FormStat)}>
              {FORM_STATS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
          {played.length ? (
            <FormChart stat={stat} label={label} average={averages[stat]}
              games={played.map((g) => ({ date: g.date, opponent: g.opponent, value: g[stat] }))} />
          ) : <Empty>No recent games.</Empty>}
        </section>
        <section className="card">
          <h3>Rest-of-season projection (per game)</h3>
          {d.projection ? (
            <div className="stat-tiles">
              {([["pts", "Points"], ["reb", "Rebounds"], ["ast", "Assists"], ["fg3m", "3PM"], ["minutes", "Minutes"]] as const).map(([k, l]) => (
                <div className="tile" key={k}>
                  <div className="small muted">{l}</div>
                  <div className="value num">{d.projection![k].expected}</div>
                  <div className="small muted num">{d.projection![k].low}–{d.projection![k].high}</div>
                </div>
              ))}
            </div>
          ) : <Empty>Projections appear after a few games.</Empty>}
        </section>
      </div>
      <section className="card">
        <h3>Most similar players (playing style)</h3>
        {d.similar.length === 0 ? <Empty>Not enough minutes to compare yet.</Empty> : (
          <ul className="stack" style={{ margin: 0, paddingLeft: 18 }}>
            {d.similar.map((s) => (
              <li key={s.player_id}>
                <Link className="link" to={`/${league}/players/${s.player_id}?season=${d.season}`}>{s.name}</Link>
                <span className="muted small"> {s.team} · {s.position} · {fixed(s.ppg)} / {fixed(s.rpg)} / {fixed(s.apg)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** An actual stat with the game's projection beside it, when there was one. */
function Actual({ value, projected }: { value: number | null; projected: number | undefined }) {
  return (
    <>
      {value ?? "–"}
      {projected !== undefined && <span className="muted small" title="Projected"> ({Math.round(projected)})</span>}
    </>
  );
}

function GameLog({ rows }: { rows: GameLogRow[] }) {
  const league = useLeague();
  const [kind, setKind] = useState<"regular" | "post">("regular");
  const shown = rows.filter((r) => (kind === "post" ? r.season_type === "post" : r.season_type !== "post")).slice().reverse();
  const hasPlayoffs = rows.some((r) => r.season_type === "post");
  const hasProjections = rows.some((r) => r.projection);
  return (
    <div className="stack">
      {hasPlayoffs && <Segmented label="Season type" value={kind} onChange={setKind} options={[["regular", "Regular season"], ["post", "Playoffs"]]} />}
      {hasProjections && <span className="small muted">Projected values are in parentheses, from the projection locked before each game.</span>}
      {shown.length === 0 ? <Empty>No games.</Empty> : (
        <div className="table-wrap">
          <table aria-label="Game log">
            <thead>
              <tr><th scope="col" className="left">Date</th><th scope="col" className="left">Opp</th><th scope="col" className="left">Result</th><th scope="col">Min</th><th scope="col">Pts</th><th scope="col">Reb</th><th scope="col">Ast</th><th scope="col">Stl</th><th scope="col">Blk</th><th scope="col">TO</th><th scope="col">FG</th><th scope="col">3PT</th><th scope="col">FT</th><th scope="col">+/-</th></tr>
            </thead>
            <tbody>
              {shown.map((g) => (
                <tr key={g.game_id}>
                  <td className="left"><Link className="link" to={`/${league}/games/${g.game_id}`}>{shortDate(g.date)}</Link></td>
                  <td className="left">{g.home ? "vs" : "@"} {g.opponent}</td>
                  <td className="left">{g.result}</td>
                  {g.did_not_play ? <td colSpan={11} className="left muted">{g.dnp_reason ?? "Did not play"}</td> : (
                    <>
                      <td><Actual value={g.minutes} projected={g.projection?.minutes} /></td>
                      <td><Actual value={g.pts} projected={g.projection?.pts} /></td>
                      <td><Actual value={g.reb} projected={g.projection?.reb} /></td>
                      <td><Actual value={g.ast} projected={g.projection?.ast} /></td>
                      <td>{g.stl}</td><td>{g.blk}</td><td>{g.tov}</td>
                      <td>{g.fgm}-{g.fga}</td>
                      <td>{g.fg3m}-{g.fg3a}{g.projection?.fg3m !== undefined && <span className="muted small" title="Projected 3PM"> ({Math.round(g.projection.fg3m)})</span>}</td>
                      <td>{g.ftm}-{g.fta}</td><td>{signed(g.plus_minus, 0)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
