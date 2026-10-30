import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync } from "node:fs";
import path from "node:path";

test.skip(
  process.env.E2E_MOCK_API !== "1",
  "Deterministic landing checks use a mock status API; no model calls",
);
async function runtime(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/v5/status", (route) =>
    route.fulfill({
      json: {
        configured: false,
        successful_calls_7d: 0,
        live_runs: false,
        invite_required: true,
        runner: "docker",
        isolated: true,
        access_policy: {
          judge_only: true,
          daily_runs_per_workspace: 12,
          cooldown_seconds: 30,
          max_active_tasks: 2,
          total_estimated_usd_cap: 20,
          reason:
            "Live inference is funded by a finite hackathon credit balance. Limits preserve access throughout judging; viewing recordings, receipts and diffs does not spend inference credits.",
        },
      },
    }),
  );
}
test("landing explains evidence, switches checks, discloses access and opens the actual replay", async ({
  page,
}) => {
  await runtime(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "The ML coding agent that checks beyond green tests.",
  );
  await expect(page.locator(".r-sidebar")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Software tests", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "The software checks passed." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "ML behavior", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Green tests. Wrong behavior." }),
  ).toBeVisible();
  await expect(
    page.getByText(/Historical unisolated test runner/),
  ).toBeVisible();
  await expect(page.getByText(/12 new runs per workspace/)).toBeVisible();
  await expect(page.getByText(/No paid plans or checkout/)).toBeVisible();
  const faq = page
    .locator("summary")
    .filter({ hasText: "Does VERIFIED mean the model is safe to deploy?" });
  await faq.click();
  await expect(
    page.getByText(/not a guarantee about every future input/),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Watch a verified repair", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/repair\/replay\/nebius_churn_verified/);
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await expect(
    page.getByRole("heading", { name: "A repair with evidence behind it." }),
  ).toBeVisible();
});
for (const theme of ["light", "dark"])
  for (const width of [1440, 375])
    test(`landing layout, keyboard and axe: ${theme} ${width}`, async ({
      page,
    }) => {
      test.setTimeout(90000);
      await runtime(page);
      await page.addInitScript(
        (t) => localStorage.setItem("atelier-theme", t),
        theme,
      );
      await page.setViewportSize({ width, height: 1000 });
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.goto("/repair");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page
        .getByRole("button", { name: "ML behavior", exact: true })
        .focus();
      await expect(
        page.getByRole("button", { name: "ML behavior", exact: true }),
      ).toBeFocused();
      await page.keyboard.press("Space");
      const audit = await new AxeBuilder({ page })
        .include(".repair")
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      expect(
        audit.violations.map((v) => ({
          id: v.id,
          nodes: v.nodes.map((n) => n.target),
        })),
      ).toEqual([]);
      await page.evaluate(() => document.fonts.ready);
      const folder = path.resolve("../docs/assets/landing");
      mkdirSync(folder, { recursive: true });
      await page.screenshot({
        path: path.join(folder, `landing-${theme}-${width}.png`),
        fullPage: true,
      });
      await page.screenshot({
        path: path.join(folder, `hero-${theme}-${width}.png`),
      });
      expect(errors).toEqual([]);
    });
