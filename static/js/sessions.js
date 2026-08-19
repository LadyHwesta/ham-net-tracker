// ============================================================
// SESSIONS
// ============================================================
async function openNet(netId) {
  closeSidebar();
  currentNetId = netId;
  currentSessionId = null;
  const net = nets.find(n => n.id === netId);
  currentNetOwnerId = net ? net.owner_id : null;
  currentNetIsAres = net ? !!net.is_ares : false;
  currentNetIsGmrs = net ? net.net_type === 'gmrs' : false;
  // Pre-fill default TG from net settings (ham only)
  const netDefaultTg = (!currentNetIsGmrs && net) ? (net.dmr_talkgroup || '') : '';
  document.getElementById('ci-dmr-tg').value = netDefaultTg;
  document.getElementById('session-net-name').textContent = net ? net.name : 'Net';

  // Show/hide ARES-specific UI elements (ham only)
  document.getElementById('ci-zone-group').style.display = currentNetIsAres ? '' : 'none';
  document.getElementById('zone-roster-panel').style.display = currentNetIsAres ? '' : 'none';
  document.getElementById('traffic-log-panel').style.display = currentNetIsAres ? '' : 'none';
  document.getElementById('th-zone').style.display = currentNetIsAres ? '' : 'none';

  // Hide DMR panel entirely for GMRS nets
  document.getElementById('dmr-heard-panel').style.display = currentNetIsGmrs ? 'none' : '';

  // Stash the raw script text; it's rendered (with variable substitution) per-session
  // in loadSessionLive, since {{net_control}}/{{broadcaster}} depend on that session's duty.
  currentNetScript = (net && net.script && net.script.trim()) ? net.script : null;

  // Update callsign input placeholder to match net type
  const ciCall = document.getElementById('ci-call');
  if (ciCall) ciCall.placeholder = currentNetIsGmrs ? 'WSMC512' : 'W1AW or suffix';

  // Load evac zones for this net
  evacZones = {};
  if (currentNetIsAres) {
    try {
      const zones = await apiFetch(`/nets/${netId}/evac-zones`);
      zones.forEach(z => { evacZones[z.callsign] = z.zone; });
      populateKnownZonesList();
    } catch {}
  }
  document.getElementById('nav-history').style.display = '';
  document.getElementById('live-session-panel').style.display = 'none';
  document.getElementById('sessions-list-container').style.display = '';
  document.getElementById('session-tab-bar').style.display = '';
  showView('session');
  switchSubTab('sessions');
  await loadSessions();
}

function toggleNetScriptPanel() {
  const body = document.getElementById('net-script-body');
  const icon = document.getElementById('net-script-toggle-icon');
  const open = body.style.display === 'none';
  body.style.display = open ? '' : 'none';
  icon.textContent = open ? '▼' : '▶';
}

// ============================================================
// NET SCRIPT — {{variable}} substitution + basic markup rendering
// ============================================================
// Supported syntax (deliberately small — this is rendered via innerHTML, so every
// value that reaches the page must go through escapeHtml first):
//   **bold**   *italic*
//   # / ## / ###  headings
//   - item  or  * item   bullet list
//   ---  or  ===  (3+ chars, alone on a line)   horizontal rule
//   {{net_control}} {{net_control_callsign}} {{net_control_name}}
//   {{broadcaster}} {{broadcaster_callsign}} {{broadcaster_name}} {{broadcast_label}}
//   {{net_control_next}} {{net_control_next_callsign}} {{net_control_next_name}}
//   {{broadcaster_next}} {{broadcaster_next_callsign}} {{broadcaster_next_name}}
//   {{net_name}}

function escapeHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function combineNameCallsign(callsign, name) {
  return callsign ? (name ? `${name} — ${callsign}` : callsign) : '';
}

function scriptVarsForSession(s) {
  const net = nets.find(n => n.id === currentNetId);
  return {
    net_name: net ? net.name : '',
    net_control: combineNameCallsign(s.ncs_callsign, s.ncs_name),
    net_control_callsign: s.ncs_callsign || '',
    net_control_name: s.ncs_name || '',
    broadcaster: combineNameCallsign(s.broadcaster_callsign, s.broadcaster_name),
    broadcaster_callsign: s.broadcaster_callsign || '',
    broadcaster_name: s.broadcaster_name || '',
    broadcast_label: s.broadcast_label || 'Broadcaster',
    net_control_next: combineNameCallsign(s.next_ncs_callsign, s.next_ncs_name),
    net_control_next_callsign: s.next_ncs_callsign || '',
    net_control_next_name: s.next_ncs_name || '',
    broadcaster_next: combineNameCallsign(s.next_broadcaster_callsign, s.next_broadcaster_name),
    broadcaster_next_callsign: s.next_broadcaster_callsign || '',
    broadcaster_next_name: s.next_broadcaster_name || '',
  };
}

// Bold/italic — applied to text that is already HTML-escaped.
function scriptInlineFormat(s) {
  return s
    .replace(/\*\*([^\n*]+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^\n*]+?)\*/g, '<em>$1</em>');
}

const SCRIPT_HEADING_STYLES = [
  'font-size:16px;color:var(--lc-orange);font-weight:700;margin:10px 0 4px;letter-spacing:.03em',
  'font-size:14px;color:var(--lc-orange);font-weight:700;margin:8px 0 4px;letter-spacing:.03em',
  'font-size:13px;color:var(--lc-blue);font-weight:700;margin:6px 0 2px;letter-spacing:.03em',
];

// Headings / rules / bullet lists — line-based, applied to already-escaped text.
function scriptBlockFormat(escapedText) {
  const lines = escapedText.split('\n');
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };

  for (const line of lines) {
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    const rule = /^(-{3,}|={3,})\s*$/.test(line);

    if (bullet) {
      if (!inList) { out.push('<ul style="margin:4px 0 4px 20px;padding:0">'); inList = true; }
      out.push(`<li style="margin:2px 0">${scriptInlineFormat(bullet[1])}</li>`);
      continue;
    }
    closeList();

    if (heading) {
      out.push(`<div style="${SCRIPT_HEADING_STYLES[heading[1].length - 1]}">${scriptInlineFormat(heading[2])}</div>`);
    } else if (rule) {
      out.push('<hr style="border:none;border-top:1px solid var(--lc-blue);margin:8px 0;opacity:.5">');
    } else {
      out.push(scriptInlineFormat(line));
    }
  }
  closeList();
  return out.join('\n');
}

function renderNetScript(session) {
  const panel = document.getElementById('net-script-panel');
  if (!currentNetScript) { panel.style.display = 'none'; return; }

  const vars = scriptVarsForSession(session);
  let text = escapeHtml(currentNetScript);
  // Substitute after escaping so {{...}} values (callsign/name) can't inject markup.
  text = text.replace(/\{\{\s*(\w+)\s*\}\}/g, (match, key) => (key in vars ? escapeHtml(vars[key]) : match));

  document.getElementById('net-script-text').innerHTML = scriptBlockFormat(text);
  document.getElementById('net-script-body').style.display = '';
  document.getElementById('net-script-toggle-icon').textContent = '▼';
  panel.style.display = '';
}

function renderDutyBar(s) {
  const bar = document.getElementById('duty-bar');
  const ncEl = document.getElementById('duty-nc');
  const bcEl = document.getElementById('duty-bc');
  if (s.ncs_callsign) {
    ncEl.querySelector('strong').textContent = s.ncs_name ? `${s.ncs_callsign} — ${s.ncs_name}` : s.ncs_callsign;
    ncEl.style.display = '';
  } else {
    ncEl.style.display = 'none';
  }
  if (s.broadcaster_callsign) {
    bcEl.querySelector('.duty-bc-label').textContent = s.broadcast_label || 'Broadcaster';
    bcEl.querySelector('strong').textContent = s.broadcaster_name ? `${s.broadcaster_callsign} — ${s.broadcaster_name}` : s.broadcaster_callsign;
    bcEl.style.display = '';
  } else {
    bcEl.style.display = 'none';
  }
  bar.style.display = (s.ncs_callsign || s.broadcaster_callsign) ? '' : 'none';
}

async function loadSessions() {
  let sessions = [];
  try { sessions = await apiFetch(`/nets/${currentNetId}/sessions`); } catch {}
  renderSessions(sessions);
}

function renderSessions(sessions) {
  const el = document.getElementById('sessions-list-container');
  if (sessions.length === 0) {
    el.innerHTML = '<div class="empty"><p>No sessions yet. Start one above.</p></div>';
    return;
  }
  el.innerHTML = `<table class="tbl"><thead><tr>
    <th>Session</th><th>Started</th><th class="col-sess-ended">Ended</th><th class="col-sess-checkins">Check-ins</th><th>Status</th><th></th>
  </tr></thead><tbody>` +
  sessions.map(s => {
    const label = s.name ? esc(s.name) : `Session ${s.id}`;
    return `<tr>
      <td>
        <a href="#" onclick="loadSessionLive(${s.id}); return false;" style="color:var(--accent-hover);text-decoration:none">${label}</a>
        <button class="btn btn-ghost btn-sm" style="margin-left:6px;font-size:11px;padding:1px 7px" onclick="promptRenameSession(${s.id}, ${JSON.stringify(s.name || '')})">✏️</button>
      </td>
      <td>${fmt(s.started_at)}</td>
      <td class="col-sess-ended">${fmt(s.ended_at)}</td>
      <td class="col-sess-checkins"><span class="badge badge-blue">${s.checkin_count}</span></td>
      <td>${s.ended_at ? '<span class="badge badge-gray">Ended</span>' : '<span class="badge badge-green">Live</span>'}</td>
      <td><button class="btn btn-danger btn-sm" onclick="deleteSession(${s.id})">Delete</button></td>
    </tr>`;
  }).join('') + '</tbody></table>';
}

function toggleStartSessionForm() {
  const f = document.getElementById('start-session-form');
  f.style.display = f.style.display === 'none' ? '' : 'none';
  if (f.style.display !== 'none') document.getElementById('new-session-name').focus();
}

async function startSession() {
  const name = document.getElementById('new-session-name').value.trim() || null;
  try {
    const s = await apiFetch(`/nets/${currentNetId}/sessions`, { method: 'POST', body: JSON.stringify({ name }) });
    document.getElementById('new-session-name').value = '';
    document.getElementById('start-session-form').style.display = 'none';
    toast('Session started');
    await loadSessions();
    await loadSessionLive(s.id);
  } catch (e) { toast(e.message, 'error'); }
}

async function promptRenameSession(id, currentName) {
  const name = prompt('Session name:', currentName);
  if (name === null) return; // cancelled
  try {
    await apiFetch(`/sessions/${id}/rename`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() || null }) });
    toast('Session renamed');
    await loadSessions();
    if (currentSessionId === id) await loadSessionLive(id);
  } catch (e) { toast(e.message, 'error'); }
}

async function loadSessionLive(sessionId) {
  currentSessionId = sessionId;
  // Reset expected stations list for fresh session
  expectedStations = [];
  lastKnownCheckins = [];
  pendingTrafficCallsigns.clear();
  document.getElementById('expected-list').innerHTML = '<p class="text-muted" style="font-size:12px;margin:0">Set filter and click Load List to see previously active stations.</p>';
  document.getElementById('expected-panel-body').style.display = 'none';
  document.getElementById('expected-toggle-icon').textContent = '▶';
  document.getElementById('traffic-banner').style.display = 'none';
  try {
    const s = await apiFetch(`/sessions/${sessionId}`);
    const ended = !!s.ended_at;
    document.getElementById('live-session-panel').style.display = '';
    // Hide the Sessions/Schedule tabs and the sessions toolbar while a session is live
    // to cut clutter; restore them once it ends (helpful when reviewing a closed session).
    document.getElementById('session-tab-bar').style.display = ended ? '' : 'none';
    document.getElementById('sessions-panel').style.display = ended ? '' : 'none';
    document.getElementById('schedule-panel').style.display = 'none';
    if (ended) {
      document.getElementById('sub-tab-sessions').classList.add('active');
      document.getElementById('sub-tab-schedule').classList.remove('active');
    } else {
      collapseSidebar();
    }
    document.getElementById('session-status-dot').className = 'status-dot' + (ended ? ' ended' : '');
    const sessionLabel = s.name ? s.name : `Session ${sessionId}`;
    document.getElementById('session-status-label').textContent = `${sessionLabel} — ${ended ? 'Ended' : 'Live'}`;
    document.getElementById('session-meta').textContent = `Started ${fmt(s.started_at)}${ended ? ' · Ended ' + fmt(s.ended_at) : ''}`;
    document.getElementById('end-session-btn').style.display = ended ? 'none' : '';
    document.getElementById('checkin-form-area').style.display = ended ? 'none' : '';
    renderDutyBar(s);
    renderNetScript(s);
    if (!ended) startClock(s.started_at); else stopClock();
    trafficMessages = [];
    renderTrafficMessages();
    await loadCheckins();
    await loadTrafficMessages();
    // DMR: init or stop polling based on session state
    if (!ended) {
      await initDmrForSession(currentNetId);
    } else {
      stopDmrPolling();
      document.getElementById('dmr-heard-panel').style.display = 'none';
      document.getElementById('ci-dmr-tg-group').style.display = 'none';
      document.getElementById('ci-dmr-region-group').style.display = 'none';
    }
  } catch (e) { toast(e.message, 'error'); }
}

async function endSession() {
  if (!confirm('End this session? No more check-ins can be added after ending.')) return;
  try {
    const sid = currentSessionId;
    await apiFetch(`/sessions/${sid}/end`, { method: 'PATCH', body: '{}' });
    toast('Session ended');
    stopClock();
    stopDmrPolling();
    currentSessionId = null;
    document.getElementById('live-session-panel').style.display = 'none';
    document.getElementById('sessions-list-container').style.display = '';
    document.getElementById('session-tab-bar').style.display = '';
    document.getElementById('sessions-panel').style.display = '';
    await loadSessions();
    // Show session summary
    try {
      const summary = await apiFetch(`/sessions/${sid}/summary`);
      showSessionSummary(summary);
    } catch {}
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteSession(id) {
  if (!confirm('Delete this session and all its check-ins?')) return;
  try {
    await apiFetch(`/sessions/${id}`, { method: 'DELETE' });
    toast('Session deleted');
    if (currentSessionId === id) {
      document.getElementById('live-session-panel').style.display = 'none';
      document.getElementById('sessions-list-container').style.display = '';
      currentSessionId = null;
    }
    await loadSessions();
  } catch (e) { toast(e.message, 'error'); }
}


// CSV EXPORT
// ============================================================
function triggerDownload(url) {
  fetch(url, { headers: { Authorization: 'Bearer ' + token } })
    .then(res => {
      const cd = res.headers.get('content-disposition') || '';
      const match = cd.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : 'export.csv';
      return res.blob().then(blob => ({ blob, filename }));
    })
    .then(({ blob, filename }) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
    })
    .catch(e => toast('Export failed: ' + e.message, 'error'));
}


// SESSION CLOCK & TIMER
// ============================================================
let sessionStartedAt = null;   // Date object, set when session loads
let clockInterval = null;

function startClock(startedAt) {
  sessionStartedAt = startedAt ? new Date(startedAt) : null;
  const bar = document.getElementById('session-clock-bar');
  bar.style.display = 'flex';
  if (clockInterval) clearInterval(clockInterval);
  clockInterval = setInterval(tickClock, 1000);
  tickClock();
}

function stopClock() {
  if (clockInterval) { clearInterval(clockInterval); clockInterval = null; }
  const bar = document.getElementById('session-clock-bar');
  if (bar) bar.style.display = 'none';
}

function tickClock() {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  document.getElementById('clk-local').textContent =
    `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  document.getElementById('clk-utc').textContent =
    `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
  if (sessionStartedAt) {
    const elapsed = Math.floor((now - sessionStartedAt) / 1000);
    const h = Math.floor(elapsed / 3600);
    const m = Math.floor((elapsed % 3600) / 60);
    const s = elapsed % 60;
    document.getElementById('clk-elapsed').textContent =
      h > 0 ? `${h}h ${pad(m)}m ${pad(s)}s` : `${pad(m)}m ${pad(s)}s`;
  }
}


// ============================================================
// SESSION SUMMARY MODAL
// ============================================================
function showSessionSummary(summary) {
  const existing = document.getElementById('session-summary-modal');
  if (existing) existing.remove();

  const dur = summary.duration_minutes != null
    ? `${Math.floor(summary.duration_minutes / 60)}h ${summary.duration_minutes % 60}m`
    : '—';

  const modal = document.createElement('div');
  modal.id = 'session-summary-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px';
  modal.innerHTML = `
    <div class="session-summary-card">
      <h2 style="margin:0 0 4px;color:var(--lc-orange);font-size:18px">Session Complete</h2>
      <p style="margin:0 0 20px;color:var(--text-muted);font-size:13px">${esc(summary.net_name)}</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">
        <div style="background:var(--bg);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:28px;font-weight:700;color:var(--lc-blue)">${summary.total_checkins}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Total Check-Ins</div>
        </div>
        <div style="background:var(--bg);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:28px;font-weight:700;color:var(--lc-orange)">${dur}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px">Duration</div>
        </div>
        <div style="background:var(--bg);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:28px;font-weight:700;color:var(--lc-red)">${summary.traffic_count}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px">With Traffic</div>
        </div>
        <div style="background:var(--bg);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:28px;font-weight:700;color:var(--success)">${summary.new_stations}</div>
          <div style="font-size:11px;color:var(--text-muted);margin-top:2px">New Stations</div>
        </div>
      </div>
      ${summary.net_frequency ? `<p style="font-size:12px;color:var(--text-muted);margin:0 0 16px">Frequency: ${esc(summary.net_frequency)}</p>` : ''}
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="openICS205(${summary.session_id})">📄 ICS-205 / Net Log</button>
        <button class="btn btn-ghost" onclick="exportSessionById(${summary.session_id})">⬇ CSV Export</button>
        <button class="btn btn-ghost" onclick="document.getElementById('session-summary-modal').remove()">Close</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

async function openICS205(sessionId) {
  try {
    const res = await fetch(`/sessions/${sessionId}/ics205`, {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!res.ok) throw new Error('Failed to load net log (' + res.status + ')');
    const html = await res.text();
    const blob = new Blob([html], { type: 'text/html' });
    window.open(URL.createObjectURL(blob), '_blank');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function exportSessionById(sessionId) {
  apiFetch(`/sessions/${sessionId}/export`)
    .then(data => {
      const blob = new Blob([data], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `session_${sessionId}.csv`; a.click();
    })
    .catch(e => toast('Export failed: ' + e.message, 'error'));
}
