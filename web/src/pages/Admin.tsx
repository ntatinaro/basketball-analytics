import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api, ApiError, post } from "../api";
import { pct, shortDate } from "../format";
import { Empty, ErrorNote, Loading, Tabs } from "../components/ui";

// The owner's admin panel (V1.1): view only, plus NCAA absences entry.

type Tab = "health" | "quality" | "models" | "absences";
// Admin rows are shown as they come from the API.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>;

function when(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return `${shortDate(iso)} ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}

/** Today's date where the owner is (not UTC, which is already tomorrow on a US evening). */
function localDate(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** An admin query that sends the owner back to the login form when the session has expired
 *  or was cleared, instead of leaving error boxes. */
function useAdmin<T>(key: string, path: string, params: Record<string, string> = {}, refetchInterval?: number) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["admin", key, params],
    queryFn: async () => {
      try {
        return await api<T>(path, params);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) await qc.invalidateQueries({ queryKey: ["admin", "me"] });
        throw e;
      }
    },
    refetchInterval,
  });
}

export function AdminPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["admin", "me"], queryFn: () => api<{ enabled: boolean; admin: boolean }>("admin/me") });
  const [tab, setTab] = useState<Tab>("health");
  const logout = useMutation({
    mutationFn: () => post("admin/logout", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  if (me.isLoading) return <main className="page"><Loading what="Loading" /></main>;
  if (me.error || !me.data) return <main className="page"><ErrorNote error={me.error} /></main>;
  if (!me.data.enabled) return <main className="page"><Empty>The admin panel is switched off (no admin password is set).</Empty></main>;
  if (!me.data.admin) return <main className="page"><Login /></main>;
  return (
    <main className="page">
      <div className="row spread">
        <h1>Admin</h1>
        <button className="btn" onClick={() => logout.mutate()}>Log out</button>
      </div>
      <Tabs tabs={[["health", "Data health"], ["quality", "Data quality"], ["models", "Models"], ["absences", "NCAA absences"]]}
        value={tab} onChange={setTab} />
      {tab === "health" && <Health />}
      {tab === "quality" && <Quality />}
      {tab === "models" && <Models />}
      {tab === "absences" && <Absences />}
    </main>
  );
}

function Login() {
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => post("admin/login", { password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); login.mutate(); };
  return (
    <form className="card stack" onSubmit={submit} style={{ maxWidth: 360 }}>
      <h1>Admin login</h1>
      <input type="password" aria-label="Password" placeholder="Password" autoComplete="current-password"
        value={password} onChange={(e) => setPassword(e.target.value)} />
      <button className="btn" type="submit" disabled={!password || login.isPending}>Log in</button>
      {login.error && <span className="small" style={{ color: "var(--critical)" }}>{(login.error as Error).message}</span>}
    </form>
  );
}

function Health() {
  const q = useAdmin<{ jobs: Row[]; recent: Row[] }>("health", "admin/health", {}, 60_000);
  if (q.isLoading) return <Loading what="Loading job history" />;
  if (q.error || !q.data) return <ErrorNote error={q.error} />;
  const problems = q.data.recent.filter((r) => r.status !== "succeeded" || r.details?.errors);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card">
        <h3>Jobs</h3>
        <div className="table-wrap auto-height">
          <table aria-label="Jobs">
            <thead><tr><th scope="col" className="left">Job</th><th scope="col" className="left">Last success</th><th scope="col" className="left">Last failure</th><th scope="col">Runs (24 h)</th><th scope="col">Failed</th><th scope="col">Partly failed</th></tr></thead>
            <tbody>
              {q.data.jobs.map((j) => (
                <tr key={`${j.job_name}-${j.league}`}>
                  <td className="left">{j.job_name}{j.league ? ` (${j.league})` : ""}</td>
                  <td className="left">{when(j.last_success)}</td><td className="left">{when(j.last_failure)}</td>
                  <td>{j.runs_24h}</td>
                  <td style={j.failures_24h ? { color: "var(--critical)", fontWeight: 700 } : undefined}>{j.failures_24h}</td>
                  <td>{j.partial_24h}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <h3>Recent failures and errors</h3>
        {problems.length === 0 ? <Empty>No failures recently.</Empty> : (
          <div className="table-wrap auto-height">
            <table aria-label="Recent failures">
              <thead><tr><th scope="col" className="left">When</th><th scope="col" className="left">Job</th><th scope="col" className="left">What happened</th></tr></thead>
              <tbody>
                {problems.map((r) => (
                  <tr key={r.job_run_id}>
                    <td className="left">{when(r.started_at)}</td>
                    <td className="left">{r.job_name}</td>
                    <td className="left" style={{ whiteSpace: "normal" }}>
                      {r.status === "failed" ? r.error : (r.details?.errors ?? []).join("; ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Quality() {
  const q = useAdmin<{ threshold: number; seasons: Row[]; flagged_games: Row[] }>("quality", "admin/quality");
  if (q.isLoading) return <Loading what="Loading quality reports" />;
  if (q.error || !q.data) return <ErrorNote error={q.error} />;
  const cell = (n: number, games: number) => {
    const share = games ? n / games : 0;
    return <span style={share > q.data!.threshold ? { color: "var(--critical)", fontWeight: 700 } : undefined}>{n} ({pct(share, 1)})</span>;
  };
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card">
        <h3>Per season (more than {pct(q.data.threshold, 0)} failing in a group is flagged)</h3>
        <div className="table-wrap auto-height">
          <table aria-label="Quality by season">
            <thead><tr><th scope="col" className="left">Season</th><th scope="col">Games</th><th scope="col">Team box</th><th scope="col">Player box</th><th scope="col">Play-by-play</th><th scope="col">Lineup notes</th></tr></thead>
            <tbody>
              {q.data.seasons.map((s) => (
                <tr key={s.season}>
                  <td className="left">{s.season - 1}-{String(s.season).slice(2)}</td><td>{s.games}</td>
                  <td>{cell(s.team, s.games)}</td><td>{cell(s.player, s.games)}</td><td>{cell(s.pbp, s.games)}</td><td>{s.lineups}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <h3>Flagged games</h3>
        {q.data.flagged_games.length === 0 ? <Empty>No flagged games.</Empty> : (
          <div className="table-wrap">
            <table aria-label="Flagged games">
              <thead><tr><th scope="col" className="left">Date</th><th scope="col" className="left">Game</th><th scope="col" className="left">Check</th><th scope="col" className="left">Detail</th></tr></thead>
              <tbody>
                {q.data.flagged_games.map((g, i) => (
                  <tr key={`${g.game_id}-${g.check_name}-${i}`}>
                    <td className="left">{shortDate(g.start_time)}</td><td className="left">{g.away} @ {g.home}</td>
                    <td className="left">{g.check_group}: {g.check_name}</td>
                    <td className="left" style={{ whiteSpace: "normal" }}>{g.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Models() {
  const q = useAdmin<{ season: number; versions: Row[]; exams: Row[]; training_runs: Row[]; live_accuracy: Row[] }>("models", "admin/models");
  if (q.isLoading) return <Loading what="Loading models" />;
  if (q.error || !q.data) return <ErrorNote error={q.error} />;
  const versionName = (id: number) => q.data!.versions.find((v) => v.model_version_id === id);
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card">
        <h3>Live accuracy this season, against the target</h3>
        {q.data.live_accuracy.length === 0 ? <Empty>No graded predictions yet this season.</Empty> : (
          <div className="table-wrap auto-height">
            <table aria-label="Live accuracy">
              <thead><tr><th scope="col" className="left">Model</th><th scope="col">Games</th><th scope="col">Right</th><th scope="col">Log loss</th><th scope="col">Market log loss</th><th scope="col" className="left">Target</th></tr></thead>
              <tbody>
                {q.data.live_accuracy.map((a) => {
                  const v = versionName(a.model_version_id);
                  return (
                    <tr key={a.model_version_id}>
                      <td className="left">{v ? `${v.version} (${v.role})` : a.model_version_id}{a.shadow ? " · shadow" : ""}</td>
                      <td>{a.games}</td><td>{a.model ? pct(a.model.accuracy, 1) : "–"}</td>
                      <td>{a.model ? a.model.log_loss.toFixed(3) : "–"}</td>
                      <td>{a.market ? a.market.market.log_loss.toFixed(3) : "–"}</td>
                      <td className="left">{a.market ? (a.market.target.within_target ? "✓ within" : "✗ outside") : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <h3>Model versions</h3>
        <div className="table-wrap auto-height">
          <table aria-label="Model versions">
            <thead><tr><th scope="col" className="left">Model</th><th scope="col" className="left">Version</th><th scope="col" className="left">Role</th><th scope="col" className="left">Created</th><th scope="col" className="left">Settings</th></tr></thead>
            <tbody>
              {q.data.versions.map((v) => (
                <tr key={v.model_version_id}>
                  <td className="left">{v.model_name}</td><td className="left">{v.version}</td><td className="left">{v.role}</td>
                  <td className="left">{when(v.created_at)}</td>
                  <td className="left small" style={{ whiteSpace: "normal", minWidth: 260 }}>
                    <code>{JSON.stringify(v.settings.members ?? v.settings.settings ?? v.settings).slice(0, 400)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <h3>Rolling exams</h3>
        <div className="table-wrap auto-height">
          <table aria-label="Rolling exams">
            <thead><tr><th scope="col" className="left">Model</th><th scope="col">Round</th><th scope="col" className="left">Tuned on</th><th scope="col">Examined on</th><th scope="col" className="left">Candidates</th><th scope="col" className="left">Winner</th><th scope="col" className="left">Exam result</th></tr></thead>
            <tbody>
              {q.data.exams.map((e) => (
                <tr key={`${e.model_name}-${e.exam_round}`}>
                  <td className="left">{e.model_name}</td><td>{e.exam_round}</td>
                  <td className="left">{(e.tuning_seasons as number[]).join(", ")}</td><td>{e.exam_season}</td>
                  <td className="left"><Candidates rows={e.table ?? []} count={e.candidates} /></td>
                  <td className="left">{e.winner}</td>
                  <td className="left small">{examText(e.winner_exam)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <h3>Training history</h3>
        <div className="table-wrap auto-height">
          <table aria-label="Training runs">
            <thead><tr><th scope="col" className="left">Finished</th><th scope="col" className="left">Model</th><th scope="col" className="left">Version</th><th scope="col" className="left">Status</th></tr></thead>
            <tbody>
              {q.data.training_runs.map((r) => (
                <tr key={r.training_run_id}>
                  <td className="left">{when(r.finished_at)}</td><td className="left">{r.model_name}</td><td className="left">{r.version}</td><td className="left">{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/** Every candidate of an exam round with its result on the tuning seasons, best first. */
function Candidates({ rows, count }: { rows: Row[]; count: number }) {
  const metric = (r: Row) => r.tuning?.log_loss ?? r.tuning?.score ?? Number.POSITIVE_INFINITY;
  const sorted = [...rows].sort((a, b) => metric(a) - metric(b));
  const label = sorted[0]?.tuning?.log_loss !== undefined ? "log loss" : "score";
  return (
    <details>
      <summary>{count} candidates</summary>
      <table aria-label="Candidates" className="small" style={{ marginTop: 6 }}>
        <thead><tr><th scope="col" className="left">Version</th><th scope="col">Tuning {label}</th></tr></thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.version}>
              <td className="left">{r.version}{r.winner ? " (winner)" : ""}</td>
              <td>{Number.isFinite(metric(r)) ? metric(r).toFixed(4) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function examText(exam: Row | null): string {
  if (!exam) return "–";
  if (exam.log_loss !== undefined) {
    const t = exam.target;
    return `log loss ${exam.log_loss.toFixed(3)}, ${pct(exam.accuracy, 1)} right` +
      (t ? ` · vs market ${t.within_target ? "within" : "outside"} target (gap ${t.log_loss_gap >= 0 ? "+" : ""}${t.log_loss_gap.toFixed(3)})` : "");
  }
  if (exam.score !== undefined) return `score ${exam.score.toFixed(3)} (1.0 = season averages)`;
  return "–";
}

function Absences() {
  const qc = useQueryClient();
  const q = useAdmin<{ absences: Row[] }>("absences", "admin/absences", { league: "ncaam" });
  const [query, setQuery] = useState("");
  const [playerId, setPlayerId] = useState<number | undefined>();
  const [status, setStatus] = useState("Out");
  const [startsOn, setStartsOn] = useState(localDate());
  const [endsOn, setEndsOn] = useState("");
  const [note, setNote] = useState("");
  const found = useQuery({
    queryKey: ["admin", "player-search", query],
    queryFn: () => api<{ results: Row[] }>("ncaam/search/players", { q: query }),
    enabled: query.trim().length > 1,
  });
  const add = useMutation({
    mutationFn: () => post("admin/absences", { league: "ncaam", player_id: playerId, status, starts_on: startsOn, ends_on: endsOn || null, note: note || null }),
    onSuccess: () => { setPlayerId(undefined); setQuery(""); setNote(""); qc.invalidateQueries({ queryKey: ["admin", "absences"] }); },
  });
  const end = useMutation({
    mutationFn: (id: number) => post(`admin/absences/${id}/end`, { ends_on: localDate() }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "absences"] }),
  });
  return (
    <div className="stack" style={{ gap: 16 }}>
      <p className="small muted">College injury reports are not available, so absences entered here stand in for them once college games are predicted (V4).</p>
      <form className="card stack" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <h3>Add an absence</h3>
        <input type="search" aria-label="Find player" placeholder="Find a player" value={query}
          onChange={(e) => { setQuery(e.target.value); setPlayerId(undefined); }} />
        {found.data && !playerId && (
          <div className="row">
            {found.data.results.slice(0, 6).map((p) => (
              <button type="button" className="btn" key={p.id} onClick={() => { setPlayerId(p.id); setQuery(p.name); }}>
                {p.name}{p.team ? ` · ${p.team}` : ""}
              </button>
            ))}
          </div>
        )}
        <div className="row">
          <label className="check">Status{" "}
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="Out">Out (changes the prediction)</option>
              <option value="Doubtful">Doubtful (shown only)</option>
              <option value="Questionable">Questionable (shown only)</option>
            </select>
          </label>
          <label className="check">From <input type="date" value={startsOn} onChange={(e) => setStartsOn(e.target.value)} /></label>
          <label className="check">Until (optional) <input type="date" value={endsOn} onChange={(e) => setEndsOn(e.target.value)} /></label>
        </div>
        <input type="text" aria-label="Note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn" type="submit" disabled={!playerId || add.isPending}>Add absence</button>
        {add.error && <span className="small" style={{ color: "var(--critical)" }}>{(add.error as Error).message}</span>}
      </form>
      <section className="card">
        <h3>Current and recent absences</h3>
        {q.isLoading ? <Loading what="Loading absences" /> : !q.data || q.data.absences.length === 0 ? <Empty>None entered.</Empty> : (
          <div className="table-wrap auto-height">
            <table aria-label="Absences">
              <thead><tr><th scope="col" className="left">Player</th><th scope="col" className="left">Team</th><th scope="col" className="left">Status</th><th scope="col" className="left">From</th><th scope="col" className="left">Until</th><th scope="col" className="left">Note</th><th scope="col" /></tr></thead>
              <tbody>
                {q.data.absences.map((a) => (
                  <tr key={a.absence_id}>
                    <td className="left">{a.player}</td><td className="left">{a.team}</td><td className="left">{a.status}</td>
                    <td className="left">{a.starts_on}</td><td className="left">{a.ends_on ?? "until further notice"}</td>
                    <td className="left">{a.note}</td>
                    <td>{!a.ends_on && <button className="btn" onClick={() => end.mutate(a.absence_id)}>Back today</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
