import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type MarketCompare, type ReportCard, type Summary } from "../api";
import { pct, seasonLabel, shortDate } from "../format";
import { CalibrationChart } from "../components/Charts";
import { Empty, ErrorNote, Loading, SeasonPicker, useLeague, useSeasonParam } from "../components/ui";

export function ReportCardPage() {
  const league = useLeague();
  const [season, setSeason] = useSeasonParam();
  const card = useQuery({
    queryKey: ["report", league, season],
    queryFn: () => api<ReportCard>(`${league}/report-card`, { season }),
  });
  return (
    <main className="page">
      <div className="row spread">
        <h1>Model report card{card.data ? ` · ${seasonLabel(card.data.season)}` : ""}</h1>
        <SeasonPicker value={season} onChange={setSeason} />
      </div>
      <p className="small muted">
        Every prediction is locked 30 minutes before tip-off and graded after the game. Only locked predictions count.
      </p>
      {card.isLoading && <Loading what="Loading report card" />}
      {card.error && <ErrorNote error={card.error} />}
      {card.data && <Live data={card.data} />}
      {card.data && <Backtests data={card.data} />}
    </main>
  );
}

function Tiles({ s }: { s: Summary }) {
  return (
    <div className="stat-tiles">
      <div className="tile"><div className="small muted">Games</div><div className="value num">{s.games}</div></div>
      <div className="tile"><div className="small muted">Picked the winner</div><div className="value num">{pct(s.accuracy, 1)}</div></div>
      <div className="tile" title="Lower is better. Rewards being confident and right; punishes being confident and wrong.">
        <div className="small muted">Log loss</div><div className="value num">{s.log_loss.toFixed(3)}</div>
      </div>
      <div className="tile" title="Lower is better."><div className="small muted">Brier score</div><div className="value num">{s.brier.toFixed(3)}</div></div>
    </div>
  );
}

function Market({ m }: { m: MarketCompare }) {
  return (
    <div className="stack">
      <div className="table-wrap auto-height">
        <table aria-label="Model vs. betting market">
          <thead><tr><th scope="col" className="left">{m.model.games} games</th><th scope="col">Right</th><th scope="col">Log loss</th></tr></thead>
          <tbody>
            <tr><td className="left">Our model</td><td>{pct(m.model.accuracy, 1)}</td><td>{m.model.log_loss.toFixed(3)}</td></tr>
            <tr><td className="left">Market</td><td>{pct(m.market.accuracy, 1)}</td><td>{m.market.log_loss.toFixed(3)}</td></tr>
          </tbody>
        </table>
      </div>
      <span className={`pill ${m.target.within_target ? "good" : "bad"}`}>
        {m.target.within_target ? "✓ Within target" : "✗ Outside target"}: {(100 * m.target.accuracy_gap).toFixed(1)} points behind on
        accuracy, {m.target.log_loss_gap >= 0 ? "+" : ""}{m.target.log_loss_gap.toFixed(3)} log loss (target: within 1.5 points and 0.010)
      </span>
      {m.note && <span className="small muted">{m.note}</span>}
    </div>
  );
}

function Live({ data }: { data: ReportCard }) {
  const league = useLeague();
  const live = data.live;
  return (
    <section className="card">
      <h2>This season</h2>
      {live.games === 0 || !live.model ? (
        <Empty>No graded predictions yet this season. The record starts with the first locked game.</Empty>
      ) : (
        <>
          <Tiles s={live.model} />
          {live.market && <Market m={live.market} />}
          <div className="grid-2">
            <div className="stack">
              <h3>Calibration</h3>
              {live.calibration && <CalibrationChart bins={live.calibration} />}
            </div>
            <div className="stack">
              <h3>By month</h3>
              <div className="table-wrap auto-height">
                <table aria-label="Accuracy by month">
                  <thead><tr><th scope="col" className="left">Month</th><th scope="col">Games</th><th scope="col">Right</th><th scope="col">Log loss</th></tr></thead>
                  <tbody>
                    {live.by_month?.map((m) => (
                      <tr key={m.month}><td className="left">{m.month}</td><td>{m.games}</td><td>{pct(m.accuracy, 1)}</td><td>{m.log_loss.toFixed(3)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <h3>Every graded prediction</h3>
          <div className="table-wrap">
            <table aria-label="Graded predictions">
              <thead><tr><th scope="col" className="left">Date</th><th scope="col" className="left">Game</th><th scope="col">Home win chance</th><th scope="col">Score</th><th scope="col" className="left">Result</th></tr></thead>
              <tbody>
                {live.predictions.map((p) => (
                  <tr key={p.game_id}>
                    <td className="left">{shortDate(p.date)}</td>
                    <td className="left"><Link className="link" to={`/${league}/games/${p.game_id}`}>{p.away} @ {p.home}</Link></td>
                    <td>{pct(p.home_win_prob)}</td><td>{p.score}</td>
                    <td className="left"><span className={`pill ${p.correct ? "good" : "bad"}`}>{p.correct ? "✓ right" : "✗ wrong"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function Backtests({ data }: { data: ReportCard }) {
  return (
    <section className="card">
      <h2>Past seasons (backtests)</h2>
      <p className="small muted">
        Each past game was predicted using only games played before it, with the same model that runs live.
        These are replays, not predictions made at the time. Past injury reports are not available, so the
        replays know which players actually sat out; live predictions only know the injury report, so expect
        live results to be a little worse.
      </p>
      {data.backtests.length === 0 && <Empty>No backtests yet.</Empty>}
      {data.backtests.map((b) => (
        <div key={b.season} className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
          <h3>{seasonLabel(b.season)}</h3>
          <Tiles s={b.model} />
          <div className="small">
            Early season (first 4 weeks): {pct(b.early_season.accuracy, 1)} right, log loss {b.early_season.log_loss.toFixed(3)} ·
            Rest of season: {pct(b.rest_of_season.accuracy, 1)} right, log loss {b.rest_of_season.log_loss.toFixed(3)}
          </div>
          {b.market && <Market m={b.market} />}
          <CalibrationChart bins={b.calibration} />
        </div>
      ))}
    </section>
  );
}
