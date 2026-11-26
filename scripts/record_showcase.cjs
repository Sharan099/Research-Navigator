/* Record real UI interactions. Historical AI output is always labeled as a replay. */
const path = require('node:path');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.resolve(__dirname, '../.cache/pw-browsers');
const { chromium, expect } = require('@playwright/test');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');

const root = path.resolve(__dirname, '..');
const rehearsal = process.argv.includes('--rehearse');
const supplied = process.argv.find(value => value.startsWith('--snapshot='));
const snapshot = supplied ? supplied.slice('--snapshot='.length)
  : '.runtime/post-migration-20261124-162506-backup.sqlite3';
const output = path.join(root, rehearsal ? '.runtime/showcase-rehearsal' : 'showcase');
const base = 'http://127.0.0.1:8016';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms)); // Presentation pacing, never readiness.
const chapters = [];
const browserErrors = [];
let browser, server, context, page, recordingStart, metadata;

async function ready() {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error(`Recording server exited: ${server.exitCode}`);
    try {
      const response = await fetch(`${base}/__showcase/metadata`);
      if (response.ok) { metadata = await response.json(); return; }
    } catch { /* Poll only the recording server, not a paid provider. */ }
    await sleep(100);
  }
  throw new Error('Isolated recording server did not become ready.');
}

async function decorate(target, title, detail, mode = 'LIVE UI - OFFLINE COPY') {
  await target.evaluate(({ title, detail, mode }) => {
    let banner = document.getElementById('showcase-caption');
    if (!banner) {
      const style = document.createElement('style');
      style.textContent = `html { scroll-padding-top: 112px !important; } body { padding-top: 88px !important; }
        #showcase-caption { position:fixed; inset:0 0 auto; height:88px; z-index:2147483647; box-sizing:border-box;
        padding:14px 32px; background:#101b30; color:white; font-family:Arial,sans-serif; display:flex;
        justify-content:space-between; align-items:center; border-bottom:3px solid #79c6ff; pointer-events:none; }
        #showcase-caption strong { display:block; font-size:24px; line-height:1.2; }
        #showcase-caption small { display:block; margin-top:5px; font-size:16px; color:#d6e4f6; }
        #showcase-caption em { font-size:12px; font-weight:bold; font-style:normal; padding:10px 12px;
        border:1px solid #8aa0bb; border-radius:20px; max-width:180px; text-align:center; }
        .activity-panel,.topic-sidebar { top:106px !important; max-height:calc(100dvh - 125px) !important; }
        dialog { z-index:2147483647; }
        @media(max-width:600px) { #showcase-caption { padding:10px 14px; } #showcase-caption strong {font-size:19px;}
        #showcase-caption small {font-size:12px;} #showcase-caption em {display:none;} }`;
      document.head.append(style);
      banner = document.createElement('div'); banner.id = 'showcase-caption';
      banner.innerHTML = '<div><strong></strong><small></small></div><em></em>';
      document.body.append(banner);
    }
    banner.querySelector('strong').textContent = title;
    banner.querySelector('small').textContent = detail;
    banner.querySelector('em').textContent = mode;
  }, { title, detail, mode });
}

async function scene(slug, title, detail, action, seconds = 8, mode) {
  const start = Date.now();
  await decorate(page, title, detail, mode);
  await action();
  await page.screenshot({ path: path.join(output, 'screenshots', `${slug}.png`) });
  chapters.push({ title, caption: detail, mode: mode || 'Live UI, offline database copy',
    seconds: Number(((start - recordingStart) / 1000).toFixed(2)), screenshot: `screenshots/${slug}.png` });
  console.log(`Captured ${slug}`);
  await sleep(rehearsal ? 100 : Math.max(500, seconds * 1000 - (Date.now() - start)));
}

async function move(target) {
  await target.scrollIntoViewIfNeeded();
  await expect(target).toBeVisible();
}
async function send(question) {
  const count = await page.locator('.assistant-block').count();
  await page.getByRole('textbox', { name: /Ask about this research topic|Answer the clarification question/ }).fill(question);
  await page.locator('#search-button').click();
  await expect(page.locator('.assistant-block')).toHaveCount(count + 1);
  await expect(page.locator('#search-button')).toBeEnabled();
  await move(page.locator('.assistant-block').last());
}
async function completed(action) {
  const count = await page.locator('.assistant-block').count();
  await action();
  await expect(page.locator('.assistant-block')).toHaveCount(count + 1);
  await expect(page.locator('#search-button')).toBeEnabled();
}
async function newTopic() {
  await page.getByRole('button', { name: '+ New topic', exact: true }).click();
  await expect(page.locator('.assistant-block')).toHaveCount(0);
  await expect(page.locator('#research-question')).toBeEnabled();
  await expect(page.locator('#hero')).toBeVisible();
}
async function activateReplay() {
  await page.locator('#topic-list').getByRole('button', { name: metadata.replay_title, exact: true }).click();
  await expect(page.locator('#topic-title')).toHaveText(metadata.replay_title);
  await expect(page.locator('#search-button')).toBeEnabled();
}

async function mobileScreenshots() {
  const mobile = await browser.newContext({ viewport: { width: 430, height: 932 }, deviceScaleFactor: 1,
    isMobile: true, hasTouch: true, bypassCSP: true, reducedMotion: 'reduce' });
  const p = await mobile.newPage();
  await p.addInitScript(id => localStorage.setItem('research-active-topic', id), metadata.replay_topic_id);
  await p.goto(base);
  await expect(p.locator('#research-question')).toBeEnabled();
  await decorate(p, 'Mobile workspace', 'Saved session - historical AI response replay', 'SAVED RESPONSE REPLAY');
  await p.getByRole('button', { name: 'Topics', exact: true }).click();
  await expect(p.locator('#topic-dialog')).toBeVisible();
  await p.screenshot({ path: path.join(output, 'screenshots/17-mobile-topics.png') });
  await p.getByRole('button', { name: 'Close', exact: true }).filter({ visible: true }).click();
  await p.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(p.locator('#activity-drawer')).toBeVisible();
  await p.screenshot({ path: path.join(output, 'screenshots/18-mobile-activity.png') });
  await mobile.close();
}

function timestamp(seconds) {
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
}
function html(text) {
  return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
}
async function viewingGuide() {
  const screenshots = (await fs.readdir(path.join(output, 'screenshots'))).filter(name => name.endsWith('.png')).sort();
  const rows = chapters.map(chapter => `| ${timestamp(chapter.seconds)} | ${chapter.title} | [PNG](${chapter.screenshot}) |`).join('\n');
  await fs.writeFile(path.join(output, 'README.md'), `# Research Navigator walkthrough\n\nOpen [the local player](index.html) or [the video](research-navigator-demo.webm).\n\nThe video has numbered English captions and no audio. It records real UI interactions in a disposable copy of a verified backup. Chapters 08–09 show genuine historical Gemini output, labeled **Saved response replay**. All new provider access is disabled; source refresh, comparisons, and summaries demonstrate the honest offline fallback. No paper metadata or AI findings were invented.\n\n## Chapters\n\n| Start | Feature | Screenshot |\n|---|---|---|\n${rows}\n\n## Screenshots\n\n${screenshots.map(name => `- [${name}](screenshots/${name})`).join('\n')}\n\n## Reproduce\n\nFrom the project root, install the locked Python and npm dependencies and Chromium, then run:\n\n\`\`\`text\nnode scripts/record_showcase.cjs --snapshot=PATH_TO_VERIFIED_SQLITE_BACKUP\n\`\`\`\n\nThe snapshot must contain a genuine saved Gemini answer. Add \`--rehearse\` for a fast interaction check without video; rehearsal outputs stay under \`.runtime/\`. The recorder uses local port 8016, shuts down only its own recording server, and verifies the source snapshot hash. The application source and personal database are not changed.\n\nThe [capture manifest](capture-manifest.json) records chapter times, replay provenance, isolation, and provider-attempt checks. This recording demonstrates behavior; it does not establish scientific accuracy or fresh provider availability.\n`, 'utf8');
  await fs.writeFile(path.join(output, 'index.html'), `<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Navigator — feature walkthrough</title>
<style>body{font:16px/1.6 system-ui,sans-serif;margin:0;background:#f1f5fb;color:#182a43}main{max-width:1200px;margin:auto;padding:28px}h1{margin-bottom:4px}p{color:#526580}video{width:100%;background:#101b30;border-radius:12px}button{font:inherit;background:white;color:#173e85;border:1px solid #ccd8ed;border-radius:8px;padding:9px 12px;cursor:pointer;text-align:left}button:focus-visible,a:focus-visible{outline:3px solid #1957e5}nav{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px;margin:18px 0}#gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}figure{margin:0;background:white;border:1px solid #d3dfed;padding:10px;border-radius:8px}img{width:100%;height:210px;object-fit:contain}figcaption{font-size:13px;overflow-wrap:anywhere}a{color:#174bba}</style>
<main><h1>Research Navigator</h1><p>A feature-by-feature walkthrough · captions · no narration</p>
<video id="video" controls preload="metadata" poster="screenshots/01-workspace.png"><source src="research-navigator-demo.webm" type="video/webm">Open the WebM file in a modern browser.</video>
<p>Live UI uses an isolated offline copy. Chapters 08–09 replay genuine saved Gemini output. No new provider calls were made.</p>
<nav aria-label="Video chapters">${chapters.map(c => `<button type="button" data-seconds="${c.seconds}">${timestamp(c.seconds)} · ${html(c.title)}</button>`).join('')}</nav>
<h2>Important steps</h2><section id="gallery" aria-label="Feature screenshots">${screenshots.map(name => `<figure><a href="screenshots/${name}"><img loading="lazy" src="screenshots/${name}" alt="${html(name.replace('.png', '').replaceAll('-', ' '))}"></a><figcaption>${html(name)}</figcaption></figure>`).join('')}</section>
<p><a href="README.md">Viewing guide</a> · <a href="capture-manifest.json">Capture manifest</a></p></main>
<script>document.querySelectorAll('[data-seconds]').forEach(button=>button.addEventListener('click',()=>{const video=document.getElementById('video');video.currentTime=Number(button.dataset.seconds);video.play().catch(()=>{});video.scrollIntoView({block:'center'});}));</script></html>`, 'utf8');
}

async function main() {
  await fs.mkdir(path.join(output, 'screenshots'), { recursive: true });
  const env = { ...process.env,
    PYTHONPATH: [path.join(root, '.tools'), root, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PLAYWRIGHT_BROWSERS_PATH: path.join(root, '.cache/pw-browsers') };
  process.env.PLAYWRIGHT_BROWSERS_PATH = env.PLAYWRIGHT_BROWSERS_PATH;
  server = spawn('python', ['scripts/showcase_server.py', snapshot], { cwd: root, env, windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'] });
  server.stderr.on('data', chunk => process.stderr.write(chunk));
  await ready();
  browser = await chromium.launch({ headless: true });
  await mobileScreenshots();
  context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1,
    bypassCSP: true, reducedMotion: 'reduce', ...(rehearsal ? {} : {
      recordVideo: { dir: path.join(output, 'raw'), size: { width: 1600, height: 1000 } } }) });
  page = await context.newPage();
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.route('https://**/*', route => route.abort());
  recordingStart = Date.now();
  await page.goto(base);
  await expect(page.locator('#research-question')).toBeEnabled();

  await scene('01-workspace', '01 / Your research workspace',
    'One conversation for discovery, paper details, follow-ups, and transparent activity.', async () => {
      await newTopic();
      await expect(page.locator('#hero')).toBeVisible();
      await page.evaluate(() => scrollTo(0, 0));
    }, 8);

  await scene('02-clarification', '02 / Clarify what matters',
    'An ambiguous request triggers a focused question. This demonstration uses the basic parser.', async () => {
      await send('Find the best tactile sensing papers');
      await expect(page.locator('#conversation')).toContainText('Should I prioritize');
    }, 9);
  await scene('03-clarification-reply', '03 / Answer in the same chat',
    'The reply becomes conversation context. Disabled providers are reported honestly.', async () => {
      await send('Prioritize topical relevance');
      await expect(page.locator('#conversation')).toContainText('local');
    }, 8);

  await scene('04-discovery', '04 / Discover publications',
    'Search real stored research. This offline recording shows the local-subset fallback.', async () => {
      await newTopic();
      await send('Find tactile sensing papers');
      await expect(page.locator('.paper')).not.toHaveCount(0);
      await move(page.locator('.paper').first());
    }, 10);
  await scene('05-activity', '05 / See how the request was processed',
    'Expand a timeline step to inspect tool parameters, cache decisions, and source availability.', async () => {
      await page.locator('.assistant-block').last().getByRole('button', { name: 'View activity' }).click();
      await expect(page.locator('#activity-list details')).not.toHaveCount(0);
      const steps = page.locator('#activity-list details');
      await steps.last().locator('summary').click();
      await move(page.locator('.paper').first());
    }, 9);

  await scene('06-details', '06 / Inspect a paper',
    'Open stored authors, publication metadata, the available abstract, and the original-source link.', async () => {
      await completed(() => page.getByRole('button', { name: /^Details:/ }).nth(1).click());
      await move(page.locator('.assistant-block').last());
      await expect(page.getByRole('button', { name: 'Ask about this paper' }).last()).toBeVisible();
    }, 10);
  await scene('07-follow-up', '07 / Ask about this paper',
    'Paper scope stays in the composer. Without AI access, inspect the provided abstract evidence.', async () => {
      await page.getByRole('button', { name: 'Ask about this paper' }).last().click();
      await expect(page.locator('#paper-scope')).toBeVisible();
      await send('Explain the methods described in this paper');
      await expect(page.locator('#paper-scope')).toBeVisible();
    }, 10);

  await scene('08-saved-answer', '08 / A grounded AI explanation',
    'Historical Gemini output from the verified backup. No new AI request is being shown.', async () => {
      await activateReplay();
      await expect(page.locator('.citation-button')).not.toHaveCount(0);
      await move(page.locator('.citation-button').first());
    }, 11, 'SAVED RESPONSE REPLAY');
  await scene('09-evidence', '09 / Check the supporting evidence',
    'Citation controls open the saved abstract excerpt and its original-source link.', async () => {
      await page.locator('.citation-button').first().click();
      await expect(page.locator('.evidence-source').first()).toHaveAttribute('open', '');
      await move(page.locator('.evidence-source').first());
    }, 10, 'SAVED RESPONSE REPLAY');

  await scene('10-comparison', '10 / Select papers to compare',
    'Choose two papers; comparison uses their available abstracts. AI generation is disabled here.', async () => {
      await page.getByRole('button', { name: 'Compare papers', exact: true }).click();
      const boxes = page.getByRole('checkbox', { name: /^Compare:/ });
      await expect(boxes).toHaveCount(3);
      await boxes.nth(0).check(); await boxes.nth(1).check();
      await move(boxes.first());
      await page.screenshot({ path: path.join(output, 'screenshots/10a-selected-papers.png') });
      await completed(() => page.getByRole('button', { name: /^Compare selected/ }).click());
      await move(page.locator('.assistant-block').last());
      await expect(page.locator('.assistant-block').last()).toContainText('unavailable');
    }, 11);
  await scene('11-summary', '11 / Summarize the topic',
    'Topic summaries use the available abstracts. An unavailable model never becomes invented findings.', async () => {
      await completed(() => page.getByRole('button', { name: 'Summarize topic', exact: true }).click());
      await move(page.locator('.assistant-block').last());
    }, 8);

  await scene('12-filters', '12 / Control dates and ranking',
    'Set an explicit publication range and choose relevance, newest, or most cited.', async () => {
      await page.locator('#filter-panel > summary').click();
      await page.locator('#date-range').selectOption('custom');
      await page.getByLabel('From', { exact: true }).fill('2022-01-01');
      await page.getByLabel('Through', { exact: true }).fill('2026-11-24');
      await page.locator('#sort-mode').selectOption('most_cited');
      await move(page.locator('#filter-panel'));
      await page.screenshot({ path: path.join(output, 'screenshots/12a-filter-controls.png') });
      await completed(() => page.getByRole('button', { name: 'Apply filters', exact: true }).click());
      await expect(page.locator('#active-filters')).toContainText('Most cited');
      await move(page.locator('#active-filters'));
    }, 10);
  await scene('13-latest', '13 / Find the latest research',
    'One click selects the last 30 days and newest-first ranking. Local coverage can be empty.', async () => {
      await completed(() => page.getByRole('button', { name: 'Latest research', exact: true }).click());
      await expect(page.locator('#date-range')).toHaveValue('last_30_days');
      await move(page.locator('.assistant-block').last());
    }, 8);
  await scene('14-refresh', '14 / Refresh sources transparently',
    'Refresh bypasses the cache. This offline run reports the disabled source and local fallback.', async () => {
      await completed(() => page.getByRole('button', { name: 'Refresh sources', exact: true }).click());
      await move(page.locator('.assistant-block').last());
    }, 8);

  await scene('15-memory', '15 / Keep and organize your research',
    'Rename a topic, reload the page, and return to its saved messages and processing activity.', async () => {
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      await page.getByLabel('Topic name', { exact: true }).fill('Showcase - research workspace');
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.locator('#rename-dialog')).not.toBeVisible();
      await page.reload();
      await expect(page.locator('#topic-title')).toHaveText('Showcase - research workspace');
      await decorate(page, '15 / Keep and organize your research', 'Saved messages survive reload. This is an isolated recording copy.');
      await page.evaluate(() => scrollTo(0, 0));
    }, 9);
  await scene('16-usage', '16 / Understand usage and delete safely',
    'Daily limits are visible. Deleting a conversation retains shared publication records.', async () => {
      await page.locator('#usage-panel > summary').click();
      await expect(page.locator('#usage-values')).toContainText('AI turns:');
      await move(page.locator('#usage-panel'));
      await page.screenshot({ path: path.join(output, 'screenshots/16a-daily-usage.png') });
      await page.getByRole('button', { name: 'Delete conversation', exact: true }).click();
      await expect(page.locator('#delete-dialog')).toBeVisible();
      await page.screenshot({ path: path.join(output, 'screenshots/16b-delete-confirmation.png') });
      await page.locator('#confirm-delete').click();
      await expect(page.locator('#delete-dialog')).not.toBeVisible();
      await page.evaluate(() => scrollTo(0, 0));
    }, 9);

  await scene('19-mobile', '17 / Research on a smaller screen',
    'Actual mobile screenshots: saved topics and the expandable processing timeline.', async () => {
      const images = await Promise.all(['17-mobile-topics.png', '18-mobile-activity.png'].map(async name =>
        `data:image/png;base64,${(await fs.readFile(path.join(output, 'screenshots', name))).toString('base64')}`));
      await page.evaluate(images => {
        const shell = document.getElementById('app-shell');
        shell.style.display = 'none';
        const gallery = document.createElement('section');
        gallery.style.cssText = 'display:flex;justify-content:center;gap:70px;height:840px;padding:20px;background:#e9eef7';
        for (const url of images) {
          const img = document.createElement('img'); img.src = url;
          img.style.cssText = 'height:830px;border:1px solid #a4b4c8;border-radius:18px;box-shadow:0 14px 35px #10213a30';
          gallery.append(img);
        }
        document.body.append(gallery); scrollTo(0, 0);
      }, images);
    }, 9, 'MOBILE SCREENSHOTS');

  if (browserErrors.length) throw new Error(`Browser errors: ${browserErrors.join('; ')}`);
  const metrics = await (await fetch(`${base}/metrics`)).text();
  if (/research_provider_attempts_total\{[^}]*outcome="(?:success|retry|transport_error|rejected)"/.test(metrics)) {
    throw new Error('Unexpected external provider attempt during offline capture.');
  }
  const video = rehearsal ? null : page.video();
  await context.close(); context = null;
  if (video) await video.saveAs(path.join(output, 'research-navigator-demo.webm'));
  await fs.writeFile(path.join(output, 'capture-manifest.json'), JSON.stringify({
    recorded_at: new Date().toISOString(), ...metadata, duration_seconds: Number(((Date.now() - recordingStart) / 1000).toFixed(2)),
    viewport: { width: 1600, height: 1000 }, narration: false, browser_errors: browserErrors,
    paid_provider_attempts: 0, chapters,
  }, null, 2) + '\n');
  await viewingGuide();
  console.log(`Completed: ${output}`);
}

main().catch(async error => {
  console.error(error);
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(output, 'capture-error.png') }).catch(() => {});
  process.exitCode = 1;
}).finally(async () => {
  if (context) await context.close().catch(() => {});
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    await fetch(`${base}/__showcase/shutdown`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});
    await Promise.race([new Promise(resolve => server.once('exit', resolve)), sleep(5000)]);
    if (server.exitCode === null) server.kill();
  }
});
