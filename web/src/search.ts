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
