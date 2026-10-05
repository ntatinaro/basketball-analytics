import { useQuery } from "@tanstack/react-query";
import { type SortingState } from "@tanstack/react-table";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type PerGame, type TeamDetail } from "../api";
import { fixed, pct, seasonLabel, shortDate, signed } from "../format";
import { RatingTrend } from "../components/Charts";
import { DataTable } from "../components/DataTable";
import { playerColumns, positionFilter, PositionFilter } from "../components/playerColumns";
import { Empty, ErrorNote, Loading, SeasonPicker, Tabs, TeamLogo, useLeague, useSeasonParam } from "../components/ui";

type Tab = "overview" | "stats" | "roster" | "schedule";

export function TeamPage() {
  const league = useLeague();
  const { id } = useParams();
  const navigate = useNavigate();
  const [season, setSeason] = useSeasonParam();
  const [tab, setTab] = useState<Tab>("overview");
  const team = useQuery({
    queryKey: ["team", league, id, season],
    queryFn: () => api<TeamDetail>(`${league}/teams/${id}`, { season }),
  });
  if (team.isLoading) return <main className="page"><Loading what="Loading team" /></main>;
  if (team.error || !team.data) return <main className="page"><ErrorNote error={team.error} /></main>;
  const d = team.data;
  const t = d.team;
  const list = d.switcher;
  const index = list.findIndex((x) => x.id === t.id);
  const go = (teamId: number) => navigate(`/${league}/teams/${teamId}${season ? `?season=${season}` : ""}`);

  return (
    <main className="page">
      <div className="row spread">
        <Link to={`/${league}/teams${season ? `?season=${season}` : ""}`} className="link small">← All teams</Link>
        <div className="row">
          <button className="btn" type="button" aria-label="Previous team" onClick={() => go(list[(index - 1 + list.length) % list.length].id)}>‹</button>
          <select aria-label="Switch team" id="team-switcher" value={t.id} onChange={(e) => go(Number(e.target.value))}>
            {list.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
          <button className="btn" type="button" aria-label="Next team" onClick={() => go(list[(index + 1) % list.length].id)}>›</button>
          <SeasonPicker value={season} onChange={setSeason} />
        </div>
      </div>

      <header className="row" style={{ gap: 14 }}>
        <TeamLogo src={t.logo} alt="" large />
        <div className="stack" style={{ gap: 4 }}>
          <h1>{t.name}</h1>
          <div className="muted">
            {t.wins}-{t.losses}{t.conference ? ` · ${t.conference}` : ""} · {seasonLabel(d.season)}
          </div>
        </div>
        <div className="stack" style={{ gap: 2, marginLeft: "auto", textAlign: "right" }}>
          <span className="rating-big num">{signed(t.rating)}</span>
          <span className="small muted">
            likely {signed((t.rating ?? 0) - 1.28 * (t.rating_se ?? 0))} to {signed((t.rating ?? 0) + 1.28 * (t.rating_se ?? 0))}
            {t.rank ? ` · #${t.rank} of ${d.league_size}` : ""}
          </span>
          {t.low_confidence && <span className="pill">Early season, low confidence</span>}
        </div>
      </header>

      <Tabs value={tab} onChange={setTab}
        tabs={[["overview", "Overview"], ["stats", "Team Stats"], ["roster", "Roster"], ["schedule", "Schedule"]]} />
      {tab === "overview" && <Overview d={d} />}
      {tab === "stats" && <TeamStats d={d} />}
      {tab === "roster" && <Roster d={d} />}
      {tab === "schedule" && <Schedule d={d} />}
    </main>
  );
}

function Overview({ d }: { d: TeamDetail }) {
  const t = d.team;
  return (
    <div className="grid-2">
      <section className="card">
        <h3>Ratings</h3>
        <div className="stat-tiles">
          <div className="tile"><div className="small muted">Overall</div><div className="value num">{signed(t.rating)}</div></div>
          <div className="tile"><div className="small muted">Offense (pts/100)</div><div className="value num">{fixed(t.offense)}</div></div>
          <div className="tile"><div className="small muted">Defense (allowed/100)</div><div className="value num">{fixed(t.defense)}</div></div>
          <div className="tile"><div className="small muted">Pace</div><div className="value num">{fixed(t.pace)}</div></div>
        </div>
        <h3>Rating over the season</h3>
        {d.overview.trend.length > 1 ? <RatingTrend points={d.overview.trend} /> : <Empty>The trend appears after a few games.</Empty>}
      </section>
      <section className="card">
        <h3>Sub-ratings (league percentile)</h3>
        {d.overview.sub_ratings.length === 0 && <Empty>Sub-ratings appear after a few games.</Empty>}
        {d.overview.sub_ratings.map((s) => (
          <div className="bar-row" key={s.metric}>
            <span className="small">{s.label}</span>
            <div className="bar-track" role="img" aria-label={`${s.label}: ${Math.round(s.percentile)}th percentile`}>
              <div className="bar-fill" style={{ width: `${Math.max(s.percentile, 2)}%` }} />
            </div>
            <span className="small num" style={{ textAlign: "right" }}>{Math.round(s.percentile)}</span>
          </div>
        ))}
        <p className="small muted">100 = best in the league at it. Pace: 100 = fastest.</p>
      </section>
    </div>
  );
}

const STAT_ROWS: [string, string, boolean][] = [
  ["pts", "Points", false], ["fg_pct", "FG%", true], ["fg3m", "3-pointers made", false], ["fg3_pct", "3P%", true],
  ["ftm", "Free throws made", false], ["ft_pct", "FT%", true], ["reb", "Rebounds", false], ["oreb", "Offensive rebounds", false],
  ["ast", "Assists", false], ["stl", "Steals", false], ["blk", "Blocks", false], ["tov", "Turnovers", false],
];

function TeamStats({ d }: { d: TeamDetail }) {
  const s = d.team_stats;
  if (!s) return <Empty>No team stats for this season yet.</Empty>;
  const cell = (row: PerGame, key: string) => fixed(row[key] ?? null);
  return (
    <div className="table-wrap auto-height">
      <table aria-label="Team stats per game">
        <thead><tr><th scope="col" className="left">Per game ({s.games} games)</th><th scope="col">{d.team.abbreviation}</th><th scope="col">Opponents</th></tr></thead>
        <tbody>
          {STAT_ROWS.map(([key, label]) => (
            <tr key={key}><td className="left">{label}</td><td>{cell(s.team, key)}</td><td>{cell(s.opponents, key)}</td></tr>
          ))}
          <tr><td className="left">Pace</td><td>{fixed(s.pace)}</td><td>{fixed(s.pace)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

function Roster({ d }: { d: TeamDetail }) {
  const league = useLeague();
  const [position, setPosition] = useState("All");
  const [sorting, setSorting] = useState<SortingState>([{ id: "ppg", desc: true }]);
  const rows = d.roster.filter(positionFilter(position));
  return (
    <div className="stack">
      <div className="row">
        <PositionFilter value={position} onChange={setPosition} />
        <span className="small muted">Impact ratings arrive in V3; sorted by points per game until then.</span>
      </div>
      <DataTable label="Roster" data={rows} columns={playerColumns(league, false)} sorting={sorting}
        onSortingChange={setSorting} rowKey={(r) => `${r.player_id}-${r.team_id}`} autoHeight />
    </div>
  );
}

function Schedule({ d }: { d: TeamDetail }) {
  const league = useLeague();
  if (!d.schedule.length) return <Empty>No games scheduled.</Empty>;
  return (
    <div className="table-wrap auto-height">
      <table aria-label="Schedule">
        <thead>
          <tr><th scope="col" className="left">Date</th><th scope="col" className="left">Opponent</th><th scope="col" title="Our win chance for this team (locked before tip-off)">Win chance</th><th scope="col">Pred. margin</th><th scope="col" className="left">Result</th></tr>
        </thead>
        <tbody>
          {d.schedule.map((g) => (
            <tr key={g.game_id}>
              <td className="left"><Link className="link" to={`/${league}/games/${g.game_id}`}>{shortDate(g.start_time)}</Link></td>
              <td className="left">{g.home ? "vs" : "@"} {g.opponent.abbreviation}</td>
              <td>{pct(g.win_prob)}{g.prediction_source === "backtest" && <span className="muted small"> bt</span>}</td>
              <td>{signed(g.predicted_margin)}</td>
              <td className="left">
                {g.result ? (
                  <>
                    {g.result.won ? "W" : "L"} {g.result.score}
                    {g.result.model_right !== null && (
                      <span className={`pill ${g.result.model_right ? "good" : "bad"}`}>{g.result.model_right ? "✓" : "✗"}</span>
                    )}
                  </>
                ) : <span className="muted">Upcoming</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small muted" style={{ padding: "0 10px" }}>“bt” = backtest prediction, replayed using only games before that date.</p>
    </div>
  );
}
