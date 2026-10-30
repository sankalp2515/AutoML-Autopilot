import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import path from "node:path";

test.skip(
  process.env.E2E_MOCK_API !== "1",
  "Set E2E_MOCK_API=1 for deterministic v5 browser contract tests",
);
const verified = JSON.parse(
  readFileSync(path.join(__dirname, "../data/repair/v2.json"), "utf8"),
);
const rejected = JSON.parse(
  readFileSync(path.join(__dirname, "../data/repair/v1.json"), "utf8"),
);
const yaml =
  'version: 1\nname: churn-service\npython: "3.12"\ninstall: "pip install -r requirements.txt"\ncommands:\n  test: "pytest -q tests"\ntask:\n  type: restore\n  fail_to_pass: []\n  ml_invariants: []\npolicy:\n  allowed_edit_roots: ["churn/", "tests/"]\n  immutable_existing_tests: true\n';
const staged = {
  id: "abcdef123456",
  source: { kind: "sample", sample: "churn-service" },
  sample: "churn-service",
  sha: "a".repeat(40),
  upstream_sha: "a".repeat(40),
  file_count: 12,
  files: ["autopilot.yaml", "churn/serve.py", "tests/test_serve.py"],
  contract_yaml: yaml,
  contract_errors: [],
  contract_source: "detected",
};
const task = {
  id: "task-browser",
  target: "Restore serving parity",
  status: "PATCHING",
  version: 4,
  created_at: 1793300000,
  repo_name: "churn-service",
  decision: null,
};
type Mock = {
  complete?: boolean;
  failStatus?: boolean;
  failTask?: boolean;
  empty?: boolean;
  calls?: { path: string; method: string; body: unknown }[];
};

test("unsupported changed files are visibly refused rather than shown as awaiting checks", async ({
  page,
}) => {
  await mock(page, { complete: true });
  const report = structuredClone(verified);
  report.candidates[0].skipped_files = ["churn/model.onnx"];
  report.candidates[0].candidate.status = "REJECTED";
  report.candidates[0].eligible = false;
  report.candidates[0].code_receipt = null;
  report.decision = "REJECTED";
  report.finalist = null;
  report.final_ml_receipt = null;
  await page.route("**/tasks/task-browser/report", (route) =>
    route.fulfill({ json: report }),
  );
  await page.goto("/repair/tasks/task-browser");
  await page.getByRole("tab", { name: "Candidates", exact: true }).click();
  await expect(
    page.getByText("Unsupported file changes were refused"),
  ).toBeVisible();
  await expect(
    page.getByText("churn/model.onnx", { exact: true }),
  ).toBeVisible();
  const candidate = page
    .locator(".r-panel")
    .filter({ hasText: "Unsupported file changes were refused" });
  await expect(
    candidate.getByText("Not eligible", { exact: true }),
  ).toBeVisible();
  await expect(
    candidate.getByText("Awaiting checks", { exact: true }),
  ).toHaveCount(0);
});
async function mock(page: Page, options: Mock = {}) {
  let cancelled = false;
  await page.route("**/api/v1/v5/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      p = url.pathname.split("/v5")[1];
    let requestBody: unknown = null;
    try {
      requestBody = req.postDataJSON();
    } catch {}
    options.calls?.push({ path: p, method: req.method(), body: requestBody });
    if (
      (p === "/status" && options.failStatus) ||
      (p === `/tasks/${task.id}` && options.failTask)
    )
      return route.fulfill({
        status: 503,
        json: { detail: "Provider temporarily unavailable" },
      });
    let body: unknown = {};
    if (p === "/status")
      body = {
        configured: true,
        provider: "nebius",
        models: { engineer: "test-model" },
        successful_calls_7d: 0,
        runner: "local-container",
        isolated: true,
        live_runs: true,
        invite_required: true,
        daily_cap_usd: 2,
      };
    else if (p === "/ready")
      body = {
        ready: true,
        database: "ok",
        worker: "ok",
        verifier: "ok",
        sandbox: "ok",
      };
    else if (p === "/budget/estimate")
      body = {
        estimated_inference_usd: 0.18,
        limitations: "Nominal ceiling; prompt overshoot and compute excluded.",
      };
    else if (p === "/samples")
      body = {
        samples: [
          {
            name: "churn-service",
            title: "Churn serving mismatch",
            task: "Restore serving parity",
            failing_tests: ["tests/test_serve.py::test_probability"],
            invariants: ["train_serve_parity"],
          },
        ],
      };
    else if (p === "/repos" || p === "/repos/upload" || p.endsWith("/contract"))
      body = staged;
    else if (p.endsWith("/discover"))
      body = {
        outcomes: {
          "tests/test_serve.py::test_probability": "failed",
          "tests/test_train.py::test_train": "passed",
        },
        failing: ["tests/test_serve.py::test_probability"],
      };
    else if (p === "/packs") body = { id: "pack-browser", rows: 2 };
    else if (p.startsWith("/packs/")) return route.fulfill({ status: 204 });
    else if (p === "/tasks")
      body =
        req.method() === "POST"
          ? task
          : options.empty
            ? []
            : [
                {
                  ...task,
                  ...(options.complete
                    ? { status: "REVIEW_READY", decision: "VERIFIED" }
                    : {}),
                },
              ];
    else if (p === `/tasks/${task.id}`)
      body = {
        ...task,
        status: cancelled
          ? "CANCELLED"
          : options.complete
            ? "REVIEW_READY"
            : task.status,
        decision: options.complete ? "VERIFIED" : null,
      };
    else if (p.endsWith("/events"))
      body = options.complete
        ? verified.events
        : [
            {
              seq: 1,
              type: "hypothesis.proposed",
              actor: "investigator",
              data: {
                hypothesis_id: "H1",
                cause: "Serving transform may differ",
                evidence: "Observed failing test",
              },
            },
          ];
    else if (p.endsWith("/candidates"))
      body = options.complete ? verified.candidates : [];
    else if (p.endsWith("/report")) body = { ...verified, task_id: task.id };
    else if (p.endsWith("/cancel")) {
      cancelled = true;
      body = { ...task, status: "CANCELLED", version: 5 };
    } else if (p.endsWith("/publish")) body = { replay_id: "shared-browser" };
    else if (p.endsWith("/pr"))
      body = { url: "https://github.com/example/ml-service/pull/42" };
    else if (p.startsWith("/replays/")) body = verified;
    else if (p === "/replays") body = { replays: [] };
    else return route.fulfill({ status: 404, json: { detail: "Not found" } });
    await route.fulfill({ json: body });
  });
}

test("home tells the evidence story, exposes all entries, and distinguishes configured from successful calls", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/repair");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "The ML coding agent that checks beyond green tests.",
  );
  await expect(
    page
      .getByRole("link", { name: "Watch a verified repair", exact: true })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Fix your repo", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Live access enabled/ }),
  ).toBeVisible();
  await expect(
    page.getByText("Live calls recorded", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("link", { name: "Try a sample repo", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Where is your code?" }),
  ).toBeVisible();
});

test("source, contract, discovery, evaluation pack and task submission use the real API shapes", async ({
  page,
}) => {
  const calls: NonNullable<Mock["calls"]> = [];
  await mock(page, { calls });
  await page.goto("/repair/new");
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await expect(page.getByLabel("autopilot.yaml", { exact: false })).toHaveValue(
    yaml,
  );
  await page.getByRole("tab", { name: "Guided builder" }).click();
  await page.getByLabel("Editable folders").fill("../outside");
  await expect(
    page.getByRole("button", { name: "Validate and continue" }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "Detected YAML" }).click();
  await page.getByRole("button", { name: "Validate and continue" }).click();
  await page.getByRole("button", { name: "Discover tests" }).click();
  await expect(
    page.getByRole("checkbox", { name: /test_probability/ }),
  ).toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: /test_train/ }),
  ).not.toBeChecked();
  await page.getByLabel("Holdout labels CSV (optional)").setInputFiles({
    name: "labels.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("customer_id,churned\n1,0\n2,1\n"),
  });
  await page.getByRole("button", { name: "Upload evaluation pack" }).click();
  await expect(
    page.getByText("Evaluation pack uploaded · 2 rows"),
  ).toBeVisible();
  await page
    .getByLabel("Live-run invite code (if required)")
    .fill("browser-invite");
  await page.getByRole("button", { name: "Start repair", exact: true }).click();
  await expect(page).toHaveURL(/\/repair\/tasks\/task-browser$/);
  const submission = calls.find(
    (c) => c.path === "/tasks" && c.method === "POST",
  );
  expect(submission?.body).toEqual({
    repo_id: staged.id,
    target: "Restore serving parity",
    fail_to_pass: ["tests/test_serve.py::test_probability"],
    pack_id: "pack-browser",
    mode: "full",
    invite_code: "browser-invite",
    budget:{max_candidates:2,coder_tokens:160000},
  });
});

test("invalid URL is rejected, source tabs work by keyboard, and ZIP intake can proceed", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/repair/new?source=url");
  await page
    .getByLabel("Repository HTTPS URL")
    .fill("https://user:password@github.com/example/repo");
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await expect(page.locator(".repair").getByRole("alert")).toContainText(
    "without credentials",
  );
  await page.getByRole("tab", { name: "Git repository" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "ZIP upload" })).toBeFocused();
  await page.getByLabel("Repository ZIP").setInputFiles({
    name: "repo.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("fixture is handled by mocked intake"),
  });
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await expect(
    page.getByRole("heading", { name: "Repair boundaries" }),
  ).toBeVisible();
});

test("recorded timeline does not leak future evidence; tour, scrubbing, comparison, diff and downloads work", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/repair/replay/churn_live_prompt_v1");
  await expect(page.getByText("Genuine historical recording")).toBeVisible();
  await expect(
    page.getByText("No candidate has been created yet."),
  ).toBeVisible();
  await expect(
    page.getByText("Tests green — rejected by ML check"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Guided tour", exact: true }).click();
  await expect(page.getByText(/1 of .*Competing explanations/)).toBeVisible();
  await page.getByRole("button", { name: "Next stop" }).click();
  await expect(page.getByText(/2 of .*An actual tool call/)).toBeVisible();
  await page.getByRole("button", { name: "Close tour" }).click();
  await page.getByLabel("Playback speed").selectOption("4");
  await page
    .getByLabel("Replay timeline")
    .fill(
      (await page.getByLabel("Replay timeline").getAttribute("max")) || "0",
    );
  await page.getByRole("tab", { name: "Candidates", exact: true }).click();
  await expect(
    page.getByText("Tests green — rejected by ML check"),
  ).toHaveCount(rejected.candidates.length);
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await expect(
    page.getByRole("heading", {
      name: "The evidence says: don’t merge this repair.",
    }),
  ).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download receipt JSON" }).click();
  expect((await downloaded).suggestedFilename()).toBe("autopilot-receipt.json");
  await page.getByRole("tab", { name: "PR handoff" }).click();
  await expect(
    page.getByRole("button", { name: "Create draft PR" }),
  ).toHaveCount(0);
});

test("verified receipt preserves true metric and runner, supports split diff and patch download", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/repair/replay/churn_live_prompt_v2");
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await expect(
    page.getByRole("heading", { name: "A repair with evidence behind it." }),
  ).toBeVisible();
  await expect(page.getByText("0.9114", { exact: true })).toHaveCount(2);
  await expect(
    page.getByText("process-unisolated", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await expect(page.locator(".r-diff-split").first()).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download recorded patch" }).click();
  expect((await downloaded).suggestedFilename()).toBe("autopilot-repair.patch");
});

test("new genuine recordings retain isolated runner and real receipt evidence", async ({page}) => {
  await mock(page);
  for (const id of ["nvidia_churn_verified", "nvidia_loan_verified"]) {
    await page.goto(`/repair/replay/${id}`);
    await page.getByRole("tab", {name:"Verdict & receipt"}).click();
    await expect(page.getByRole("heading", {name:"A repair with evidence behind it."})).toBeVisible();
    await expect(page.getByText("local-container", {exact:true})).toBeVisible();
    await expect(page.getByText("nvidia_api", {exact:true})).toBeVisible();
    const downloaded=page.waitForEvent("download");
    await page.getByRole("button", {name:"Download receipt JSON"}).click();
    expect((await downloaded).suggestedFilename()).toBe("autopilot-receipt.json");
  }
});

test("live workspace keeps evidence on connection failure and cancel requires explicit confirmation", async ({
  page,
}) => {
  const calls: NonNullable<Mock["calls"]> = [],
    options: Mock = { calls };
  await mock(page, options);
  await page.goto(`/repair/tasks/${task.id}`);
  await expect(
    page.getByText("Serving transform may differ", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cancel repair" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/cancel"))).toHaveLength(0);
  options.failTask = true;
  await expect(
    page.getByText("Reconnecting to the repair service"),
  ).toBeVisible();
  await expect(
    page.getByText("Serving transform may differ", { exact: true }).first(),
  ).toBeVisible();
  options.failTask = false;
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(
    page.getByText("Reconnecting to the repair service"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel repair" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm cancellation" })
    .click();
  await expect
    .poll(() => calls.filter((c) => c.path.endsWith("/cancel")).length)
    .toBe(1);
  expect(calls.find((c) => c.path.endsWith("/cancel"))?.body).toEqual({
    version: 4,
  });
});

test("finished task sharing requires consent and PR credentials stay out of persistent storage", async ({
  page,
}) => {
  const calls: NonNullable<Mock["calls"]> = [];
  await mock(page, { complete: true, calls });
  await page.goto(`/repair/tasks/${task.id}`);
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await page.getByRole("button", { name: "Share a read-only replay" }).click();
  expect(calls.filter((c) => c.path.endsWith("/publish"))).toHaveLength(0);
  await page.getByRole("button", { name: "Keep private" }).click();
  await page.getByRole("button", { name: "Share a read-only replay" }).click();
  await page
    .getByRole("button", { name: "Publish replay", exact: true })
    .click();
  await expect(page.getByText("Read-only replay published")).toBeVisible();
  await page.getByRole("tab", { name: "PR handoff" }).click();
  await page
    .getByLabel("GitHub repository", { exact: true })
    .fill("example/ml-service");
  await page
    .getByLabel("GitHub access token", { exact: false })
    .fill("test-only-browser-token");
  await page.getByRole("button", { name: "Create draft PR" }).click();
  await expect(
    page.getByRole("link", { name: "Open draft on GitHub" }),
  ).toHaveAttribute("href", "https://github.com/example/ml-service/pull/42");
  await expect(
    page.getByLabel("GitHub access token", { exact: false }),
  ).toHaveValue("");
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain("test-only-browser-token");
});

test("runs filter, benchmark limitations, docs, runtime and missing-task recovery remain usable offline", async ({
  page,
}) => {
  await mock(page, { empty: true, failStatus: true, failTask: true });
  await page.goto("/repair/runs");
  await page.getByLabel("Run type").selectOption("live");
  await expect(
    page.getByRole("heading", { name: "Your first repair starts here" }),
  ).toBeVisible();
  await page.getByLabel("Run type").selectOption("replays");
  await page.getByLabel("Decision", { exact: true }).selectOption("REJECTED");
  await expect(
    page.locator('a[href="/repair/replay/churn_live_prompt_v1"]'),
  ).toBeVisible();
  await expect(
    page.locator('a[href="/repair/replay/nebius_refund_rejected"]'),
  ).toBeVisible();
  await expect(
    page.locator('a[href="/repair/replay/nebius_churn_verified"]'),
  ).toHaveCount(0);
  await expect(
    page.locator('a[href="/repair/replay/churn_live_prompt_v2"]'),
  ).toHaveCount(0);
  await page.goto("/repair/benchmark");
  await expect(
    page.getByText("The matched-budget comparison is not published yet"),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Not measured", exact: true }),
  ).toHaveCount(12);
  await page.goto("/repair/how-it-works");
  await expect(
    page.getByText("The service has not reported runtime status"),
  ).toBeVisible();
  await page.goto("/repair/docs?section=contract");
  await expect(
    page.getByRole("heading", { name: "The autopilot.yaml contract" }),
  ).toBeVisible();
  await page.goto(`/repair/tasks/${task.id}`);
  await expect(page.getByText("We couldn’t open this repair")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Watch a verified recording" }),
  ).toBeVisible();
});

for (const theme of ["light", "dark"])
  for (const width of [1280, 375])
    test(`accessible pages and bounded layout: ${theme} ${width}`, async ({
      page,
    }) => {
      test.setTimeout(120000);
      await mock(page);
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(
        (t) => localStorage.setItem("atelier-theme", t),
        theme,
      );
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      for (const route of [
        "/repair",
        "/repair/new",
        "/repair/runs",
        "/repair/benchmark",
        "/repair/how-it-works",
        "/repair/docs",
        "/repair/settings",
        "/repair/replay/churn_live_prompt_v2",
      ]) {
        await page.goto(route);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        if (route.includes("/replay/"))
          await page.getByRole("tab", { name: "Verdict & receipt" }).click();
        const results = await new AxeBuilder({ page })
          .include(".repair")
          .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
          .analyze();
        expect(
          results.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => ({
              target: n.target,
              summary: n.failureSummary,
            })),
          })),
          route,
        ).toEqual([]);
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth + 1,
          ),
          route,
        ).toBe(true);
      }
      expect(errors).toEqual([]);
    });

test("contract/task forms, active workspace, candidate comparison and mobile navigation are accessible", async ({
  page,
}) => {
  test.setTimeout(120000);
  await mock(page);
  await page.goto("/repair/new");
  const audit = async () => {
    const result = await new AxeBuilder({ page })
      .include(".repair")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(
      result.violations.map((v) => ({
        id: v.id,
        nodes: v.nodes.map((n) => ({
          target: n.target,
          summary: n.failureSummary,
        })),
      })),
    ).toEqual([]);
  };
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await audit();
  await page.getByRole("tab", { name: "Guided builder" }).click();
  await audit();
  await page.getByRole("tab", { name: "Detected YAML" }).click();
  await page.getByRole("button", { name: "Validate and continue" }).click();
  await audit();
  await page.goto(`/repair/tasks/${task.id}`);
  await expect(
    page.getByRole("heading", { name: "Agent activity" }),
  ).toBeVisible();
  await audit();
  await page.goto("/repair/replay/churn_live_prompt_v1");
  await page.getByRole("tab", { name: "Verdict & receipt" }).click();
  await page.getByRole("tab", { name: "Candidates", exact: true }).click();
  await audit();
  await page.setViewportSize({ width: 375, height: 900 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await audit();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(
    await page
      .locator(".repair")
      .evaluate((e) => getComputedStyle(e).scrollBehavior),
  ).not.toBe("smooth");
});

test("handoff report renders probe evidence, drift, image and relative error improvement", async ({
  page,
}) => {
  await mock(page, { complete: true });
  const report = structuredClone(verified);
  report.hypotheses[0].status = "confirmed";
  report.data_profile = {
    columns: 3,
    flagged: [{ column: "tenure", psi: 0.4, missing_shift: 0.1 }],
  };
  report.image = "sha256:" + "a".repeat(64);
  report.final_ml_receipt.metrics.segments = {
    column: "region",
    metric: "mae",
    by_group: {
      north: { candidate: 10.5, baseline: 10, delta: -0.05, rows: 120 },
    },
    too_small: ["small"],
  };
  report.maintainability_notes = ["Review the shared feature-building path."];
  report.final_ml_receipt.metrics.holdout = {
    metric: "rmse",
    candidate: 9,
    baseline: 10,
    rows: 600,
  };
  report.final_ml_receipt.metrics.delta = {
    delta: 0.1,
    ci_low: 0.05,
    ci_high: 0.15,
    relative: true,
  };
  await page.route("**/tasks/task-browser/report", (r) =>
    r.fulfill({ json: report }),
  );
  await page.goto("/repair/tasks/task-browser");
  await expect(
    page.getByText("Probe confirmed", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Data drift findings" }),
  ).toBeVisible();
  await page
    .getByRole("tab", { name: "Verdict & receipt", exact: true })
    .click();
  await expect(page.getByText("10.00%", { exact: true })).toBeVisible();
  await expect(
    page.getByText(/Paired improvement.*5.00%.*15.00%/),
  ).toBeVisible();
  await expect(page.getByText(report.image, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Maintainability review notes" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Group quality guardrails" }),
  ).toBeVisible();
  await expect(page.getByText("-5.00%", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Some groups could not be judged", { exact: true }),
  ).toBeVisible();
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
});

test("no-label verification prominently states its unmeasured quality limit", async ({
  page,
}) => {
  await mock(page, { complete: true });
  const report = structuredClone(verified);
  report.reasons = ["LIMITED_NO_LABELS"];
  report.final_ml_receipt.holdout_opened = false;
  report.final_ml_receipt.metrics = {};
  await page.route("**/tasks/task-browser/report", (r) =>
    r.fulfill({ json: report }),
  );
  await page.goto("/repair/tasks/task-browser");
  await page
    .getByRole("tab", { name: "Verdict & receipt", exact: true })
    .click();
  await expect(
    page.getByText("Quality on unseen data was not measured", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Held-out predictive quality" }),
  ).toHaveCount(0);
});

test("guided contract keeps problem and metric compatible and sends a custom loader", async ({
  page,
}) => {
  const calls: { path: string; method: string; body: unknown }[] = [];
  await mock(page, { calls });
  await page.goto("/repair/new");
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await page.getByRole("tab", { name: "Guided builder", exact: true }).click();
  await page
    .getByLabel("Prediction problem", { exact: true })
    .selectOption("regression");
  await expect(page.getByLabel("Quality metric", { exact: true })).toHaveValue(
    "rmse",
  );
  await expect(
    page.getByLabel("Quality metric", { exact: true }).locator("option"),
  ).toHaveText(["rmse", "mae", "r2"]);
  await page
    .getByLabel("Custom model loader", { exact: true })
    .fill("churn.io:load");
  await page.getByRole("button", { name: "Validate and continue" }).click();
  const body = calls.find(
    (c) => c.method === "PUT" && c.path.endsWith("/contract"),
  )?.body as { yaml: string };
  expect(body.yaml).toContain("problem: regression");
  expect(body.yaml).toContain("metric: rmse");
  expect(body.yaml).toContain('load_model: "churn.io:load"');
});

test("budget controls bind the request and show server estimate", async ({
  page,
}) => {
  const calls: { path: string; method: string; body: unknown }[] = [];
  await mock(page, { calls });
  await page.goto("/repair/new");
  await page.getByRole("button", { name: "Continue to contract" }).click();
  await page.getByRole("button", { name: "Validate and continue" }).click();
  await page
    .getByText("Advanced: execution and spend", { exact: true })
    .click();
  await page.getByLabel("Maximum candidates", { exact: true }).fill("1");
  await page.getByLabel("Coding token ceiling", { exact: true }).fill("4096");
  await expect(
    page.getByText("Estimated inference at nominal token ceilings", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start repair", exact: true }).click();
  const body = calls.find((c) => c.method === "POST" && c.path === "/tasks")
    ?.body as { budget: unknown };
  expect(body.budget).toEqual({ max_candidates: 1, coder_tokens: 4096 });
});

test("live progress shows source edits, steps and usage before final report", async ({
  page,
}) => {
  await mock(page);
  await page.route("**/tasks/task-browser/events?after=*", (r) =>
    r.fulfill({
      json: [
        {
          seq: 1,
          type: "candidate.created",
          actor: "platform",
          data: { candidate_id: "stream-c1" },
        },
        {
          seq: 2,
          type: "diagnostic.executed",
          actor: "agent",
          data: {
            kind: "telemetry", telemetry_type:"candidate.progress", telemetry_version:1,
            candidate_id: "stream-c1",
            step: {
              n: 1,
              thought: "Use the shared feature list.",
              action: "apply_edit",
              target: "churn/serve.py",
              ok: true,
              excerpt: "Applied edit",
            },
            diff: "--- a/churn/serve.py\n+++ b/churn/serve.py\n@@ -1 +1 @@\n-old\n+shared_features\n",
          },
        },
        {
          seq: 3,
          type: "diagnostic.executed",
          actor: "platform",
          data: {
            kind: "telemetry", telemetry_type:"usage.updated", telemetry_version:1,
            usage: {
              super: {
                calls: 1,
                prompt_tokens: 900,
                completion_tokens: 100,
                budget: 4096,
                est_cost_usd: 0.001,
              },
              total_est_cost_usd: 0.001,
            },
            tool_calls: 1,
          },
        },
      ],
    }),
  );
  await page.goto("/repair/tasks/task-browser");
  await expect(
    page.locator(".r-diff").getByText("shared_features", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("1,000 / 4,096 tokens", { exact: false }),
  ).toBeVisible();
  await expect(
    page.locator(".r-terminal").filter({hasText:"Applied edit"}).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "A repair with evidence behind it." }),
  ).toHaveCount(0);
});

test("benchmark exposes checker and public-subset evidence without agent claims", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/repair/benchmark");
  await expect(
    page.getByRole("heading", { name: "Five independent checker proofs" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Published MLOps repository patterns" }),
  ).toBeVisible();
  await expect(
    page.getByText("great-expectations-serving", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("mlflow-features", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Subset feasibility, not a leaderboard result", {
      exact: true,
    }),
  ).toBeVisible();
});


test("judge limits explain the allowance and a manual 429 retry preserves intent", async ({page}) => {
  await mock(page);
  await page.route("**/api/v1/v5/status", route=>route.fulfill({json:{configured:false,successful_calls_7d:0,runner:"docker",isolated:true,live_runs:true,invite_required:true,access_policy:{judge_only:true,daily_runs_per_workspace:12,cooldown_seconds:30,max_active_tasks:2,total_estimated_usd_cap:20,reason:"Finite hackathon credit preserves judge access; recordings remain available."}}}));
  const keys:string[]=[];
  await page.route("**/api/v1/v5/tasks",async route=>{
    if(route.request().method()!=="POST")return route.fallback();
    keys.push(route.request().headers()["idempotency-key"]);
    expect(route.request().postDataJSON().invite_code).toBe("judge-test");
    if(keys.length===1)return route.fulfill({status:429,headers:{"Retry-After":"30"},json:{detail:"The live repair slots are busy. Let an existing repair finish, then retry."}});
    return route.fulfill({status:201,json:task});
  });
  await page.goto("/repair/new");
  await page.getByRole("button",{name:"Continue to contract"}).click();
  await page.getByRole("button",{name:"Validate and continue"}).click();
  await expect(page.getByText("Live evaluation for judges",{exact:true})).toBeVisible();
  await expect(page.getByText(/12 new runs per workspace/)).toBeVisible();
  await page.getByRole("button",{name:"Discover tests"}).click();
  await page.getByLabel("Live-run invite code (if required)").fill("judge-test");
  await page.getByRole("button",{name:"Start repair",exact:true}).click();
  await expect(page.getByText(/live repair slots are busy/)).toBeVisible();
  await expect(page.getByLabel("Live-run invite code (if required)")).toHaveValue("judge-test");
  await page.getByRole("button",{name:"Start repair",exact:true}).click();
  await expect(page).toHaveURL(/tasks\/task-browser$/);
  expect(keys.length).toBe(2);expect(keys[0]).toBeTruthy();expect(keys[0]).toBe(keys[1]);
});
