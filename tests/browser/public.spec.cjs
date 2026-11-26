const { test, expect } = require('@playwright/test');

async function proxy(context, blockCookies = false) {
  await context.route('https://demo.hf.space/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const headers = { ...request.headers(), host: 'demo.hf.space' };
    if (blockCookies) delete headers.cookie;
    const response = await route.fetch({ url: `http://127.0.0.1:8015${url.pathname}${url.search}`, headers });
    await route.fulfill({ response });
  });
}

test('two browsers keep separate topics and reject cross-visitor edits and streams', async ({ browser }) => {
  const owner = await browser.newContext();
  const stranger = await browser.newContext();
  try {
    await proxy(owner); await proxy(stranger);
    const first = await owner.newPage(); const second = await stranger.newPage();
    await first.goto('https://demo.hf.space/');
    await second.goto('https://demo.hf.space/');
    await expect(first.locator('#research-question')).toBeEnabled();
    await expect(first.locator('#public-demo-notice')).toBeVisible();
    await expect(second.locator('#research-question')).toBeEnabled();
    const topic = await first.evaluate(async () => (await (await fetch('/api/conversations')).json())[0].id);
    const result = await second.evaluate(async id => {
      const status = {};
      for (const [method, path, body] of [
        ['GET', `/api/conversations/${id}`],
        ['PATCH', `/api/conversations/${id}`, { title: 'stolen' }],
        ['DELETE', `/api/conversations/${id}`],
        ['GET', `/api/conversations/${id}/messages`],
        ['POST', `/api/conversations/${id}/messages`, { request_id: crypto.randomUUID(), message: 'explain this' }],
        ['GET', `/api/conversations/${id}/runs/unknown/events`],
      ]) status[`${method} ${path}`] = (await fetch(path, { method,
        ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })).status;
      return status;
    }, topic);
    expect(Object.values(result).every(status => status === 404)).toBe(true);
    await first.locator('#research-question').fill('Find robot learning papers');
    await first.locator('#search-button').click();
    await expect(first.locator('#conversation')).toContainText('Synthetic robot learning benchmark fixture');
    await expect(second.locator('#conversation')).not.toContainText('Synthetic robot learning benchmark fixture');
    await first.reload();
    await expect(first.locator('#conversation')).toContainText('Synthetic robot learning benchmark fixture');
  } finally { await owner.close(); await stranger.close(); }
});

test('blocked cookies show a direct app link without sharing a topic', async ({ browser }) => {
  const context = await browser.newContext();
  try {
    await proxy(context, true);
    const page = await context.newPage();
    await page.goto('https://demo.hf.space/');
    await expect(page.locator('#workspace-notice')).toContainText('Enable cookies');
    await expect(page.getByRole('link', { name: 'Open application' })).toHaveAttribute('href', 'https://demo.hf.space');
    await expect(page.locator('#research-question')).toBeDisabled();
  } finally { await context.close(); }
});
