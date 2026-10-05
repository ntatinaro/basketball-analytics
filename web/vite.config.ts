import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately on port 8000; in production the internal
// Caddy serves these files and forwards /api to the API container.
// HOOPS_API points the dev and preview servers at another API (for example a second
// checkout's API on another port).
const api = process.env.HOOPS_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": api } },
  preview: { proxy: { "/api": api } },
  build: { sourcemap: false },
});
