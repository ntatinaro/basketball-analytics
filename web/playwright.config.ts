import { defineConfig, devices } from "@playwright/test";

// Simple click-through tests. They expect the site at HOOPS_WEB_URL (default: vite preview
// on port 4173) with the API running against a database that has data loaded.
export default defineConfig({
  testDir: "tests",
  timeout: 30_000,
  use: { baseURL: process.env.HOOPS_WEB_URL ?? "http://127.0.0.1:4173" },
  projects: [
    { name: "phone", use: { ...devices["Pixel 7"] } },
    { name: "desktop", use: { viewport: { width: 1280, height: 900 } } },
  ],
});
