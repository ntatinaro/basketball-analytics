import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type League, type Meta } from "../api";
import { seasonLabel } from "../format";

export function useLeague(): League {
  const { league } = useParams();
  return league === "ncaam" ? "ncaam" : "nba";
}

export function useMeta() {
  const league = useLeague();
  return useQuery({ queryKey: ["meta", league], queryFn: () => api<Meta>(`${league}/meta`), staleTime: 60_000 });
}

/** The season in the URL (?season=), or undefined for the current season. */
export function useSeasonParam(): [number | undefined, (s: number | undefined) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("season");
  const season = raw ? Number(raw) : undefined;
  const set = (s: number | undefined) => {
    const next = new URLSearchParams(params);
    if (s === undefined) next.delete("season");
    else next.set("season", String(s));
    setParams(next, { replace: true });
  };
  return [season, set];
}

export function SeasonPicker({ value, onChange }: { value: number | undefined; onChange: (s: number | undefined) => void }) {
  const meta = useMeta();
  if (!meta.data) return null;
  const current = meta.data.current_season;
  return (
    <select
      aria-label="Season"
      id="season-picker"
      value={value ?? current}
      onChange={(e) => onChange(Number(e.target.value) === current ? undefined : Number(e.target.value))}
    >
      {meta.data.seasons.map((s) => (
        <option key={s} value={s}>
          {seasonLabel(s)}
        </option>
      ))}
    </select>
  );
}

export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: [T, string][];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: [T, string][]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([v, text]) => (
        <button key={v} type="button" role="tab" aria-selected={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

/** An image that quietly leaves an empty space of the same size if it fails to load. */
function SafeImage({ src, alt, className }: { src?: string | null; alt: string; className: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <span className={className} aria-hidden />;
  return <img className={className} src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />;
}

export function TeamLogo({ src, alt, large = false }: { src?: string | null; alt: string; large?: boolean }) {
  return <SafeImage src={src} alt={alt} className={large ? "logo-lg" : "logo"} />;
}

export function Headshot({ src, alt, large = false }: { src?: string | null; alt: string; large?: boolean }) {
  return <SafeImage src={src} alt={alt} className={large ? "headshot-lg" : "headshot"} />;
}

export function ProbBar({ homeProb, label }: { homeProb: number; label: string }) {
  return (
    <div className="prob-bar" role="img" aria-label={label}>
      <span style={{ width: `${(1 - homeProb) * 100}%`, opacity: 0.45 }} />
      <span style={{ width: `${homeProb * 100}%` }} />
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="empty">{what}…</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return <div className="empty">Couldn't load this: {message}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
