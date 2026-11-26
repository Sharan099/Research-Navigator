/* Render title and architecture cards for the demo; this never opens the application. */
const path = require('node:path');
const fs = require('node:fs/promises');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.resolve(__dirname, '../.cache/pw-browsers');
const { chromium } = require('@playwright/test');

const directory = path.resolve(process.argv[2]);
const steps = ['Acquire', 'Preserve raw', 'Validate', 'Normalize', 'Upsert', 'Reconcile'];
const details = [
  'OpenAlex metadata preview', 'Original JSON + provider ID + retrieval time',
  'Field types, identifiers and year/date agreement', 'DOI values and abstract word positions',
  'Stable provider identity; ordered author links', 'Received = valid + rejected',
];
const style = `<style>
*{box-sizing:border-box}body{margin:0;width:1920px;height:1080px;background:#081525;color:#eff7ff;
font:30px/1.45 'Segoe UI',Arial,sans-serif;overflow:hidden}.grid{position:absolute;inset:0;
background-image:linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff05 1px,transparent 1px);
background-size:72px 72px}.shell{position:relative;padding:90px 120px;height:100%}.eyebrow{font-size:24px;
letter-spacing:5px;color:#6de5cf;text-transform:uppercase}h1{font-size:100px;line-height:1.12;letter-spacing:-4px;
margin:45px 0 25px;max-width:1550px}p{color:#a9bdd5;font-size:36px;max-width:1450px}.pill{display:inline-block;
padding:12px 25px;border:1px solid #527290;border-radius:40px;font-size:24px;margin:10px 15px 0 0;color:#bfdaef}
footer{position:absolute;bottom:64px;left:120px;right:120px;border-top:1px solid #30455e;padding-top:24px;
display:flex;justify-content:space-between;color:#8fa9c3;font-size:22px}.accent{color:#6de5cf}.line{display:flex;
gap:20px;margin-top:65px}.step{width:262px;min-height:220px;border:1px solid #38516c;border-radius:20px;
padding:24px 18px;background:#102238}.step.active{border:3px solid #6de5cf;background:#143c41;box-shadow:0 0 40px #6de5cf18}
.number{font-size:22px;color:#80a4c6}.step b{display:block;font-size:30px;margin:14px 0}.step small{font-size:20px;color:#a9bdd5;
display:block}.database{margin-top:35px;border-left:5px solid #6de5cf;padding:15px 28px;background:#102238;
font-size:28px;color:#b8d1e5}.github{font-size:45px;color:#6de5cf;margin-top:50px}.mark{display:inline-flex;gap:18px;
align-items:center;color:#6de5cf;font-size:22px}.dot{width:14px;height:14px;background:#6de5cf;border-radius:50%}
</style>`;

function card(body, footer = 'Architecture illustration · no live ingestion shown') {
  return `<!doctype html><meta charset="utf-8">${style}<div class="grid"></div><main class="shell">${body}
  <footer><span>RESEARCH NAVIGATOR</span><span>${footer}</span></footer></main>`;
}

async function main() {
  await fs.mkdir(directory, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
    const cards = [
      ['intro', card(`<div class="eyebrow">Scientific discovery / Data engineering</div>
      <h1>Research<br><span class="accent">Navigator</span></h1>
      <p>From scientific publications<br>to actionable research insights.</p>
      <span class="pill">Discover</span><span class="pill">Validate</span><span class="pill">Explore</span>`,
      'Recorded offline demonstration · historical research metadata')],
      ['closing', card(`<div class="eyebrow">Explore the code. Follow the data.</div>
      <h1>Research Navigator</h1><p>A practical data engineering learning project.</p>
      <div class="github">github.com/Sharan099/Research-Navigator</div>
      <p>Architecture · Source code · Reproducible evaluations</p>
      <div class="mark"><span class="dot"></span>GitHub repository · live deployment not verified</div>`,
      'Local prototype · AI-assisted development')],
    ];
    for (let selected = 0; selected < steps.length; selected++) {
      cards.push([`pipeline-${selected}`, card(`<div class="eyebrow">20–35 seconds / Backend architecture</div>
      <h1 style="font-size:72px;margin-top:25px">From source records to reliable storage</h1>
      <p style="font-size:30px">Preserve evidence. Validate metadata. Record uncertainty.</p>
      <section class="line">${steps.map((step, index) => `<div class="step ${index === selected ? 'active' : ''}">
      <span class="number">0${index + 1} →</span><b>${step}</b><small>${details[index]}</small></div>`).join('')}</section>
      <div class="database">SQL storage: raw observations / publications / authors / ingestion runs</div>
      <p style="font-size:25px">Optional LLM: query planning + abstract-grounded answers. Metadata validation stays deterministic.</p>`)]);
    }
    for (const [name, html] of cards) {
      await page.setContent(html);
      await page.screenshot({ path: path.join(directory, `${name}.png`) });
    }
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
