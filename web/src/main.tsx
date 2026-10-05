import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { GamePage } from "./pages/Game";
import { PlayersPage } from "./pages/Players";
import { TeamsPage } from "./pages/Teams";
import { TonightPage } from "./pages/Tonight";
import "./styles.css";

// The pages with charts load the chart library on demand, so it never delays Tonight.
const TeamPage = lazy(() => import("./pages/Team").then((m) => ({ default: m.TeamPage })));
const PlayerPage = lazy(() => import("./pages/Player").then((m) => ({ default: m.PlayerPage })));
const ReportCardPage = lazy(() => import("./pages/ReportCard").then((m) => ({ default: m.ReportCardPage })));

function OnDemand({ children }: { children: ReactNode }) {
  return <Suspense fallback={<main className="page"><Loading what="Loading" /></main>}>{children}</Suspense>;
}

try {
  document.documentElement.dataset.theme = localStorage.getItem("theme") ?? "dark";
} catch {
  document.documentElement.dataset.theme = "dark";
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: true, retry: 1 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/nba/tonight" replace />} />
          <Route path="/:league" element={<Layout />}>
            <Route index element={<Navigate to="tonight" replace />} />
            <Route path="tonight" element={<TonightPage />} />
            <Route path="games/:id" element={<GamePage />} />
            <Route path="teams" element={<TeamsPage />} />
            <Route path="teams/:id" element={<OnDemand><TeamPage /></OnDemand>} />
            <Route path="players" element={<PlayersPage />} />
            <Route path="players/:id" element={<OnDemand><PlayerPage /></OnDemand>} />
            <Route path="report-card" element={<OnDemand><ReportCardPage /></OnDemand>} />
          </Route>
          <Route path="*" element={<Navigate to="/nba/tonight" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
