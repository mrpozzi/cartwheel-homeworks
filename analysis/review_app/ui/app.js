/* Cartwheel HW4 review interface.
   Four views: Review (session timeline + margin notes), Taxonomy, Labels, Progress.
   All state is loaded from and saved to the file-backed API in server.py. */

'use strict';

const state = {
  view: 'review',
  sessions: [], sessionIndex: {}, traceIndex: {},
  current: null, currentId: null,
  annotations: [], traceNotes: { traces: {}, sessions: {} },
  patterns: { modes: [] }, suggestions: [], manifest: { batches: [] },
  labels: {}, spec: [], tags: {}, source: {},
  filters: { search: '', batch: '', role: '', reviewed: '', tag: '', tool: '', badge: '' },
  order: 'scenario_asc', shuffleSeed: 1,
  filtered: [],
  pending: null,
  showExpected: false,
  labelsShowAll: false, labelsCandidates: false, labelsIncomplete: false,
  collapsed: new Set(),
};

/* ---------------------------------------------------------------- utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const short = (id) => String(id || '').slice(0, 8);
const clip = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n) + '…' : s; };
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const now = () => new Date().toISOString();
const pretty = (v) => { try { return JSON.stringify(v, null, 2); } catch { return String(v); } };
const compact = (v) => { const s = JSON.stringify(v); return s && s.length < 110 ? s : pretty(v); };
function md(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`\n]+)`/g, '<code>$1</code>');
}
function toast(msg, ms = 2200) {
  const el = $('#toast'); el.textContent = msg; el.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove('show'), ms);
}
async function api(path, method = 'GET', body) {
  const res = await fetch(path, { method, headers: body !== undefined ? { 'Content-Type': 'application/json' } : {}, body: body !== undefined ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const parseTags = (s) => String(s || '').split(',').map((t) => t.trim()).filter(Boolean);
const typing = () => ['INPUT', 'TEXTAREA', 'SELECT'].includes((document.activeElement || {}).tagName);

/* ------------------------------------------------------------- derived */
const humanAnns = () => state.annotations.filter((a) => a.author !== 'ai');
const annsFor = (tid) => humanAnns().filter((a) => a.trace_id === tid);
const isReviewed = (tid) => annsFor(tid).length > 0;
const hasFailure = (tid) => annsFor(tid).some((a) => !a.no_failure);
const noFailureAnn = (tid) => annsFor(tid).find((a) => a.no_failure);
function batchOf(tid) {
  for (const b of state.manifest.batches || []) if ((b.trace_ids || []).includes(tid)) return b.name;
  return null;
}
function reviewSet() {
  const set = new Set();
  for (const b of state.manifest.batches || []) for (const t of b.trace_ids || []) set.add(t);
  return set.size ? set : null;
}
const modes = (all = false) => (state.patterns.modes || []).filter((m) => all || (m.status || 'candidate') === 'final');
const labelOf = (mode, tid) => (state.labels[mode] || {})[tid];
function reviewStatus(s) {
  const rs = reviewSet();
  const ids = rs && s.trace_ids.some((t) => rs.has(t)) ? s.trace_ids.filter((t) => rs.has(t)) : s.trace_ids;
  const reviewed = ids.filter(isReviewed).length; const failures = ids.filter(hasFailure).length;
  return { total: ids.length, reviewed, failures, cls: reviewed === 0 ? 'none' : reviewed < ids.length ? 'partial' : 'done' };
}
function seededRandom(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function sessionTags(s) {
  const out = new Set((state.traceNotes.sessions[s.session_id] || {}).tags || []);
  for (const t of s.trace_ids) {
    for (const tag of (state.traceNotes.traces[t] || {}).tags || []) out.add(tag);
    for (const a of annsFor(t)) for (const tag of a.tags || []) out.add(tag);
  }
  return out;
}

/* ---------------------------------------------------------------- boot */
async function loadAll() {
  const [source, sessions, anns, notes, patterns, suggestions, manifest, labels, spec, tags] = await Promise.all([
    api('/api/source'), api('/api/sessions'), api('/api/annotations'), api('/api/trace_notes'), api('/api/patterns'),
    api('/api/suggestions'), api('/api/manifest'), api('/api/labels'), api('/api/spec'), api('/api/tags'),
  ]);
  state.source = source; state.sessions = sessions;
  state.annotations = Array.isArray(anns) ? anns : anns.annotations || [];
  state.traceNotes = { traces: {}, sessions: {}, ...(notes || {}) };
  state.patterns = patterns && patterns.modes ? patterns : { modes: [] };
  state.suggestions = Array.isArray(suggestions) ? suggestions : [];
  state.manifest = manifest && manifest.batches ? manifest : { batches: [] };
  state.labels = labels || {}; state.spec = spec || []; state.tags = tags || {};
  state.sessionIndex = {}; state.traceIndex = {};
  for (const s of sessions) { state.sessionIndex[s.session_id] = s; s.turns.forEach((t) => { state.traceIndex[t.trace_id] = { session_id: s.session_id, index: t.index }; }); }
  const src = state.source.used || '?';
  $('#source-badge').textContent = `${src} · ${state.source.session_count} sessions · ${state.source.trace_count} traces`;
  $('#source-badge').title = state.source.reason || '';
  populateFilters();
}
function populateFilters() {
  const fill = (sel, values, label) => {
    const el = $(sel); const cur = el.value; el.innerHTML = `<option value="">${label}</option>` + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    el.value = cur;
  };
  fill('#f-batch', (state.manifest.batches || []).map((b) => b.name), 'all batches');
  fill('#f-tag', Object.keys(state.tags), 'any tag');
  fill('#f-tool', Array.from(new Set(state.sessions.flatMap((s) => s.tools))).sort(), 'any tool');
  fill('#f-badge', Array.from(new Set(state.sessions.flatMap((s) => s.badges.map((b) => b.text)))).sort(), 'any outcome');
  $('#tag-options').innerHTML = Object.keys(state.tags).map((t) => `<option value="${esc(t)}">`).join('');
}

/* ------------------------------------------------------------- sidebar */
function applyFilters() {
  const f = state.filters; const q = f.search.trim().toLowerCase(); const rs = reviewSet();
  state.filtered = state.sessions.filter((s) => {
    if (f.role && s.role !== f.role) return false;
    if (f.tool && !s.tools.includes(f.tool)) return false;
    if (f.badge && !s.badges.some((b) => b.text === f.badge)) return false;
    if (f.batch && !s.trace_ids.some((t) => batchOf(t) === f.batch)) return false;
    if (f.tag && !sessionTags(s).has(f.tag)) return false;
    if (f.reviewed) {
      const ids = rs ? s.trace_ids.filter((t) => rs.has(t)) : s.trace_ids;
      if (f.reviewed === 'no' && !(ids.length && ids.some((t) => !isReviewed(t)))) return false;
      if (f.reviewed === 'yes' && !ids.some((t) => isReviewed(t))) return false;
      if (f.reviewed === 'failure' && !s.trace_ids.some((t) => hasFailure(t))) return false;
    }
    if (q) {
      const hay = [s.scenario_id, s.session_id, s.role, s.scenario.intent, s.scenario.record_state, s.scenario.applicable_policy, ...s.trace_ids, ...s.turns.map((t) => t.user)].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const byId = (a, b) => String(a.scenario_id).localeCompare(String(b.scenario_id));
  const o = state.order;
  if (o === 'scenario_desc') state.filtered.sort((a, b) => byId(b, a));
  else if (o === 'unreviewed_first' || o === 'reviewed_first') {
    const rank = (s) => { const r = reviewStatus(s); return r.total ? r.reviewed / r.total : 0; };
    state.filtered.sort((a, b) => (o === 'unreviewed_first' ? rank(a) - rank(b) : rank(b) - rank(a)) || byId(a, b));
  } else if (o === 'random') {
    const rnd = seededRandom(state.shuffleSeed); const keyed = state.filtered.map((s) => [rnd(), s]);
    keyed.sort((a, b) => a[0] - b[0]); state.filtered = keyed.map(([, s]) => s);
  } else state.filtered.sort(byId);
  renderSidebar();
}
function renderSidebar() {
  const rs = reviewSet();
  $('#session-list').innerHTML = state.filtered.map((s) => {
    const turns = s.turns.map((t) => {
      const cls = hasFailure(t.trace_id) ? 'failure' : isReviewed(t.trace_id) ? 'reviewed' : '';
      const inSet = rs && rs.has(t.trace_id) ? '●' : '';
      return `<span class="${cls}" title="${esc(t.trace_id)}${batchOf(t.trace_id) ? ' · ' + esc(batchOf(t.trace_id)) : ''}">${inSet}${t.index}</span>`;
    }).join('');
    const st = reviewStatus(s);
    const pill = `<span class="pill ${st.cls}" title="${st.reviewed} of ${st.total} turn(s) reviewed${st.failures ? ', ' + st.failures + ' with a failure' : ''}">${st.reviewed}/${st.total}${st.failures ? ' ✗' + st.failures : ''}</span>`;
    return `<li data-id="${esc(s.session_id)}" class="${st.cls} ${s.session_id === state.currentId ? 'active' : ''}">
      <div class="sid">${esc(s.scenario_id)} <span class="dim">· ${esc(s.role)} · ${s.turn_count} turn${s.turn_count > 1 ? 's' : ''}</span>${pill}</div>
      <div class="meta">${esc(s.scenario.intent || '')}${s.scenario.data_quality_case_id ? ' · ' + esc(s.scenario.data_quality_case_id) : ''}</div>
      <div>${s.badges.map((b) => `<span class="badge ${b.kind}">${esc(b.text)}</span>`).join('')}</div>
      <div class="turns">${turns}</div></li>`;
  }).join('');
  $('#f-count').textContent = `${state.filtered.length} of ${state.sessions.length} sessions`;
  updatePos();
}
function updatePos() {
  const i = state.filtered.findIndex((s) => s.session_id === state.currentId);
  $('#pos').textContent = `${i >= 0 ? i + 1 : 0} / ${state.filtered.length}`;
}
function step(delta) {
  if (!state.filtered.length) return;
  let i = state.filtered.findIndex((s) => s.session_id === state.currentId);
  i = Math.min(state.filtered.length - 1, Math.max(0, i + delta));
  openSession(state.filtered[i].session_id);
}

/* -------------------------------------------------------------- session */
async function openSession(id, traceId, annId) {
  const sid = state.sessionIndex[id] ? id : (state.traceIndex[id] || {}).session_id;
  if (!sid) { toast('unknown session'); return; }
  if (state.currentId !== sid || !state.current) {
    state.current = await api(`/api/session?id=${encodeURIComponent(sid)}`);
    state.currentId = sid;
  }
  switchView('review');
  renderSession();
  $$('#session-list li').forEach((li) => li.classList.toggle('active', li.dataset.id === sid));
  const li = $(`#session-list li[data-id="${sid}"]`); if (li) li.scrollIntoView({ block: 'nearest' });
  updatePos();
  location.hash = `s=${sid}${traceId ? '&t=' + traceId : ''}`;
  if (traceId) { const el = $(`.turn[data-trace="${traceId}"]`); if (el) el.scrollIntoView({ block: 'start' }); }
  if (annId) setTimeout(() => focusAnn(annId, true), 50);
}
function renderSession() {
  const s = state.current; if (!s) return;
  const sn = state.traceNotes.sessions[s.session_id] || {};
  const sc = s.scenario || {};
  const fact = (k, v) => (v === null || v === undefined || v === '') ? '' : `<div class="fact"><div class="k">${esc(k)}</div><div class="v">${v}</div></div>`;
  const facts = fact('role', esc(s.role)) + fact('user id', esc(s.user_id)) + fact('store', esc(s.store_id || 'none')) + fact('intent', esc(sc.intent)) + fact('turns', s.turn_count) + fact('record state', esc(sc.record_state)) + fact('applicable policy', esc(sc.applicable_policy));
  const rest = [['scenario group', sc.scenario_group], ['difficulty', sc.difficulty], ['user style', sc.user_style], ['rule pressure', sc.rule_pressure], ['tools needed', sc.tools_needed], ['planned turns', sc.turn_count], ['order id', sc.order_id], ['data quality case', sc.data_quality_case_id], ['prompt version', s.prompt_version], ['session id', s.session_id]].filter(([, v]) => v !== null && v !== undefined && v !== '');
  const def = (rows) => `<div class="def">${rows.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${v}</span>`).join('')}</div>`;
  let expected = '';
  if (s.expected && typeof s.expected === 'object') {
    const e = s.expected; const rows = [];
    if (e.evaluation) rows.push(['evaluation', esc(e.evaluation)]);
    if (e.outcome) rows.push(['expected outcome', `<code>${esc(e.outcome)}</code>`]);
    if (e.criteria) rows.push(['criteria', Array.isArray(e.criteria) ? e.criteria.map(esc).join('<br/>') : esc(e.criteria)]);
    if (e.reason) rows.push(['reason', esc(e.reason)]);
    if (e.source) rows.push(['source', typeof e.source === 'object' ? `${esc(e.source.type)} · <code>${esc(e.source.reference)}</code>` : esc(e.source)]);
    for (const [k, v] of Object.entries(e)) if (!['evaluation', 'outcome', 'criteria', 'reason', 'source'].includes(k)) rows.push([k, esc(typeof v === 'object' ? JSON.stringify(v) : v)]);
    expected = `<details class="panel expected" ${state.showExpected ? 'open' : ''}><summary>Scenario expectation</summary>${def(rows)}</details>`;
  }
  const sys = s.system_prompt || {};
  const html = `<div class="session-wrap"><div class="session-body">
    <div class="session-header">
      <h2>${esc(s.scenario_id)} <span class="dim">session ${esc(short(s.session_id))}</span> <span class="hdr-badges">${s.badges.map((b) => `<span class="badge ${b.kind}">${esc(b.text)}</span>`).join('')}</span></h2>
      <div class="facts">${facts}</div>
      <div class="tools-row"><span class="k">tools used</span>${s.tools.length ? s.tools.map((t) => `<button class="toolchip ${state.filters.tool === t ? 'on' : ''}" data-tool="${esc(t)}" title="filter the sidebar to sessions using ${esc(t)}">${esc(t)}</button>`).join('') : '<span class="dim">none</span>'}</div>
      <div class="controls"><button data-act="collapse-all">collapse all turns</button><button data-act="expand-all">expand all</button><span class="dim">a collapsed turn keeps its user message and a one-line summary</span></div>
      <div class="notes-row"><input class="s-tags" placeholder="session tags (comma separated)" value="${esc((sn.tags || []).join(', '))}" list="tag-options" /><span class="chips" data-chips="session">${chips(sn.tags)}</span><textarea class="s-comment" placeholder="session-level comment (e.g. constraint lost across turns)">${esc(sn.comment || '')}</textarea></div>
      <details class="panel"><summary>Scenario details</summary>${def(rest.map(([k, v]) => [k, esc(v)]))}</details>
      ${expected}
      <details class="panel"><summary>System prompt</summary>
        <div class="ctx">${(sys.context || []).map((l) => `<span>${esc(l)}</span>`).join('')}</div><details class="inner"><summary>full prompt (${(sys.full || '').length} chars)</summary><pre>${esc(sys.full || '')}</pre></details></details>
    </div>
    ${s.turns.map(renderTurn).join('')}
  </div><div class="margin-col" id="margin-col"></div></div>`;
  $('#session-root').innerHTML = html;
  bindSessionEvents();
  applyHighlights();
  layoutMargin();
}
const chips = (tags) => (tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('');
function renderTurn(t) {
  const tid = t.trace_id; const nf = noFailureAnn(tid); const fail = hasFailure(tid);
  const tn = state.traceNotes.traces[tid] || {}; const batch = batchOf(tid); const collapsed = state.collapsed.has(tid);
  const steps = t.steps.map((st, i) => {
    const call = st.tool_call || {}; const res = st.tool_result;
    let inner = '';
    if (st.narration) inner += block(t, 'narration', 'Assistant, before the call', call, `<div class="text">${md(st.narration)}</div>`, i);
    inner += block(t, 'tool_call', 'Tool call', call, `<div><span class="tool-name">${esc(call.name)}</span>(<span class="tool-args">${esc(compact(call.arguments))}</span>)</div>`, i);
    if (res) {
      const bad = res.output && res.output.ok === false;
      inner += block(t, 'tool_result', 'Tool result', call, `<div class="result-summary ${bad ? 'bad' : ''}">${esc(res.summary)}</div>${res.permission_denied ? `<div class="badge danger">permission denied: ${esc(res.permission_reason || '')}</div>` : ''}${renderResult(call.name, res.output)}`, i);
    } else inner += `<div class="block tool_result"><div class="role">Tool result</div><div class="dim">no result recorded</div></div>`;
    return `<div class="step">${inner}</div>`;
  }).join('');
  const status = fail ? '<span class="badge danger">failure noted</span>' : nf ? '<span class="badge ok">reviewed · no failure</span>' : '<span class="badge muted">unreviewed</span>';
  const nSteps = t.steps.length;
  return `<div class="turn ${collapsed ? 'collapsed' : ''}" data-trace="${esc(tid)}">
    <div class="turn-header" data-trace="${esc(tid)}" data-turn="${t.index}" data-kind="trace" data-obs="" data-tool="" data-step="">
      <button class="toggle" data-trace="${esc(tid)}" title="collapse or expand this turn (keeps the user message)">${collapsed ? '▸' : '▾'}</button>
      <span class="n">Turn ${t.index}</span>
      <span class="tid"><a href="${esc(t.langfuse_url || '#')}" target="_blank" title="open in Langfuse">${esc(short(tid))}…</a> <button class="copy" data-copy="${esc(tid)}" title="copy trace id">copy</button></span>
      <span class="stats">${t.latency != null ? `<span class="st">${Number(t.latency).toFixed(1)}s</span>` : ''}<span class="st">${nSteps} tool call${nSteps === 1 ? '' : 's'}</span><span class="st">${t.tokens.total} tok</span>${batch ? `<span class="st batch">${esc(batch)}</span>` : ''}</span>
      <span>${t.badges.map((b) => `<span class="badge ${b.kind}">${esc(b.text)}</span>`).join('')}</span>
      <span class="right">${status}
        <button class="failobs" data-trace="${esc(tid)}" title="write the first failure you observed in this turn (anchored to the whole turn)">✗ failure observed</button>
        <button class="nofail ${nf ? 'on' : ''}" data-trace="${esc(tid)}" ${fail ? 'disabled title="this turn has a failure note; delete it first to mark no failure"' : 'title="record that you reviewed this turn and found no failure"'}>✓ no failure</button></span>
    </div>
    <div class="turn-notes"><input class="t-tags" data-trace="${esc(tid)}" list="tag-options" placeholder="trace tags" value="${esc((tn.tags || []).join(', '))}" /><span class="chips" data-chips="${esc(tid)}">${chips(tn.tags)}</span><textarea class="t-comment" data-trace="${esc(tid)}" placeholder="trace-level comment">${esc(tn.comment || '')}</textarea></div>
    ${block(t, 'user', 'User', null, `<div class="text">${md(t.user)}</div>`)}
    <div class="turn-summary">${nSteps} step${nSteps === 1 ? '' : 's'} (${t.tools.map(esc).join(', ') || 'no tools'}) → reply: ${esc(clip(String(t.reply).replace(/\s+/g, ' '), 150))}</div>
    <div class="turn-body">${steps}${block(t, 'reply', 'Assistant reply', null, `<div class="text">${md(t.reply)}</div>`)}</div>
  </div>`;
}
function toggleCollapse(tid) {
  const turn = $(`.turn[data-trace="${tid}"]`); if (!turn) return;
  if (state.collapsed.has(tid)) state.collapsed.delete(tid); else state.collapsed.add(tid);
  turn.classList.toggle('collapsed', state.collapsed.has(tid));
  const btn = $('.toggle', turn); if (btn) btn.textContent = state.collapsed.has(tid) ? '▸' : '▾';
  layoutMargin();
}
function block(t, kind, label, call, content, stepIdx) {
  const obs = call ? call.observation_id || '' : ''; const tool = call ? call.name || '' : '';
  return `<div class="block ${kind}" data-trace="${esc(t.trace_id)}" data-turn="${t.index}" data-kind="${kind}" data-obs="${esc(obs)}" data-tool="${esc(tool)}" data-step="${stepIdx ?? ''}">
    <div class="role">${label}${tool && kind !== 'narration' ? ` <code>${esc(tool)}</code>` : ''}<button class="add" title="comment on this whole block">+ note</button></div>
    <div class="content">${content}</div></div>`;
}
function renderResult(name, out) {
  if (!out || typeof out !== 'object') return '';
  let special = '';
  if (name === 'search_help_center' && Array.isArray(out.results)) {
    special = `<ul class="retrieval">${out.results.map((r) => `<li><span class="pid">${esc(r.policy_id)}</span> ${esc(r.title)} <span class="score">score ${Number(r.score).toFixed(2)}</span><details class="json"><summary>snippet</summary><pre>${esc(r.snippet)}</pre></details></li>`).join('')}</ul>`;
  } else if (name === 'get_policy' && out.body) {
    special = `<details class="json"><summary>policy body: ${esc(out.title)} (${esc(out.audience)})</summary><pre>${esc(out.body)}</pre></details>`;
  } else if (name === 'get_store_info' && out.store) {
    special = `<div class="kv">${Object.entries(out.store).map(([k, v]) => `<span class="k">${esc(k)}</span><span>${esc(v)}</span>`).join('')}</div>`;
  } else if (name === 'get_order' && out.order) {
    special = `<div class="kv">${Object.entries(out.order).map(([k, v]) => `<span class="k">${esc(k)}</span><span>${esc(v)}</span>`).join('')}</div>`;
  }
  return `${special}<details class="json"><summary>raw result</summary><pre>${esc(pretty(out))}</pre></details>`;
}
function bindSessionEvents() {
  const root = $('#session-root');
  root.onclick = async (e) => {
    const t = e.target;
    if (t.classList.contains('add')) { startBlockNote(t.closest('.block')); return; }
    if (t.classList.contains('toggle')) { toggleCollapse(t.dataset.trace); return; }
    if (t.classList.contains('toolchip')) {
      const v = state.filters.tool === t.dataset.tool ? '' : t.dataset.tool;
      state.filters.tool = v; $('#f-tool').value = v; applyFilters();
      $$('.toolchip').forEach((c) => c.classList.toggle('on', c.dataset.tool === v));
      toast(v ? `sidebar: ${state.filtered.length} sessions use ${v}` : 'tool filter cleared'); return;
    }
    if (t.classList.contains('failobs')) { startBlockNote(t.closest('.turn-header')); return; }
    if (t.dataset.act === 'collapse-all') { state.current.trace_ids.forEach((id) => state.collapsed.add(id)); renderSession(); return; }
    if (t.dataset.act === 'expand-all') { state.collapsed.clear(); renderSession(); return; }
    if (t.classList.contains('nofail')) { await toggleNoFailure(t.dataset.trace); return; }
    if (t.classList.contains('copy')) { navigator.clipboard?.writeText(t.dataset.copy); toast('trace id copied'); return; }
    if (t.tagName === 'MARK' && t.dataset.ann) { focusAnn(t.dataset.ann, true); return; }
  };
  root.onmouseup = (e) => { if (!e.target.closest('.content')) return; setTimeout(handleSelection, 0); };
  root.onchange = async (e) => {
    const t = e.target;
    if (t.classList.contains('t-tags') || t.classList.contains('t-comment')) {
      const tid = t.dataset.trace; const turn = t.closest('.turn');
      state.traceNotes.traces[tid] = { tags: parseTags($('.t-tags', turn).value), comment: $('.t-comment', turn).value.trim(), ts: now() };
      $(`.chips[data-chips="${tid}"]`, turn).innerHTML = chips(state.traceNotes.traces[tid].tags);
      await saveNotes();
    } else if (t.classList.contains('s-tags') || t.classList.contains('s-comment')) {
      state.traceNotes.sessions[state.currentId] = { tags: parseTags($('.s-tags').value), comment: $('.s-comment').value.trim(), ts: now() };
      $('.chips[data-chips="session"]').innerHTML = chips(state.traceNotes.sessions[state.currentId].tags);
      await saveNotes();
    }
  };
  $$('details', root).forEach((d) => d.addEventListener('toggle', () => layoutMargin()));
  $$('details.expected', root).forEach((d) => d.addEventListener('toggle', () => { state.showExpected = d.open; }));
}

/* --------------------------------------------------------- highlighting */
function highlightIn(container, start, end, cls, id) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const nodes = []; let n; while ((n = walker.nextNode())) nodes.push(n);
  let pos = 0; const marks = [];
  for (const node of nodes) {
    const len = node.nodeValue.length; const a = pos, b = pos + len; pos = b;
    if (b <= start || a >= end) continue;
    const from = Math.max(start, a) - a, to = Math.min(end, b) - a;
    let target = node;
    if (from > 0) target = target.splitText(from);
    if (to - from < target.nodeValue.length) target.splitText(to - from);
    const mark = document.createElement('mark'); mark.className = cls; if (id) mark.dataset.ann = id;
    target.parentNode.replaceChild(mark, target); mark.appendChild(target); marks.push(mark);
  }
  return marks;
}
function findBlock(a) {
  const anchor = a.anchor || {};
  const turn = $(`.turn[data-trace="${a.trace_id}"]`); if (!turn) return null;
  if (anchor.kind === 'trace' || !anchor.kind) return turn.querySelector('.turn-header');
  const cands = $$(`.block[data-kind="${anchor.kind}"]`, turn);
  if (!cands.length) return null;
  if (anchor.observation_id) { const hit = cands.find((b) => b.dataset.obs === anchor.observation_id); if (hit) return hit; }
  if (anchor.step !== undefined && anchor.step !== null && anchor.step !== '') { const hit = cands.find((b) => b.dataset.step === String(anchor.step)); if (hit) return hit; }
  return cands[0];
}
function itemsForSession() {
  if (!state.current) return [];
  const ids = new Set(state.current.trace_ids);
  const anns = state.annotations.filter((a) => ids.has(a.trace_id) && !a.no_failure).map((a) => ({ ...a, _type: 'ann' }));
  const sugg = state.suggestions.filter((s) => ids.has(s.trace_id) && (s.status || 'pending') === 'pending').map((s) => ({ ...s, _type: 'sugg' }));
  return anns.concat(sugg);
}
function applyHighlights() {
  for (const it of itemsForSession()) {
    const blockEl = findBlock(it); if (!blockEl) continue;
    const content = blockEl.querySelector('.content');
    if (it.quote && content && it.start != null && it.end != null) {
      const marks = highlightIn(content, it.start, it.end, 'hl' + (it._type === 'sugg' ? ' ai' : ''), it.id);
      if (!marks.length) blockEl.classList.add('noted');
    } else blockEl.classList.add('noted');
  }
}
function layoutMargin() {
  const col = $('#margin-col'); if (!col) return;
  const body = $('.session-body'); col.style.height = body.scrollHeight + 'px';
  const colRect = col.getBoundingClientRect();
  const items = itemsForSession().map((it) => {
    const mark = it.id ? $(`mark[data-ann="${it.id}"]`) : null;
    let el = mark || findBlock(it); if (!el) return null;
    if (el.offsetParent === null) { const turn = el.closest('.turn'); el = turn ? $('.turn-header', turn) : el; it = { ...it, _collapsed: true }; }
    return { it, top: el.getBoundingClientRect().top - colRect.top };
  }).filter(Boolean).sort((a, b) => a.top - b.top);
  col.innerHTML = items.map(({ it }) => noteHtml(it)).join('');
  let last = -Infinity;
  items.forEach(({ it, top }, i) => {
    const el = col.children[i]; if (!el) return;
    const y = Math.max(top, last + 6); el.style.top = y + 'px'; last = y + el.offsetHeight;
  });
  col.onmouseover = (e) => { const n = e.target.closest('.mnote'); if (n) focusAnn(n.dataset.ann, false); };
  col.onmouseout = () => $$('.focus').forEach((x) => x.classList.remove('focus'));
  col.onclick = async (e) => {
    const n = e.target.closest('.mnote'); if (!n) return; const id = n.dataset.ann; const t = e.target;
    if (t.dataset.act === 'delete') { if (confirm('Delete this note?')) { state.annotations = state.annotations.filter((a) => a.id !== id); await saveAnnotations(); renderSession(); renderSidebar(); } }
    else if (t.dataset.act === 'edit') { editNote(n, id); }
    else if (t.dataset.act === 'accept') { await decideSuggestion(id, true); }
    else if (t.dataset.act === 'reject') { const reason = $('input', n).value.trim(); if (!reason) { toast('give a one-line reason for rejecting'); return; } await decideSuggestion(id, false, reason); }
    else if (t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA' && t.tagName !== 'BUTTON') {
      const item = state.annotations.concat(state.suggestions).find((a) => a.id === id) || {};
      if (item.trace_id && state.collapsed.has(item.trace_id)) toggleCollapse(item.trace_id);
      const m = $(`mark[data-ann="${id}"]`); (m || findBlock(item))?.scrollIntoView({ block: 'center' });
    }
  };
}
function noteHtml(it) {
  const ai = it._type === 'sugg';
  const tags = (it.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join('');
  const where = `turn ${it.turn || (state.traceIndex[it.trace_id] || {}).index || '?'} · ${esc((it.anchor || {}).kind || 'trace')}${(it.anchor || {}).tool_name ? ' · ' + esc(it.anchor.tool_name) : ''}${it._collapsed ? ' · (turn collapsed)' : ''}`;
  if (ai) return `<div class="mnote ai" data-ann="${esc(it.id)}"><div class="who">AI suggestion${it.mode ? ' · ' + esc(it.mode) : ''}</div><div class="q">${where}${it.quote ? ' · “' + esc(clip(it.quote, 60)) + '”' : ''}</div><div>${esc(it.note || it.rationale || '')}</div>${tags}
    <div class="acts"><button data-act="accept">accept</button><button data-act="reject" class="danger">reject</button></div><input placeholder="reason if rejecting" /></div>`;
  return `<div class="mnote ${it.author === 'ai-accepted' ? 'ai' : ''}" data-ann="${esc(it.id)}">${it.author === 'ai-accepted' ? '<div class="who">accepted from AI</div>' : ''}<div class="q">${where}${it.quote ? ' · “' + esc(clip(it.quote, 60)) + '”' : ''}</div><div class="note-text">${esc(it.note)}</div>${tags}
    <div class="acts"><button class="hover" data-act="edit">edit</button><button class="hover danger" data-act="delete">delete</button></div></div>`;
}
function focusAnn(id, scroll) {
  $$('.focus').forEach((x) => x.classList.remove('focus'));
  const m = $(`mark[data-ann="${id}"]`), n = $(`.mnote[data-ann="${id}"]`);
  if (m) m.classList.add('focus'); if (n) n.classList.add('focus');
  if (scroll && n) n.scrollIntoView({ block: 'center' });
}
function editNote(noteEl, id) {
  const a = state.annotations.find((x) => x.id === id); if (!a) return;
  noteEl.innerHTML = `<textarea rows="3">${esc(a.note)}</textarea><input list="tag-options" value="${esc((a.tags || []).join(', '))}" placeholder="tags" /><div class="acts"><button data-act="save-edit" class="primary">save</button><button data-act="cancel-edit">cancel</button></div>`;
  const ta = $('textarea', noteEl); ta.focus();
  noteEl.onclick = async (e) => {
    if (e.target.dataset.act === 'save-edit') { a.note = ta.value.trim(); a.tags = parseTags($('input', noteEl).value); a.edited_at = now(); await saveAnnotations(); renderSession(); }
    else if (e.target.dataset.act === 'cancel-edit') renderSession();
    e.stopPropagation();
  };
}

/* ----------------------------------------------------- selection/popover */
function handleSelection() {
  const sel = window.getSelection(); if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  const content = range.startContainer.parentElement?.closest('.content');
  if (!content || !content.contains(range.endContainer)) { toast('select within one block'); sel.removeAllRanges(); return; }
  const blockEl = content.closest('.block');
  const pre = document.createRange(); pre.selectNodeContents(content); pre.setEnd(range.startContainer, range.startOffset);
  const start = pre.toString().length; const quote = range.toString(); const end = start + quote.length;
  if (!quote.trim()) return;
  clearPending();
  const marks = highlightIn(content, start, end, 'hl pending', 'pending');
  sel.removeAllRanges();
  state.pending = { blockEl, quote, start, end, marks };
  const rect = (marks[0] || blockEl).getBoundingClientRect();
  showPopover(rect, `Turn ${blockEl.dataset.turn} · ${blockEl.dataset.kind}${blockEl.dataset.tool ? ' · ' + blockEl.dataset.tool : ''} · “${clip(quote, 70)}”`);
}
function startBlockNote(blockEl) {
  clearPending();
  blockEl.classList.add('noted');
  state.pending = { blockEl, quote: null, start: null, end: null, marks: [] };
  const k = blockEl.dataset.kind;
  showPopover(blockEl.getBoundingClientRect(), `Turn ${blockEl.dataset.turn} · ${k === 'trace' ? 'whole turn' : 'whole ' + k + ' block'}${blockEl.dataset.tool ? ' · ' + blockEl.dataset.tool : ''}`);
}
function showPopover(rect, anchorText) {
  const pop = $('#popover'); pop.classList.remove('hidden');
  $('#popover-anchor').textContent = anchorText;
  $('#note-input').value = ''; $('#tag-input').value = '';
  const h = pop.offsetHeight || 200;
  let top = rect.bottom + 6; if (top + h > window.innerHeight - 8) top = Math.max(8, rect.top - h - 6);
  const left = Math.max(8, Math.min(window.innerWidth - 400, rect.left));
  pop.style.top = top + 'px'; pop.style.left = left + 'px';
  $('#note-input').focus();
}
function clearPending() {
  if (state.pending) {
    for (const m of state.pending.marks) { const p = m.parentNode; while (m.firstChild) p.insertBefore(m.firstChild, m); p.removeChild(m); p.normalize(); }
    if (!state.pending.quote) state.pending.blockEl.classList.remove('noted');
  }
  state.pending = null; $('#popover').classList.add('hidden');
}
async function commitAnnotation() {
  const p = state.pending; if (!p) return;
  const note = $('#note-input').value.trim(); if (!note) { toast('write the observation first'); return; }
  const b = p.blockEl; const tid = b.dataset.trace;
  const ann = {
    id: uid(), trace_id: tid, session_id: state.currentId, scenario_id: state.current.scenario_id, turn: Number(b.dataset.turn),
    anchor: { kind: b.dataset.kind, observation_id: b.dataset.obs || null, tool_name: b.dataset.tool || null, step: b.dataset.step === '' ? null : Number(b.dataset.step) },
    quote: p.quote, start: p.start, end: p.end, note, tags: parseTags($('#tag-input').value), ts: now(), author: 'human', batch: batchOf(tid),
  };
  const nf = noFailureAnn(tid);
  if (nf) { state.annotations = state.annotations.filter((a) => a.id !== nf.id); toast('removed the earlier "no failure observed" mark'); }
  state.annotations.push(ann);
  state.pending = null; $('#popover').classList.add('hidden');
  await saveAnnotations(); renderSession(); renderSidebar();
}
async function toggleNoFailure(tid) {
  const nf = noFailureAnn(tid);
  if (nf) state.annotations = state.annotations.filter((a) => a.id !== nf.id);
  else {
    if (hasFailure(tid)) { toast('this turn already has a failure note; delete it first'); return; }
    state.annotations.push({ id: uid(), trace_id: tid, session_id: state.currentId, scenario_id: state.current.scenario_id, turn: (state.traceIndex[tid] || {}).index, anchor: { kind: 'trace' }, quote: null, start: null, end: null, note: 'no failure observed', no_failure: true, tags: [], ts: now(), author: 'human', batch: batchOf(tid) });
  }
  await saveAnnotations(); renderSession(); renderSidebar();
}
async function saveAnnotations() {
  try { localStorage.setItem('cw_hw4_annotations', JSON.stringify(state.annotations)); } catch { /* ignore */ }
  try { await api('/api/annotations', 'POST', { annotations: state.annotations }); refreshTags(); } catch (e) { toast('save failed: ' + e.message, 5000); }
}
async function saveNotes() { try { await api('/api/trace_notes', 'POST', state.traceNotes); refreshTags(); } catch (e) { toast('save failed: ' + e.message, 5000); } }
async function refreshTags() { state.tags = await api('/api/tags'); populateFilters(); }
async function decideSuggestion(id, accept, reason) {
  const s = state.suggestions.find((x) => x.id === id); if (!s) return;
  s.status = accept ? 'accepted' : 'rejected'; s.decided_at = now(); if (reason) s.decision_reason = reason;
  if (accept) {
    const ann = { id: uid(), trace_id: s.trace_id, session_id: s.session_id || (state.traceIndex[s.trace_id] || {}).session_id, turn: s.turn || (state.traceIndex[s.trace_id] || {}).index, anchor: s.anchor || { kind: 'trace' }, quote: s.quote || null, start: s.start ?? null, end: s.end ?? null, note: s.note || s.rationale || '', tags: s.tags || [], ts: now(), author: 'ai-accepted', origin: { suggestion_id: s.id, mode: s.mode || null }, batch: batchOf(s.trace_id) };
    s.annotation_id = ann.id; state.annotations.push(ann);
    const nf = noFailureAnn(s.trace_id); if (nf) state.annotations = state.annotations.filter((a) => a.id !== nf.id);
    await saveAnnotations();
  }
  try { await api('/api/suggestions', 'POST', state.suggestions); } catch (e) { toast('save failed: ' + e.message, 5000); }
  toast(accept ? 'suggestion accepted as an annotation' : 'suggestion rejected');
  if (state.view === 'review') { renderSession(); renderSidebar(); } else renderView();
}

/* ------------------------------------------------------------ taxonomy */
function renderTaxonomy() {
  const el = $('#taxonomy-view'); const ms = state.patterns.modes || [];
  if (!ms.length) { el.innerHTML = '<p class="empty">No modes yet. Candidate groups appear here after axial coding writes patterns.json.</p>'; return; }
  const annById = Object.fromEntries(state.annotations.map((a) => [a.id, a]));
  el.innerHTML = ms.map((m, i) => {
    const lab = state.labels[m.name] || {}; const vals = Object.values(lab);
    const fails = vals.filter((r) => r.label === 1).length, passes = vals.filter((r) => r.label === 0).length;
    const chips = (ids) => (ids || []).map((t) => `<a class="chip" data-open="${esc(t)}">${esc(short(t))}…</a>`).join('') || '<span class="dim">none</span>';
    const anns = (m.origin_annotations || []).map((id) => { const a = annById[id]; return a ? `<div class="ann" data-open="${esc(a.trace_id)}" data-ann="${esc(a.id)}"><b>${esc(short(a.trace_id))}</b> ${esc(a.note)} ${a.quote ? `<span class="q">— “${esc(clip(a.quote, 80))}”</span>` : ''}</div>` : `<div class="ann dim">${esc(id)} (annotation not found)</div>`; }).join('');
    return `<div class="mode-card" data-i="${i}"><h3>${esc(m.name)} <span class="status ${esc(m.status || 'candidate')}">${esc(m.status || 'candidate')}</span><span class="dim" style="font-family:inherit;font-size:12px">labels: ${fails} fail · ${passes} pass</span></h3>
      <label>binary definition</label><textarea data-f="definition" rows="2">${esc(m.definition || '')}</textarea>
      <label>boundary with the nearest mode</label><textarea data-f="boundary" rows="2">${esc(m.boundary || '')}</textarea>
      <div class="grid"><div><label>requirement source (SPEC id or revision)</label><input data-f="requirement_source" value="${esc(m.requirement_source || '')}" /></div>
      <div><label>likely evaluator</label><select data-f="evaluator_type"><option value="">?</option><option ${m.evaluator_type === 'code_check' ? 'selected' : ''} value="code_check">code check</option><option ${m.evaluator_type === 'llm_judge' ? 'selected' : ''} value="llm_judge">LLM judge</option></select></div>
      <div><label>status</label><select data-f="status"><option ${(m.status || 'candidate') === 'candidate' ? 'selected' : ''} value="candidate">candidate</option><option ${m.status === 'final' ? 'selected' : ''} value="final">final</option><option ${m.status === 'rejected' ? 'selected' : ''} value="rejected">rejected</option><option ${m.status === 'merged' ? 'selected' : ''} value="merged">merged</option></select></div>
      <div><label>notes / history</label><input data-f="notes" value="${esc(m.notes || '')}" /></div></div>
      <label>confirmed positives (${(m.positives || []).length})</label><div class="links">${chips(m.positives)}</div>
      <label>close negatives (${(m.close_negatives || []).length})</label><div class="links">${chips(m.close_negatives)}</div>
      <label>origin annotations (${(m.origin_annotations || []).length})</label>${anns || '<div class="dim">none linked</div>'}
      <div class="acts"><button class="primary" data-save="${i}">save changes</button></div></div>`;
  }).join('');
  el.onclick = async (e) => {
    const t = e.target;
    if (t.dataset.save !== undefined) {
      const card = t.closest('.mode-card'); const m = ms[Number(card.dataset.i)];
      $$('[data-f]', card).forEach((f) => { m[f.dataset.f] = f.value.trim(); });
      m.updated_at = now();
      try { await api('/api/patterns', 'POST', state.patterns); toast('taxonomy saved'); renderTaxonomy(); } catch (err) { toast('save failed: ' + err.message, 5000); }
      return;
    }
    const opener = t.closest('[data-open]'); if (opener) openSession(opener.dataset.open, opener.dataset.open, opener.dataset.ann);
  };
}

/* -------------------------------------------------------------- labels */
function labelRows() {
  const rs = reviewSet(); let ids;
  if (state.labelsShowAll || !rs) ids = state.sessions.flatMap((s) => s.trace_ids).filter((t) => state.labelsShowAll || isReviewed(t));
  else ids = Array.from(rs);
  return ids.map((t) => ({ tid: t, ...(state.traceIndex[t] || {}) })).filter((r) => r.session_id).sort((a, b) => (state.sessionIndex[a.session_id].scenario_id || '').localeCompare(state.sessionIndex[b.session_id].scenario_id || '') || a.index - b.index);
}
function renderLabels() {
  const el = $('#labels-view'); const ms = modes(state.labelsCandidates);
  let rows = labelRows();
  const incomplete = (tid) => ms.some((m) => !labelOf(m.name, tid));
  if (state.labelsIncomplete) rows = rows.filter((r) => incomplete(r.tid));
  const counts = ms.map((m) => { const lab = state.labels[m.name] || {}; const ids = labelRows().map((r) => r.tid); const f = ids.filter((t) => lab[t]?.label === 1).length, p = ids.filter((t) => lab[t]?.label === 0).length; return { m, f, p, u: ids.length - f - p }; });
  el.innerHTML = `<div class="labels-toolbar">
      <label><input type="checkbox" id="l-cand" ${state.labelsCandidates ? 'checked' : ''}/> include candidate modes</label>
      <label><input type="checkbox" id="l-all" ${state.labelsShowAll ? 'checked' : ''}/> all traces (not only the review set)</label>
      <label><input type="checkbox" id="l-inc" ${state.labelsIncomplete ? 'checked' : ''}/> incomplete rows only</label>
      <button id="l-sync">retry Langfuse sync</button>
      <span class="dim">${rows.length} rows · F = failure present (1) · P = absent (0). Each click saves and writes a Langfuse score.</span></div>
    ${ms.length ? `<table class="labels"><thead><tr><th>trace</th><th>open codes</th>${counts.map(({ m, f, p, u }) => `<th class="mode">${esc(m.name)}<small>${f} fail · ${p} pass · ${u} unset</small></th>`).join('')}<th>evidence note for next click</th></tr></thead><tbody>
    ${rows.map((r) => { const s = state.sessionIndex[r.session_id]; const t = s.turns.find((x) => x.trace_id === r.tid) || {}; const notes = annsFor(r.tid).map((a) => a.no_failure ? 'no failure observed' : a.note);
      return `<tr class="${incomplete(r.tid) ? 'incomplete' : ''}"><td class="trace"><span class="tid" data-open="${esc(r.tid)}">${esc(short(r.tid))}…</span><div class="sub">${esc(s.scenario_id)} · ${esc(s.role)} · turn ${r.index}${batchOf(r.tid) ? ' · ' + esc(batchOf(r.tid)) : ''}</div><div>${(t.badges || []).map((b) => `<span class="badge ${b.kind}">${esc(b.text)}</span>`).join('')}</div></td>
        <td style="max-width:320px;font-size:12px">${notes.length ? notes.map((n) => esc(clip(n, 160))).join('<br/>') : '<span class="dim">unreviewed</span>'}</td>
        ${ms.map((m) => { const l = labelOf(m.name, r.tid); const v = l ? l.label : null; const uns = l && l.langfuse && !l.langfuse.synced ? '<div class="unsynced" title="' + esc(l.langfuse.error || '') + '">not in Langfuse</div>' : ''; return `<td class="cell" title="${esc(l?.note || '')}"><button data-tid="${esc(r.tid)}" data-mode="${esc(m.name)}" data-v="1" class="${v === 1 ? 'on-fail' : ''}">F</button> <button data-tid="${esc(r.tid)}" data-mode="${esc(m.name)}" data-v="0" class="${v === 0 ? 'on-pass' : ''}">P</button>${uns}</td>`; }).join('')}
        <td class="note"><input data-note="${esc(r.tid)}" placeholder="evidence (optional)" /></td></tr>`; }).join('')}</tbody></table>` : '<p class="empty">No final modes yet. Mark modes as final in the Taxonomy view, or tick "include candidate modes".</p>'}`;
  $('#l-cand').onchange = (e) => { state.labelsCandidates = e.target.checked; renderLabels(); };
  $('#l-all').onchange = (e) => { state.labelsShowAll = e.target.checked; renderLabels(); };
  $('#l-inc').onchange = (e) => { state.labelsIncomplete = e.target.checked; renderLabels(); };
  $('#l-sync').onclick = async () => { const r = await api('/api/labels/sync', 'POST', {}); toast(`retried ${r.retried}, synced ${r.synced}`); state.labels = await api('/api/labels'); renderLabels(); };
  el.onclick = async (e) => {
    const t = e.target;
    if (t.dataset.open) { openSession(t.dataset.open, t.dataset.open); return; }
    if (t.dataset.mode && t.dataset.v !== undefined) {
      const tid = t.dataset.tid, mode = t.dataset.mode, v = Number(t.dataset.v);
      const noteEl = $(`input[data-note="${tid}"]`, el); const note = noteEl ? noteEl.value.trim() : '';
      t.disabled = true;
      try {
        const r = await api('/api/labels', 'POST', { trace_id: tid, mode, label: v, note });
        state.labels[mode] = state.labels[mode] || {}; state.labels[mode][tid] = r.record;
        if (!r.record.langfuse.synced) toast('saved locally; Langfuse write failed: ' + r.record.langfuse.error, 5000);
        renderLabels();
      } catch (err) { toast('label failed: ' + err.message, 5000); t.disabled = false; }
    }
  };
}

/* ------------------------------------------------------------ progress */
function renderProgress() {
  const el = $('#progress-view'); const src = state.source; const rs = reviewSet();
  const batches = state.manifest.batches || [];
  const allReviewed = state.sessions.flatMap((s) => s.trace_ids).filter(isReviewed);
  const batchRows = batches.map((b) => { const ids = b.trace_ids || []; const rev = ids.filter(isReviewed), fail = ids.filter(hasFailure); const todo = ids.filter((t) => !isReviewed(t));
    return `<tr><td><b>${esc(b.name)}</b><div class="dim">${esc(b.method || '')}${b.dimension ? ' · ' + esc(b.dimension) : ''}</div></td><td>${ids.length}</td><td>${rev.length}</td><td>${fail.length}</td><td><div class="bar"><i style="width:${ids.length ? (100 * rev.length / ids.length) : 0}%"></i></div></td><td>${todo.slice(0, 12).map((t) => `<a class="chip" data-open="${esc(t)}">${esc(short(t))}…</a>`).join('')}${todo.length > 12 ? `<span class="dim">+${todo.length - 12}</span>` : ''}</td></tr>`; }).join('');
  const ms = modes(true); const ids = rs ? Array.from(rs) : allReviewed;
  const modeRows = ms.map((m) => { const lab = state.labels[m.name] || {}; const f = ids.filter((t) => lab[t]?.label === 1).length, p = ids.filter((t) => lab[t]?.label === 0).length, u = ids.length - f - p; const uns = Object.values(lab).filter((r) => r.langfuse && !r.langfuse.synced).length;
    return `<tr><td><code>${esc(m.name)}</code> <span class="dim">${esc(m.status || 'candidate')}</span></td><td>${f}</td><td>${p}</td><td>${u}</td><td>${ids.length ? (100 * f / ids.length).toFixed(1) + '%' : '–'}</td><td><div class="bar"><i style="width:${ids.length ? (100 * (f + p) / ids.length) : 0}%"></i></div></td><td>${uns ? `<span class="badge warn">${uns} unsynced</span>` : ''}</td></tr>`; }).join('');
  const pend = state.suggestions.filter((s) => (s.status || 'pending') === 'pending'), acc = state.suggestions.filter((s) => s.status === 'accepted'), rej = state.suggestions.filter((s) => s.status === 'rejected');
  const suggHtml = (s) => `<div class="sugg ${esc(s.status || 'pending')}" data-id="${esc(s.id)}"><div class="head"><span class="mode">${esc(s.mode || 'suggestion')}</span><a class="chip" data-open="${esc(s.trace_id)}">${esc(short(s.trace_id))}…</a><span class="dim">${esc(s.search || s.signal || '')}</span></div>${s.quote ? `<div class="q">“${esc(clip(s.quote, 140))}”</div>` : ''}<div>${esc(s.note || s.rationale || '')}</div>${s.status === 'rejected' ? `<div class="dim">rejected: ${esc(s.decision_reason || '')}</div>` : ''}${(s.status || 'pending') === 'pending' ? `<div class="acts"><button data-act="accept">accept</button><button data-act="reject" class="danger">reject</button><input placeholder="reason if rejecting" /></div>` : ''}</div>`;
  el.innerHTML = `<div class="prog-section"><h3>Source</h3><div class="dim">${esc(src.used)} · ${esc(src.reason || '')}${src.fallback_reason ? ' · ' + esc(src.fallback_reason) : ''} · state in ${esc(src.state_dir || '')}</div>
      <div style="margin-top:6px">${state.sessions.length} sessions · ${src.trace_count} traces · ${allReviewed.length} traces reviewed (${allReviewed.filter(hasFailure).length} with a failure note, ${allReviewed.filter((t) => !hasFailure(t)).length} marked no failure) · ${humanAnns().filter((a) => !a.no_failure).length} open codes</div></div>
    <div class="prog-section"><h3>Review batches</h3>${batches.length ? `<table><thead><tr><th>batch</th><th>size</th><th>reviewed</th><th>with failure</th><th></th><th>still to review</th></tr></thead><tbody>${batchRows}</tbody></table>` : '<div class="dim">No sample manifest yet. Batches appear here once Part B sampling writes sample_manifest.json.</div>'}</div>
    <div class="prog-section"><h3>Structured labels (${rs ? 'review set' : 'reviewed traces'}: ${ids.length} traces)</h3>${ms.length ? `<table><thead><tr><th>mode</th><th>fail</th><th>pass</th><th>unset</th><th>sample fraction</th><th>complete</th><th></th></tr></thead><tbody>${modeRows}</tbody></table>` : '<div class="dim">No modes yet.</div>'}</div>
    <div class="prog-section"><h3>AI suggestions · ${pend.length} pending · ${acc.length} accepted · ${rej.length} rejected</h3>${pend.map(suggHtml).join('') || '<div class="dim">nothing pending</div>'}
      <details style="margin-top:8px"><summary class="dim">decided suggestions (${acc.length + rej.length})</summary>${acc.concat(rej).map(suggHtml).join('')}</details></div>
    <div class="prog-section"><h3>Keys</h3><div class="dim"><kbd>↑</kbd>/<kbd>↓</kbd>, <kbd>PgUp</kbd>/<kbd>PgDn</kbd>, <kbd>Space</kbd> scroll the feed · <kbd>←</kbd>/<kbd>→</kbd> or <kbd>k</kbd>/<kbd>j</kbd> previous/next session · <kbd>Esc</kbd> cancel note · select text in any block to annotate · <kbd>+ note</kbd> comments on a whole block · <kbd>✗ failure observed</kbd> notes the whole turn · <kbd>▾</kbd> collapses a turn</div></div>`;
  el.onclick = async (e) => {
    const t = e.target; const opener = t.closest('[data-open]'); if (opener) { openSession(opener.dataset.open, opener.dataset.open); return; }
    const box = t.closest('.sugg'); if (!box) return;
    if (t.dataset.act === 'accept') await decideSuggestion(box.dataset.id, true);
    if (t.dataset.act === 'reject') { const reason = $('input', box).value.trim(); if (!reason) { toast('give a one-line reason for rejecting'); return; } await decideSuggestion(box.dataset.id, false, reason); }
  };
}

/* ---------------------------------------------------------------- spec */
function renderSpec() {
  $('#spec-list').innerHTML = state.spec.map((r) => `<div class="req" data-id="${esc(r.id)}"><b>${esc(r.id)}</b> <span class="sec">${esc(r.section)}</span><div>${md(r.text)}</div></div>`).join('');
  $('#spec-list').onclick = (e) => { const r = e.target.closest('.req'); if (!r) return; const id = r.dataset.id;
    const tagIn = $('#tag-input'); if (!$('#popover').classList.contains('hidden')) { tagIn.value = tagIn.value ? tagIn.value.replace(/,?\s*$/, ', ') + id : id; toast(`${id} added to tags`); } else { navigator.clipboard?.writeText(id); toast(`${id} copied`); } };
}

/* ------------------------------------------------------------- views */
function switchView(name) {
  state.view = name;
  $$('.views button').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `${name}-view`));
  renderView();
}
function renderView() {
  if (state.view === 'taxonomy') renderTaxonomy();
  else if (state.view === 'labels') renderLabels();
  else if (state.view === 'progress') renderProgress();
  else if (state.view === 'review' && state.current) layoutMargin();
}

/* ------------------------------------------------------------- polling */
let pollSig = '';
async function poll() {
  try {
    const [sugg, patterns, labels] = await Promise.all([api('/api/suggestions'), api('/api/patterns'), api('/api/labels')]);
    const sig = JSON.stringify([sugg, patterns]);
    if (sig !== pollSig) {
      const before = state.suggestions.filter((s) => (s.status || 'pending') === 'pending').length;
      state.suggestions = Array.isArray(sugg) ? sugg : []; state.patterns = patterns && patterns.modes ? patterns : { modes: [] }; state.labels = labels || {};
      const after = state.suggestions.filter((s) => (s.status || 'pending') === 'pending').length;
      if (pollSig && after > before) toast(`${after - before} new AI suggestion${after - before > 1 ? 's' : ''} to review`, 4000);
      pollSig = sig;
      if (state.view === 'review' && state.current) { renderSession(); } else renderView();
    }
  } catch { /* server away; try again */ }
  setTimeout(poll, 5000);
}

/* ---------------------------------------------------------------- init */
(async function boot() {
  await loadAll();
  renderSpec();
  applyFilters();
  $$('.views button').forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
  $('#prev').onclick = () => step(-1); $('#next').onclick = () => step(1);
  $('#spec-toggle').onclick = () => $('#spec-drawer').classList.toggle('hidden');
  $('#spec-close').onclick = () => $('#spec-drawer').classList.add('hidden');
  $('#session-list').onclick = (e) => { const li = e.target.closest('li'); if (li) openSession(li.dataset.id); };
  for (const [id, key] of [['#f-search', 'search'], ['#f-batch', 'batch'], ['#f-role', 'role'], ['#f-reviewed', 'reviewed'], ['#f-tag', 'tag'], ['#f-tool', 'tool'], ['#f-badge', 'badge']]) {
    $(id).addEventListener('input', (e) => { state.filters[key] = e.target.value; applyFilters(); });
  }
  $('#f-order').addEventListener('input', (e) => { state.order = e.target.value; applyFilters(); });
  $('#f-shuffle').onclick = () => { state.shuffleSeed = (Math.random() * 2 ** 31) >>> 0; state.order = 'random'; $('#f-order').value = 'random'; applyFilters(); toast('new random order'); };
  $('#popover-save').onclick = commitAnnotation; $('#popover-cancel').onclick = clearPending;
  $('#note-input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commitAnnotation(); } if (e.key === 'Escape') clearPending(); });
  $('#tag-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commitAnnotation(); } if (e.key === 'Escape') clearPending(); });
  document.addEventListener('mousedown', (e) => { if (state.pending && !e.target.closest('#popover') && !e.target.closest('#spec-drawer') && !e.target.closest('mark.pending')) clearPending(); });
  document.addEventListener('keydown', (e) => {
    if (typing()) return;
    const main = $('#main'); const page = main.clientHeight * 0.9;
    const scroll = { ArrowDown: 80, ArrowUp: -80, PageDown: page, PageUp: -page, ' ': e.shiftKey ? -page : page };
    if (e.key in scroll) { e.preventDefault(); main.scrollBy({ top: scroll[e.key] }); return; }
    if (e.key === 'Home') { e.preventDefault(); main.scrollTo({ top: 0 }); return; }
    if (e.key === 'End') { e.preventDefault(); main.scrollTo({ top: main.scrollHeight }); return; }
    if (e.key === 'j' || e.key === 'ArrowRight') step(1);
    if (e.key === 'k' || e.key === 'ArrowLeft') step(-1);
    if (e.key === 'Escape') clearPending();
  });
  window.addEventListener('resize', () => layoutMargin());
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get('s')) await openSession(h.get('s'), h.get('t'));
  else if (state.filtered.length) await openSession(state.filtered[0].session_id);
  setTimeout(poll, 5000);
})();
