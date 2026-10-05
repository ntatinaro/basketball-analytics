import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "./api";

// Forgiving in-page search: case-insensitive, accent-insensitive, and tolerant of a
// typo or two in longer words ("giannis antetokounpo", "jaln brunsn").

function normalize(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function distance(a: string, b: string): number {
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++)
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return dp[a.length][b.length];
}

export function matches(query: string, fields: string[]): boolean {
  const q = normalize(query.trim());
  if (!q) return true;
  const hay = normalize(fields.join(" "));
  if (hay.includes(q)) return true;
  const words = hay.split(/[\s.'-]+/).filter(Boolean);
  return q.split(/\s+/).every((term) =>
    words.some((w) => w.startsWith(term) || (term.length > 3 && distance(term, w.slice(0, term.length + 1)) <= (term.length > 6 ? 2 : 1))),
  );
}

// Server search adds what the page cannot know: nicknames ("sixers", "steph") and the
// database's typo-tolerant matching. Results are the matching IDs, after a short pause in
// typing; undefined until the server answers (pages show local matches meanwhile).
export function useServerMatches(league: string, kind: "teams" | "players", query: string): Set<number> | undefined {
  const q = query.trim();
  const [debounced, setDebounced] = useState(q);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 200);
    return () => clearTimeout(t);
  }, [q]);
  const result = useQuery({
    queryKey: ["search", league, kind, debounced],
    queryFn: () => api<{ results: { id: number; score: number }[] }>(`${league}/search/${kind}`, { q: debounced }),
    enabled: debounced.length > 0,
    staleTime: 5 * 60_000,
  });
  return useMemo(() => {
    if (!result.data || debounced !== q) return undefined;
    // Only close matches: fuzzy search also returns weak look-alikes further down.
    const top = Math.max(0, ...result.data.results.map((r) => r.score));
    return new Set(result.data.results.filter((r) => r.score >= 0.6 * top).map((r) => r.id));
  }, [result.data, debounced, q]);
}
