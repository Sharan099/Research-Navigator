// Node's standard library exercises conversation state; this does not test browser layout.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

async function application(responses, details = {}, savedTopics = new Map(), environment = {}) {
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag;
      this.children = [];
      this.events = {};
      this.attributes = {};
      this.value = '';
      this.open = false;
      this.classList = { add() {}, toggle() {} };
    }
    append(...children) { children.forEach(child => this.insertBefore(child, null)); }
    replaceChildren(...children) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this.append(...children); }
    insertBefore(child, before) {
      child.remove();
      const index = before ? this.children.indexOf(before) : this.children.length;
      this.children.splice(index, 0, child); child.parentNode = this;
    }
    remove() {
      if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
    }
    get firstElementChild() { return this.children[0]; }
    get lastElementChild() { return this.children.at(-1); }
    addEventListener(event, handler) { this.events[event] = handler; }
    setAttribute(key, value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key]; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    focus() { document.activeElement = this; }
    scrollIntoView(options) { this.scrollOptions = options; }
    set innerHTML(value) { throw new Error('Untrusted HTML must not be assigned'); }
  }
  const nodes = new Map();
  const prompt = new Element('button');
  prompt.setAttribute('data-prompt', 'Find microscopy papers');
  const document = {
    querySelector(id) {
      if (!nodes.has(id)) nodes.set(id, new Element());
      return nodes.get(id);
    },
    querySelectorAll() { return [prompt]; },
    createElement(tag) { return new Element(tag); },
  };
  const requests = [];
  let topicNumber = savedTopics.size;
  const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
  const fetch = async (url, options = {}) => {
    if (environment.fetch) {
      const response = await environment.fetch(url, options, reply);
      if (response !== undefined) return response;
    }
    if (url === '/api/health') return reply({});
    if (url === '/api/session') return reply({ public_demo: false });
    if (url === '/api/usage') return reply({ day: '2026-03-02', used: { ai_turns: 1, gemini: 1, openalex: 0 }, limits: { ai_turns: 3, gemini: 6, openalex: 20 } });
    if (url.startsWith('/api/works/')) return reply(details[url], details[url] ? 200 : 404);
    if (url === '/api/conversations') {
      if (options.method === 'POST') {
        const id = `saved-topic-${++topicNumber}`;
        const topic = { id, title: 'New topic', draft: '', state: {}, messages: [], runs: [], busy: false };
        savedTopics.set(id, topic);
        return reply(topic, 201);
      }
      return reply([...savedTopics.values()].map(({ id, title, busy }) => ({ id, title, busy })));
    }
    const id = url.split('/')[3];
    const topic = savedTopics.get(id);
    if (!url.endsWith('/messages')) {
      if (options.method === 'DELETE') { savedTopics.delete(id); return reply(null, 204); }
      if (options.method === 'PATCH') {
        const patch = JSON.parse(options.body);
        if (patch.draft !== undefined) topic.draft = patch.draft;
        if (patch.title) topic.title = patch.title;
        if (patch.filters) topic.state.filters = patch.filters;
        if (patch.selected_work_ids) topic.state.selected_work_ids = patch.selected_work_ids;
      }
      return reply(structuredClone(topic));
    }
    const request = JSON.parse(options.body);
    requests.push(request);
    assert.ok(responses.length, 'Unexpected chat request');
    const next = responses.shift();
    const response = typeof next === 'function' ? await next(request) : next;
    if (response.error) return reply(response, 503);
    const activity = { id: 1, conversation_id: id, request_id: request.request_id,
      stage: 'request', status: response.gate ? 'running' : 'completed', at: '2026-03-02', duration_ms: 15, details: {} };
    const frames = response.frames || [
      ['activity', activity], ['result', { request_id: request.request_id, conversation_id: id, result: response }],
    ];
    let streamText = frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('');
    if (response.crlf) streamText = streamText.replace(/\n/g, '\r\n');
    const encoded = new TextEncoder().encode(streamText);
    // Split inside JSON and multibyte data to exercise incremental decoding.
    return { ok: true, status: 200, body: new ReadableStream({ async start(controller) {
      if (response.gate) {
        const firstFrame = new TextEncoder().encode(`event: activity\ndata: ${JSON.stringify(activity)}\n\n`);
        controller.enqueue(firstFrame);
        await response.gate;
        controller.enqueue(encoded.slice(firstFrame.length));
        controller.close(); return;
      }
      const chunkSize = response.chunkSize || 17;
      for (let offset = 0; offset < encoded.length; offset += chunkSize) controller.enqueue(encoded.slice(offset, offset + chunkSize));
      controller.close();
    } }) };
  };
  vm.runInNewContext(readFileSync(join(__dirname, '../frontend/app.js'), 'utf8'), {
    document, fetch, URL, AbortController, AbortSignal, setTimeout, clearTimeout, TextDecoder, crypto: require('node:crypto').webcrypto,
    ...environment.globals,
  });
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const node = id => nodes.get(`#${id}`);
  const descendants = root => [root, ...root.children.flatMap(descendants)];
  await flush();
  return { nodes, node, requests, prompt, flush, descendants, savedTopics, document,
    async submit(message) {
      await flush();
      node('research-question').value = message;
      node('research-question').events.input();
      node('search-form').events.submit({ preventDefault() {} });
      await flush();
    },
  };
}

const clarification = { status: 'clarification', question: 'Which robotics application matters most?',
  priorities: ['Clarify application'], parser_mode: 'gemini', warnings: [] };
const work = n => ({ id: `https://openalex.org/W${n}`, title: `Synthetic fixture ${n}`, publication_year: 2026,
  publication_date: '2026-03-01', authors: [], source: null, cited_by_count: 1, has_abstract: true });
const results = { status: 'results', answer: 'Synthetic search response', works: [work(1)],
  query: { query: 'robotic grasping', sort: 'relevance', max_results: 10 }, retrieval_source: 'local_cache', warnings: [] };
const answer = { status: 'answer', answer: 'Synthetic explanation', claims: [], sources: [], warnings: [] };

test('clarification answers retain the original request; new topics have independent context', async () => {
  const app = await application([clarification, results, results]);
  await app.submit('Find best robotics papers');
  assert.equal(app.node('search-button-text').textContent, 'Send answer');
  await app.submit('Robotic grasping, prioritize recency');
  assert.equal(app.requests[1].message, 'Robotic grasping, prioritize recency');
  assert.equal('history' in app.requests[1], false);
  assert.equal(app.node('search-button-text').textContent, 'Send');
  app.node('new-topic-button').events.click();
  await app.submit('Find microscopy papers');
  assert.equal(app.requests[2].message, 'Find microscopy papers');
  assert.equal('history' in app.requests[2], false);
  assert.equal('previous_query' in app.requests[2], false);
});

test('failed answers can be retried without losing or duplicating context', async () => {
  const app = await application([clarification, { error: true, detail: 'Synthetic server failure' }, results]);
  await app.submit('Find robotics papers');
  await app.submit('Robotic grasping');
  assert.equal(app.node('research-question').value, 'Robotic grasping');
  assert.equal(app.node('search-button-text').textContent, 'Send answer');
  await app.submit('Robotic grasping');
  assert.deepEqual(app.requests[2], app.requests[1]);
});

test('example prompts start independent topics while a clarification is pending', async () => {
  const app = await application([clarification, results]);
  await app.submit('Find robotics papers');
  app.prompt.events.click();
  await app.flush();
  assert.equal(app.requests[1].message, 'Find microscopy papers');
  assert.equal('clarifications' in app.requests[1], false);
});

test('switching topics restores each draft, filters, results, and previous query', async () => {
  const app = await application([results, { ...results, query: { ...results.query, query: 'microscopy' } }, results]);
  app.node('latest-button').events.click();
  await app.submit('Find robotics papers');
  app.node('research-question').value = 'Unsent robotics question';
  app.node('research-question').events.input();
  app.node('new-topic-button').events.click();
  await app.submit('Find microscopy papers');
  assert.equal(app.requests[1].filters.date_range, 'auto');
  app.node('topic-list').children[0].events.click();
  assert.equal(app.node('research-question').value, 'Unsent robotics question');
  assert.equal(app.node('date-range').value, 'last_30_days');
  assert.equal(app.node('sort-mode').value, 'newest');
  await app.submit('Only papers about learning methods');
  assert.equal('previous_query' in app.requests[2], false);
  assert.equal('work_ids' in app.requests[2], false);
});

test('in-flight responses belong to their originating thread', async () => {
  let resolveOld;
  const delayed = new Promise(resolve => { resolveOld = resolve; });
  const app = await application([() => delayed, { ...results, query: { ...results.query, query: 'microscopy' } }]);
  await app.submit('Find robotics papers');
  app.node('new-topic-button').events.click();
  await app.submit('Find microscopy papers');
  resolveOld(results);
  await app.flush();
  assert.equal(app.node('topic-title').textContent, 'Find microscopy papers');
  app.node('topic-list').children[0].events.click();
  assert.equal(app.node('topic-title').textContent, 'Find robotics papers');
  assert.equal(app.node('summarize-button').disabled, false);
});

test('latest filters, refresh, and summary actions use explicit contracts', async () => {
  const app = await application([results, results, results, answer]);
  await app.submit('Find robotics papers');
  app.node('latest-button').events.click();
  await app.flush();
  assert.equal(app.requests[1].action, 'search');
  assert.equal(app.requests[1].filters.date_range, 'last_30_days');
  assert.equal(app.requests[1].filters.sort, 'newest');
  assert.equal(app.requests[1].reuse_query, true);
  app.node('refresh-button').events.click();
  await app.flush();
  assert.equal(app.requests[2].refresh, true);
  assert.equal(app.requests[2].reuse_query, true);
  app.node('summarize-button').events.click();
  await app.flush();
  assert.equal(app.requests[3].action, 'answer');
  assert.deepEqual(app.requests[3].selected_work_ids, []);
});

test('custom date validation prevents a request with reversed or missing dates', async () => {
  const app = await application([results]);
  await app.submit('Find robotics papers');
  app.node('date-range').value = 'custom';
  app.node('date-from').value = '2026-03-02';
  app.node('date-to').value = '2026-03-01';
  app.node('apply-filters').events.click();
  assert.equal(app.requests.length, 1);
  assert.match(app.node('workspace-notice').textContent, /start date/);
});

test('comparison selection is bounded and sends only selected IDs', async () => {
  const app = await application([{ ...results, works: Array.from({ length: 6 }, (_, i) => work(i + 1)) }, answer]);
  await app.submit('Find robotics papers');
  app.node('compare-button').events.click();
  const inputs = app.descendants(app.node('conversation')).filter(node => node.type === 'checkbox');
  assert.equal(inputs.length, 6);
  inputs.forEach(input => { input.checked = true; input.events.change(); });
  assert.equal(inputs[5].checked, false);
  app.node('compare-selected-button').events.click();
  await app.flush();
  assert.equal(app.requests[1].selected_work_ids.length, 5);
});

test('paper details can scope a question; source text never becomes HTML', async () => {
  const detail = { ...work(1), title: '<img src=x onerror=alert(1)>', abstract: 'Synthetic abstract text', last_fetched_at: '2026-03-02' };
  const app = await application([results, { status: 'details', work: detail }, answer]);
  await app.submit('Find robotics papers');
  const detailsButton = app.descendants(app.node('conversation')).find(node => node.textContent === 'Details');
  detailsButton.events.click();
  await app.flush();
  assert.equal(app.requests[1].action, 'details');
  app.descendants(app.node('conversation')).find(node => node.textContent === 'Ask about this paper').events.click();
  await app.submit('Explain this paper');
  assert.deepEqual(app.requests[2].selected_work_ids, [work(1).id]);
  assert.equal(app.requests[2].action, 'auto');
});

test('chat commands leave history ownership to the backend', async () => {
  const app = await application(Array(6).fill(results));
  for (let n = 0; n < 6; n++) await app.submit(`Find topic refinement ${n}`);
  assert.ok(app.requests.every(request => !('history' in request) && !('work_ids' in request)));
  assert.equal(new Set(app.requests.map(request => request.request_id)).size, 6);
});

test('contextual actions preserve an unsent composer draft', async () => {
  const app = await application([results, results, answer]);
  await app.submit('Find robotics papers');
  app.node('research-question').value = 'My unsent follow-up question';
  app.node('research-question').events.input();
  app.node('latest-button').events.click();
  await app.flush();
  assert.equal(app.node('research-question').value, 'My unsent follow-up question');
  app.node('summarize-button').events.click();
  await app.flush();
  assert.equal(app.node('research-question').value, 'My unsent follow-up question');
});

test('numbered citations open their matching server-supplied abstract', async () => {
  const cited = { ...answer, sources: [{ ...work(1), excerpt: 'Known abstract evidence.', fetched_at: '2026-03-02' }],
    claims: [{ text: 'Synthetic supported claim', citations: [{ work_id: work(1).id, quote: 'Known abstract evidence.' }] }] };
  const app = await application([results, cited]);
  await app.submit('Find robotics papers');
  app.node('summarize-button').events.click();
  await app.flush();
  const nodes = app.descendants(app.node('conversation'));
  const citation = nodes.find(node => node.className === 'citation-button');
  citation.events.click();
  assert.equal(nodes.find(node => node.className === 'evidence-source').open, true);
  assert.equal(citation.title, 'Known abstract evidence.');
});

test('unused blank topics do not accumulate when choosing an example', async () => {
  const app = await application([results]);
  app.node('new-topic-button').events.click();
  assert.equal(app.node('topic-list').children.length, 1);
  app.prompt.events.click();
  await app.flush();
  assert.equal(app.node('topic-list').children.length, 1);
});

test('saved conversations restore messages, drafts, filters, paper scope, and activity', async () => {
  const topics = new Map([['restored', { id: 'restored', title: 'Saved sensors', draft: 'Continue here', busy: false,
    state: { query: results.query, results: results.works, filters: { date_range: 'last_90_days', sort: 'newest' }, selected_work_ids: [work(1).id] },
    messages: [{ role: 'user', text: 'Earlier failed question', request_id: 'failed-run' },
      { role: 'user', text: 'Old research question', request_id: 'old-run' }, { role: 'assistant', result: results, request_id: 'old-run' }],
    runs: [{ id: 'failed-run', status: 'failed', error: 'Earlier operation failed', events: [] },
      { id: 'old-run', status: 'completed', events: [{ id: 1, stage: 'cache', status: 'completed', at: '2026-03-02', duration_ms: 5, details: { decision: 'hit' } }] }] }]]);
  const app = await application([answer], {}, topics);
  assert.equal(app.node('topic-title').textContent, 'Saved sensors');
  assert.equal(app.node('research-question').value, 'Continue here');
  assert.equal(app.node('date-range').value, 'last_90_days');
  assert.match(app.node('scope-title').textContent, /Synthetic fixture 1/);
  assert.match(app.node('activity-status').textContent, /completed/);
  assert.ok(app.descendants(app.node('activity-list')).some(node => /Check saved research/.test(node.textContent || '')));
  const restored = app.descendants(app.node('conversation'));
  assert.ok(restored.findIndex(node => node.textContent === 'Earlier operation failed') < restored.findIndex(node => node.textContent === 'Old research question'));
  await app.submit('Explain this paper');
  assert.deepEqual(app.requests[0].selected_work_ids, [work(1).id]);
});

test('renaming and deleting conversations use saved topic endpoints', async () => {
  const app = await application([results]);
  await app.submit('Find sensor papers');
  app.node('rename-topic').events.click();
  app.node('rename-value').value = 'Sensor review';
  await app.node('rename-form').events.submit({ preventDefault() {} });
  assert.equal(app.node('topic-title').textContent, 'Sensor review');
  assert.equal([...app.savedTopics.values()][0].title, 'Sensor review');
  app.node('delete-topic').events.click();
  await app.node('confirm-delete').events.click();
  assert.equal(app.savedTopics.size, 1);
  assert.equal(app.node('topic-title').textContent, 'New topic');
});

test('a truncated stream retains the request ID for a safe replay retry', async () => {
  const app = await application([{ frames: [] }, results]);
  await app.submit('Find sensor papers');
  assert.ok(app.descendants(app.node('conversation')).some(node => /Connection interrupted/.test(node.textContent || '')));
  await app.submit('Find sensor papers');
  assert.equal(app.requests[0].request_id, app.requests[1].request_id);
  assert.equal(app.descendants(app.node('conversation')).filter(node => node.className === 'user-bubble').length, 1);
});

test('streamed operational errors are visible and allow a new request', async () => {
  const app = await application([request => ({ frames: [['error', { request_id: request.request_id, message: 'Synthetic operation failed' }]] }), results]);
  await app.submit('Find sensor papers');
  assert.ok(app.descendants(app.node('conversation')).some(node => node.textContent === 'Synthetic operation failed'));
  assert.equal(app.node('search-button').disabled, false);
});

test('activity renders while the final response is still pending', async () => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const app = await application([{ ...results, gate }]);
  await app.submit('Find sensor papers');
  assert.equal(app.node('activity-status').textContent, 'Request running');
  assert.equal(app.node('search-button').disabled, true);
  finish();
  await app.flush();
  assert.equal(app.node('activity-status').textContent, 'Request completed');
  assert.equal(app.node('search-button').disabled, false);
});

test('denied browser storage does not prevent restoring and using saved chat', async () => {
  const storage = { getItem() { throw new Error('Storage denied'); }, setItem() { throw new Error('Storage denied'); } };
  const topics = new Map([['saved', { id: 'saved', title: 'Saved topic', state: {}, messages: [], runs: [], busy: false }]]);
  const app = await application([results], {}, topics, { globals: { localStorage: storage } });
  assert.equal(app.node('topic-title').textContent, 'Saved topic');
  await app.submit('Find research papers');
  assert.equal(app.requests.length, 1);
});

test('failed topic activation keeps current topic usable and exposes a retry', async () => {
  const topics = new Map([
    ['first', { id: 'first', title: 'First topic', state: {}, messages: [], runs: [], busy: false }],
    ['second', { id: 'second', title: 'Second topic', state: {}, messages: [], runs: [], busy: false }],
  ]);
  let fail = true;
  const app = await application([], {}, topics, { fetch(url, options, reply) {
    if (url === '/api/conversations/second' && !options.method && fail) return reply({ detail: 'Synthetic load failure' }, 503);
  } });
  await app.node('topic-list').children[1].events.click();
  assert.equal(app.node('topic-title').textContent, 'First topic');
  assert.equal(app.node('research-question').disabled, false);
  assert.equal(app.node('retry-topic').hidden, false);
  fail = false;
  await app.node('retry-topic').events.click();
  assert.equal(app.node('topic-title').textContent, 'Second topic');
  assert.equal(app.node('retry-topic').hidden, true);
});

test('stale topic failures do not overwrite the newer active topic', async () => {
  const topics = new Map([
    ['first', { id: 'first', title: 'First topic', state: {}, messages: [], runs: [], busy: false }],
    ['second', { id: 'second', title: 'Second topic', state: {}, messages: [], runs: [], busy: false }],
  ]);
  let fail;
  const delayed = new Promise(resolve => { fail = resolve; });
  const app = await application([], {}, topics, { fetch(url, options) {
    if (url === '/api/conversations/second' && !options.method) return delayed;
  } });
  const loading = app.node('topic-list').children[1].events.click();
  await app.node('topic-list').children[0].events.click();
  fail({ ok: false, status: 503, json: async () => ({ detail: 'Stale failure' }) });
  await loading;
  assert.equal(app.node('topic-title').textContent, 'First topic');
  assert.equal(app.node('workspace-notice').textContent, '');
  assert.equal(app.node('research-question').disabled, false);
});

test('rename and deletion failures remain inside their open dialogs', async () => {
  const app = await application([], {}, new Map(), { fetch(url, options, reply) {
    if (options.method === 'PATCH' && JSON.parse(options.body).title) return reply({ detail: 'Synthetic rename failure' }, 503);
    if (options.method === 'DELETE') return reply({ detail: 'Synthetic delete failure' }, 503);
  } });
  app.node('rename-topic').events.click();
  app.node('rename-value').value = 'New title';
  await app.node('rename-form').events.submit({ preventDefault() {} });
  assert.equal(app.node('rename-dialog').open, true);
  assert.equal(app.node('rename-error').textContent, 'Synthetic rename failure');
  assert.equal(app.node('save-rename').disabled, false);
  app.node('cancel-rename').events.click();
  app.node('delete-topic').events.click();
  await app.node('confirm-delete').events.click();
  assert.equal(app.node('delete-dialog').open, true);
  assert.equal(app.node('delete-error').textContent, 'Synthetic delete failure');
  assert.equal(app.node('confirm-delete').disabled, false);
});

test('stable transcript and timeline nodes retain disclosure and keyboard focus', async () => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const cited = { ...answer, sources: [{ ...work(1), excerpt: 'Known abstract.', fetched_at: '2026-03-02' }] };
  const app = await application([results, cited, { ...results, gate }]);
  await app.submit('Find robotics papers');
  await app.submit('Explain these papers');
  const evidence = app.descendants(app.node('conversation')).find(node => node.className === 'evidence-source');
  evidence.open = true;
  const summary = evidence.firstElementChild;
  summary.focus();
  await app.submit('Find further robotics papers');
  assert.ok(app.descendants(app.node('conversation')).includes(evidence));
  assert.equal(evidence.open, true);
  assert.equal(app.document.activeElement, summary);
  const stage = app.node('activity-list').firstElementChild;
  stage.open = true;
  finish();
  await app.flush();
  assert.equal(app.node('activity-list').firstElementChild, stage);
  assert.equal(stage.open, true);
  assert.ok(app.descendants(app.node('conversation')).includes(evidence));
});

test('busy state disables inline paper actions as well as toolbar buttons', async () => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  const app = await application([results, { ...results, gate }]);
  await app.submit('Find robotics papers');
  const detailsButton = app.descendants(app.node('conversation')).find(node => node.textContent === 'Details');
  await app.submit('Find more robotics papers');
  assert.equal(detailsButton.disabled, true);
  finish();
  await app.flush();
  assert.equal(detailsButton.disabled, false);
});

test('earlier messages load by cursor without replacing existing evidence', async () => {
  const saved = { id: 'saved', title: 'Saved topic', state: {}, busy: false, messages_before: 50,
    messages: [{ id: 50, role: 'user', text: 'Latest question', request_id: 'recent' },
      { id: 51, role: 'assistant', result: answer, request_id: 'recent' }], runs: [] };
  const topics = new Map([['saved', saved]]);
  const app = await application([], {}, topics, { fetch(url, options, reply) {
    if (url.includes('/messages?before_id=50')) return reply({ messages: [
      { id: 1, role: 'user', text: 'Earlier question', request_id: 'old' },
      { id: 2, role: 'assistant', result: results, request_id: 'old' },
    ], next_before: null });
  } });
  const last = app.node('conversation').lastElementChild;
  assert.equal(app.node('older-messages').hidden, false);
  await app.node('older-messages').events.click();
  assert.equal(app.node('conversation').lastElementChild, last);
  assert.equal(app.node('older-messages').hidden, true);
  assert.ok(app.descendants(app.node('conversation')).some(node => node.textContent === 'Earlier question'));
});

test('historical activity loads its bounded event pages on demand', async () => {
  let calls = 0;
  const saved = { id: 'saved', title: 'Saved topic', state: {}, busy: false,
    messages: [{ id: 1, role: 'assistant', result: answer, request_id: 'old' }], runs: [] };
  const app = await application([], {}, new Map([['saved', saved]]), { fetch(url, options, reply) {
    if (url.includes('/runs/old/events?')) {
      calls++;
      return reply({ events: [{ id: calls, stage: calls === 1 ? 'planning' : 'cache', status: 'completed', details: {}, duration_ms: 2, at: '2026-03-02' }],
        next_after: calls, has_more: calls === 1, status: 'completed' });
    }
  } });
  app.descendants(app.node('conversation')).find(node => node.textContent === 'View activity').events.click();
  await app.flush();
  assert.equal(calls, 2);
  assert.equal(app.node('activity-list').children.length, 2);
  assert.equal(app.node('activity-status').textContent, 'Request completed');
});

test('citation scrolling honors reduced motion and moves focus to the source', async () => {
  const cited = { ...answer, sources: [{ ...work(1), excerpt: 'Known abstract.', fetched_at: '2026-03-02' }],
    claims: [{ text: 'Synthetic claim', citations: [{ work_id: work(1).id, quote: 'Known abstract.' }] }] };
  const app = await application([cited], {}, new Map(), { globals: { matchMedia: query => ({ matches: query.includes('reduced-motion') }) } });
  await app.submit('Explain available research');
  const nodes = app.descendants(app.node('conversation'));
  nodes.find(node => node.className === 'citation-button').events.click();
  const source = nodes.find(node => node.className === 'evidence-source');
  assert.equal(source.scrollOptions.behavior, 'instant');
  assert.equal(app.document.activeElement, source.firstElementChild);
});

test('unknown citation counts remain visibly unknown', async () => {
  const app = await application([{ ...results, works: [{ ...work(1), cited_by_count: null }] }]);
  await app.submit('Find research papers');
  assert.ok(app.descendants(app.node('conversation')).some(node => /Citation count unknown/.test(node.textContent || '')));
});

test('daily allowance is fetched on expansion and refreshed after completed requests', async () => {
  let calls = 0;
  const app = await application([results], {}, new Map(), { fetch(url, options, reply) {
    if (url === '/api/usage') { calls++; return reply({ day: '2026-03-02', used: { ai_turns: calls - 1, gemini: 0, openalex: 0 }, limits: { ai_turns: 3, gemini: 6, openalex: 20 } }); }
  } });
  app.node('usage-panel').open = true;
  app.node('usage-panel').events.toggle();
  await app.flush();
  assert.match(app.node('usage-status').textContent, /2026-03-02 \(UTC\)/);
  assert.equal(app.node('usage-values').children[0].textContent, 'AI turns: 0 / 3');
  await app.submit('Find research papers');
  await app.flush();
  assert.equal(calls, 2);
  assert.equal(app.node('usage-values').children[0].textContent, 'AI turns: 1 / 3');
});

test('Groq plans and usage are identified without being labeled Gemini', async () => {
  const app = await application([{ ...results, parser_mode: 'groq' }], {}, new Map(), { fetch(url, options, reply) {
    if (url === '/api/usage') return reply({ day: '2026-03-02',
      used: { ai_turns: 1, gemini: 0, groq: 1, openalex: 0 },
      limits: { ai_turns: 3, gemini: 6, groq: 6, openalex: 20 } });
  } });
  await app.submit('Find research papers');
  assert.ok(app.descendants(app.node('conversation')).some(node => node.textContent === 'Planned with Groq'));
  app.node('usage-panel').open = true;
  app.node('usage-panel').events.toggle();
  await app.flush();
  assert.ok(app.node('usage-values').children.some(node => node.textContent === 'Groq API attempts: 1 / 6'));
});

test('daily usage fetch failure is scoped and does not disable research', async () => {
  const app = await application([results], {}, new Map(), { fetch(url, options, reply) {
    if (url === '/api/usage') return reply({ detail: 'Synthetic usage failure' }, 503);
  } });
  app.node('usage-panel').open = true;
  app.node('usage-panel').events.toggle();
  await app.flush();
  assert.match(app.node('usage-status').textContent, /temporarily unavailable/);
  assert.equal(app.node('research-question').disabled, false);
  await app.submit('Find research papers');
  assert.equal(app.requests.length, 1);
});

test('saved failed assistant messages use their safe error exactly once', async () => {
  const topics = new Map([['saved', { id: 'saved', title: 'Saved topic', state: {}, busy: false,
    messages: [{ id: 1, role: 'user', text: 'Failed question', request_id: 'old' },
      { id: 2, role: 'assistant', text: 'Safe failure', result: null, error: 'Safe failure', request_id: 'old' }],
    runs: [{ id: 'old', status: 'failed', error: 'Safe failure', events: [] }] }]]);
  const app = await application([], {}, topics);
  assert.equal(app.descendants(app.node('conversation')).filter(node => node.textContent === 'Safe failure').length, 1);
});

test('a dropped final result replays its completed request without executing a new one', async () => {
  const app = await application([request => ({ frames: [['activity', { id: 1, request_id: request.request_id, stage: 'request', status: 'completed', details: {}, at: '2026-03-02', duration_ms: 2 }]] }), results]);
  await app.submit('Find research papers');
  await app.submit('Find research papers');
  assert.equal(app.requests[1].request_id, app.requests[0].request_id);
  assert.equal(app.descendants(app.node('conversation')).filter(node => node.className === 'user-bubble').length, 1);
});

test('stream parsing tolerates CRLF boundaries split across single-byte chunks', async () => {
  const app = await application([{ ...results, crlf: true, chunkSize: 1 }]);
  await app.submit('Find research papers');
  assert.ok(app.descendants(app.node('conversation')).some(node => node.textContent === results.answer));
  assert.equal(app.node('search-button').disabled, false);
});
