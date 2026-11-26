(() => {
  'use strict';
  const $ = id => document.querySelector(`#${id}`);
  const threads = new Map();
  let activeId;
  let creating = false;
  let activation = 0;
  let loadingTopic = false;
  let failedTopic = null;
  let moreTopics = false;
  let loadingTopics = false;
  let topicsCursor = null;
  let usageSequence = 0;
  const current = () => threads.get(activeId);
  const defaultFilters = () => ({ date_range: 'auto', date_from: null, date_to: null, sort: null });
  // Browser storage is an optional convenience; the database owns conversation memory.
  function storedTopic(value) {
    try {
      if (value !== undefined) globalThis.localStorage?.setItem('research-active-topic', value);
      else return globalThis.localStorage?.getItem('research-active-topic');
    } catch { /* Private browsing and browser policies may deny storage. */ }
    return null;
  }
  function announce(text) { $('chat-announcement').textContent = text; }
  function scrollTo(node, options = {}) {
    const reduced = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    node?.scrollIntoView({ behavior: reduced ? 'instant' : 'smooth', ...options });
  }
  function nearBottom() {
    return !document.documentElement || document.documentElement.scrollHeight - globalThis.innerHeight - globalThis.scrollY < 300;
  }
  async function loadUsage() {
    const sequence = ++usageSequence;
    try {
      const usage = await api('/api/usage');
      if (sequence !== usageSequence) return;
      if (!usage?.day || !usage.used || !usage.limits) throw new Error('Usage unavailable');
      const labels = { ai_turns: 'AI turns', gemini: 'Gemini API attempts', groq: 'Groq API attempts', openalex: 'OpenAlex API attempts' };
      const rows = Object.entries(labels).filter(([key]) => key !== 'groq' || usage.limits.groq !== undefined).map(([key, label]) => {
        const used = usage.used[key], limit = usage.limits[key];
        if (!Number.isFinite(used) || used < 0 || !(limit === null || Number.isFinite(limit))) throw new Error('Invalid usage');
        return element('li', '', `${label}: ${used} / ${limit === null ? 'no cap' : limit}`);
      });
      $('usage-values').replaceChildren(...rows);
      $('usage-status').textContent = `Daily allowance · ${usage.day} (UTC)`;
    } catch {
      if (sequence === usageSequence) $('usage-status').textContent = 'Usage is temporarily unavailable. Research controls remain available.';
    }
  }
  // Move only changed children. Existing disclosure and focus state stay in the DOM.
  function reconcile(container, children) {
    for (let index = 0; index < children.length; index++) {
      if (container.children[index] !== children[index]) container.insertBefore(children[index], container.children[index] || null);
    }
    while (container.children.length > children.length) container.lastElementChild.remove();
  }

  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json' },
      signal: options.signal || AbortSignal.timeout(15000) });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The conversation could not be saved.');
    return data;
  }
  function restoredMessages(rows, runs) {
    const errors = new Map(runs.filter(run => run.error).map(run => [run.id, run.error]));
    const replies = new Set(rows.filter(row => row.role === 'assistant').map(row => row.request_id));
    return rows.flatMap(row => {
      const message = { ...row, text: row.text || '',
        error: row.error || (row.role === 'assistant' && !row.result ? 'Response unavailable.' : null) };
      return row.role === 'user' && errors.has(row.request_id) && !replies.has(row.request_id)
        ? [message, { role: 'assistant', error: errors.get(row.request_id), request_id: row.request_id }]
        : [message];
    });
  }
  function hydrate(saved) {
    const state = saved.state || {};
    const thread = { ...saved, loaded: true, query: state.query || null,
      results: state.results || [], selected: new Set(state.selected_work_ids || []),
      scope: null, pending: state.pending || null, filters: state.filters || defaultFilters(),
      comparison: false, runs: (saved.runs || []).map(run => ({ ...run, activityLoaded: Boolean(run.events?.length) })), busy: saved.busy, draft: saved.draft || '',
      messagesBefore: saved.messages_before || null, messageViews: new Map() };
    thread.messages = restoredMessages(saved.messages, thread.runs);
    if (thread.selected.size === 1) {
      const selected = [...thread.selected][0];
      thread.scope = [...thread.results, ...thread.messages.flatMap(row => [...(row.result?.works || []), ...(row.result?.sources || []), ...(row.result?.work ? [row.result.work] : [])])]
        .find(work => work.id === selected) || null;
    }
    thread.comparison = thread.selected.size >= 2;
    // Share the latest query object so comparison controls identify its result cards.
    const latest = [...thread.messages].reverse().find(row => row.result?.status === 'results');
    if (latest) thread.query = latest.result.query;
    thread.timelineId = thread.runs.at(-1)?.id;
    return thread;
  }
  async function saveDraft(thread) {
    clearTimeout(thread.saveTimer);
    if (!thread.loaded) return;
    const patch = { draft: thread.draft };
    if (!thread.busy) Object.assign(patch, { filters: thread.filters,
      selected_work_ids: thread.scope ? [thread.scope.id] : [...thread.selected] });
    // Serialize writes per topic so an older draft cannot overwrite a newer one.
    thread.savePromise = (thread.savePromise || Promise.resolve()).catch(() => {}).then(() =>
      api(`/api/conversations/${thread.id}`, { method: 'PATCH', body: JSON.stringify(patch) }));
    try { await thread.savePromise; }
    catch (error) { if (activeId === thread.id) $('workspace-notice').textContent = error.message; throw error; }
  }
  function scheduleSave() {
    const thread = current();
    clearTimeout(thread.saveTimer);
    thread.saveTimer = setTimeout(() => saveDraft(thread).catch(() => {}), 400);
  }

  // Provider and model text are untrusted: create text nodes, never HTML strings.
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function button(text, handler, className = 'quiet-button') {
    const node = element('button', className, text);
    node.type = 'button';
    node.addEventListener('click', handler);
    return node;
  }
  function safeLink(value) {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }
  function link(parent, label, value) {
    const href = safeLink(value);
    if (!href) return;
    const node = element('a', '', label);
    node.href = href;
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
    parent.append(node);
  }
  function warnings(parent, messages = []) {
    if (!messages.length) return;
    const box = element('div', 'warning-list');
    messages.forEach(message => box.append(element('p', '', message)));
    parent.append(box);
  }
  async function activate(id) {
    const turn = ++activation;
    if (activeId && current()?.loaded && activeId !== id) {
      current().draft = $('research-question').value;
      saveDraft(current()).catch(() => {});
    }
    const candidate = threads.get(id);
    if (!candidate) return;
    if (!candidate.loaded) {
      loadingTopic = true;
      failedTopic = null;
      $('workspace-notice').textContent = 'Loading conversation…';
      $('retry-topic').hidden = true;
      if (current()?.loaded) syncControls();
      try {
        const saved = await api(`/api/conversations/${id}`);
        if (turn !== activation) return;
        threads.set(id, hydrate(saved));
      } catch (error) {
        if (turn !== activation) return;
        failedTopic = id;
        $('workspace-notice').textContent = `Could not load this conversation. ${error.message}`;
        $('retry-topic').hidden = false;
        return;
      } finally {
        if (turn === activation) { loadingTopic = false; if (current()?.loaded) syncControls(); }
      }
    }
    if (turn !== activation) return;
    activeId = id;
    loadingTopic = false;
    failedTopic = null;
    $('retry-topic').hidden = true;
    $('workspace-notice').textContent = '';
    storedTopic(id);
    if ($('topic-dialog').open) $('topic-dialog').close();
    $('filter-panel').open = false;
    render();
    if (current().busy && !current().streaming) pollTopic(current());
    $('research-question').focus();
  }
  function pollTopic(thread) {
    if (thread.polling || thread.streaming) return;
    thread.polling = true;
    setTimeout(async () => {
      try {
        const saved = hydrate(await api(`/api/conversations/${thread.id}`));
        if (thread.loadedOlder) {
          const recent = new Set(saved.messages.map(row => `${row.role}:${row.request_id || row.id}`));
          saved.messages = [...thread.messages.filter(row => !recent.has(`${row.role}:${row.request_id || row.id}`)), ...saved.messages];
          saved.messagesBefore = thread.messagesBefore;
        }
        const previousRuns = new Map(thread.runs.map(run => [run.id, run]));
        saved.runs = saved.runs.map(run => {
          const previous = previousRuns.get(run.id);
          if (!previous) return run;
          const events = run.events?.length ? run.events : previous.events;
          return Object.assign(previous, run, { events, activityLoaded: Boolean(events?.length) || previous.activityLoaded });
        });
        for (const run of previousRuns.values()) if (!saved.runs.some(item => item.id === run.id)) saved.runs.push(run);
        // Keep the owner object used by stable message controls and streamed callbacks.
        Object.assign(thread, saved, { messageViews: thread.messageViews, timelineId: thread.timelineId || saved.timelineId, polling: false });
        if (activeId === thread.id) render();
        if (thread.busy) pollTopic(thread);
      } catch (error) {
        thread.polling = false;
        if (activeId === thread.id) $('workspace-notice').textContent = `Could not refresh saved activity. ${error.message}`;
      }
    }, 1000);
  }
  async function newTopic() {
    if (creating) return;
    const unused = activeId && current().loaded && !current().messages.length && !current().draft.trim()
      && !$('research-question').value.trim() && current().filters.date_range === 'auto' && !current().filters.sort;
    if (unused) return activate(activeId);
    creating = true;
    try {
      const saved = await api('/api/conversations', { method: 'POST' });
      threads.set(saved.id, hydrate(saved));
      await activate(saved.id);
    } catch (error) { $('workspace-notice').textContent = error.message; }
    finally { creating = false; }
  }
  function renderTopics() {
    for (const container of [$('topic-list'), $('mobile-topic-list')]) {
      container.topicViews ||= new Map();
      const items = [];
      for (const thread of threads.values()) {
        let item = container.topicViews.get(thread.id);
        if (!item) { item = button('', () => activate(thread.id), 'topic-button'); container.topicViews.set(thread.id, item); }
        item.textContent = thread.title + (thread.busy ? ' · working' : '');
        item.setAttribute('aria-current', thread.id === activeId ? 'true' : 'false');
        items.push(item);
      }
      reconcile(container, items);
    }
    for (const id of ['load-more-topics', 'mobile-load-more-topics']) { $(id).hidden = !moreTopics; $(id).disabled = loadingTopics; }
  }
  function syncControls() {
    const thread = current();
    $('research-question').value = thread.draft;
    const blocked = Boolean(thread.busy || loadingTopic || thread.sending);
    $('research-question').disabled = blocked;
    $('search-button').disabled = blocked;
    $('search-button-text').textContent = thread.busy ? 'Thinking…' : thread.pending ? 'Send answer' : 'Send';
    $('research-question').placeholder = thread.pending ? 'Answer the clarification question…'
      : thread.query ? 'Ask a follow-up about this topic or refine the search…' : 'What would you like to research?';
    $('research-question').setAttribute('aria-label', thread.pending ? 'Answer the clarification question' : 'Ask about this research topic');
    $('topic-title').textContent = thread.title;
    $('rename-topic').disabled = blocked;
    $('delete-topic').disabled = blocked;
    $('date-range').value = thread.filters.date_range;
    $('date-from').value = thread.filters.date_from || '';
    $('date-to').value = thread.filters.date_to || '';
    $('sort-mode').value = thread.filters.sort || 'auto';
    $('custom-dates').hidden = thread.filters.date_range !== 'custom';
    for (const id of ['date-range', 'date-from', 'date-to', 'sort-mode', 'apply-filters', 'latest-button']) $(id).disabled = blocked;
    $('topic-actions').hidden = !thread.results.length || Boolean(thread.pending);
    $('summarize-button').disabled = blocked;
    $('refresh-button').disabled = blocked;
    $('compare-button').disabled = blocked;
    $('compare-selected-button').hidden = !thread.comparison;
    $('compare-selected-button').disabled = blocked || thread.selected.size < 2;
    $('compare-selected-button').textContent = `Compare selected (${thread.selected.size}/5)`;
    $('compare-button').textContent = thread.comparison ? 'Cancel comparison' : 'Compare papers';
    $('paper-scope').hidden = !thread.scope;
    $('scope-title').textContent = thread.scope ? `Asking about: ${thread.scope.title || 'Selected paper'}` : '';
    $('clear-scope').disabled = blocked;
    $('older-messages').hidden = !thread.messagesBefore;
    $('older-messages').disabled = Boolean(thread.loadingOlder);
    for (const view of thread.messageViews.values()) for (const control of view.actions || []) control.disabled = blocked || Boolean(thread.pending);
    const chips = $('active-filters');
    chips.replaceChildren();
    if (thread.query) {
      const q = thread.query;
      const lower = q.date_from || (q.year_from ? `${q.year_from}-01-01` : 'Any start');
      const upper = q.date_to || (q.year_to ? `${q.year_to}-12-31` : 'Any end');
      chips.append(element('span', 'filter-chip', `${lower} → ${upper}`),
        element('span', 'filter-chip', { relevance: 'Relevance', newest: 'Newest first', most_cited: 'Most cited' }[q.sort]));
    }
  }
  function paperCard(work, index, thread, isCurrentResult) {
    const card = element('article', 'paper');
    const top = element('div', 'paper-top');
    const heading = element('h3', 'paper-title', work.title || 'Untitled publication');
    top.append(heading, element('span', 'paper-index', String(index + 1).padStart(2, '0')));
    card.append(top);
    const metadata = [work.publication_date || work.publication_year, work.source];
    metadata.push(Number.isFinite(work.cited_by_count) ? `${work.cited_by_count.toLocaleString()} citations` : 'Citation count unknown');
    card.append(element('p', 'paper-metadata', metadata.filter(value => value !== null && value !== undefined && value !== '').join(' · ')));
    if (work.authors?.length) card.append(element('p', 'paper-authors', work.authors.slice(0, 5).join(', ') + (work.authors.length > 5 ? ' …' : '')));
    const actions = element('div', 'paper-links');
    const detailsButton = button('Details', () => send(`Show details for ${work.id}`, { action: 'details', paperIds: [work.id], preserveDraft: true }));
    detailsButton.setAttribute('aria-label', `Details: ${work.title || 'Untitled publication'}`);
    detailsButton.researchAction = true;
    actions.append(detailsButton);
    link(actions, 'Publication ↗', work.landing_page_url || (work.doi ? `https://doi.org/${work.doi}` : work.id));
    if (!work.has_abstract) actions.append(element('span', 'muted', 'Abstract unavailable'));
    if (thread.comparison && isCurrentResult) {
      const label = element('label', 'paper-selection');
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', `Compare: ${work.title || 'Untitled publication'}`);
      checkbox.researchAction = true;
      checkbox.checked = thread.selected.has(work.id);
      checkbox.disabled = thread.busy || Boolean(thread.pending);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && thread.selected.size >= 5) {
          checkbox.checked = false;
          $('workspace-notice').textContent = 'Select up to five papers for comparison.';
          return;
        }
        if (checkbox.checked) thread.selected.add(work.id); else thread.selected.delete(work.id);
        scheduleSave();
        syncControls();
      });
      label.append(checkbox, element('span', '', 'Compare'));
      actions.append(label);
    }
    card.append(actions);
    return card;
  }
  function renderAnswer(body, result) {
    body.append(element('p', 'answer-body', result.answer));
    const sources = new Map();
    (result.sources || []).forEach((source, index) => {
      const detail = element('details', 'evidence-source');
      detail.append(element('summary', '', `[${index + 1}] ${source.title || 'Untitled publication'}`),
        element('blockquote', '', source.excerpt),
        element('p', 'muted', `Retrieved ${source.fetched_at}${source.truncated ? ' · Abstract excerpt truncated' : ''}`));
      link(detail, 'Original source ↗', source.landing_page_url || source.id);
      sources.set(source.id, { detail, number: index + 1 });
    });
    for (const claim of result.claims || []) {
      const paragraph = element('p', 'answer-body', claim.text);
      for (const citation of claim.citations || []) {
        const source = sources.get(citation.work_id);
        if (!source) continue;
        const cite = button(`[${source.number}]`, () => {
          source.detail.open = true;
          scrollTo(source.detail, { block: 'center' });
          source.detail.firstElementChild?.focus();
        }, 'citation-button');
        cite.title = citation.quote;
        cite.setAttribute('aria-label', `Supporting abstract ${source.number}: ${citation.quote}`);
        paragraph.append(cite);
      }
      body.append(paragraph);
    }
    if (sources.size) body.append(element('h3', '', 'Abstract evidence'));
    sources.forEach(source => body.append(source.detail));
    warnings(body, result.warnings);
  }
  function renderResult(body, result, thread) {
    if (result.status === 'details') {
      const work = result.work;
      body.append(element('h3', '', work.title || 'Untitled publication'),
        element('p', 'paper-metadata', [work.publication_date || work.publication_year, work.source].filter(Boolean).join(' · ')),
        element('p', '', work.authors.join(', ')), element('p', 'answer-body', work.abstract || 'No abstract is available.'),
        element('p', 'muted', `Metadata retrieved ${work.last_fetched_at}`));
      link(body, 'Original publication', work.landing_page_url || work.id);
      const ask = button('Ask about this paper', () => {
        if (thread.busy || loadingTopic) return;
        thread.scope = work; thread.selected.clear(); scheduleSave(); syncControls(); $('research-question').focus();
      });
      ask.researchAction = true;
      body.append(ask);
      return;
    }
    if (result.status === 'answer') { renderAnswer(body, result); return; }
    body.append(element('p', 'answer-body', result.status === 'clarification' ? result.question : result.answer));
    warnings(body, result.warnings);
    if (result.status === 'clarification') return;
    if (result.works?.length) {
      const list = element('div', 'papers');
      const isCurrentResult = result.query === thread.query;
      result.works.forEach((work, index) => list.append(paperCard(work, index, thread, isCurrentResult)));
      body.append(list);
    }
    const labels = { local_cache: 'Saved preview', openalex_fresh: 'Retrieved from OpenAlex', local_fallback: 'Local subset · upstream unavailable' };
    body.append(element('p', 'muted', `${labels[result.retrieval_source] || ''}${result.fetched_at ? ` · Retrieved ${result.fetched_at}` : ''}`));
    const details = element('details', 'query-details');
    details.append(element('summary', '', 'Search details'));
    if (result.priorities?.length) details.append(element('p', '', result.priorities.join(' · ')));
    if (result.search_rationale) details.append(element('p', '', result.search_rationale));
    details.append(element('p', '', result.parser_mode === 'gemini' ? 'Planned with Gemini'
      : result.parser_mode === 'groq' ? 'Planned with Groq'
      : result.parser_mode === 'reused' ? 'Reused topic search parameters' : 'Basic rule parser'),
      element('pre', '', JSON.stringify(result.query, null, 2)));
    body.append(details);
  }
  function render() {
    const thread = current();
    if (!thread?.loaded) return;
    $('app-shell').classList.toggle('has-chat', thread.messages.length > 0);
    thread.messageViews ||= new Map();
    const children = [];
    const kept = new Set();
    function actionsWithin(node) { return [...node.children].flatMap(child => [ ...(child.researchAction ? [child] : []), ...actionsWithin(child)]); }
    for (const [index, message] of thread.messages.entries()) {
      const key = `${message.role}:${message.request_id || message.id || index}`;
      const signature = JSON.stringify([message.text, message.error, message.result,
        message.result?.status === 'results' && message.result.query === thread.query ? thread.comparison : false]);
      let view = thread.messageViews.get(key);
      kept.add(key);
      if (view?.signature === signature) { children.push(view.node); continue; }
      let node;
      if (message.role === 'user') {
        const row = element('div', 'message-user');
        row.append(element('div', 'user-bubble', message.text));
        node = row;
      } else {
        const block = element('article', 'assistant-block');
        block.append(element('h2', 'assistant-header', 'Research Navigator'));
        const body = element('div', 'assistant-content');
        if (message.error) body.append(element('div', 'error-box', message.error));
        else renderResult(body, message.result, thread);
        block.append(body);
        if (message.request_id) block.append(button('View activity', () => {
          if (!thread.runs.some(run => run.id === message.request_id)) thread.runs.push({ id: message.request_id, status: 'saved', events: [] });
          thread.timelineId = message.request_id; renderTimeline(); loadActivity(thread, message.request_id);
          if (globalThis.matchMedia?.('(max-width: 1150px)').matches) $('activity-drawer').showModal();
        }));
        node = block;
      }
      view = { node, signature, actions: actionsWithin(node) };
      thread.messageViews.set(key, view);
      children.push(node);
    }
    for (const key of thread.messageViews.keys()) if (!kept.has(key)) thread.messageViews.delete(key);
    if (thread.busy) {
      thread.loadingNode ||= element('div', 'loading-line', 'Processing your request. Activity shows the current stage.');
      children.push(thread.loadingNode);
    }
    reconcile($('conversation'), children);
    renderTopics();
    syncControls();
    renderTimeline();
  }

  function renderTimeline() {
    const thread = current();
    if (!thread?.loaded) return;
    const run = thread.runs?.find(item => item.id === thread.timelineId) || thread.runs?.at(-1);
    const labels = { request: 'Request', memory: 'Load conversation memory', dispatch: 'Choose research tool',
      planning: 'Understand question', clarification: 'Ask a follow-up', cache: 'Check saved research',
      source_search: 'Search OpenAlex', record_validation: 'Validate and save records',
      local_fallback: 'Search local subset', evidence: 'Load abstract evidence', explanation: 'Generate explanation',
      citation_checks: 'Check citations', answer_fallback: 'Use stored evidence', paper_details: 'Open paper details' };
    for (const container of [$('activity-list'), $('mobile-activity-list')]) {
      container.stageViews ||= new Map();
      const children = [];
      if (!run) {
        container.emptyNote ||= element('p', 'muted', 'Send a message to see the tools and sources used.');
        reconcile(container, [container.emptyNote]); continue;
      }
      // One row per stage; completion replaces its running state using real backend events.
      const stages = new Map();
      (run.events || []).forEach(event => stages.set(event.stage, event));
      stages.forEach(event => {
        const key = `${run.id}:${event.stage}`;
        let view = container.stageViews.get(key);
        if (!view) {
          view = { row: element('details'), heading: element('summary'), time: element('p', 'muted'), details: element('pre') };
          view.row.append(view.heading, view.time, view.details);
          container.stageViews.set(key, view);
        }
        view.row.className = `activity-step activity-${event.status}`;
        view.heading.textContent = `${labels[event.stage] || event.stage} · ${event.status}`;
        view.time.textContent = `${event.at} · ${event.duration_ms} ms`;
        view.details.hidden = !Object.keys(event.details || {}).length;
        view.details.textContent = JSON.stringify(event.details || {}, null, 2);
        children.push(view.row);
      });
      if (run.activityError || !stages.size) children.push(element('p', 'muted', run.activityError || (run.loadingActivity ? 'Loading saved activity…' : 'No activity recorded.')));
      reconcile(container, children);
      // Release nodes for other requests; their durable data can be loaded again.
      for (const key of container.stageViews.keys()) if (!key.startsWith(`${run.id}:`)) container.stageViews.delete(key);
    }
    $('activity-status').textContent = run ? `Request ${run.status}` : 'Ready';
  }

  async function loadActivity(thread, runId) {
    const run = thread.runs.find(item => item.id === runId);
    if (!run || run.loadingActivity || run.activityLoaded) return;
    run.loadingActivity = true;
    run.activityError = null;
    if (thread.id === activeId) renderTimeline();
    try {
      let after = 0, more = true;
      run.events ||= [];
      while (more) {
        const page = await api(`/api/conversations/${thread.id}/runs/${run.id}/events?after_id=${after}&limit=100`);
        for (const event of page.events) if (!run.events.some(item => item.id === event.id)) run.events.push(event);
        run.status = page.status;
        more = page.has_more;
        if (more && (page.next_after === null || page.next_after <= after)) throw new Error('Invalid activity cursor.');
        after = page.next_after;
      }
      run.activityLoaded = true;
    } catch { run.activityError = 'Saved activity could not be loaded. Select View activity to retry.'; }
    finally { run.loadingActivity = false; if (thread.id === activeId) renderTimeline(); }
  }

  async function olderMessages() {
    const thread = current();
    if (!thread?.messagesBefore || thread.loadingOlder) return;
    thread.loadingOlder = true;
    syncControls();
    const first = $('conversation').firstElementChild;
    const previousTop = first?.getBoundingClientRect?.().top;
    try {
      const page = await api(`/api/conversations/${thread.id}/messages?before_id=${encodeURIComponent(thread.messagesBefore)}&limit=30`);
      for (const run of page.runs || []) if (!thread.runs.some(item => item.id === run.id)) thread.runs.push(run);
      const seen = new Set(thread.messages.map(row => `${row.role}:${row.request_id || row.id}`));
      thread.messages = [...restoredMessages(page.messages, page.runs || []).filter(row => !seen.has(`${row.role}:${row.request_id || row.id}`)), ...thread.messages];
      thread.messagesBefore = page.next_before;
      thread.loadedOlder = true;
      if (thread.id === activeId) {
        render();
        const newTop = first?.getBoundingClientRect?.().top;
        if (Number.isFinite(previousTop) && Number.isFinite(newTop)) globalThis.scrollBy?.({ top: newTop - previousTop, behavior: 'instant' });
        announce('Earlier messages loaded.');
      }
    } catch (error) { if (thread.id === activeId) $('workspace-notice').textContent = `Could not load earlier messages. ${error.message}`; }
    finally { thread.loadingOlder = false; if (thread.id === activeId) syncControls(); }
  }

  async function olderTopics() {
    if (loadingTopics || !moreTopics) return;
    loadingTopics = true;
    renderTopics();
    try {
      const last = topicsCursor;
      const page = await api(`/api/conversations?before_updated_at=${encodeURIComponent(last.updated_at)}&before_id=${encodeURIComponent(last.id)}&limit=30`);
      for (const topic of page) if (!threads.has(topic.id)) threads.set(topic.id, { ...topic, loaded: false });
      moreTopics = page.length === 30;
      if (page.length) topicsCursor = page.at(-1);
    } catch (error) { $('workspace-notice').textContent = `Could not load earlier topics. ${error.message}`; }
    finally { loadingTopics = false; renderTopics(); }
  }

  async function readStream(response, thread, run) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '', result, terminalError;
    function frame(text) {
      let name = '', data = [];
      text.split('\n').forEach(line => {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      });
      if (!data.length) return;
      const value = JSON.parse(data.join('\n'));
      if (value.request_id !== run.id || (value.conversation_id && value.conversation_id !== thread.id)) throw new Error('Mismatched response identifiers. Reload this conversation.');
      if (name === 'activity') {
        if (!run.events.some(event => event.id === value.id)) run.events.push(value);
        if (value.stage === 'request') run.status = value.status;
        if (thread.id === activeId) renderTimeline();
      } else if (name === 'result') { result = value.result; run.status = 'completed'; }
      else if (name === 'error') { terminalError = value.message; run.status = 'failed'; }
    }
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n');
        if (buffer.length > 2000000) throw new Error('The activity stream exceeded its limit. Reload this conversation.');
        let end;
        while ((end = buffer.indexOf('\n\n')) >= 0) { frame(buffer.slice(0, end)); buffer = buffer.slice(end + 2); }
        if (done) break;
      }
      if (terminalError) throw new Error(terminalError);
      if (!result) throw new Error('Connection interrupted. Reload to recover saved activity, or retry this message.');
      return result;
    } finally { reader.releaseLock(); }
  }
  async function send(text, { action = 'auto', refresh = false, topicScope = false, preserveDraft = false, reuseQuery = false, paperIds = null } = {}) {
    const thread = current();
    text = text.trim();
    if (!thread?.loaded || loadingTopic || thread.busy || thread.sending || text.length < (thread.pending ? 1 : 3)) return;
    const followLatest = nearBottom();
    const selected = paperIds || (topicScope ? [] : thread.scope ? [thread.scope.id] : [...thread.selected]);
    const draft = $('research-question').value;
    const request = { request_id: globalThis.crypto.randomUUID(), message: text, filters: { ...thread.filters },
      action, refresh, reuse_query: reuseQuery,
      selected_work_ids: selected };
    const signature = JSON.stringify({ ...request, request_id: null });
    if (thread.retry?.signature === signature) request.request_id = thread.retry.id;
    thread.sending = true;
    syncControls();
    try { await saveDraft(thread); }
    catch { thread.sending = false; if (thread.id === activeId) syncControls(); return; }
    thread.sending = false;
    if (!thread.messages.length) thread.title = text.slice(0, 65);
    if (!thread.messages.some(row => row.role === 'user' && row.request_id === request.request_id)) thread.messages.push({ role: 'user', text, request_id: request.request_id });
    let run = thread.runs.find(item => item.id === request.request_id);
    if (!run) { run = { id: request.request_id, status: 'running', events: [] }; thread.runs.push(run); }
    thread.timelineId = run.id;
    thread.busy = true;
    thread.streaming = true;
    announce('Request started. Activity shows the tools and sources used.');
    thread.draft = preserveDraft ? draft : '';
    $('workspace-notice').textContent = '';
    render();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 75000);
    try {
      const response = await fetch(`/api/conversations/${thread.id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request), signal: controller.signal });
      if (!response.ok) {
        const failure = await response.json();
        throw new Error(typeof failure.detail === 'string' ? failure.detail : `Request failed (HTTP ${response.status}).`);
      }
      const result = await readStream(response, thread, run);
      thread.retry = null;
      thread.messages = thread.messages.filter(row => !(row.request_id === run.id && row.error));
      thread.messages.push({ role: 'assistant', result, request_id: run.id });
      if (thread.id === activeId) announce(result.status === 'clarification' ? `Follow-up question: ${result.question}` : 'Response ready in the conversation.');
      thread.pending = result.status === 'clarification' ? { question: result.question } : null;
      if (result.status === 'results') {
        thread.query = result.query;
        thread.results = result.works;
        thread.scope = null;
        thread.selected.clear();
        thread.comparison = false;
      }
    } catch (error) {
      // A terminal activity event can arrive just before a dropped final result frame.
      // Replaying that request retrieves its durable result instead of executing twice.
      thread.retry = ['running', 'completed'].includes(run.status) ? { id: request.request_id, signature } : null;
      if (run.status === 'running') run.status = 'interrupted';
      thread.draft = preserveDraft ? draft : text;
      thread.messages.push({ role: 'assistant', request_id: run.id, error: error.name === 'AbortError' ? 'The request took too long. Reload to recover saved activity.' : error.message });
      if (thread.id === activeId) announce('The request could not finish. Check the error in the conversation.');
    } finally {
      clearTimeout(timeout);
      loadUsage();
      thread.busy = false;
      thread.streaming = false;
      saveDraft(thread).catch(() => {});
      if (thread.id === activeId) {
        render();
        if (followLatest && nearBottom()) {
          $('research-question').focus();
          scrollTo($('conversation').lastElementChild, { block: 'start' });
        }
      } else renderTopics();
    }
  }
  function applyFilters() {
    const thread = current();
    const custom = $('date-range').value === 'custom';
    const from = custom ? $('date-from').value : null;
    const to = custom ? $('date-to').value : null;
    if (custom && (!from || !to || from > to)) {
      $('workspace-notice').textContent = 'Choose a start date and an end date in order.';
      return false;
    }
    thread.filters = { date_range: $('date-range').value, date_from: from, date_to: to,
      sort: $('sort-mode').value === 'auto' ? null : $('sort-mode').value };
    thread.draft = $('research-question').value;
    $('filter-panel').open = false;
    $('workspace-notice').textContent = thread.pending ? 'Filters selected. Answer the clarification question below.'
      : thread.query ? '' : 'Filters selected. Enter your research topic below.';
    if (thread.query && !thread.pending) send('Find publications for the current topic using these filters.', { action: 'search', topicScope: true, preserveDraft: true, reuseQuery: true });
    else syncControls();
    return true;
  }

  $('search-form').addEventListener('submit', event => { event.preventDefault(); send($('research-question').value); });
  $('usage-panel').addEventListener('toggle', () => { if ($('usage-panel').open) loadUsage(); });
  $('research-question').addEventListener('input', () => { current().draft = $('research-question').value; scheduleSave(); });
  $('research-question').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); send($('research-question').value);
    }
  });
  $('new-topic-button').addEventListener('click', newTopic);
  $('retry-topic').addEventListener('click', () => failedTopic && activate(failedTopic));
  $('older-messages').addEventListener('click', olderMessages);
  $('load-more-topics').addEventListener('click', olderTopics);
  $('mobile-load-more-topics').addEventListener('click', olderTopics);
  $('topics-button').addEventListener('click', () => $('topic-dialog').showModal());
  $('close-topics').addEventListener('click', () => $('topic-dialog').close());
  $('activity-button').addEventListener('click', () => { renderTimeline(); $('activity-drawer').showModal(); });
  $('close-activity').addEventListener('click', () => $('activity-drawer').close());
  $('rename-topic').addEventListener('click', () => {
    $('rename-value').value = current().title;
    $('rename-error').textContent = '';
    $('rename-dialog').showModal();
  });
  $('rename-form').addEventListener('submit', async event => {
    event.preventDefault();
    const thread = current(), title = $('rename-value').value.trim();
    if (!title || $('save-rename').disabled) return;
    $('save-rename').disabled = true;
    $('rename-value').disabled = true;
    $('rename-error').textContent = '';
    try { await api(`/api/conversations/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ title }) });
      thread.title = title; $('rename-dialog').close(); if (thread.id === activeId) render();
    } catch (error) { $('rename-error').textContent = error.message; }
    finally { $('save-rename').disabled = false; $('rename-value').disabled = false; }
  });
  $('cancel-rename').addEventListener('click', () => $('rename-dialog').close());
  $('delete-topic').addEventListener('click', () => { $('delete-error').textContent = ''; $('delete-dialog').showModal(); });
  $('cancel-delete').addEventListener('click', () => $('delete-dialog').close());
  $('confirm-delete').addEventListener('click', async () => {
    const thread = current();
    if ($('confirm-delete').disabled) return;
    $('confirm-delete').disabled = true;
    $('delete-error').textContent = '';
    try {
      await saveDraft(thread);
      await api(`/api/conversations/${thread.id}`, { method: 'DELETE' });
      threads.delete(thread.id); activeId = null; $('delete-dialog').close();
      if (threads.size) await activate(threads.keys().next().value); else await newTopic();
    } catch (error) { $('delete-error').textContent = error.message; }
    finally { $('confirm-delete').disabled = false; }
  });
  $('clear-scope').addEventListener('click', () => { current().scope = null; current().selected.clear(); scheduleSave(); syncControls(); });
  $('date-range').addEventListener('change', () => { $('custom-dates').hidden = $('date-range').value !== 'custom'; });
  $('apply-filters').addEventListener('click', applyFilters);
  $('latest-button').addEventListener('click', () => {
    $('date-range').value = 'last_30_days'; $('sort-mode').value = 'newest'; applyFilters();
  });
  $('summarize-button').addEventListener('click', () => send('Summarize the main topics and methods supported by these abstracts.', { action: 'answer', topicScope: true, preserveDraft: true }));
  $('refresh-button').addEventListener('click', () => send('Refresh the publications for the current topic.', { action: 'search', refresh: true, topicScope: true, preserveDraft: true, reuseQuery: true }));
  $('compare-button').addEventListener('click', () => {
    current().draft = $('research-question').value;
    current().comparison = !current().comparison;
    current().selected.clear(); current().scope = null; render();
    scheduleSave();
  });
  $('compare-selected-button').addEventListener('click', () => {
    if (current().selected.size >= 2) send('Compare the research approaches and findings actually described in these selected abstracts.', { action: 'answer', preserveDraft: true });
  });
  document.querySelectorAll('[data-prompt]').forEach(node => node.addEventListener('click', async () => {
    await newTopic(); send(node.getAttribute('data-prompt'), { action: 'search' });
  }));
  async function boot() {
    $('research-question').disabled = true; $('search-button').disabled = true;
    $('new-topic-button').disabled = true;
    for (const id of ['rename-topic', 'delete-topic', 'latest-button', 'apply-filters']) $(id).disabled = true;
    try {
      const session = await api('/api/session', { method: 'POST' });
      $('public-demo-notice').hidden = !session.public_demo;
      const saved = await api('/api/conversations');
      saved.forEach(topic => threads.set(topic.id, { ...topic, loaded: false }));
      moreTopics = saved.length === 30;
      topicsCursor = saved.at(-1) || null;
      const previous = storedTopic();
      if (saved.length) await activate(threads.has(previous) ? previous : saved[0].id);
      else await newTopic();
    } catch (error) {
      $('workspace-notice').textContent = `Could not load conversations. ${error.message} Reload to retry. `;
      if (globalThis.location?.origin) {
        const direct = document.createElement('a'); direct.href = globalThis.location.origin;
        direct.target = '_blank'; direct.rel = 'noopener'; direct.textContent = 'Open application';
        $('workspace-notice').append(direct);
      }
    }
    finally { $('new-topic-button').disabled = false; }
  }
  boot();
  fetch('/api/health', { cache: 'no-store', signal: AbortSignal.timeout(6000) }).then(response => {
    if (!response.ok) throw new Error('offline');
    $('connection-status').classList.add('online'); $('connection-text').textContent = 'Service online';
  }).catch(() => { $('connection-status').classList.add('offline'); $('connection-text').textContent = 'Service offline'; });
})();
