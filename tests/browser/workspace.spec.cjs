const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

test('research chat, paper details, filters, persistence and activity', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#research-question')).toBeEnabled();
  await page.locator('#new-topic-button').click();
  await page.locator('#research-question').fill('Find robot learning papers');
  await page.locator('#search-button').click();
  await expect(page.locator('#conversation')).toContainText('Synthetic robot learning benchmark fixture');
  await expect(page.locator('#search-button')).toBeEnabled();
  await page.reload();
  await expect(page.locator('#conversation')).toContainText('Synthetic robot learning benchmark fixture');
  await page.locator('#conversation').getByRole('button', { name: /^Details/ }).first().click();
  await expect(page.locator('#conversation')).toContainText('Synthetic robot learning fixture.');
  await expect(page.locator('#search-button')).toBeEnabled();
  await page.locator('#filter-panel > summary').click();
  await page.locator('#sort-mode').selectOption('most_cited');
  await page.locator('#apply-filters').click();
  await expect(page.locator('#search-button')).toBeEnabled();
  await expect(page.locator('#active-filters')).toContainText(/cited/i);
  if (test.info().project.name === 'mobile') {
    await page.locator('#activity-button').click();
    await expect(page.locator('#activity-drawer')).toBeVisible();
    await expect(page.locator('#mobile-activity-list')).not.toBeEmpty();
    await page.locator('#close-activity').click();
  } else {
    await expect(page.locator('#activity-list')).not.toBeEmpty();
  }
  await expect(page.locator('body')).toHaveJSProperty('scrollWidth', await page.locator('body').evaluate(el => el.clientWidth));
  await page.screenshot({ path: `evaluation/reports/workspace-${test.info().project.name}.png`, fullPage: true });
});

test('keyboard, dialog errors, storage failure and accessibility', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { get() { throw new Error('Synthetic storage denial'); } });
  });
  await page.goto('/');
  await expect(page.locator('#research-question')).toBeEnabled();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(results.violations).toEqual([]);
  await page.locator('#rename-topic').click();
  await expect(page.locator('#rename-dialog')).toBeVisible();
  await page.route('**/api/conversations/*', route => route.request().method() === 'PATCH'
    ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic save failure' }) })
    : route.continue());
  await page.locator('#rename-value').fill('Synthetic browser test');
  await page.locator('#save-rename').click();
  await expect(page.locator('#rename-error')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#rename-dialog')).not.toBeVisible();
  await page.screenshot({ path: `evaluation/reports/accessibility-${test.info().project.name}.png`, fullPage: true });
});
