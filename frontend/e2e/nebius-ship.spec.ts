import { test, expect } from "@playwright/test";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

test.skip(process.env.E2E_NEBIUS_SHIP !== "1", "Explicitly opt in: genuine, credit-capped Nebius repair");
test.use({ trace: "off", screenshot: "off", video: "off" });

test("public judge flow creates a genuine Nebius repair and preserves its receipts", async ({ page }) => {
  test.setTimeout(1500000);
  page.setDefaultTimeout(30000);
  const access = JSON.parse(readFileSync(process.env.E2E_JUDGE_ACCESS_FILE!, "utf8"));
  const sample = process.env.E2E_SHIP_SAMPLE || "churn-service";
  const out = path.resolve("../docs/benchmarks/repair/nebius-live", sample);
  mkdirSync(out, { recursive: true });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/repair");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("link", { name: "Try a sample repo", exact: true }).first().click();
  await page.getByRole("button", { name: new RegExp(sample) }).click();
  await page.getByRole("button", { name: "Continue to contract", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Repair boundaries", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Validate and continue", exact: true }).click();
  await page.locator("summary").filter({ hasText: "Advanced: execution and spend" }).click();
  await page.getByLabel("Maximum candidates", { exact: true }).fill("1");
  await page.getByLabel("Coding token ceiling", { exact: true }).fill("64000");
  await page.getByLabel("Live-run invite code (if required)", { exact: true }).fill(access.invite);
  const response = page.waitForResponse(r => r.request().method() === "POST" && /\/v5\/tasks$/.test(r.url()), { timeout: 960000 });
  await page.getByRole("button", { name: "Start repair", exact: true }).click();
  const created = await response;
  expect(created.ok()).toBe(true);
  const task = await created.json();
  console.log("NEBIUS_TASK_CREATED " + JSON.stringify({ sample, id: task.id }));
  await expect(page).toHaveURL(new RegExp(`/repair/tasks/${task.id}$`));
  const terminal = new Set(["REVIEW_READY", "PR_DRAFTED", "REJECTED", "INCONCLUSIVE", "BLOCKED", "FAILED", "CANCELLED"]);
  let last = "";
  let finished: any;
  for (let i = 0; i < 120; i++) {
    const current = await page.evaluate(async id => {
      const headers: Record<string, string> = { "X-Autopilot-Session": localStorage.getItem("autopilot-session")! };
      const r = await fetch(`/api/v1/v5/tasks/${id}`, { headers });
      if (!r.ok) throw Error(`Task polling HTTP ${r.status}`);
      return r.json();
    }, task.id);
    if (current.status !== last) {
      console.log("NEBIUS_TASK_STATE " + JSON.stringify({ sample, status: current.status }));
      last = current.status;
    }
    if (terminal.has(current.status)) { finished = current; break; }
    await page.waitForTimeout(10000);
  }
  expect(finished, "repair must reach a recorded terminal outcome").toBeTruthy();
  writeFileSync(path.join(out, "task.json"), JSON.stringify(finished, null, 2));
  const report = await page.evaluate(async id => {
    const headers = { "X-Autopilot-Session": localStorage.getItem("autopilot-session")! };
    const r = await fetch(`/api/v1/v5/tasks/${id}/report`, { headers });
    if (!r.ok) throw Error(`Report HTTP ${r.status}`);
    return r.json();
  }, task.id);
  writeFileSync(path.join(out, "report.json"), JSON.stringify(report, null, 2));
  expect(report.provider).toBe("nebius_token_factory");
  expect(report.runner).toBe("tokenfactory-sandbox");
  expect(report.image).toMatch(/^nebius:/);
  expect(report.calls.some((call: any) => call.ok && call.provider === "nebius_token_factory")).toBe(true);
  expect(report.usage.accounting_complete).toBe(true);
  console.log("NEBIUS_FINAL " + JSON.stringify({ sample, id: task.id, decision: report.decision, cost: report.usage.total_est_cost_usd }));
  await page.getByRole("tab", { name: "Verdict & receipt", exact: true }).click();
  expect(errors).toEqual([]);
  // Save genuine failures too; do not turn infrastructure/inconclusive results
  // into claimed verification or publish them as successful repair evidence.
  expect(["VERIFIED", "REJECTED"]).toContain(report.decision);
});
