import { useQuery } from "@tanstack/react-query";
import { type SortingState } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { api, type PlayerRow, type TeamRow } from "../api";
import { seasonLabel } from "../format";
import { DataTable } from "../components/DataTable";
import { playerColumns, positionFilter, PositionFilter, qualifierFor } from "../components/playerColumns";
import { ErrorNote, Loading, SeasonPicker, Segmented, useLeague, useMeta, useSeasonParam } from "../components/ui";
import { matches } from "../search";

type Scope = "league" | "team" | "conference";

export function PlayersPage() {
  const league = useLeague();
  const meta = useMeta();
  const [season, setSeason] = useSeasonParam();
  const [scope, setScope] = useState<Scope>("league");
  const [team, setTeam] = useState<number | undefined>();
  const [conf, setConf] = useState<string | undefined>();
  const [position, setPosition] = useState("All");
  const [qualifiedOnly, setQualifiedOnly] = useState(true);
  const [query, setQuery] = useState("");
  const [sorting, setSorting] = useState<SortingState>([{ id: "ppg", desc: true }]);

  const teams = useQuery({
    queryKey: ["teams", league, season],
    queryFn: () => api<{ teams: TeamRow[] }>(`${league}/teams`, { season }),
  });
  const teamList = useMemo(() => [...(teams.data?.teams ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [teams.data]);
  const teamId = scope === "team" ? team ?? teamList[0]?.id : undefined;
  const confName = scope === "conference" ? conf ?? meta.data?.conferences[0] : undefined;

  const players = useQuery({
    queryKey: ["players", league, season, scope, teamId, confName],
    queryFn: () => api<{ players: PlayerRow[] }>(`${league}/players`, { season, scope, team: teamId, conf: confName }),
    enabled: scope === "league" || (scope === "team" && teamId !== undefined) || (scope === "conference" && !!confName),
  });

  const qualifier = qualifierFor(sorting[0]?.id);
  const rows = (players.data?.players ?? []).filter(
    (r) =>
      positionFilter(position)(r) &&
      matches(query, [r.name]) &&
      (!qualifiedOnly || scope === "team" || query !== "" || r.qualified[qualifier]),
  );
  const columns = useMemo(() => playerColumns(league, scope !== "team"), [league, scope]);
  const scopes: [Scope, string][] = league === "nba"
    ? [["team", "Team"], ["league", "League"]]
    : [["team", "Team"], ["conference", "Conference"], ["league", "Division I"]];

  return (
    <main className="page">
      <div className="row spread">
        <h1>{league === "nba" ? "NBA players" : "Division I players"}{season ? ` · ${seasonLabel(season)}` : ""}</h1>
        <SeasonPicker value={season} onChange={setSeason} />
      </div>
      <div className="row">
        <input type="search" id="player-search" placeholder="Search players" value={query}
          onChange={(e) => setQuery(e.target.value)} aria-label="Search players" />
        <Segmented label="Scope" value={scope} onChange={setScope} options={scopes} />
        {scope === "team" && (
          <select aria-label="Team" id="players-team" value={teamId ?? ""} onChange={(e) => setTeam(Number(e.target.value))}>
            {teamList.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        )}
        {scope === "conference" && (
          <select aria-label="Conference" id="players-conf" value={confName ?? ""} onChange={(e) => setConf(e.target.value)}>
            {(meta.data?.conferences ?? []).map((c) => <option key={c}>{c}</option>)}
          </select>
        )}
      </div>
      <div className="row">
        <PositionFilter value={position} onChange={setPosition} />
        {scope !== "team" && (
          <label className="check" title={league === "nba"
            ? "Official NBA minimums: 70% of team games; shooting percentages need 300 FG, 82 threes, or 125 free throws made over a full season"
            : "Official NCAA minimums"}>
            <input type="checkbox" id="qualified-only" checked={qualifiedOnly} onChange={(e) => setQualifiedOnly(e.target.checked)} />
            Qualified only
          </label>
        )}
      </div>
      {players.isLoading && <Loading what="Loading players" />}
      {players.error && <ErrorNote error={players.error} />}
      {players.data && (
        <DataTable label="Players" data={rows} columns={columns} sorting={sorting} onSortingChange={setSorting}
          rowKey={(r) => `${r.player_id}-${r.is_total ? "total" : r.team_id}`} />
      )}
      <p className="small muted">
        {rows.length} players. Tap a column to sort. Traded players show their combined totals; per-team rows are on each team's roster.
      </p>
    </main>
  );
}
