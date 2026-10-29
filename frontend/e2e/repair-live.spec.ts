import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

test.skip(
  process.env.E2E_REPAIR_LIVE !== "1",
  "Opt in with E2E_REPAIR_LIVE=1 against a running v5 stack; no inference or task creation",
);
test("real v5 stack reports status, samples, staging, validation and replay-safe navigation", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/repair/how-it-works");
  await expect(
    page.getByText("Successful calls, last 7 days", { exact: true }),
  ).toBeVisible();
  await page.goto("/repair/new");
  await expect(page.locator(".r-sample").first()).toBeVisible();
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await expect(
    page.getByRole("heading", { name: "Repair boundaries" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Validate and continue" }).click();
  await expect(
    page.getByRole("heading", { name: "What should the repair achieve?" }),
  ).toBeVisible();
  // Deliberately stop before Start: the smoke test never spends inference credits.
  await page.goto("/repair/replay/churn_live_prompt_v2");
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await expect(
    page.getByRole("heading", { name: "A repair with evidence behind it." }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("capture actual local UI in both themes and at mobile width", async ({
  page,
}) => {
  test.skip(
    process.env.E2E_REPAIR_SCREENSHOTS !== "1",
    "Opt-in review artifacts",
  );
  const output = path.resolve("../docs/assets/repair");
  mkdirSync(output, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/repair");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: /Replay mode/ })).toBeVisible();
  await page.screenshot({
    path: path.join(output, "home-light.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: path.join(output, "home-1440.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({
    path: path.join(output, "home-dark.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.goto("/repair/new");
  await expect(page.locator(".r-sample").first()).toBeVisible();
  await page.screenshot({
    path: path.join(output, "source-light.png"),
    fullPage: true,
  });
  await page.goto("/repair/replay/churn_live_prompt_v1");
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await page.getByRole("tab", { name: "Candidates", exact: true }).click();
  await expect(
    page.getByText("Tests green — rejected by ML check").first(),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(output, "candidate-evidence.png"),
    fullPage: true,
  });
  await page.goto("/repair/replay/churn_live_prompt_v2");
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await page.screenshot({
    path: path.join(output, "verified-receipt.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/repair");
  await page.screenshot({
    path: path.join(output, "home-mobile.png"),
    fullPage: true,
  });
});
