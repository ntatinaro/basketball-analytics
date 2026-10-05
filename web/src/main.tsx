import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { GamePage } from "./pages/Game";
import { PlayerPage } from "./pages/Player";
import { PlayersPage } from "./pages/Players";
import { ReportCardPage } from "./pages/ReportCard";
import { TeamPage } from "./pages/Team";
import { TeamsPage } from "./pages/Teams";
import { TonightPage } from "./pages/Tonight";
import "./styles.css";

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
            <Route path="teams/:id" element={<TeamPage />} />
            <Route path="players" element={<PlayersPage />} />
            <Route path="players/:id" element={<PlayerPage />} />
            <Route path="report-card" element={<ReportCardPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/nba/tonight" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
