(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const pretty = (value) => escape(JSON.stringify(value ?? {}, null, 2));
  const human = (value) => String(value || 'Ready').replace(/[_-]+/g, ' ');
  const scenarioNotes = {'normal-confirmation':'Fresh, exact consent','missing-confirmation':'No consent, no mutation','changed-payload':'New payload, new consent','stale-consent':'Old “yes” cannot carry over','airline-transfer':'Same boundary, new domain','rejection':'A refusal closes the boundary','ambiguous-response':'Unclear consent keeps the gate closed','uncertain-outcome':'Unknown outcome, stop safely'};
  let scenarios = [];
  let snapshot = null;
  let busy = false;
  let sessionId = null;
  let lastError = false;
  let serial = 0;

  function requestId() {
    return globalThis.crypto?.randomUUID?.() || `demo-${Date.now()}-${++serial}-${Math.random().toString(36).slice(2)}`;
  }

  async function api(path, body) {
    const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    let response;
    try { response = await fetch(path, {...options, cache:'no-store', signal:controller.signal}); }
    catch (error) { throw new Error(error.name === 'AbortError' ? 'The local request timed out; its outcome is unknown.' : 'The local server could not be reached.'); }
    finally { clearTimeout(timeout); }
    let data;
    try { data = await response.json(); } catch { throw new Error(`The local server returned an unreadable response (HTTP ${response.status}).`); }
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : (data.error?.message || data.message || `Request failed (HTTP ${response.status}).`));
    return data;
  }

  function showError(error) {
    lastError = true;
    $('error').textContent = `${error.message || error} The action was not automatically retried. Reload to reconcile the session, or reset to start again.`;
    $('error').hidden = false;
    $('connection-status').textContent = 'Local request failed';
  }

  function updateControls() {
    $('workspace').setAttribute('aria-busy', String(busy));
    document.querySelectorAll('.scenario').forEach((button) => { button.disabled = busy; });
    $('step-button').disabled = busy || !snapshot || snapshot.complete || lastError;
    $('run-button').disabled = busy || !snapshot || snapshot.complete || lastError;
    $('reset-button').disabled = busy || !sessionId;
    const canRespond = !busy && !lastError && snapshot?.complete && Boolean(snapshot?.lanes?.gated?.pending) && snapshot?.lanes?.gated?.status !== 'rejected';
    document.querySelectorAll('[data-response], #customer-response, #send-response').forEach((element) => { element.disabled = !canRespond; });
    $('run-button').innerHTML = busy ? 'Working…' : '<span aria-hidden="true">▶</span> Run scenario';
  }

  async function exclusive(work) {
    if (busy) return;
    busy = true;
    $('error').hidden = true;
    updateControls();
    try { await work(); lastError = false; }
    catch (error) { showError(error); }
    finally { busy = false; updateControls(); }
  }

  function rememberSession(id) {
    sessionId = id;
    const url = new URL(location.href);
    url.searchParams.set('session', id);
    history.replaceState(null, '', url);
    try { localStorage.setItem('rra-session', id); } catch { /* URL remains the source of reload continuity. */ }
  }

  function renderScenarios() {
    $('scenario-count').textContent = `${scenarios.length} scripted scenarios · Select one to explore`;
    $('scenario-list').innerHTML = scenarios.map((scenario, index) => `<button class="scenario${scenario.id === snapshot?.scenario?.id ? ' active' : ''}" data-scenario="${escape(scenario.id)}" data-testid="scenario-${escape(scenario.id)}" type="button" aria-pressed="${scenario.id === snapshot?.scenario?.id}"><span class="scenario-top"><span>${String(index + 1).padStart(2,'0')} / ${escape(String(scenario.domain || 'retail').toUpperCase())}</span><span class="scenario-check" aria-hidden="true"></span></span><span class="scenario-title">${escape(scenario.title)}</span><span class="scenario-tag">${escape(scenarioNotes[scenario.id] || 'Inspect the consent boundary')}</span></button>`).join('');
  }

  function conversation(lane) {
    if (!lane.conversation?.length) return '<p class="empty">The conversation will appear as the scenario progresses.</p>';
    return lane.conversation.map((message) => {
      const role = ['customer','assistant','system'].includes(message.role) ? message.role : 'system';
      const name = role === 'customer' ? 'Customer' : role === 'assistant' ? 'Assistant' : 'System';
      return `<div class="message ${role}"><span class="avatar" aria-hidden="true">${role === 'customer' ? 'C' : role === 'assistant' ? 'A' : '·'}</span><div><p class="message-name">${name}</p><p class="message-content">${escape(message.content)}</p></div></div>`;
    }).join('');
  }

  function fingerprint(value) { return value ? `<p class="fingerprint">SHA-256<br><span>${escape(value)}</span></p>` : ''; }

  function pending(lane, key) {
    const item = lane.pending;
    const confirmation = lane.confirmation || {};
    return `<section class="inspector"><div class="inspector-heading"><h4>Exact tool payload</h4><span class="mini-label">${item ? 'PROPOSED ACTION' : 'NO PENDING ACTION'}</span></div>${item ? `<p class="tool-name">${escape(item.tool_name)}</p><pre class="payload">${pretty(item.arguments)}</pre>${fingerprint(item.fingerprint)}<p class="fingerprint">Operation · ${escape(item.operation_id)}</p>` : '<p class="empty">No mutation is currently waiting at the boundary. Executed payloads remain in the dispatch log below.</p>'}${confirmation.prompt ? `<div class="confirmation-box"><div class="confirmation-meta"><strong>CONFIRMATION</strong><span>${escape(human(confirmation.status))}</span></div><p class="fingerprint">Prompt · ${escape(confirmation.prompt_id)}${confirmation.fingerprint ? `<br>Bound SHA-256 · ${escape(confirmation.fingerprint)}` : ''}</p><details class="confirmation-details" data-detail="${key}-confirmation"><summary>Review complete confirmation prompt</summary><p>${escape(confirmation.prompt)}</p></details>${item && snapshot?.complete ? '<a class="respond-link" href="#customer-controls">Give a fresh response ↓</a>' : ''}</div>` : ''}</section>`;
  }

  function dispatches(lane, key) {
    const rows = lane.dispatches || [];
    return `<details class="lane-details" data-detail="${key}-dispatches"><summary>Local dispatch log <span class="detail-count">${rows.length} ${rows.length === 1 ? 'call' : 'calls'}</span><span class="details-indicator" aria-hidden="true">+</span></summary><div data-testid="${key}-dispatch-list">${rows.length ? rows.map((row) => `<article class="dispatch-entry"><strong>${escape(row.tool_name)}</strong><span class="outcome">Outcome · ${escape(typeof row.outcome === 'object' ? JSON.stringify(row.outcome) : row.outcome)}</span><pre class="payload">${pretty(row.arguments)}</pre>${fingerprint(row.fingerprint)}<p class="fingerprint">Dispatch · ${escape(row.id)}</p></article>`).join('') : '<p class="empty">No local function has been dispatched.</p>'}</div></details>`;
  }

  function states(lane, key) {
    const changed = JSON.stringify(lane.before_state) !== JSON.stringify(lane.after_state);
    return `<details class="lane-details" data-detail="${key}-state"><summary>Fixture state <span class="detail-count">${changed ? 'Changed' : 'Unchanged'}</span><span class="details-indicator" aria-hidden="true">+</span></summary><div class="state-columns"><div class="state-column"><span>BEFORE</span><pre class="payload" data-testid="${key}-before-state">${pretty(lane.before_state)}</pre></div><div class="state-column"><span>AFTER</span><pre class="payload" data-testid="${key}-after-state">${pretty(lane.after_state)}</pre></div></div></details>`;
  }

  function trace(lane, key) {
    const events = lane.events || [];
    return `<details class="lane-details" data-detail="${key}-trace"><summary>Decision trace <span class="detail-count">${events.length} events</span><span class="details-indicator" aria-hidden="true">+</span></summary><ol class="trace-list" data-testid="${key}-trace">${events.length ? events.map((event) => `<li class="trace-event"><p class="event-type">${escape(String(event.seq).padStart(2,'0'))} · ${escape(human(event.type))}</p><p class="event-title">${escape(event.title)}</p><p class="event-detail">${escape(event.detail)}</p>${fingerprint(event.fingerprint)}</li>`).join('') : '<li class="empty">The trace records decisions and actual calls as they happen.</li>'}</ol></details>`;
  }

  function laneHTML(lane, key) {
    const gated = key === 'gated';
    return `<article class="lane ${key}" id="${key}-lane" tabindex="-1" aria-label="${gated ? 'Gated agent' : 'Illustrative baseline'}" data-testid="${key}-lane"><header class="lane-header"><p class="lane-kicker">${gated ? 'PAYLOAD-BOUND CONFIRMATION' : 'ILLUSTRATIVE · UNGATED'}</p><div class="lane-heading"><h3>${gated ? 'Gated agent' : 'Scripted baseline'}</h3><div class="dispatch-count"><b data-testid="${key}-dispatch-count">${lane.dispatches?.length || 0}</b><span>LOCAL<br>DISPATCHES</span></div></div><p class="lane-subtitle">${gated ? 'The gate decides whether this exact action may run.' : 'Illustrative scripted behavior · not a measured model failure.'}</p></header><div class="lane-body"><div class="status-line"><span class="mini-label">Execution status</span><span class="status" data-status="${escape(lane.status)}" data-testid="${key}-status">${escape(human(lane.status))}</span></div><div class="conversation-heading"><h4>Conversation</h4><span class="mini-label">LOCAL SESSION</span></div><div class="conversation" data-testid="${key}-conversation" tabindex="0" aria-label="${gated ? 'Gated' : 'Baseline'} conversation">${conversation(lane)}</div>${pending(lane, key)}${dispatches(lane, key)}${states(lane, key)}${trace(lane, key)}</div></article>`;
  }

  function renderOutcome(data) {
    const lane = data.lanes.gated;
    const calls = lane.dispatches?.length || 0;
    let title = 'Ready to inspect the boundary';
    let description = 'Choose Next step to follow each decision, or Run scenario to see the complete local sequence.';
    let tone = 'neutral';
    if (lane.status === 'uncertain') {
      title = 'Attempt recorded. Outcome uncertain.';
      description = 'The tool was invoked and the gate has stopped. Inspect the dispatch log and fixture state; this operation will not be retried.';
      tone = 'caution';
    } else if (lane.status === 'rejected') {
      title = 'Customer rejection blocks this operation';
      description = 'The proposal is closed. Reset the synthetic scenario to explore a separate run.';
      tone = 'caution';
    } else if (calls > 0) {
      title = `${calls} confirmed local ${calls === 1 ? 'dispatch' : 'dispatches'}`;
      description = 'Inspect the exact executed arguments and before/after fixture state in the gated lane below.';
      tone = 'confirmed';
    } else if (lane.pending) {
      title = data.complete ? 'No gated dispatch. Fresh consent required.' : 'The proposed action is at the boundary';
      description = data.complete ? 'Review the current exact-payload prompt, then add your own response to continue this synthetic run.' : 'Finish the scripted sequence to see how its response is handled. Your response controls unlock after the final step.';
      tone = 'waiting';
    } else if (data.step_index > 0) {
      title = 'Script in progress. No gated dispatch yet.';
      description = 'Follow the conversation and proposal as the scenario reaches its consent boundary.';
    }
    $('scenario-outcome').dataset.tone = tone;
    $('outcome-title').textContent = title;
    $('outcome-description').textContent = description;
    const confirmation = lane.confirmation;
    const canReview = lane.pending && confirmation?.prompt && lane.status !== 'rejected';
    $('response-prompt-review').innerHTML = canReview ? `<div class="response-binding"><span class="mini-label">YOUR RESPONSE APPLIES TO</span><p class="tool-name">${escape(lane.pending.tool_name)}</p><details class="confirmation-details" data-detail="response-confirmation"><summary>Review the current exact-payload prompt</summary><p class="response-prompt">${escape(confirmation.prompt)}</p><p class="fingerprint">Prompt · ${escape(confirmation.prompt_id)}</p>${fingerprint(confirmation.fingerprint)}</details></div>` : '';
  }

  function render(data) {
    if (!data?.scenario || !data?.lanes?.baseline || !data?.lanes?.gated || !data.session_id) throw new Error('The server returned an incomplete session snapshot.');
    const previousScenario = snapshot?.scenario?.id;
    const openDetails = new Set([...document.querySelectorAll('[data-detail][open]')].map((item) => item.dataset.detail));
    snapshot = data;
    rememberSession(data.session_id);
    $('demo-title').textContent = data.scenario.title;
    $('demo-domain').textContent = `${data.scenario.domain || 'Retail'} · SCRIPTED LOCAL SCENARIO`;
    $('demo-description').textContent = data.scenario.description;
    $('lesson-text').textContent = data.scenario.lesson;
    const total = Number(data.total_steps) || 0;
    const index = Number(data.step_index) || 0;
    const percent = total ? Math.min(100, Math.max(0, index / total * 100)) : 0;
    $('progress').setAttribute('aria-valuenow', String(Math.round(percent)));
    $('progress-fill').style.width = `${percent}%`;
    $('step-count').textContent = `${index} / ${total} steps`;
    const current = data.current_step;
    $('current-step').innerHTML = current ? `<strong>${escape(current.title)}</strong> ${escape(current.description || '')}${data.complete ? ' <span class="mini-label">· Script finished</span>' : ''}` : '<strong>Ready when you are.</strong> Advance one step to inspect each decision, or run the scripted sequence.';
    const baselineCalls = data.lanes.baseline.dispatches?.length || 0;
    const gatedCalls = data.lanes.gated.dispatches?.length || 0;
    const gatedChanged = JSON.stringify(data.lanes.gated.before_state) !== JSON.stringify(data.lanes.gated.after_state);
    $('comparison-summary').innerHTML = `<div class="summary-cell"><span class="mini-label">Illustrative baseline</span><p><strong>${baselineCalls}</strong> local ${baselineCalls === 1 ? 'dispatch' : 'dispatches'}</p></div><div class="summary-cell"><span class="mini-label">Gated agent</span><p><strong>${gatedCalls}</strong> local ${gatedCalls === 1 ? 'dispatch' : 'dispatches'}</p></div><div class="summary-cell summary-state"><span class="mini-label">Gate status</span><p><span class="status" data-status="${escape(data.lanes.gated.status)}">${escape(human(data.lanes.gated.status))}</span></p></div><div class="summary-cell"><span class="mini-label">Gated fixture state</span><p class="state-signal">${gatedChanged ? 'Changed' : 'Unchanged'}<span aria-hidden="true">${gatedChanged ? '↗' : '—'}</span></p></div>`;
    $('lanes').innerHTML = laneHTML(data.lanes.baseline, 'baseline') + laneHTML(data.lanes.gated, 'gated');
    renderOutcome(data);
    if (previousScenario === data.scenario.id) document.querySelectorAll('[data-detail]').forEach((item) => { item.open = openDetails.has(item.dataset.detail); });
    document.querySelectorAll('.conversation').forEach((item) => { item.scrollTop = item.scrollHeight; });
    $('response-help').textContent = !data.complete ? 'Finish the four-step script, then add your fresh response.' : data.lanes.gated.status === 'rejected' ? 'This proposal was rejected. Reset the synthetic scenario to present a new proposal.' : data.lanes.gated.status === 'uncertain' ? 'The tool outcome is uncertain. This gate has stopped; no mutation will be retried. Reset only to start a separate synthetic run.' : data.lanes.gated.pending ? 'Your response is a new customer turn for the current gate. Inspect the exact payload and confirmation above before choosing.' : 'No action is awaiting a response. Step through the script or reset this scenario to try another path.';
    $('connection-status').innerHTML = '<i class="dot"></i>Connected · local execution';
    $('development-version').textContent = data.development?.version || 'current-dev-v5';
    renderScenarios();
    updateControls();
  }

  async function selectScenario(id) {
    const restoreFocus = document.activeElement?.dataset.scenario === id;
    await exclusive(async () => {
      const data = await api('/api/sessions', {scenario_id:id, request_id:requestId()});
      render(data);
      $('customer-response').value = '';
    });
    if (restoreFocus && document.activeElement === document.body) {
      [...document.querySelectorAll('[data-scenario]')].find((button) => button.dataset.scenario === id)?.focus({preventScroll:true});
    }
  }

  async function action(name, body = {}) {
    if (!sessionId) return;
    // Bind this customer turn to the prompt the customer actually saw.
    // Never fetch a new identity and silently replay a stale response.
    if (name === 'respond') body = {...body, prompt_id:snapshot?.lanes?.gated?.confirmation?.prompt_id};
    await exclusive(async () => {
      const sessionPath = `/api/sessions/${encodeURIComponent(sessionId)}`;
      try {
        await api(`${sessionPath}/${name}`, {...body, request_id:requestId()});
        // A read reconciles a late idempotent replay with the current session.
        render(await api(sessionPath));
      } catch (error) {
        // Read current state after an uncertain request; never retry the mutation.
        try {
          render(await api(sessionPath));
          error.message += ' Current local state was refreshed for inspection.';
        } catch { /* Keep the original action error when reconciliation is unavailable. */ }
        throw error;
      }
      if (name === 'respond' || name === 'reset') $('customer-response').value = '';
    });
  }

  $('scenario-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-scenario]');
    if (button && !button.disabled && button.dataset.scenario !== snapshot?.scenario?.id) selectScenario(button.dataset.scenario);
  });
  $('step-button').addEventListener('click', () => action('step'));
  $('run-button').addEventListener('click', () => action('run'));
  $('reset-button').addEventListener('click', () => action('reset'));
  document.querySelectorAll('[data-response]').forEach((button) => button.addEventListener('click', () => { if (!button.disabled) action('respond', {text:button.dataset.response}); }));
  $('response-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const text = $('customer-response').value.trim();
    if (text && !$('send-response').disabled) action('respond', {text});
  });

  exclusive(async () => {
    const result = await api('/api/scenarios');
    scenarios = result.scenarios;
    if (!Array.isArray(scenarios) || !scenarios.length) throw new Error('No demo scenarios are available.');
    renderScenarios();
    let saved = new URL(location.href).searchParams.get('session');
    if (!saved) { try { saved = localStorage.getItem('rra-session'); } catch { /* Storage may be disabled. */ } }
    if (saved) {
      try { render(await api(`/api/sessions/${encodeURIComponent(saved)}`)); return; }
      catch { /* Sessions are in memory: a server restart needs a new session. */ }
    }
    render(await api('/api/sessions', {scenario_id:scenarios.find((item) => item.id === 'normal-confirmation')?.id || scenarios[0].id, request_id:requestId()}));
  });
})();
