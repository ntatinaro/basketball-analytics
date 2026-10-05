import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useLeague, useMeta } from "./ui";

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("theme") ?? "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* private mode: the choice just isn't remembered */
    }
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}

export function Layout() {
  const league = useLeague();
  const meta = useMeta();
  const [theme, toggleTheme] = useTheme();
  const last = meta.data?.last_update ? new Date(meta.data.last_update) : null;
  // NCAA screens arrive in V4; the switch appears once the league is available.
  const ncaaReady = false;

  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to={`/${league}/tonight`} className="brand" aria-label="Hoops home">
          <span className="brand-dot" aria-hidden />
          Hoops
        </NavLink>
        <div className="row">
          {ncaaReady && (
            <div className="seg" role="group" aria-label="League">
              <NavLink to="/nba/tonight">NBA</NavLink>
              <NavLink to="/ncaam/tonight">NCAA</NavLink>
            </div>
          )}
          <button className="btn small" type="button" onClick={toggleTheme} aria-label="Switch color theme">
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>
      {meta.data?.delayed && (
        <div className="page" style={{ paddingBottom: 0 }}>
          <div className="banner" role="status">
            Data delayed{last ? `, last updated ${last.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}` : ""}.
            Scores and predictions may be out of date.
          </div>
        </div>
      )}
      {meta.data?.rehearsal && (
        <div className="page" style={{ paddingBottom: 0 }}>
          <div className="banner" role="status">Rehearsal mode: preseason games get test predictions that are never graded publicly.</div>
        </div>
      )}
      <Outlet />
      <nav className="bottom-nav" aria-label="Main">
        <NavLink to={`/${league}/tonight`}>Tonight</NavLink>
        <NavLink to={`/${league}/teams`}>Teams</NavLink>
        <NavLink to={`/${league}/players`}>Players</NavLink>
        <NavLink to={`/${league}/report-card`}>Report card</NavLink>
      </nav>
    </div>
  );
}
