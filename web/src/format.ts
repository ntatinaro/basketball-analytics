// Display formatting shared by every screen. Times are shown in the viewer's time zone.

export const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

export function pct(p: number | null | undefined, digits = 0): string {
  return p === null || p === undefined ? "–" : `${(100 * p).toFixed(digits)}%`;
}

export function signed(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  const v = Number(x.toFixed(digits));
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}

export function fixed(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined || Number.isNaN(x) ? "–" : x.toFixed(digits);
}

export function seasonLabel(season: number): string {
  return `${season - 1}-${String(season % 100).padStart(2, "0")}`;
}

export function tipTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function longDate(d: Date): string {
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function parseIsoDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function height(inches: number | null): string {
  if (!inches) return "–";
  return `${Math.floor(inches / 12)}′${inches % 12}″`;
}

export function age(birth: string | null): number | null {
  if (!birth) return null;
  const b = new Date(birth);
  const now = new Date();
  let a = now.getFullYear() - b.getFullYear();
  if (now < new Date(now.getFullYear(), b.getMonth(), b.getDate())) a -= 1;
  return a;
}

/** "Boston 68% · favored by 5 · total 224 · range −7 to +17" from the home team's side. */
export function favoredText(homeName: string, awayName: string, prob: number, margin: number): string {
  const homeFav = prob >= 0.5;
  const name = homeFav ? homeName : awayName;
  const p = homeFav ? prob : 1 - prob;
  const m = Math.abs(margin);
  return `${name} ${pct(p)} · favored by ${m < 0.5 ? "<1" : Math.round(m)}`;
}

/** The likely margin range from the favored team's side, e.g. "−7 to +17". */
export function rangeText(homeProb: number, low: number, high: number): string {
  const [a, b] = homeProb >= 0.5 ? [low, high] : [-high, -low];
  return `${signed(a, 0)} to ${signed(b, 0)}`;
}
