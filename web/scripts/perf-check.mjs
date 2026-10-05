// Performance budget check (architecture doc, section 12), on a simulated mid-range phone:
// 4x slower CPU and a 4G connection. Usage: node scripts/perf-check.mjs [base URL] [path]
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://127.0.0.1:4173";
const path = process.argv[3] ?? "/nba/players";
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2.6, isMobile: true, hasTouch: true });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send("Network.enable");
await cdp.send("Network.emulateNetworkConditions", {
  offline: false, latency: 85, downloadThroughput: (9 * 1024 * 1024) / 8, uploadThroughput: (1.5 * 1024 * 1024) / 8,
});
await cdp.send("Emulation.setCPUThrottlingRate", { rate: Number(process.env.CPU_RATE ?? 4) });

const start = Date.now();
await page.goto(base + path);
await page.locator("table tbody tr td a").first().waitFor();
const firstLoad = Date.now() - start;

// Time from the tap until the re-sorted table is rendered and laid out (the paint follows
// within the same frame), measured inside the page. Median of several sorts.
const sortMs = await page.evaluate(async () => {
  const times = [];
  for (const col of ["RPG", "APG", "PPG", "MPG", "FG%"]) {
    const header = [...document.querySelectorAll("th button")].find((b) => b.textContent?.startsWith(col));
    const t0 = performance.now();
    header.click();
    await new Promise((r) => requestAnimationFrame(() => { void document.body.offsetHeight; r(); }));
    times.push(performance.now() - t0);
    await new Promise((r) => setTimeout(r, 200));
  }
  times.sort((a, b) => a - b);
  return Math.round(times[Math.floor(times.length / 2)]);
});
const rows = await page.locator("table tbody tr").count();

console.log(JSON.stringify({ path, firstLoadMs: firstLoad, sortMs, renderedRows: rows,
  budget: { firstLoadMs: 2000, sortMs: 100 },
  pass: { firstLoad: firstLoad < 2000, sort: sortMs < 100 } }, null, 2));
await browser.close();
