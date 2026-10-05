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
