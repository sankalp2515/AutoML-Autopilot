import { expect, test, type Page } from "@playwright/test";

/**
 * Frontend smoke E2E.
 *
 * What this catches that `tsc --noEmit` and `next build` cannot: a page that
 * compiles and builds but throws on render, a client component that crashes on a
 * null field, a fetch to a route that no longer exists, and uncaught console
 * errors on the critical pages.
 *
 * AUTH. This deployment has auth enabled, so an unauthenticated visit to any app
 * route renders the shell briefly and then redirects to /login. That is correct
 * behaviour and the unauthenticated tests assert it. The authenticated pages are
 * only exercised when E2E_WEB_USER / E2E_WEB_PASSWORD are supplied — otherwise
 * they skip rather than silently asserting on a login form.
 */

const USER = process.env.E2E_WEB_USER || "";
const PASSWORD = process.env.E2E_WEB_PASSWORD || "";
const HAS_CREDENTIALS = Boolean(USER && PASSWORD);

/** Console/page errors that are expected and are not defects. */
function realErrors(errors: string[], opts: { allow404?: boolean } = {}) {
  return errors.filter((e) => {
    if (e.includes("Download the React DevTools")) return false;
    if (e.includes("hydration")) return false;
    if (/favicon/i.test(e)) return false;
    // A 401 is the CORRECT response while signed out; the UI redirects on it.
    if (/status of 401/.test(e)) return false;
    if (opts.allow404 && /status of 404/.test(e)) return false;
    return true;
  });
}

function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  return errors;
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(USER);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 20_000 });
}

// ── unauthenticated behaviour ───────────────────────────────────────────────

test("an unauthenticated visit is redirected to sign in", async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto("/");
  await page.waitForURL(/\/login/, { timeout: 20_000 });
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  expect(realErrors(errors)).toEqual([]);
});

test("the login page renders its form fields", async ({ page }) => {
  const errors = trackErrors(page);
  await page.goto("/login");
  await expect(page.getByLabel(/email/i)).toBeVisible();
  await expect(page.getByLabel(/password/i)).toBeVisible();
  expect(realErrors(errors)).toEqual([]);
});

test("the app shell renders before the auth redirect", async ({ page }) => {
  /* Proves the layout itself does not throw. The shell paints, then the client
     redirects; if the layout were broken we would get a blank page instead. */
  await page.goto("/");
  await expect(page.locator("body")).not.toHaveText("", { timeout: 15_000 });
});

test("an unknown route returns 404 without crashing", async ({ page }) => {
  const errors = trackErrors(page);
  const res = await page.goto("/this-route-does-not-exist");
  expect(res?.status()).toBe(404);
  await expect(page.locator("body")).toBeVisible();
  // The 404 fetch itself logs a console error — expected, not a defect.
  expect(realErrors(errors, { allow404: true })).toEqual([]);
});

// ── authenticated pages ─────────────────────────────────────────────────────

test.describe("signed in", () => {
  test.skip(!HAS_CREDENTIALS,
    "set E2E_WEB_USER and E2E_WEB_PASSWORD to exercise the authenticated pages");

  test("dashboard renders the control plane", async ({ page }) => {
    const errors = trackErrors(page);
    await signIn(page);
    await expect(page.locator("body")).toContainText(/Orchestrator/i, { timeout: 15_000 });
    expect(realErrors(errors)).toEqual([]);
  });

  for (const [name, path] of [
    ["runs", "/runs"],
    ["registry", "/registry"],
    ["settings", "/settings"],
  ] as const) {
    test(`${name} page renders without errors`, async ({ page }) => {
      const errors = trackErrors(page);
      await signIn(page);
      await page.goto(path);
      await expect(page.locator("body")).not.toHaveText("", { timeout: 15_000 });
      expect(realErrors(errors)).toEqual([]);
    });
  }
});
