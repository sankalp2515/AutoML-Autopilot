import { test, expect } from "@playwright/test";

test.skip(process.env.E2E_REPAIR_LIVE !== "1", "Requires a real local v5 API; no inference or task creation");

test("new user inspects a sample then plays genuine cached engineering evidence in one tab", async ({ page, context }) => {
  test.setTimeout(240000);
  page.setDefaultTimeout(20000);
  const timings: Record<string, number> = {};
  const createdTasks: string[] = [];
  const errors: string[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && /\/v5\/tasks(?:\?|$)/.test(request.url())) createdTasks.push(request.url());
  });
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  let start = Date.now();
  await page.goto("/repair");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Service unavailable", { exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "Try a sample repo", exact: true }).first().click();
  await page.getByRole("button", { name: /churn-service/ }).click();
  timings.source_ms = Date.now() - start;
  start = Date.now();
  await page.getByRole("button", { name: "Continue to contract", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Repair boundaries", exact: true })).toBeVisible();
  await expect(page.getByLabel("autopilot.yaml", { exact: true })).toHaveValue(/immutable_existing_tests: true/);
  timings.intake_ms = Date.now() - start;
  start = Date.now();
  await page.getByRole("button", { name: "Validate and continue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "What should the repair achieve?", exact: true })).toBeVisible();
  await expect(page.getByText(/changes in this form are not executed or evaluated/)).toBeVisible();
  timings.validate_ms = Date.now() - start;
  if (process.env.E2E_DEMO_DISCOVERY !== "0") {
    start = Date.now();
    const discoveryResponse = page.waitForResponse(
      response => response.request().method() === "POST" && response.url().endsWith("/discover"),
      { timeout: 180000 },
    );
    await page.getByRole("button", { name: "Discover tests", exact: true }).click();
    expect((await discoveryResponse).ok()).toBe(true);
    await expect(page.getByText("Tests that must pass after repair", { exact: true })).toBeVisible();
    timings.real_sandbox_discovery_ms = Date.now() - start;
  }
  start = Date.now();
  await page.getByRole("link", { name: "Open cached sample run", exact: true }).click();
  const nebius = process.env.E2E_EXPECT_NEBIUS !== "0";
  await expect(page).toHaveURL(nebius ? /\/repair\/replay\/nebius_churn_verified$/ : /\/repair\/replay\/nvidia_churn_verified$/);
  await expect(page.getByText("Genuine historical recording", { exact: true })).toBeVisible();
  await expect(page.getByText(/Playback does not execute code or call a model/)).toBeVisible();
  await page.getByLabel("Playback speed", { exact: true }).selectOption("4");
  await page.getByRole("button", { name: "Play recording", exact: true }).click();
  const timeline = page.getByLabel("Replay timeline", { exact: true });
  const max = await timeline.getAttribute("max");
  await expect(timeline).toHaveValue(max!, { timeout: 45000 });
  timings.cached_playback_ms = Date.now() - start;
  await expect(page.getByRole("heading", { name: "Candidate source & terminal", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Verdict & receipt", exact: true }).click();
  await expect(page.getByRole("heading", { name: "A repair with evidence behind it.", exact: true })).toBeVisible();
  await expect(page.getByText(nebius ? "tokenfactory-sandbox" : "local-container", { exact: true })).toBeVisible();
  await expect(page.getByText(nebius ? "nebius_token_factory" : "nvidia_api", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await expect(page.locator(".r-diff-split").first()).toBeVisible();
  await page.getByRole("tab", { name: "PR handoff", exact: true }).click();
  expect(context.pages()).toHaveLength(1);
  expect(createdTasks).toEqual([]);
  expect(errors).toEqual([]);
  console.log("REAL_CACHED_SAMPLE_FLOW " + JSON.stringify(timings));
});

for (const width of [623, 920]) {
  test(`playback speed label stays centered at reported width ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 668 });
    await page.goto("/repair/replay/nvidia_churn_verified");
    const label = page.locator(".r-replay-speed");
    await expect(label).toBeVisible();
    expect(await label.evaluate(el => getComputedStyle(el).alignItems)).toBe("center");
    expect(await label.evaluate(el => getComputedStyle(el).whiteSpace)).toBe("nowrap");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}
