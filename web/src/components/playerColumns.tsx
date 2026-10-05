import { createColumnHelper } from "@tanstack/react-table";
import type { League, PlayerRow } from "../api";
import { fixed } from "../format";
import { RowImage, Segmented } from "./ui";

const col = createColumnHelper<PlayerRow>();

function stat(key: keyof PlayerRow, header: string, title: string, opts: { asc?: boolean } = {}) {
  return col.accessor((r) => r[key] as number | null, {
    id: String(key),
    header,
    meta: { title },
    sortDescFirst: !opts.asc,
    sortUndefined: "last",
    cell: (c) => fixed(c.getValue() as number | null),
  });
}

/** The shared player table columns: the roster tab and the league-wide Players table. */
export function playerColumns(league: League, withTeam: boolean) {
  return [
    col.accessor("name", {
      header: "Player",
      meta: { left: true },
      sortDescFirst: false,
      cell: (c) => {
        const r = c.row.original;
        const team = r.is_total ? r.teams.join("/") : r.team;
        return (
          <a href={`/${league}/players/${r.player_id}`} className="player-cell">
            <RowImage src={r.headshot} className="headshot" />
            <span className="player-name">
              <span className="link">{r.name}</span>
              <span className="muted small">{[withTeam ? team : null, r.position].filter(Boolean).join(" · ")}</span>
            </span>
          </a>
        );
      },
    }),
    col.accessor("games", { header: "GP", meta: { title: "Games played" } }),
    stat("mpg", "MPG", "Minutes per game"),
    stat("ppg", "PPG", "Points per game"),
    stat("rpg", "RPG", "Rebounds per game"),
    stat("apg", "APG", "Assists per game"),
    stat("spg", "SPG", "Steals per game"),
    stat("bpg", "BPG", "Blocks per game"),
    stat("topg", "TOV", "Turnovers per game", { asc: true }),
    stat("fg3m_pg", "3PM", "3-pointers made per game"),
    stat("fg_pct", "FG%", "Field goal percentage"),
    stat("fg3_pct", "3P%", "3-point percentage"),
    stat("ft_pct", "FT%", "Free throw percentage"),
    stat("orpg", "OREB", "Offensive rebounds per game"),
    stat("drpg", "DREB", "Defensive rebounds per game"),
    stat("fpg", "PF", "Personal fouls per game", { asc: true }),
    ...(withTeam
      ? [col.accessor((r) => (r.is_total ? r.teams.join("/") : r.team), {
          id: "team", header: "Team", meta: { left: true }, sortDescFirst: false,
        })]
      : []),
    col.accessor("position", { header: "Pos", meta: { left: true, title: "Position" }, sortDescFirst: false }),
  ];
}

/** Which leaderboard minimum applies to the column being sorted. */
export function qualifierFor(sortId: string | undefined): keyof PlayerRow["qualified"] {
  if (sortId === "fg_pct") return "fg";
  if (sortId === "fg3_pct") return "fg3";
  if (sortId === "ft_pct") return "ft";
  return "games";
}

export function positionFilter(position: string) {
  return (r: PlayerRow) => position === "All" || (r.position ?? "").includes(position);
}

export function PositionFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Segmented label="Position" value={value} onChange={onChange}
      options={[["All", "All positions"], ["G", "G"], ["F", "F"], ["C", "C"]]} />
  );
}
