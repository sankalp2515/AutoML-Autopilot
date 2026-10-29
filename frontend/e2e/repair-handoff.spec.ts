import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";

test.describe("real API handoff integration with seeded local identities and scripted model", () => {
  test.skip(
    process.env.E2E_REPAIR_HANDOFF !== "1",
    "Run the dedicated local QA server; never target a production deployment",
  );
  function credentials() {
    return JSON.parse(readFileSync(process.env.E2E_REPAIR_IDENTITY!, "utf8"));
  }
  async function login(page: any, who = "owner", workspace?: string) {
    const d = credentials();
    await page.addInitScript(
      ({ token, workspace }) => {
        if (!sessionStorage.getItem("repair-token")) {
          sessionStorage.setItem("repair-token", token);
          localStorage.setItem("repair-workspace", workspace);
        }
      },
      { token: d[who].token, workspace: workspace || d.team.id },
    );
    return d;
  }
  test("owner manages membership, preserves last owner, and links CI repositories", async ({
    page,
  }) => {
    const d = await login(page);
    await page.goto("/repair/settings");
    await expect(page.getByText("Signed in as qa-owner")).toBeVisible();
    await expect(page.getByLabel("Current workspace")).toHaveValue(d.team.id);
    await page
      .getByRole("button", { name: "Remove qa-owner", exact: true })
      .click();
    await expect(page.locator("main").getByRole("alert")).toContainText(
      "must keep at least one owner",
    );
    await page.getByLabel("Role for qa-viewer").selectOption("member");
    await expect(page.getByText("Access updated for qa-viewer.")).toBeVisible();
    await page.getByLabel("Role for qa-viewer").selectOption("viewer");
    await expect(page.getByLabel("Role for qa-viewer")).toHaveValue("viewer");
    await page
      .getByLabel("GitHub repository", { exact: true })
      .fill("qa-owner/repair-fixture");
    await page
      .getByRole("button", { name: "Link repository", exact: true })
      .click();
    await expect(page.getByText("Installation not confirmed")).toBeVisible();
    await page
      .getByRole("button", {
        name: "Unlink qa-owner/repair-fixture",
        exact: true,
      })
      .click();
    await expect(page.getByText("Repository unlinked.")).toBeVisible();
    await expect(
      page.getByRole("button", {
        name: "Unlink qa-owner/repair-fixture",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(
      (
        await new AxeBuilder({ page })
          .include(".repair")
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.setViewportSize({ width: 375, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(
      (
        await new AxeBuilder({ page })
          .include(".repair")
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => {
      (document.activeElement as HTMLElement)?.blur();
      window.scrollTo(0, 0);
    });
    if (process.env.E2E_REPAIR_SCREENSHOTS === "1") {
      mkdirSync(path.resolve("../docs/assets/repair"), { recursive: true });
      await page.screenshot({
        path: path.resolve("../docs/assets/repair/workspace-teams.png"),
        fullPage: true,
      });
    }
    await page
      .getByLabel("New workspace name")
      .fill("Browser-created workspace");
    await page
      .getByRole("button", { name: "Create workspace", exact: true })
      .click();
    await expect(page).toHaveURL(/repair\/runs/);
    await expect(page.locator(".r-breadcrumb")).toContainText(
      "Browser-created workspace",
    );
  });
  test("viewer cannot start repairs or manage members; headers enforce workspace isolation", async ({
    page,
    request,
  }) => {
    const d = await login(page, "viewer");
    await page.goto("/repair/settings");
    await expect(
      page.getByText("viewer access", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Add teammate", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Link repository", exact: true }),
    ).toHaveCount(0);
    await page.goto("/repair/new");
    await expect(
      page.getByText("Read-only workspace", { exact: true }),
    ).toBeVisible();
    const headers = {
      "X-Autopilot-Token": d.viewer.token,
      "X-Workspace": d.team.id,
    };
    expect(
      (
        await request.post("/api/v1/v5/repos", {
          headers,
          data: { sample: "churn-service" },
        })
      ).status(),
    ).toBe(403);
    expect(
      (
        await request.get("/api/v1/v5/me", {
          headers: { ...headers, "X-Workspace": d.owner.personal_workspace },
        })
      ).status(),
    ).toBe(404);
  });
  test("source to verified receipt through real intake, durable worker, checks and browser", async ({
    page,
  }) => {
    test.setTimeout(900000);
    await login(page);
    await page.goto("/repair/new");
    await page.getByRole("button", { name: "Continue to contract" }).click();
    await page.getByRole("button", { name: "Validate and continue" }).click();
    await page.getByRole("button", { name: /Start repair/ }).click();
    await expect(page).toHaveURL(/repair\/tasks\//);
    await expect(
      page.getByText("Probe confirmed", { exact: true }),
    ).toBeVisible({ timeout: 240000 });
    await expect(
      page.getByText("Probe refuted", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("tab", { name: "Verdict & receipt", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "A repair with evidence behind it." }),
    ).toBeVisible({ timeout: 600000 });
    await expect(
      page.getByRole("heading", { name: "Maintainability review notes" }),
    ).toBeVisible();
    await expect(
      page.getByText("scripted-ui-qa", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Download .patch", exact: true }),
    ).toBeEnabled();
    expect(
      (
        await new AxeBuilder({ page })
          .include(".repair")
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
  });
});

test.describe("OAuth browser guards", () => {
  test.skip(
    process.env.E2E_MOCK_API !== "1",
    "Offline browser contract checks",
  );
  test("forged callback is refused before exchanging a code", async ({
    page,
  }) => {
    let exchanges = 0;
    await page.route("**/auth/github/callback", (r) => {
      exchanges++;
      return r.fulfill({ status: 401, json: { detail: "bad state" } });
    });
    await page.goto("/auth/callback?code=unused&state=forged");
    await expect(page.locator("main").getByRole("alert")).toContainText(
      "could not be verified",
    );
    expect(exchanges).toBe(0);
    await expect(page).toHaveURL(/auth\/callback$/);
  });
  test("valid callback exchanges once and subsequent requests carry account/workspace headers", async ({
    page,
  }) => {
    await page.addInitScript(() =>
      sessionStorage.setItem("repair-oauth-state", "expected"),
    );
    let exchanges = 0,
      scoped = 0;
    await page.route("**/api/v1/v5/**", async (r) => {
      const p = new URL(r.request().url()).pathname;
      if (p.endsWith("/auth/github/callback")) {
        exchanges++;
        return r.fulfill({
          json: { token: "qa-token", personal_workspace: "ws1" },
        });
      }
      if (p.endsWith("/me")) {
        if (
          r.request().headers()["x-autopilot-token"] === "qa-token" &&
          r.request().headers()["x-workspace"] === "ws1"
        )
          scoped++;
        return r.fulfill({
          json: {
            account: { id: "a", login: "owner", name: "Owner" },
            personal_workspace: "ws1",
            current_workspace: "ws1",
            role: "owner",
            workspaces: [{ id: "ws1", name: "Personal", role: "owner" }],
          },
        });
      }
      return r.fulfill({ json: { members: [], links: [] } });
    });
    await page.goto("/auth/callback?code=code1&state=expected");
    await expect(page).toHaveURL(/repair\/settings$/);
    await expect(page.getByText("Signed in as owner")).toBeVisible();
    expect(exchanges).toBe(1);
    expect(scoped).toBeGreaterThan(0);
  });
});

test("expired account tokens clear scope and explain sign-in recovery", async ({
  page,
}) => {
  test.skip(process.env.E2E_MOCK_API !== "1", "Offline contract check");
  await page.addInitScript(() => {
    sessionStorage.setItem("repair-token", "expired");
    localStorage.setItem("repair-workspace", "ws-old");
  });
  await page.route("**/api/v1/v5/**", (r) => {
    const p = new URL(r.request().url()).pathname;
    if (p.endsWith("/me"))
      return r.fulfill({ status: 401, json: { detail: "expired" } });
    if (p.endsWith("/auth/github"))
      return r.fulfill({
        status: 503,
        json: { detail: "GitHub sign-in is not configured on this server" },
      });
    return r.fulfill({
      json: { configured: false, live_runs: false, successful_calls_7d: 0 },
    });
  });
  await page.goto("/repair/settings");
  await expect(page.locator("main").getByRole("alert")).toContainText(
    "Your sign-in expired",
  );
  expect(
    await page.evaluate(() => sessionStorage.getItem("repair-token")),
  ).toBeNull();
  expect(
    await page.evaluate(() => localStorage.getItem("repair-workspace")),
  ).toBeNull();
  await expect(
    page.getByRole("button", { name: "Add teammate", exact: true }),
  ).toHaveCount(0);
});
