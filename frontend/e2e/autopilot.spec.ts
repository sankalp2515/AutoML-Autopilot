import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';

test.skip(process.env.E2E_MOCK_API !== '1', 'Set E2E_MOCK_API=1 for deterministic Autopilot UI contract tests');
const fixture = JSON.parse(readFileSync(path.join(__dirname, '../../docs/benchmarks/autopilot_showcase.json'), 'utf8'));

async function mockAPI(page: Page) {
  let reads = 0;
  await page.route('**/api/v1/autopilot/**', async (route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname.split('/autopilot')[1];
    let body: unknown = {};
    if (p === '/scenarios') body = { scenarios: [{ scenario_id: 'S02', kind: 'degradation', title: fixture.scenario.title, symptom: fixture.scenario.symptom, rows_reference: 3000, rows_current: 2000, labels_available: true }, { scenario_id: 'S03', kind: 'degradation', title: 'Configuration change', symptom: 'Recall fell', rows_reference: 3000, rows_current: 2000, labels_available: true }] };
    else if (p === '/providers') body = { tavily: { configured: false }, nebius_token_factory: { configured: false }, runner: 'process' };
    else if (p === '/investigations' && req.method() === 'GET') body = { investigations: [] };
    else if (p === '/investigations') body = { run_id: fixture.run_id, status: 'CREATED', created: true };
    else if (p === '/showcase' || p.startsWith('/shared/')) body = fixture;
    else if (p.endsWith('/share')) body = { path: '/autopilot/share/test-read-only', token: 'test-read-only', expires_at: 1999999999 };
    else if (p.startsWith('/investigations/')) {
      reads++;
      if (reads === 1) return route.fulfill({ status: 503, json: { detail: 'Temporary network failure' } });
      body = fixture;
    } else if (p === '/samples') body = { samples: [{ id: 'units-bug', title: 'Loan model: sample incident', symptom: 'Quality dropped', notes: 'Adapter release', suggested: { target: 'defaulted', positive_label: '1', serving_threshold: 0.3 } }] };
    else if (p.endsWith('.csv')) return route.fulfill({ contentType: 'text/csv', body: 'income,defaulted\n1,0\n2,1\n' });
    else if (p === '/uploads' && req.method() === 'GET') body = { uploads: [] };
    else if (p === '/uploads') body = { upload_id: '123456abcdef', rows: { reference: 3000, current: 2000 }, columns: [{ name: 'defaulted', kind: 'categorical', unique: 2, null_rate: 0, values: ['0','1'], in_current: true }, { name: 'income', kind: 'numeric', unique: 2000, null_rate: 0.02, values: [], samples: ['40000','30000'], in_current: true }], target_candidates: ['defaulted'], segment_candidates: [], configured: false };
    else if (p.endsWith('/configure')) body = { configured: true, warnings: ['Current labels may be incomplete.'], features: { numeric: ['income'], categorical: [], dropped: [] }, labelled_current_rows: 1200 };
    await route.fulfill({ json: body });
  });
}
test.beforeEach(async ({ page }) => { await mockAPI(page); });

test('primary actions are visible above the fold and scenario keyboard navigation works', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.goto('/autopilot');
  const action = page.getByRole('button', { name: 'Run a sample incident (~40 s)' });
  await expect(action).toBeVisible();
  expect((await action.boundingBox())!.y).toBeLessThan(550);
  await expect(page.getByRole('link', { name: 'Use your own data', exact: true })).toBeVisible();
  await page.getByRole('radio').first().focus(); await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('radio').nth(1)).toBeFocused();
  await expect(page.getByRole('radio').nth(1)).toHaveAttribute('aria-checked', 'true');
});
test('sample reaches its verdict despite a transient API failure', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/autopilot'); await page.getByRole('button', { name: 'Run a sample incident (~40 s)' }).click();
  await expect(page.getByRole('heading', { name: 'Correction verified', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Evidence', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Independent verification' })).toBeVisible();
  expect(errors).toEqual([]);
});
test('upload warnings persist until explicitly continued', async ({ page }) => {
  await page.goto('/autopilot/new');
  await page.getByRole('button', { name: 'sample incident', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Column review' })).toBeVisible();
  await page.getByRole('button', { name: 'Start investigation', exact: true }).click();
  await expect(page.getByText('Current labels may be incomplete.')).toBeVisible();
  await expect(page).toHaveURL(/\/autopilot\/new$/);
  await page.getByRole('button', { name: 'Continue anyway' }).click();
  await expect(page.getByRole('heading', { name: 'Correction verified', exact: true })).toBeVisible();
});
test('empty thresholds and invalid packages produce visible errors', async ({ page }) => {
  await page.goto('/autopilot/new'); await page.getByRole('button', { name: 'sample incident', exact: true }).click();
  await page.getByLabel('Decision threshold used in production').fill('');
  await page.getByRole('button', { name: 'Start investigation', exact: true }).click();
  await expect(page.getByText('Serving threshold must be between 0.01 and 0.99.', { exact: true })).toBeVisible();
  await page.getByLabel('Decision threshold used in production').fill('0.3');
  await page.getByText('Library upgrade, error log and documentation (Tavily research)', { exact: true }).click();
  await page.getByLabel('Package versions before').fill('not a valid package');
  await page.getByRole('button', { name: 'Start investigation', exact: true }).click();
  await expect(page.getByText(/Invalid package line/).last()).toBeVisible();
  await page.getByLabel('Package versions before').fill('');
  await page.getByLabel('Public documentation to read').fill('http://invalid.example');
  await page.getByRole('button', { name: 'Start investigation', exact: true }).click();
  await expect(page.getByText('Enter up to three public HTTPS documentation URLs.')).toBeVisible();
});
test('share page is read-only in a fresh browser session', async ({ browser }) => {
  const context = await browser.newContext(); const page = await context.newPage(); await mockAPI(page);
  await page.goto('/autopilot/share/test-read-only');
  await expect(page.getByText('Read-only shared report')).toBeVisible();
  for (const name of ['Record approval','Record rejection','Cancel','Run next stage','Share read-only link']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
  await context.close();
});
test('guided example has five stops and dismissal is remembered', async ({ page }) => {
  await page.goto('/autopilot/example');
  const guide = page.getByRole('complementary', { name: 'Guided example' });
  await expect(guide).toContainText('1/5');
  for (let i = 0; i < 4; i++) await guide.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(guide).toContainText('5/5'); await guide.getByRole('button', { name: 'Finish tour' }).click();
  await page.reload(); await expect(guide).toHaveCount(0);
});
test('mobile report has no horizontal page overflow or runtime errors', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', (e) => errors.push(e.message));
  await page.setViewportSize({ width: 375, height: 812 }); await page.goto('/autopilot/share/test-read-only');
  await expect(page.getByRole('heading', { name: 'Correction verified', exact: true })).toBeVisible();
  for (const section of ['Summary','Evidence','Audit trail']) {
    await page.getByRole('button', { name: section, exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  expect(errors).toEqual([]);
});

for (const theme of ['light', 'dark']) {
  test(`WCAG AA automated checks in ${theme} theme`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem('atelier-theme', value), theme);
    await page.goto('/autopilot');
    await expect(page.getByRole('button', { name: 'Run a sample incident (~40 s)' })).toBeVisible();
    expect((await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze()).violations).toEqual([]);
    await page.goto('/autopilot/new'); await page.getByRole('button', { name: 'sample incident', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Column review' })).toBeVisible();
    expect((await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze()).violations).toEqual([]);
    await page.goto('/autopilot/share/test-read-only');
    await expect(page.getByRole('heading', { name: 'Correction verified', exact: true })).toBeVisible();
    for (const section of ['Summary','Evidence','Audit trail']) {
      await page.getByRole('button', { name: section, exact: true }).click();
      expect((await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze()).violations).toEqual([]);
    }
  });
}
