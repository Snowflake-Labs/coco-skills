'use strict';

// ===========================================================================
// Spark Migration — Progress Tracker (Experimental) (front-end)
//
// State is snapshot-authoritative: /api/snapshot is rebuilt server-side from
// the full event log + collectors, so we treat it as the source of truth for
// all aggregate state (phases, counts, reports, collector metrics). SSE is
// used for instant feed lines and to trigger an immediate snapshot refresh, so
// the UI reacts the moment an event lands rather than waiting for the next
// poll tick.
// ===========================================================================

// ---------------------------------------------------------------------------
// Stage model — four top-level stages, each with named sub-phases.
// ---------------------------------------------------------------------------
const STAGES = [
  { id: 'assessment', label: 'Assessment', icon: 'assessment', sub: [
    { id: 'preprocessing',     label: 'Preprocess' },
    { id: 'sql-rewrite',       label: 'SQL rewrite' },
    { id: 'analysis',          label: 'Analyze' },
    { id: 'report-assessment', label: 'Readiness report' },
  ]},
  { id: 'migration', label: 'Migration', icon: 'sync', sub: [
    { id: 'migration',       label: 'Convert' },
    { id: 'imports-headers', label: 'Imports & headers' },
    { id: 'verification',    label: 'Verify' },
  ]},
  { id: 'reports', label: 'Reports', icon: 'description', sub: [
    { id: 'reports', label: 'Generate reports' },
  ]},
  { id: 'validation', label: 'Validation', icon: 'verified', sub: [
    { id: 'survey',       label: 'Survey & batch prep' },
    { id: 'phase-a',      label: 'Phase A — local Spark' },
    { id: 'phase-b',      label: 'Phase B — SCOS' },
    { id: 'harvest',      label: 'Harvest & report' },
  ]},
];

// Validation phase A/B status → display
const EP_STATUS_META = {
  pending:            { cls: '',        label: 'Pending' },
  running:            { cls: 'running', label: 'Running' },
  passed:             { cls: 'passed',  label: 'Passed' },
  passed_no_baseline: { cls: 'passed',  label: 'Passed*' },
  failed:             { cls: 'failed',  label: 'Failed' },
  hard_stuck:         { cls: 'stuck',   label: 'Stuck' },
  skipped:            { cls: 'skipped', label: 'Skipped' },
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const S = {
  lastSeq:          -1,
  startTs:          null,
  project:          null,
  convType:         null,
  overallPct:       0,
  activePhase:      null,
  lastMsg:          '',
  subStatus:        {},       // subId → 'active' | 'done' | 'failed'
  files:            { converted: 0, verified: 0, failed: 0, reverted: 0, total: null },
  // Validation entrypoint tracking
  validationEps:    {},       // epId → { phaseA: status, phaseB: status, lastMsg: string }
  validationTotal:  null,     // total entrypoints in run
  activeEp:         null,     // currently running ep_id
  scos:             {},
  git:              {},
  sqlite:           {},
  reports:          [],
  feedMap:          {},       // seq → event
  done:             false,        // whole run finished
  migrationDone:    false,        // migrate `$BUS summary` received
  summary:          {},
  elapsedTimer:     null,
  elapsedOffset:    0,        // seconds accumulated before the current timing segment (supports pause/resume)
  elapsedResumeTs:  null,     // Date when the current segment started
  validationStarted: false,   // true once validation activity arrives after migration summary
  validationComplete: false,  // true after validation completion event
};

const el = id => document.getElementById(id);
const NUM = n => (n == null ? '—' : Number(n).toLocaleString());

// ---------------------------------------------------------------------------
// Phase → sub matching
// ---------------------------------------------------------------------------
function _matchSub(phase) {
  if (!phase) return null;
  const p = String(phase).toLowerCase();
  for (const stage of STAGES) {
    for (const sub of stage.sub) {
      if (p === sub.id || p.includes(sub.id) || sub.id.includes(p)) {
        return { stageId: stage.id, subId: sub.id };
      }
    }
  }
  return null;
}

// ===========================================================================
// Apply snapshot (authoritative)
// ===========================================================================
function applySnapshot(snap) {
  if (!snap) return;

  const ctx = snap.run_ctx || {};
  if (ctx.project_name)    S.project  = ctx.project_name;
  if (ctx.conversion_type) S.convType = ctx.conversion_type;
  if (snap.start_ts && !S.startTs) { S.startTs = new Date(snap.start_ts); startElapsed(); }

  if (snap.file_counts) S.files = Object.assign({}, S.files, snap.file_counts);
  if (typeof snap.overall_pct === 'number') S.overallPct = snap.overall_pct;

  // Sub-phase status from authoritative phase list
  S.subStatus = {};
  for (const ph of (snap.phases || [])) {
    const m = _matchSub(ph.phase);
    if (!m) continue;
    if (ph.status === 'done')          S.subStatus[m.subId] = 'done';
    else if (ph.status === 'running' && S.subStatus[m.subId] !== 'done') S.subStatus[m.subId] = 'active';
  }

  S.activePhase = snap.active_phase || null;
  S.lastMsg = _latestMessage(snap);

  if (snap.scos)   S.scos   = snap.scos;
  if (snap.git)    S.git    = snap.git;
  if (snap.sqlite) S.sqlite = snap.sqlite;

  if (S.files.total == null) {
    S.files.total = (S.scos.inventory && S.scos.inventory.total) || S.scos.files_discovered || null;
  }

  if (snap.reports) {
    S.reports = [];
    const seen = new Set();
    for (const r of snap.reports) {
      if (r.file && !seen.has(r.file)) { seen.add(r.file); S.reports.push(r); }
    }
  }

  // Feed + validation_ep events
  for (const ev of (snap.feed || [])) mergeFeed(ev);

  for (const ev of (snap.feed || [])) {
    if (ev.type === 'error') {
      const m = _matchSub(ev.phase);
      if (m && S.subStatus[m.subId] !== 'done') S.subStatus[m.subId] = 'failed';
    }
  }

  if (snap.has_summary && !S.migrationDone) {
    S.migrationDone = true;
    S.summary = snap.summary || {};
    // Pause elapsed timer: save accumulated seconds so we can resume for validation.
    if (S.elapsedTimer) {
      clearInterval(S.elapsedTimer);
      S.elapsedTimer = null;
      if (S.elapsedResumeTs) {
        S.elapsedOffset += Math.max(0, Math.floor((Date.now() - S.elapsedResumeTs) / 1000));
        S.elapsedResumeTs = null;
      }
    }
  } else if (snap.has_summary) {
    S.summary = snap.summary || {};
  }

  if (snap.has_validation_complete) {
    S.validationComplete = true;
    S.validationStarted = true;
  }

  // Detect validation activity after migration summary → resume timer.
  if (S.migrationDone && !S.validationStarted) {
    const validationPhases = ['validation', 'survey', 'phase-a', 'phase-b', 'harvest', 'phase_a', 'phase_b'];
    const hasValidationPhase = (snap.phases || []).some(ph =>
      validationPhases.some(vp => String(ph.phase).toLowerCase().includes(vp))
    );
    const hasEpEvents = Object.keys(S.validationEps).length > 0;
    if (hasValidationPhase || hasEpEvents) {
      S.validationStarted = true;
      resumeElapsed();
    }
  }

  // Whole-run done: migration finished and either no validation ran, or validation finished.
  S.done = S.migrationDone && (!S.validationStarted || S.validationComplete);
  if (S.done) S.activePhase = null;
  else if (S.validationStarted) {
    // Keep showing the active validation phase from the snapshot.
    S.activePhase = snap.active_phase || S.activePhase;
  }

  renderAll();
}

function _latestMessage(snap) {
  const feed = snap.feed || [];
  for (let i = feed.length - 1; i >= 0; i--) {
    const ev = feed[i];
    if (ev.message && ['file_progress', 'agent_status', 'milestone', 'phase_start', 'validation_ep'].includes(ev.type)) {
      return ev.message;
    }
  }
  return snap.active_phase ? `Running ${snap.active_phase}…` : '';
}

function mergeFeed(ev) {
  if (ev == null) return;
  const seq = ev.seq != null ? ev.seq : (S.lastSeq + 1);
  if (S.feedMap[seq]) return;
  S.feedMap[seq] = ev;
  if (ev.seq != null) S.lastSeq = Math.max(S.lastSeq, ev.seq);

  // Merge validation_ep events into S.validationEps
  if (ev.type === 'validation_ep') {
    const d = ev.data || {};
    const epId = d.ep_id;
    if (!epId) return;
    if (!S.validationEps[epId]) S.validationEps[epId] = { phaseA: null, phaseB: null, lastMsg: '' };
    if (d.phase === 'a') S.validationEps[epId].phaseA = d.status;
    if (d.phase === 'b') S.validationEps[epId].phaseB = d.status;
    if (ev.message) S.validationEps[epId].lastMsg = ev.message;
    if (d.total_eps != null) S.validationTotal = d.total_eps;
    // Track which ep is currently running
    if (d.status === 'running') S.activeEp = epId;
    else if (S.activeEp === epId && d.status !== 'running') S.activeEp = null;
    if (S.migrationDone && !S.validationStarted) {
      S.validationStarted = true;
      resumeElapsed();
    }
  }

  // phase_start for validation also resumes the timer
  if (ev.type === 'phase_start' && S.migrationDone && !S.validationStarted) {
    const p = String(ev.phase || '').toLowerCase();
    if (['validation', 'survey', 'phase-a', 'phase-b', 'harvest'].some(k => p.includes(k))) {
      S.validationStarted = true;
      resumeElapsed();
    }
  }

  if (ev.type === 'summary' && (ev.data || {}).validation_complete) {
    S.validationComplete = true;
    S.validationStarted = true;
  }
}

// ===========================================================================
// Stage status derivation
// ===========================================================================
function computeStageStatuses() {
  const activeIdx = STAGES.findIndex(st => st.sub.some(s => S.subStatus[s.id] === 'active'));
  let maxSeen = -1;
  STAGES.forEach((st, i) => { if (st.sub.some(s => S.subStatus[s.id])) maxSeen = i; });

  return STAGES.map((st, i) => {
    // After migration summary, Assessment / Migration / Reports are done —
    // Validation stays live until it finishes (or never starts).
    if (S.migrationDone && st.id !== 'validation') return 'done';
    if (S.validationComplete && st.id === 'validation') return 'done';

    const hasFailed = st.sub.some(s => S.subStatus[s.id] === 'failed');
    const hasActive = st.sub.some(s => S.subStatus[s.id] === 'active');
    const hasDone   = st.sub.some(s => S.subStatus[s.id] === 'done');
    if (hasActive) return 'active';
    if (hasFailed) return 'failed';
    if (activeIdx >= 0) return i < activeIdx ? 'done' : (hasDone ? 'done' : 'pending');
    return i <= maxSeen ? 'done' : 'pending';
  });
}

// ===========================================================================
// Render
// ===========================================================================
function renderAll() {
  renderProject();
  renderStatus();
  renderOverall();
  renderTiles();
  renderActive();
  const stageStates = computeStageStatuses();
  renderStageNav(stageStates);
  renderTracker(stageStates);
  renderInsights();
  renderFeed();
  renderReports();
  renderDone();
}

// ── Project (sidebar) ──────────────────────────────────
function renderProject() {
  if (!S.project) return;
  el('sidebarProject').style.display = '';
  el('sidebarProjectName').textContent = S.project;
  el('sidebarProjectType').textContent = S.convType || '';

  const sub = el('pageSub');
  const bits = [];
  if (S.convType) bits.push(S.convType);
  if (S.files.total) bits.push(`${NUM(S.files.total)} files`);
  sub.textContent = bits.length ? bits.join('  ·  ') : 'Experimental live migration tracker';
}

// ── Status badge + sidebar status ──────────────────────
function renderStatus() {
  let state, label;
  if (S.done) {
    state = 'complete'; label = 'Complete';
  } else if (S.validationStarted) {
    state = 'running'; label = 'Validating';
  } else if (S.migrationDone) {
    state = 'complete'; label = 'Migrated';
  } else if (S.activePhase || S.overallPct > 0) {
    state = 'running'; label = 'Running';
  } else {
    state = 'pending'; label = 'Waiting';
  }

  const badge = el('statusBadge');
  badge.className = `status-badge ${state}`;
  badge.textContent = label;

  const sb = el('sidebarStatus');
  sb.className = `sidebar-status ${state}`;
  el('sidebarStatusText').textContent = label;

  const pill = el('livePill');
  if (S.done) { pill.className = 'live-pill off'; pill.textContent = 'ended'; }
  else if (S.migrationDone && !S.validationStarted) { pill.className = 'live-pill off'; pill.textContent = 'paused'; }
  else { pill.className = 'live-pill'; pill.textContent = 'live'; }
}

// ── Overall progress ───────────────────────────────────
function renderOverall() {
  const pct = S.done ? 100 : Math.max(0, Math.min(100, S.overallPct));
  const fill = el('overallFill');
  fill.style.width = pct + '%';
  fill.className = 'progress-fill ' + (S.done ? 'green' : (S.validationStarted ? 'blue' : (S.migrationDone ? 'green' : 'blue')));
  el('overallPct').textContent = pct + '%';

  const states = computeStageStatuses();
  const idx = states.findIndex(s => s === 'active');
  let cap;
  if (S.done) cap = 'All stages complete';
  else if (S.validationStarted) cap = idx >= 0 ? `Validating · ${STAGES[idx].label}` : 'Validation in progress…';
  else if (S.migrationDone) cap = 'Migration complete — validation optional';
  else if (idx >= 0) cap = `Stage ${idx + 1} of ${STAGES.length} · ${STAGES[idx].label}`;
  else if (pct > 0) cap = 'Between stages…';
  else cap = 'Not started';
  el('overallCaption').textContent = cap;
}

// ── Metric tiles ───────────────────────────────────────
function renderTiles() {
  const f = S.files;
  const tiles = [];

  tiles.push({ label: 'Files', value: f.total != null ? NUM(f.total) : (f.converted ? NUM(f.converted) : '—') });
  tiles.push({ label: 'Converted', value: NUM(f.converted), cls: 'blue' });
  tiles.push({ label: 'Verified',  value: NUM(f.verified),  cls: 'green' });

  const issues = S.scos.issues && S.scos.issues.total;
  if (issues != null) tiles.push({ label: 'Issues', value: NUM(issues), cls: issues > 0 ? 'yellow' : 'green' });

  if (f.failed > 0)   tiles.push({ label: 'Failed',   value: NUM(f.failed),   cls: 'red' });
  if (f.reverted > 0) tiles.push({ label: 'Reverted', value: NUM(f.reverted), cls: 'yellow' });

  // Validation tiles
  const eps = Object.values(S.validationEps);
  if (eps.length > 0) {
    const vPassed = eps.filter(e => e.phaseB === 'passed' || e.phaseB === 'passed_no_baseline').length;
    const vFailed = eps.filter(e => e.phaseB === 'failed' || e.phaseB === 'hard_stuck').length;
    tiles.push({ label: 'Val. passed', value: NUM(vPassed), cls: vPassed > 0 ? 'green' : '' });
    if (vFailed > 0) tiles.push({ label: 'Val. failed', value: NUM(vFailed), cls: 'red' });
  }

  const readiness = (S.sqlite && S.sqlite.readiness_pct) != null ? S.sqlite.readiness_pct
                  : (S.scos && S.scos.feasibility_score) != null ? S.scos.feasibility_score
                  : null;
  if (readiness != null) {
    const r = Math.round(readiness);
    tiles.push({ label: 'Readiness', value: `${r}<span class="sub">%</span>`, cls: r >= 80 ? 'green' : r >= 50 ? 'yellow' : 'red' });
  }

  const container = el('tiles');
  const prev = container._values || {};
  container.innerHTML = tiles.map(t =>
    `<div class="tile" data-k="${esc(t.label)}">
       <div class="label">${esc(t.label)}</div>
       <div class="value ${t.cls || ''}">${t.value}</div>
     </div>`
  ).join('');

  const next = {};
  container.querySelectorAll('.tile').forEach(node => {
    const k = node.dataset.k;
    const v = node.querySelector('.value').textContent;
    next[k] = v;
    if (prev[k] != null && prev[k] !== v) {
      node.classList.add('tile-flash');
      setTimeout(() => node.classList.remove('tile-flash'), 600);
    }
  });
  container._values = next;
}

// ── Now running card ───────────────────────────────────
function renderActive() {
  const card = el('activeCard');
  if (!S.activePhase || S.done) { card.style.display = 'none'; return; }
  card.style.display = '';

  const phaseName = _prettyPhase(S.activePhase);
  el('activePhase').textContent = phaseName;

  // Show active entrypoint if in validation
  let detail = S.lastMsg || `Running ${S.activePhase}…`;
  if (S.activeEp && (S.activePhase === 'phase-a' || S.activePhase === 'phase-b')) {
    const phaseLabel = S.activePhase === 'phase-a' ? 'Phase A' : 'Phase B';
    detail = `${phaseLabel}: validating  ${S.activeEp}`;
    const epData = S.validationEps[S.activeEp];
    if (epData && epData.lastMsg) detail = epData.lastMsg;
  }
  el('activeDetail').textContent = detail;

  // Show per-file progress bar inside active card if in validation
  const existing = card.querySelector('.active-val-prog');
  const eps = Object.values(S.validationEps);
  if (eps.length > 0 && S.validationTotal) {
    const terminal = eps.filter(e =>
      ['passed','passed_no_baseline','failed','hard_stuck','skipped'].includes(
        S.activePhase === 'phase-a' ? e.phaseA : e.phaseB)
    ).length;
    const total = S.validationTotal;
    const pct   = Math.round(terminal / total * 100);

    let prog = card.querySelector('.active-val-prog');
    if (!prog) {
      prog = document.createElement('div');
      prog.className = 'active-val-prog';
      card.querySelector('.active-body').appendChild(prog);
    }
    prog.innerHTML = `
      <div class="avp-bar-row">
        <div class="progress-bar"><div class="progress-fill blue" style="width:${pct}%"></div></div>
        <span class="avp-cap">${terminal} / ${total} entrypoints</span>
      </div>`;
  } else if (existing) {
    existing.remove();
  }
}

function _prettyPhase(p) {
  const m = _matchSub(p);
  if (m) {
    const stage = STAGES.find(s => s.id === m.stageId);
    const sub   = stage.sub.find(s => s.id === m.subId);
    return `${stage.label} — ${sub.label}`;
  }
  return p;
}

// ── Stage nav (sidebar) ────────────────────────────────
function renderStageNav(states) {
  const nav = el('stageNav');
  nav.innerHTML = STAGES.map((st, i) => {
    const status = states[i];
    const doneCount = st.sub.filter(s => S.subStatus[s.id] === 'done').length;
    const icon = status === 'done'   ? '<span class="msym">check</span>'
               : status === 'failed' ? '<span class="msym">close</span>'
               : `<span class="nav-num">${i + 1}</span>`;
    const meta = status === 'done' ? '' : `${doneCount}/${st.sub.length}`;
    return `<a class="nav-link ${status}" data-stage="${st.id}">
      <span class="nav-dot">${icon}</span>
      <span class="nav-label">${esc(st.label)}</span>
      <span class="nav-meta">${meta}</span>
    </a>`;
  }).join('');

  nav.querySelectorAll('.nav-link').forEach(link => {
    link.onclick = () => {
      const target = el('stage-' + link.dataset.stage);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };
  });
}

// ── Stage tracker (main) ───────────────────────────────
function renderTracker(states) {
  const tracker = el('tracker');
  tracker.innerHTML = STAGES.map((st, i) => {
    const status = states[i];
    const last   = i === STAGES.length - 1;

    const node = status === 'done'   ? '<span class="msym">check</span>'
               : status === 'failed' ? '<span class="msym">priority_high</span>'
               : `${i + 1}`;

    const statusLabel = { done: 'Done', active: 'Running', failed: 'Attention', pending: 'Pending' }[status];

    // sub-phase chips
    const subs = st.sub.map(s => {
      const ss  = S.subStatus[s.id];
      const cls = ss === 'done' ? 'done' : ss === 'active' ? 'active' : ss === 'failed' ? 'failed' : '';
      const ic  = ss === 'done'   ? '<span class="msym">check</span>'
                : ss === 'failed' ? '<span class="msym">close</span>' : '';
      return `<span class="tsub ${cls}"><span class="tsub-ic">${ic}</span>${esc(s.label)}</span>`;
    }).join('');

    // live message on the active stage
    const msg = (status === 'active' && S.lastMsg && st.id !== 'validation')
      ? `<div class="tstage-msg">${esc(S.lastMsg)}</div>` : '';

    // Migration stage: file progress bar
    let prog = '';
    if (st.id === 'migration' && (status === 'active' || status === 'done') && S.files.total) {
      const done = Math.min(S.files.total, S.files.converted + S.files.verified);
      const pct  = Math.round(done / S.files.total * 100);
      prog = `<div class="tstage-prog">
        <div class="progress-bar"><div class="progress-fill blue" style="width:${pct}%"></div></div>
        <div class="tstage-prog-cap">${NUM(S.files.converted)} converted · ${NUM(S.files.verified)} verified of ${NUM(S.files.total)}</div>
      </div>`;
    }

    // Validation stage: per-entrypoint Phase A/B grid
    let valGrid = '';
    if (st.id === 'validation' && (status === 'active' || status === 'done')) {
      valGrid = _renderValidationGrid(status);
    }

    return `<div class="tstage ${status}" id="stage-${st.id}">
      <div class="tstage-rail">
        <div class="tstage-node">${node}</div>
        ${last ? '' : '<div class="tstage-line"></div>'}
      </div>
      <div class="tstage-body">
        <div class="tstage-title-row">
          <span class="tstage-title">${esc(st.label)}</span>
          <span class="tstage-status ${status}">${statusLabel}</span>
        </div>
        ${msg}
        <div class="tsubs">${subs}</div>
        ${prog}
        ${valGrid}
      </div>
    </div>`;
  }).join('');
}

// ── Validation entrypoint grid ─────────────────────────
function _renderValidationGrid(stageStatus) {
  const eps = S.validationEps;
  const epIds = Object.keys(eps);

  // Overall validation progress bar
  const total = S.validationTotal || epIds.length || null;
  let overallBar = '';
  if (total) {
    const termA = epIds.filter(id => eps[id].phaseA && eps[id].phaseA !== 'pending' && eps[id].phaseA !== 'running').length;
    const termB = epIds.filter(id => ['passed','passed_no_baseline','failed','hard_stuck','skipped'].includes(eps[id].phaseB)).length;
    const done  = termB;
    const pct   = Math.round(done / total * 100);
    const cap   = stageStatus === 'done'
      ? `${done} of ${total} entrypoints validated`
      : `Phase B: ${done} / ${total} complete`;
    overallBar = `<div class="tstage-prog" style="margin-top:14px;max-width:none">
      <div class="progress-bar"><div class="progress-fill ${stageStatus === 'done' ? 'green' : 'blue'}" style="width:${pct}%"></div></div>
      <div class="tstage-prog-cap">${cap}</div>
    </div>`;
  }

  if (!epIds.length) return overallBar;

  const cards = epIds.map(epId => {
    const ep = eps[epId];
    const isActive = S.activeEp === epId;
    const aStatus  = ep.phaseA || 'pending';
    const bStatus  = ep.phaseB || 'pending';
    const aMeta    = EP_STATUS_META[aStatus] || { cls: '', label: aStatus };
    const bMeta    = EP_STATUS_META[bStatus] || { cls: '', label: bStatus };

    // Overall card state: failed > running > passed > pending
    const cardCls = isActive ? 'active'
      : (bStatus === 'failed' || bStatus === 'hard_stuck') ? 'failed'
      : (bStatus === 'passed' || bStatus === 'passed_no_baseline') ? 'passed'
      : (aStatus === 'running' || bStatus === 'running') ? 'running'
      : '';

    const msgHtml = isActive && ep.lastMsg
      ? `<div class="vep-msg">${esc(ep.lastMsg.replace(/^.*?:\s*/, ''))}</div>` : '';

    // Running phase indicator
    let runIndicator = '';
    if (aStatus === 'running') runIndicator = '<span class="vep-run-badge">Phase A</span>';
    else if (bStatus === 'running') runIndicator = '<span class="vep-run-badge">Phase B</span>';

    return `<div class="vep-card ${cardCls}">
      <div class="vep-id">${esc(epId)}${runIndicator}</div>
      <div class="vep-phases">
        <span class="vep-phase ${aMeta.cls}" title="Phase A — local Spark">A: ${esc(aMeta.label)}</span>
        <span class="vep-phase ${bMeta.cls}" title="Phase B — SCOS">B: ${esc(bMeta.label)}</span>
      </div>
      ${msgHtml}
    </div>`;
  }).join('');

  return `${overallBar}<div class="veps-grid">${cards}</div>`;
}

// ── Live feed ──────────────────────────────────────────
function renderFeed() {
  const feed = el('feed');
  const events = Object.values(S.feedMap)
    .filter(ev => ev.type !== 'metric')
    .sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))
    .slice(-80)
    .reverse();

  if (!events.length) { feed.innerHTML = '<div class="feed-empty">Waiting for activity…</div>'; return; }

  feed.innerHTML = events.map(ev => {
    const ts  = ev.ts ? ev.ts.slice(11, 19) : '';
    const wkr = ev.worker || '';
    const msg = ev.message || ev.type || '';
    const isVal = ev.type === 'validation_ep';
    const lvl = ev.level === 'milestone' || ev.type === 'phase_start' || ev.type === 'phase_end' ? 'milestone'
              : ev.type === 'error' || ev.level === 'error' ? 'error'
              : isVal && (ev.level === 'milestone') ? 'milestone' : '';
    const icon = lvl === 'milestone' ? '◆' : lvl === 'error' ? '✕' : isVal ? '⬡' : '·';
    return `<div class="feed-row ${lvl}">
      <span class="feed-ts">${esc(ts)}</span>
      ${wkr ? `<span class="feed-worker">${esc(wkr)}</span>` : ''}
      <span class="feed-icon">${icon}</span>
      <span class="feed-msg">${esc(msg)}</span>
    </div>`;
  }).join('');
}

// ── Reports ────────────────────────────────────────────
const REPORT_KIND_META = {
  assessment: { icon: 'summarize',   badge: 'Readiness', featured: true,  html: true },
  html:       { icon: 'summarize',   badge: 'Report',    featured: false, html: true },
  ir:         { icon: 'data_object', badge: 'IR (JSON)', featured: false, html: false },
  csv:        { icon: 'table_view',  badge: 'CSV',       featured: false, html: false },
};

function _reportMeta(r) {
  if (r.kind && REPORT_KIND_META[r.kind]) return REPORT_KIND_META[r.kind];
  const name = (r.file || '').split('/').pop();
  const isHtml = /\.html?$/i.test(name);
  return isHtml ? REPORT_KIND_META.html
       : /\.csv$/i.test(name) ? REPORT_KIND_META.csv
       : { icon: 'description', badge: '', featured: false, html: false };
}

function renderReports() {
  const box = el('reports');
  if (!S.reports.length) {
    box.innerHTML = '<div class="feed-empty">No reports generated yet. The readiness report appears here as soon as the assessment stage finishes.</div>';
    return;
  }
  box.innerHTML = S.reports.map(r => {
    const name  = r.file.split('/').pop();
    const label = r.label || name;
    const url   = `/file?path=${encodeURIComponent(r.file)}`;
    const meta  = _reportMeta(r);
    const encPath = encodeURIComponent(r.file);
    const encLabel = encodeURIComponent(label);
    // All files get a "View" button (HTML→iframe, text→pre); all get an open-in-tab link.
    const viewBtn = `<button class="report-preview-btn" onclick="openReportDrawer('${encPath}','${encLabel}',${meta.html})" title="View inline">
           <span class="msym">${meta.html ? 'open_in_full' : 'visibility'}</span>
         </button>`;
    const badge = meta.badge ? `<span class="report-badge">${esc(meta.badge)}</span>` : '';
    return `<div class="report-row ${meta.featured ? 'featured' : ''}">
      <span class="report-icon msym">${meta.icon}</span>
      <span class="report-name">${esc(label)}${badge}</span>
      <div class="report-actions">
        ${viewBtn}
        <a class="report-open-btn" href="${url}" target="_blank" title="Open in new tab">
          <span class="msym">open_in_new</span>
        </a>
      </div>
    </div>`;
  }).join('');
}

// ── Workload insights ──────────────────────────────────
function renderInsights() {
  const sec = el('insightsSection');
  if (!sec) return;

  const sc = S.scos || {};
  const hasData = sc.api_calls_found != null || sc.unsupported_apis != null ||
                  sc.issues != null || sc.dependency_edges != null ||
                  sc.files_discovered != null;

  if (!hasData) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  // Sub-heading: when was this assessed?
  const sub = el('insightsSub');
  if (sub) sub.textContent = 'From the assessment phase — read-only snapshot of the original workload.';

  const body = el('insightsBody');
  if (!body) return;

  // ── Stat rows ──
  const stats = [];

  if (sc.files_discovered != null)
    stats.push({ label: 'Source files', value: NUM(sc.files_discovered), icon: 'folder_open' });

  if (sc.api_calls_found != null)
    stats.push({ label: 'Spark API calls', value: NUM(sc.api_calls_found), icon: 'code' });

  if (sc.unsupported_apis != null) {
    const pct = sc.api_calls_found ? Math.round(sc.unsupported_apis / sc.api_calls_found * 100) : null;
    const sub = pct != null ? `<span class="insight-sub">${pct}% of calls</span>` : '';
    const cls = sc.unsupported_apis > 0 ? 'warn' : 'ok';
    stats.push({ label: 'Unsupported APIs', value: NUM(sc.unsupported_apis) + sub, cls, icon: 'warning' });
  }

  if (sc.dependency_edges != null)
    stats.push({ label: 'Dependency edges', value: NUM(sc.dependency_edges), icon: 'account_tree' });

  let html = '';

  if (stats.length) {
    html += `<div class="insight-stats">${stats.map(s =>
      `<div class="insight-stat ${s.cls || ''}">
        <span class="msym insight-stat-icon">${s.icon}</span>
        <div class="insight-stat-body">
          <div class="insight-stat-label">${esc(s.label)}</div>
          <div class="insight-stat-value">${s.value}</div>
        </div>
      </div>`
    ).join('')}</div>`;
  }

  // ── Issues breakdown ──
  const issues = sc.issues;
  if (issues && issues.total > 0) {
    const cats = Object.entries(issues.by_category || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6);                      // top 6 categories
    const totalIssues = issues.total;

    html += `<div class="insight-issues">
      <div class="insight-issues-head">
        <span class="msym insight-issues-icon">error_outline</span>
        <span class="insight-issues-title">${NUM(totalIssues)} EWI issue${totalIssues !== 1 ? 's' : ''} found</span>
      </div>`;

    if (cats.length) {
      html += `<div class="insight-cats">${cats.map(([cat, n]) => {
        const pct = Math.round(n / totalIssues * 100);
        const barW = Math.max(4, pct);
        return `<div class="insight-cat-row">
          <span class="insight-cat-name" title="${esc(cat)}">${esc(cat)}</span>
          <div class="insight-cat-bar-wrap">
            <div class="insight-cat-bar" style="width:${barW}%"></div>
          </div>
          <span class="insight-cat-count">${n}</span>
        </div>`;
      }).join('')}</div>`;
    }

    html += `</div>`;
  }

  body.innerHTML = html || '<p class="feed-empty">Assessment data not yet available.</p>';
}

// ── Report drawer ──────────────────────────────────────
async function openReportDrawer(encodedPath, encodedName, isHtml) {
  if (typeof closeChat === 'function') closeChat();
  const drawer = el('reportDrawer');
  const frame  = el('reportFrame');
  const pre    = el('reportPre');
  const title  = el('reportDrawerTitle');
  title.textContent = decodeURIComponent(encodedName);
  drawer.classList.add('open');
  document.body.classList.add('drawer-open');

  if (isHtml) {
    frame.style.display = '';
    if (pre) pre.style.display = 'none';
    frame.src = `/file?path=${encodedPath}`;
  } else {
    frame.style.display = 'none';
    frame.src = 'about:blank';
    if (pre) {
      pre.style.display = '';
      pre.textContent = 'Loading…';
      try {
        const resp = await fetch(`/file?path=${encodedPath}`);
        pre.textContent = resp.ok ? await resp.text() : `Could not load file (HTTP ${resp.status})`;
      } catch (_) {
        pre.textContent = 'Could not load file.';
      }
    }
  }
}

function closeReportDrawer() {
  const drawer = el('reportDrawer');
  const frame  = el('reportFrame');
  const pre    = el('reportPre');
  drawer.classList.remove('open');
  // Only remove the shared overlay class if chat is also closed.
  const chatOpen = el('chatDrawer') && el('chatDrawer').classList.contains('open');
  if (!chatOpen) document.body.classList.remove('drawer-open');
  frame.src = 'about:blank';
  if (pre) pre.textContent = '';
}

// ── Completion summary ─────────────────────────────────
function renderDone() {
  const sec = el('doneSection');
  // Show after migration finishes (even while validation is still optional / running).
  if (!S.migrationDone) { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const titleEl = sec.querySelector('.done-head h2');
  if (titleEl) {
    titleEl.textContent = S.validationComplete ? 'Migration & validation complete'
                      : S.validationStarted ? 'Migration complete — validating…'
                      : 'Migration complete';
  }

  const s = S.summary || {};
  const entries = Object.entries(s).filter(([k]) => k !== 'validation_complete');
  if (entries.length) {
    el('doneStats').innerHTML = entries.map(([k, v]) =>
      `<div class="tile"><div class="label">${esc(_labelize(k))}</div><div class="value">${esc(String(v))}</div></div>`
    ).join('');
  } else {
    el('doneStats').innerHTML = '';
  }

  const parts = [];
  if (S.files.total)    parts.push(`${NUM(S.files.total)} files`);
  if (S.files.verified) parts.push(`${NUM(S.files.verified)} verified`);

  // Validation summary
  const eps = Object.values(S.validationEps);
  if (eps.length > 0) {
    const vPass = eps.filter(e => e.phaseB === 'passed' || e.phaseB === 'passed_no_baseline').length;
    parts.push(`${vPass}/${eps.length} validated`);
  }

  if (S.validationComplete) {
    el('doneSub').textContent = parts.length ? parts.join(' · ') + ' — all stages finished.' : 'All stages finished.';
  } else if (S.validationStarted) {
    el('doneSub').textContent = parts.length ? parts.join(' · ') + ' — validation running.' : 'Validation running…';
  } else {
    el('doneSub').textContent = parts.length ? parts.join(' · ') + ' — migration complete.' : 'Migration complete.';
  }

  // Validation hint / status
  const ctaBox = el('doneCta');
  if (!ctaBox) return;
  if (S.validationComplete) {
    ctaBox.innerHTML = `<div class="done-validation-running">
      <span class="msym">verified</span>
      <span>Validation finished — see the Validation stage above for results.</span>
    </div>`;
  } else if (S.validationStarted) {
    ctaBox.innerHTML = `<div class="done-validation-running">
      <span class="msym">verified</span>
      <span>Validation is running — see the Validation stage above for live progress.</span>
    </div>`;
  } else {
    ctaBox.innerHTML = `<div class="done-hint">
      <span class="msym done-hint-icon">tips_and_updates</span>
      <span><strong>Optional:</strong> ask the agent to validate the migrated workload end-to-end with synthetic data.</span>
    </div>`;
  }
}

function _labelize(k) {
  return String(k).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Elapsed timer (supports pause/resume for migration → validation gap)
// ---------------------------------------------------------------------------
function _elapsedTick() {
  const segSecs = S.elapsedResumeTs
    ? Math.max(0, Math.floor((Date.now() - S.elapsedResumeTs) / 1000))
    : 0;
  const diff = S.elapsedOffset + segSecs;
  const h = String(Math.floor(diff / 3600)).padStart(2, '0');
  const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
  const s = String(diff % 60).padStart(2, '0');
  el('sidebarElapsed').textContent = `${h}:${m}:${s} elapsed`;
}

function startElapsed() {
  if (S.elapsedTimer || !S.startTs) return;
  el('sidebarElapsed').style.display = '';
  S.elapsedResumeTs = S.startTs;
  _elapsedTick();
  S.elapsedTimer = setInterval(_elapsedTick, 1000);
}

function resumeElapsed() {
  if (S.elapsedTimer) return;
  S.elapsedResumeTs = new Date();
  el('sidebarElapsed').style.display = '';
  _elapsedTick();
  S.elapsedTimer = setInterval(_elapsedTick, 1000);
}

// ---------------------------------------------------------------------------
// SSE + polling
// ---------------------------------------------------------------------------
let _refreshTimer = null;
function scheduleRefresh(delay = 250) {
  if (_refreshTimer) return;
  _refreshTimer = setTimeout(() => { _refreshTimer = null; poll(); }, delay);
}

async function poll() {
  try {
    const r = await fetch('/api/snapshot');
    if (r.ok) applySnapshot(await r.json());
  } catch (_) {}
}

function connectSSE() {
  const es = new EventSource(`/events?since=${S.lastSeq}`);
  es.onmessage = e => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    mergeFeed(ev);
    renderFeed();
    renderActive();   // update active-file display immediately
    scheduleRefresh();
  };
  es.onerror = () => { es.close(); setTimeout(connectSSE, 2500); };
}

// ---------------------------------------------------------------------------
// Escape
// ---------------------------------------------------------------------------
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Chat — grounded Q&A over the live run (Cortex COMPLETE via /api/chat)
// ---------------------------------------------------------------------------
const CHAT = { messages: [], busy: false, abort: null };

const CHAT_SUGGESTIONS = [
  "What's the current status?",
  'Did any files fail, and why?',
  'What happens in the validation phase?',
];

function toggleChat() {
  const open = el('chatDrawer').classList.contains('open');
  if (open) closeChat(); else openChat();
}

function openChat() {
  // Chat and the report drawer are mutually exclusive.
  if (typeof closeReportDrawer === 'function') closeReportDrawer();
  el('chatDrawer').classList.add('open');
  el('chatFab').classList.add('hidden');
  document.body.classList.add('drawer-open');
  setTimeout(() => el('chatInput') && el('chatInput').focus(), 260);
}

function closeChat() {
  // Cancel any in-flight request so the drawer doesn't stay locked.
  if (CHAT.abort) { CHAT.abort.abort(); CHAT.abort = null; }
  CHAT.busy = false;
  el('chatDrawer').classList.remove('open');
  el('chatFab').classList.remove('hidden');
  // Only drop the shared overlay if the report drawer isn't also open.
  const reportOpen = el('reportDrawer') && el('reportDrawer').classList.contains('open');
  if (!reportOpen) document.body.classList.remove('drawer-open');
  renderChatMessages(false);
}

function cancelChat() {
  if (CHAT.abort) { CHAT.abort.abort(); CHAT.abort = null; }
  CHAT.busy = false;
  const send = el('chatSend');
  if (send) send.disabled = false;
  renderChatMessages(false);
  renderChatCancel();
}

function renderChatSuggest() {
  const box = el('chatSuggest');
  if (!box) return;
  // Hide suggestions once a conversation has started.
  if (CHAT.messages.length) { box.innerHTML = ''; return; }
  box.innerHTML = CHAT_SUGGESTIONS.map(q =>
    `<button class="chat-chip" type="button">${esc(q)}</button>`
  ).join('');
  box.querySelectorAll('.chat-chip').forEach((chip, i) => {
    chip.onclick = () => sendChat(CHAT_SUGGESTIONS[i]);
  });
}

function renderChatCancel() {
  const btn = el('chatCancel');
  if (!btn) return;
  btn.style.display = CHAT.busy ? '' : 'none';
}

// ---------------------------------------------------------------------------
// Lightweight markdown → safe HTML (escape first, then apply a tiny subset)
// Supports: **bold**, *italic*, `code`, ```fences```, lists, paragraphs, links.
// ---------------------------------------------------------------------------
function _mdInline(escaped) {
  // Inline code first so we don't format inside backticks.
  let s = escaped.replace(/`([^`\n]+)`/g, '<code class="chat-code">$1</code>');
  // Bold **...** or __...__
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  // Italic *...* or _..._ (avoid matching already-bold leftovers)
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');
  // Autolink bare https URLs (already HTML-escaped so &amp; etc. are fine)
  s = s.replace(
    /(https?:\/\/[^\s<]+)/g,
    '<a class="chat-link" href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
  );
  return s;
}

function formatChatMarkdown(raw) {
  const text = String(raw == null ? '' : raw).replace(/\r\n/g, '\n');
  // Split on fenced code blocks so we don't format inside them.
  const parts = text.split(/(```[\s\S]*?```)/);
  let html = '';

  for (const part of parts) {
    if (/^```[\s\S]*```$/.test(part)) {
      const inner = part.replace(/^```[^\n]*\n?/, '').replace(/```$/, '');
      html += `<pre class="chat-pre"><code>${esc(inner.trimEnd())}</code></pre>`;
      continue;
    }

    const lines = part.split('\n');
    let i = 0;
    let para = [];
    let listType = null; // 'ul' | 'ol'
    let listItems = [];

    const flushPara = () => {
      if (!para.length) return;
      // Keep soft line breaks inside a paragraph so stacked labels stay readable.
      html += `<p>${para.map(line => _mdInline(esc(line))).join('<br>')}</p>`;
      para = [];
    };
    const flushList = () => {
      if (!listItems.length) return;
      const tag = listType || 'ul';
      html += `<${tag}>${listItems.map(li =>
        `<li>${_mdInline(esc(li))}</li>`
      ).join('')}</${tag}>`;
      listItems = [];
      listType = null;
    };

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        flushPara();
        flushList();
        i++;
        continue;
      }

      const ul = trimmed.match(/^[-*•]\s+(.+)$/);
      if (ul) {
        flushPara();
        if (listType && listType !== 'ul') flushList();
        listType = 'ul';
        listItems.push(ul[1]);
        i++;
        continue;
      }

      const ol = trimmed.match(/^\d+[.)]\s+(.+)$/);
      if (ol) {
        flushPara();
        if (listType && listType !== 'ol') flushList();
        listType = 'ol';
        listItems.push(ol[1]);
        i++;
        continue;
      }

      flushList();
      // Start a fresh paragraph when the line looks like a bold label heading.
      if (para.length && /^\*\*[^*]+\*\*/.test(trimmed)) flushPara();
      para.push(trimmed);
      i++;
    }
    flushPara();
    flushList();
  }

  return html || `<p>${esc(text)}</p>`;
}

function renderChatMessages(typing) {
  const box = el('chatMessages');
  if (!box) return;
  const intro = CHAT.messages.length ? '' :
    `<div class="chat-intro">
       <div class="chat-intro-icon msym">forum</div>
       <p>Ask about this run's progress, why a file failed, or how any
          part of the Snowpark&nbsp;Connect migration &amp; validation process works.</p>
     </div>`;
  const bubbles = CHAT.messages.map(m => {
    const isUser = m.role === 'user';
    const cls = isUser ? 'user' : (m.error ? 'error' : 'assistant');
    const body = isUser || m.error
      ? esc(m.content)
      : formatChatMarkdown(m.content);
    const avatar = isUser
      ? ''
      : `<div class="chat-avatar ${m.error ? 'err' : ''}" aria-hidden="true">
           <span class="msym">${m.error ? 'error' : 'smart_toy'}</span>
         </div>`;
    return `<div class="chat-row ${cls}">
      ${avatar}
      <div class="chat-msg ${cls}">${body}</div>
    </div>`;
  }).join('');
  const typer = typing
    ? `<div class="chat-row assistant">
         <div class="chat-avatar" aria-hidden="true"><span class="msym">smart_toy</span></div>
         <div class="chat-typing"><span></span><span></span><span></span></div>
       </div>` : '';
  box.innerHTML = intro + bubbles + typer;
  box.scrollTop = box.scrollHeight;
  renderChatCancel();
}

// Map raw backend errors to a friendlier, actionable message.
function _friendlyChatError(msg) {
  const m = String(msg || '').toLowerCase();
  if (m.includes('403') || m.includes('forbidden') || m.includes('not authorized') || m.includes('unauthorized'))
    return "This Snowflake account isn't entitled to Cortex LLM inference, so the assistant can't answer. Progress tracking is unaffected.";
  if (m.includes('could not open a snowflake session') || m.includes('could not open a snowflake connection'))
    return "Couldn't open a Snowflake session for the assistant. If you use browser SSO, complete the login prompt once — later messages reuse that session.";
  if (m.includes('uv is not installed'))
    return "The assistant needs `uv` installed on this host. Progress tracking still works without it.";
  if (m.includes('too long'))
    return "The assistant took too long to respond. Please try again.";
  if (m.includes('chat worker failed to start'))
    return "Couldn't start the chat assistant. Check the Progress UI server log for details.";
  return msg || 'The assistant is unavailable right now.';
}

async function sendChat(text) {
  const input = el('chatInput');
  const raw = (text != null ? text : (input ? input.value : '')).trim();
  if (!raw || CHAT.busy) return;

  CHAT.busy = true;
  CHAT.abort = new AbortController();
  CHAT.messages.push({ role: 'user', content: raw });
  if (input) { input.value = ''; input.style.height = 'auto'; }
  renderChatSuggest();
  renderChatMessages(true);
  el('chatSend').disabled = true;

  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: CHAT.messages }),
      signal: CHAT.abort.signal,
    });
    const data = r.ok ? await r.json() : { error: `request failed (${r.status})` };
    if (data.answer) CHAT.messages.push({ role: 'assistant', content: data.answer });
    else CHAT.messages.push({ role: 'assistant', error: true,
      content: _friendlyChatError(data.error) });
  } catch (err) {
    if (err && err.name === 'AbortError') {
      // User cancelled — remove the pending user message too for cleanliness.
      CHAT.messages.pop();
    } else {
      CHAT.messages.push({ role: 'assistant', error: true,
        content: 'Could not reach the assistant. Check your connection and retry.' });
    }
  } finally {
    CHAT.busy = false;
    CHAT.abort = null;
    const send = el('chatSend');
    if (send) send.disabled = false;
    renderChatMessages(false);
    if (input) input.focus();
  }
}

function initChat() {
  renderChatSuggest();
  renderChatMessages(false);

  const input  = el('chatInput');
  const send   = el('chatSend');
  const cancel = el('chatCancel');
  if (send)   send.onclick   = () => sendChat();
  if (cancel) cancel.onclick = () => cancelChat();
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });
    input.addEventListener('input', () => {   // auto-grow
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    });
  }
  // Esc closes whichever drawer is open.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (el('chatDrawer').classList.contains('open')) closeChat();
    else if (el('reportDrawer') && el('reportDrawer').classList.contains('open')
             && typeof closeReportDrawer === 'function') closeReportDrawer();
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
renderAll();
initChat();
poll().then(connectSSE);
setInterval(poll, 3000);
