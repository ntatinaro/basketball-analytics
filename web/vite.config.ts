import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately on port 8000; in production the internal
// Caddy serves these files and forwards /api to the API container.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  preview: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { sourcemap: false },
});
