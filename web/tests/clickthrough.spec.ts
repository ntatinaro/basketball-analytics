import { expect, test } from "@playwright/test";

const SEASON = process.env.HOOPS_TEST_SEASON ?? "2022";

test("teams table sorts and opens a team page", async ({ page }) => {
  await page.goto(`/nba/teams?season=${SEASON}`);
  const rows = page.locator("table tbody tr");
  await expect(rows).toHaveCount(30);
  await page.getByRole("columnheader", { name: /Def/ }).click();
  await expect(page.getByRole("columnheader", { name: /Def/ })).toHaveAttribute("aria-sort", /ascending|descending/);
  await rows.first().getByRole("link").click();
  await expect(page.locator(".rating-big")).toBeVisible();
  for (const tab of ["Team Stats", "Roster", "Schedule", "Overview"]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByRole("tab", { name: tab })).toHaveAttribute("aria-selected", "true");
  }
  await page.getByRole("button", { name: "Next team" }).click();
  await expect(page.locator(".rating-big")).toBeVisible();
});

test("tables sort from the keyboard and teams can be found by nickname", async ({ page }) => {
  await page.goto(`/nba/teams?season=${SEASON}`);
  const pace = page.getByRole("button", { name: /^Pace/ });
  await pace.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("columnheader", { name: /Pace/ })).toHaveAttribute("aria-sort", /ascending|descending/);
  await page.getByLabel("Search teams").fill("sixers");
  await expect(page.locator("table tbody tr").first()).toContainText("76ers");
  await expect(page.locator("table tbody tr")).toHaveCount(1);
});

test("players table: search, scope, and player page", async ({ page }) => {
  await page.goto(`/nba/players?season=${SEASON}`);
  await expect(page.locator("table tbody tr").first()).toBeVisible();
  await page.getByLabel("Search players").fill("jokc");
  await expect(page.locator("table tbody tr")).toHaveCount(1);
  await page.locator("table tbody tr a").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Jokic");
  await page.getByRole("tab", { name: "Game log" }).click();
  await expect(page.getByRole("table", { name: "Game log" })).toBeVisible();
});

test("tonight and game page", async ({ page }) => {
  await page.goto(`/nba/tonight?date=${Number(SEASON) - 1}-12-25`);
  const card = page.getByTestId("game-card").first();
  await expect(card).toBeVisible();
  await card.click();
  await expect(page.getByRole("tab", { name: "Box score" })).toBeVisible();
  await page.getByRole("tab", { name: "Preview" }).click();
});

test("report card loads", async ({ page }) => {
  await page.goto("/nba/report-card");
  await expect(page.getByRole("heading", { name: /Model report card/ })).toBeVisible();
  await expect(page.getByText("Past seasons (backtests)")).toBeVisible();
});

test("no sideways scrolling on the main screens", async ({ page }) => {
  for (const path of ["/nba/tonight", `/nba/teams?season=${SEASON}`, `/nba/players?season=${SEASON}`, "/nba/report-card"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(overflow, path).toBe(false);
  }
});

test("projections tab shows ranges for an upcoming game, when projections exist", async ({ page, request }) => {
  const meta = await (await request.get("/api/nba/meta")).json();
  const today = new Date().toISOString().slice(0, 10);
  let gameId: number | undefined;
  for (let d = 0; d < 7 && gameId === undefined; d++) {
    const date = new Date(Date.parse(today) + d * 86_400_000).toISOString().slice(0, 10);
    const games = (await (await request.get(`/api/nba/games?date=${date}`)).json()).games ?? [];
    for (const g of games) {
      const proj = await (await request.get(`/api/nba/games/${g.id}/projections`)).json();
      if (proj.available) { gameId = g.id; break; }
    }
  }
  test.skip(gameId === undefined || !meta, "no projections in this database");
  await page.goto(`/nba/games/${gameId}`);
  await page.getByRole("tab", { name: "Projections" }).click();
  const table = page.locator("table").first();
  await expect(table.getByRole("columnheader", { name: "Pts" })).toBeVisible();
  await expect(table.locator("tbody tr").first()).toContainText(/\d+–\d+/);
  await page.getByLabel("Full projection").check();
  await expect(table.getByRole("columnheader", { name: "FGA" })).toBeVisible();
});

test("admin panel: login, tabs, logout (when an admin password is set)", async ({ page, request }) => {
  const me = await (await request.get("/api/admin/me")).json();
  const password = process.env.HOOPS_ADMIN_PASSWORD;
  test.skip(!me.enabled || !password, "admin panel switched off, or HOOPS_ADMIN_PASSWORD not given to the test");
  // The wrong-password path is covered by the API tests: failures here would spend the
  // shared login limit (10 per 15 minutes) and make repeated runs fail.
  await page.goto("/nba/admin");
  await page.getByLabel("Password").fill(password!);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("table", { name: "Jobs" })).toBeVisible();
  for (const [tab, table] of [["Data quality", "Quality by season"], ["Models", "Model versions"], ["NCAA absences", "Find player"]]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByRole(tab === "NCAA absences" ? "searchbox" : "table", { name: table })).toBeVisible();
  }
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByLabel("Password")).toBeVisible();

  // An expired or cleared session goes back to the login form, not to error boxes.
  await page.getByLabel("Password").fill(password!);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("tab", { name: "Data health" }).click();
  await expect(page.getByRole("table", { name: "Jobs" })).toBeVisible();
  await page.context().clearCookies();
  await page.getByRole("tab", { name: "Data quality" }).click();
  await expect(page.getByLabel("Password")).toBeVisible();
});
