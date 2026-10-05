import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, type SortingState } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { api, type TeamRow } from "../api";
import { fixed, seasonLabel, signed } from "../format";
import { DataTable } from "../components/DataTable";
import { ErrorNote, Loading, RowImage, SeasonPicker, Segmented, useLeague, useSeasonParam } from "../components/ui";
import { matches, useServerMatches } from "../search";

const col = createColumnHelper<TeamRow>();

export function TeamsPage() {
  const league = useLeague();
  const [season, setSeason] = useSeasonParam();
  const [scope, setScope] = useState<"all" | "East" | "West">("all");
  const [query, setQuery] = useState("");
  const [sorting, setSorting] = useState<SortingState>([{ id: "rating", desc: true }]);
  const teams = useQuery({
    queryKey: ["teams", league, season],
    queryFn: () => api<{ season: number; teams: TeamRow[] }>(`${league}/teams`, { season }),
  });

  const columns = useMemo(() => [
    col.accessor("name", {
      header: "Team",
      meta: { left: true },
      cell: (c) => (
        <a href={`/${league}/teams/${c.row.original.id}${season ? `?season=${season}` : ""}`} className="player-cell">
          <RowImage src={c.row.original.logo} className="logo" />
          <span className="link">{c.row.original.name}</span>
        </a>
      ),
    }),
    col.accessor("rank", { header: "Rank", meta: { title: "League rank by overall rating" }, sortDescFirst: false,
      cell: (c) => c.getValue() ?? "–" }),
    col.accessor("rating", {
      header: "Rating", meta: { title: "Net points per 100 possessions vs. an average team, neutral court. Likely range in brackets." },
      cell: (c) => (
        <span>
          {signed(c.getValue())} <span className="muted small">±{fixed((c.row.original.rating_se ?? 0) * 1.28)}</span>
          {c.row.original.low_confidence && <span className="pill" title="Early season, low confidence">early</span>}
        </span>
      ),
    }),
    col.accessor("offense", { header: "Off", meta: { title: "Points scored per 100 possessions, opponent-adjusted" }, cell: (c) => fixed(c.getValue()) }),
    col.accessor("defense", { header: "Def", sortDescFirst: false, meta: { title: "Points allowed per 100 possessions, opponent-adjusted (lower is better)" }, cell: (c) => fixed(c.getValue()) }),
    col.accessor((r) => r.wins - r.losses, { id: "record", header: "W-L", cell: (c) => `${c.row.original.wins}-${c.row.original.losses}` }),
    col.accessor("ppg", { header: "PPG", meta: { title: "Points per game" }, cell: (c) => fixed(c.getValue()) }),
    col.accessor("opp_ppg", { header: "Opp PPG", sortDescFirst: false, meta: { title: "Opponent points per game" }, cell: (c) => fixed(c.getValue()) }),
    col.accessor("pace", { header: "Pace", meta: { title: "Possessions per 48 minutes" }, cell: (c) => fixed(c.getValue()) }),
    col.accessor("conference_abbr", { header: "Conf", meta: { left: true }, cell: (c) => c.getValue() ?? "–" }),
  ], [league, season]);

  const server = useServerMatches(league, "teams", query);
  const rows = useMemo(() => (teams.data?.teams ?? []).filter(
    (t) => (scope === "all" || t.conference_abbr === scope)
      && (matches(query, [t.name, t.abbreviation, t.location ?? ""]) || server?.has(t.id)),
  ), [teams.data, scope, query, server]);

  return (
    <main className="page">
      <div className="row spread">
        <h1>{league === "nba" ? "NBA teams" : "Division I teams"}{season ? ` · ${seasonLabel(season)}` : ""}</h1>
        <SeasonPicker value={season} onChange={setSeason} />
      </div>
      <div className="row">
        <input type="search" id="team-search" placeholder="Search teams" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Search teams" />
        <Segmented label="Conference" value={scope} onChange={setScope}
          options={[["all", "League"], ["East", "East"], ["West", "West"]]} />
      </div>
      {teams.isLoading && <Loading what="Loading teams" />}
      {teams.error && <ErrorNote error={teams.error} />}
      {teams.data && (
        <DataTable label="Teams" data={rows} columns={columns} sorting={sorting} onSortingChange={setSorting}
          rowKey={(r) => String(r.id)} autoHeight />
      )}
      <p className="small muted">
        Rating = points per 100 possessions better or worse than an average team on a neutral court.
        Ratings update after every game.
      </p>
    </main>
  );
}
