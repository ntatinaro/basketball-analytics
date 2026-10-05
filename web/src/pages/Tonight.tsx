import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, type GameCard } from "../api";
import { favoredText, isoDate, longDate, parseIsoDate, pct, rangeText, timeZone, tipTime } from "../format";
import { Empty, ErrorNote, Loading, ProbBar, TeamLogo, useLeague } from "../components/ui";

export function TonightPage() {
  const league = useLeague();
  const [params, setParams] = useSearchParams();
  const date = params.get("date") ?? isoDate(new Date());
  const day = parseIsoDate(date);
  const games = useQuery({
    queryKey: ["games", league, date],
    queryFn: () => api<{ games: GameCard[] }>(`${league}/games`, { date, tz: timeZone }),
    refetchInterval: 60_000,
  });

  const shift = (days: number) => {
    const next = new Date(day);
    next.setDate(next.getDate() + days);
    setParams({ date: isoDate(next) });
  };
  const groups: [string, GameCard[]][] = [
    ["Live", (games.data?.games ?? []).filter((g) => g.status === "live")],
    ["Upcoming", (games.data?.games ?? []).filter((g) => g.status === "scheduled" || g.status === "postponed")],
    ["Final", (games.data?.games ?? []).filter((g) => g.status === "final")],
  ];

  return (
    <main className="page">
      <div className="row spread">
        <button className="btn" type="button" onClick={() => shift(-1)} aria-label="Previous day">‹</button>
        <h1 style={{ textAlign: "center", flex: 1 }}>{date === isoDate(new Date()) ? "Tonight" : longDate(day)}</h1>
        <button className="btn" type="button" onClick={() => shift(1)} aria-label="Next day">›</button>
      </div>
      {games.isLoading && <Loading what="Loading games" />}
      {games.error && <ErrorNote error={games.error} />}
      {games.data && games.data.games.length === 0 && <Empty>No games on {longDate(day)}.</Empty>}
      {groups.map(([title, list]) =>
        list.length ? (
          <section key={title} className="stack">
            <h3>{title}</h3>
            <div className="game-list">
              {list.map((g) => <GameCardView key={g.id} game={g} />)}
            </div>
          </section>
        ) : null,
      )}
    </main>
  );
}

function GameCardView({ game }: { game: GameCard }) {
  const league = useLeague();
  const p = game.prediction;
  const shown = p?.updated_after_lock ?? p;
  const scored = game.status === "final" || game.status === "live";
  const outs = game.players_out.home + game.players_out.away;
  return (
    <Link to={`/${league}/games/${game.id}`} className="card game-card" data-testid="game-card">
      <div className="row spread small muted">
        <span>
          {game.status === "live" ? <span className="pill live">Live</span>
            : game.status === "final" ? "Final"
            : game.status === "postponed" ? "Postponed" : tipTime(game.start_time)}
          {game.neutral_site ? " · neutral site" : ""}
        </span>
        {outs > 0 && <span className="pill">{outs} out</span>}
      </div>
      {[game.away, game.home].map((team, i) => (
        <div className="team-line" key={team.id}>
          <TeamLogo src={team.logo} alt="" />
          <span className="name">
            {team.name}
            {team.record && <span className="muted small"> {team.record.wins}-{team.record.losses}</span>}
          </span>
          <span className="score num">{scored ? (i === 0 ? game.away_score : game.home_score) : ""}</span>
        </div>
      ))}
      {shown && (
        <>
          <ProbBar homeProb={shown.home_win_prob} label={`${game.home.name} win chance ${pct(shown.home_win_prob)}`} />
          <div className="small">
            {favoredText(game.home.short_name ?? game.home.name, game.away.short_name ?? game.away.name,
              shown.home_win_prob, shown.margin_home)}
            {" · total "}{Math.round(shown.total)}
            {shown.margin_low !== null && shown.margin_high !== null && (
              <span className="muted"> · range {rangeText(shown.home_win_prob, shown.margin_low, shown.margin_high)}</span>
            )}
          </div>
          {p?.updated_after_lock && <div className="small muted">Updated after lock, not graded</div>}
          {p?.grade && (
            <span className={`pill ${p.grade.correct ? "good" : "bad"}`}>
              {p.grade.correct ? "✓ Model was right" : "✗ Model was wrong"}
            </span>
          )}
          {p?.source === "backtest" && <span className="small muted">Backtest prediction</span>}
        </>
      )}
    </Link>
  );
}
