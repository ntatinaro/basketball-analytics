import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CalibrationBin } from "../api";
import { pct, shortDate, signed } from "../format";

// Single-series charts: one validated series color, a recessive grid and axes, thin
// marks, and a hover tooltip. Text uses text colors, never the series color.
const axis = { stroke: "var(--line)", tick: { fill: "var(--muted)", fontSize: 12 }, tickLine: false };
const tooltipStyle = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--text)" },
  labelStyle: { color: "var(--text-2)" },
  itemStyle: { color: "var(--text)" },
};

/** Team rating over the season, with its likely range shaded. */
export function RatingTrend({ points }: { points: { as_of: string; overall: number; overall_se: number; games_played: number }[] }) {
  const data = points.map((p) => ({
    date: p.as_of,
    rating: p.overall,
    range: [p.overall - 1.28 * p.overall_se, p.overall + 1.28 * p.overall_se],
    games: p.games_played,
  }));
  return (
    <div className="chart" role="img" aria-label="Rating over the season with likely range">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="date" {...axis} tickFormatter={shortDate} minTickGap={40} />
          <YAxis {...axis} tickFormatter={(v) => signed(v, 0)} width={44} />
          <ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="4 4" />
          <Area dataKey="range" stroke="none" fill="var(--series-band)" isAnimationActive={false} />
          <Line dataKey="rating" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Tooltip
            {...tooltipStyle}
            labelFormatter={(v) => shortDate(String(v))}
            formatter={(value: number | number[], name) =>
              name === "range"
                ? [`${signed((value as number[])[0])} to ${signed((value as number[])[1])}`, "Likely range"]
                : [signed(value as number), "Rating"]
            }
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One stat across recent games, with the season average as a reference line. */
export function FormChart({ games, stat, label, average }: {
  games: { date: string; opponent: string; value: number | null }[];
  stat: string;
  label: string;
  average: number | null;
}) {
  return (
    <div className="chart" role="img" aria-label={`${label} in recent games`}>
      <ResponsiveContainer>
        <ComposedChart data={games} margin={{ top: 8, right: 12, bottom: 0, left: -20 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="opponent" {...axis} interval={0} />
          <YAxis {...axis} width={40} allowDecimals={false} />
          {average !== null && (
            <ReferenceLine y={average} stroke="var(--muted)" strokeDasharray="4 4"
              label={{ value: `season ${average.toFixed(1)}`, fill: "var(--muted)", fontSize: 12, position: "insideTopRight" }} />
          )}
          <Bar dataKey="value" name={stat} fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
          <Tooltip {...tooltipStyle} cursor={{ fill: "var(--surface-2)" }}
            labelFormatter={(v, payload) => (payload?.[0] ? `vs ${v} · ${shortDate(payload[0].payload.date)}` : String(v))}
            formatter={(value: number) => [value, label]} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Do 70% picks win about 70% of the time? Dots should sit on the dashed diagonal. */
export function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  const data = bins.map((b) => ({ predicted: b.predicted, actual: b.actual, games: b.games }));
  return (
    <div className="chart" role="img" aria-label="Predicted win chance against how often the favorite actually won">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -8 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" />
          <XAxis type="number" dataKey="predicted" domain={[0.5, 1]} {...axis} tickFormatter={(v) => pct(v)}
            label={{ value: "predicted", fill: "var(--muted)", fontSize: 12, position: "insideBottomRight", offset: -2 }} />
          <YAxis type="number" domain={[0.4, 1]} {...axis} tickFormatter={(v) => pct(v)} width={48} />
          <ReferenceLine segment={[{ x: 0.5, y: 0.5 }, { x: 1, y: 1 }]} stroke="var(--muted)" strokeDasharray="4 4" />
          <Line dataKey="actual" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Scatter dataKey="actual" fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false} />
          <Tooltip {...tooltipStyle}
            labelFormatter={(v) => `Predicted ${pct(Number(v))}`}
            formatter={(value: number, _n, item) => [`${pct(value)} (${item.payload.games} games)`, "Favorite won"]} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
