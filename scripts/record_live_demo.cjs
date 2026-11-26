/* Record a live product demo against an already-running Research Navigator server.
 *
 * Every response in the video comes from live providers (OpenAlex + Gemini/Groq). Nothing is replayed
 * or scripted on the backend. Waits for the backend are logged so build_live_demo.py can compress them.
 *
 *   node scripts/record_live_demo.cjs [--base=http://127.0.0.1:8031] [--rehearse]
 */
const path = require('node:path');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.resolve(__dirname, '../.cache/pw-browsers');
const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs/promises');

const root = path.resolve(__dirname, '..');
const rehearsal = process.argv.includes('--rehearse');
const base = (process.argv.find(v => v.startsWith('--base=')) || '--base=http://127.0.0.1:8031').slice(7);
const output = path.join(root, '.runtime', rehearsal ? 'live-demo-rehearsal' : 'live-demo');
const size = { width: 1920, height: 1000 };
const timeline = [];
const shots = [];
let page, t0;
const now = () => (Date.now() - t0) / 1000;
const pause = ms => page.waitForTimeout(rehearsal ? 50 : ms);

async function caption(title, detail) {
  await page.evaluate(([title, detail]) => {
    let box = document.getElementById('demo-caption');
    if (!box) {
      box = document.createElement('div');
      box.id = 'demo-caption';
      box.style.cssText = 'position:fixed;left:50%;top:22px;transform:translateX(-50%);z-index:2147483646;'
        + 'background:rgba(16,27,48,.92);color:#fff;font:600 22px/1.3 system-ui,sans-serif;padding:12px 22px;'
        + 'border-radius:12px;box-shadow:0 10px 30px #0004;pointer-events:none;text-align:center;max-width:1100px';
      document.body.append(box);
    }
    box.innerHTML = `${title}<br><small style="font-weight:400!important;font-size:16px!important;color:#cfe1ff!important">${detail}</small>`;
  }, [title, detail]);
}

async function cursor() {
  await page.evaluate(() => {
    if (document.getElementById('demo-cursor')) return;
    const c = document.createElement('div');
    c.id = 'demo-cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l16 9-7 2-3 7z" fill="#111" stroke="#fff" stroke-width="1.5"/></svg>';
    c.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;transition:transform .08s';
    document.body.append(c);
    addEventListener('mousemove', e => { c.style.left = `${e.clientX - 3}px`; c.style.top = `${e.clientY - 2}px`; }, true);
    addEventListener('mousedown', () => { c.style.transform = 'scale(.8)'; }, true);
    addEventListener('mouseup', () => { c.style.transform = ''; }, true);
  });
}

async function click(locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: rehearsal ? 1 : 18 });
  await pause(250);
  await locator.click();
}

async function type(text) {
  const box = page.locator('#research-question');
  await click(box);
  await box.pressSequentially(text, { delay: rehearsal ? 0 : 28 });
  await pause(400);
}

// Everything between the submit click and the finished response is a backend wait.
async function waitForReply(action, label) {
  const count = await page.locator('.assistant-block').count();
  const start = now();
  await action();
  await expect(page.locator('.assistant-block')).toHaveCount(count + 1, { timeout: 120_000 });
  await expect(page.locator('#search-button')).toBeEnabled({ timeout: 120_000 });
  timeline.push({ label, start, end: now() });
  console.log(`${label}: ${(now() - start).toFixed(1)}s`);
}

async function shot(name) {
  await page.screenshot({ path: path.join(output, `${name}.png`) });
  shots.push(name);
}

async function scrollTo(locator) {
  await locator.evaluate(el => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  await pause(900);
}

// Browser-window frame plus intro/outro cards (1920x1080 PNGs) for build_live_demo.py.
async function cards() {
  const browser = await chromium.launch({ headless: true });
  const p = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const font = 'font-family:system-ui,Segoe UI,sans-serif';
  const card = (title, lines) => `<body style="margin:0;height:1080px;display:flex;flex-direction:column;
    justify-content:center;align-items:center;background:linear-gradient(135deg,#0d1a33,#173e85);color:#fff;${font}">
    <div style="font-size:84px;font-weight:700;letter-spacing:-1px">${title}</div>
    ${lines.map(l => `<div style="font-size:34px;color:#cfe1ff;margin-top:22px">${l}</div>`).join('')}</body>`;
  const shots = {
    frame: `<body style="margin:0;background:#dee3ea;${font}"><div style="height:80px;background:#dee3ea;display:flex;
      align-items:center;gap:14px;padding:0 22px;box-sizing:border-box">
      <span style="width:14px;height:14px;border-radius:50%;background:#ff5f57"></span>
      <span style="width:14px;height:14px;border-radius:50%;background:#febc2e"></span>
      <span style="width:14px;height:14px;border-radius:50%;background:#28c840"></span>
      <span style="margin-left:24px;color:#5b6472;font-size:22px">&#8592; &#8594; &#8635;</span>
      <div style="flex:1;margin-left:16px;height:44px;border-radius:22px;background:#fff;display:flex;align-items:center;
        padding:0 22px;color:#202124;font-size:20px">&#128274;&nbsp; 127.0.0.1:8000 &nbsp;<span style="color:#777">— Research Navigator</span></div>
      </div></body>`,
    intro: card('Research Navigator', ['Find, filter and understand research papers in plain English',
      '<span style="font-size:26px;color:#9fb8e0">Live recording · OpenAlex + Gemini, with Groq free models as fallback</span>']),
    outro: card('Research Navigator', ['FastAPI · SQLAlchemy · OpenAlex · Gemini / Groq · Docker',
      'Raw-record retention · validated citations · auditable activity',
      '<span style="font-size:30px;color:#fff">github.com/Sharan099/Research-Navigator</span>']),
  };
  for (const [name, html] of Object.entries(shots)) {
    await p.setContent(html);
    await p.screenshot({ path: path.join(output, `${name}.png`), omitBackground: false });
  }
  await browser.close();
}

async function main() {
  await fs.mkdir(output, { recursive: true });
  if (process.argv.includes('--cards')) return cards();
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: size, deviceScaleFactor: 1,
    ...(rehearsal ? {} : { recordVideo: { dir: output, size } }) });
  page = await context.newPage();
  page.setDefaultTimeout(30_000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t0 = Date.now();
  await page.goto(base);
  await expect(page.locator('#research-question')).toBeEnabled();
  await cursor();
  await page.mouse.move(960, 500);
  if (await page.locator('.assistant-block').count()) {
    await click(page.getByRole('button', { name: '+ New topic', exact: true }));
    await expect(page.locator('#hero')).toBeVisible();
  }
  timeline.push({ label: 'start', start: now(), end: now() });
  await pause(1200);

  // 1. Natural-language discovery.
  await caption('Ask for research in plain English', 'An LLM turns the request into a structured OpenAlex query');
  await type('Show me the most cited papers on large language models for robot task planning since 2022');
  await waitForReply(() => click(page.locator('#search-button')), 'search');
  await caption('Live results from OpenAlex', 'Query, date range and ranking were planned by the LLM');
  await scrollTo(page.locator('.paper').first());
  await pause(1800);
  await scrollTo(page.locator('.paper').nth(4));
  await pause(1200);
  await shot('01-results');

  // 2. Natural-language filtering in the same chat.
  await caption('Refine with a follow-up, not a form', 'The conversation keeps context between turns');
  await type('Only keep papers from 2023 onward about language agents that use tools');
  await waitForReply(() => click(page.locator('#search-button')), 'refine');
  await scrollTo(page.locator('.assistant-block').last());
  await pause(2200);
  await shot('02-refined');

  // 3. Open a paper and ask the LLM to explain it.
  await caption('Open a paper', 'Authors, venue, citations and the stored abstract');
  const details = page.locator('.assistant-block').last().getByRole('button', { name: /^Details:/ }).first();
  await waitForReply(() => click(details), 'details');
  await scrollTo(page.locator('.assistant-block').last());
  await pause(1600);
  await click(page.getByRole('button', { name: 'Ask about this paper' }).last());
  await caption('Ask the LLM to explain it', 'Answers may only use the supplied abstract');
  await type('Explain this paper in simple terms. What problem does it solve?');
  await waitForReply(() => click(page.locator('#search-button')), 'explain');
  await scrollTo(page.locator('.assistant-block').last());
  await pause(2500);
  await shot('03-explanation');

  // 4. Verify the evidence behind a claim.
  await caption('Every claim carries a checked quote', 'Citations are validated against the abstract text');
  const citation = page.locator('.assistant-block').last().locator('.citation-button').first();
  await click(citation);
  await scrollTo(page.locator('.evidence-source[open]').first());
  await pause(2500);
  await shot('04-evidence');

  // 5. Auditable activity, including the provider fallback.
  await caption('Transparent processing', 'Each step is logged, including Gemini → Groq fallback');
  await click(page.locator('.assistant-block').last().getByRole('button', { name: 'View activity' }));
  await pause(1500);
  const fallback = page.locator('#activity-list summary', { hasText: 'provider_fallback' }).first();
  if (await fallback.count()) await click(fallback);
  await pause(3000);
  await shot('05-activity');
  timeline.push({ label: 'end', start: now(), end: now() });

  const video = page.video();
  await context.close();
  await browser.close();
  if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`);
  if (video) await fs.rename(await video.path(), path.join(output, 'raw.webm'));
  await fs.writeFile(path.join(output, 'timeline.json'), JSON.stringify({ base, recorded_at: new Date().toISOString(),
    size, waits: timeline, shots }, null, 2));
  console.log(`Done: ${output}`);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
