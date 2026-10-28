import { defineConfig, devices } from "@playwright/test";

/**
 * Frontend E2E. Until now the frontend was covered by `tsc --noEmit` + `next build`
 * only — both of which pass happily while every page throws at runtime.
 *
 * These specs drive a REAL browser against a REAL running stack. They are opt-in:
 * `E2E_WEB_URL` must point at a running frontend, otherwise the suite is skipped,
 * so a plain `npm test` on a laptop with nothing running does not fail.
 *
 * Deliberately NOT a full pipeline run — that belongs in the backend `e2e_full`
 * tier, which can assert on model correctness rather than pixels. What a browser
 * uniquely proves is that the pages render, the API contract the UI expects is the
 * one being served, and navigation works.
 */
const baseURL = process.env.E2E_WEB_URL || "http://localhost:3002";

export default defineConfig({
  testDir: "./e2e",
  // A failing E2E must be actionable, so keep traces/screenshots on failure.
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
