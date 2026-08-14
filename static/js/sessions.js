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
  // Pre-fill default TG from net settings
  const netDefaultTg = net ? (net.dmr_talkgroup || '') : '';
  document.getElementById('ci-dmr-tg').value = netDefaultTg;
  document.getElementById('session-net-name').textContent = net ? net.name : 'Net';

  // Show/hide ARES-specific UI elements
  document.getElementById('ci-zone-group').style.display = currentNetIsAres ? '' : 'none';
  document.getElementById('zone-roster-panel').style.display = currentNetIsAres ? '' : 'none';
  document.getElementById('traffic-log-panel').style.display = currentNetIsAres ? '' : 'none';
  document.getElementById('th-zone').style.display = currentNetIsAres ? '' : 'none';

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
  showView('session');
  switchSubTab('sessions');
  await loadSessions();
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
    <th>Session</th><th>Started</th><th>Ended</th><th>Check-ins</th><th>Status</th><th></th>
  </tr></thead><tbody>` +
  sessions.map(s => {
    const label = s.name ? esc(s.name) : `Session ${s.id}`;
    return `<tr>
      <td>
        <a href="#" onclick="loadSessionLive(${s.id}); return false;" style="color:var(--accent-hover);text-decoration:none">${label}</a>
        <button class="btn btn-ghost btn-sm" style="margin-left:6px;font-size:11px;padding:1px 7px" onclick="promptRenameSession(${s.id}, ${JSON.stringify(s.name || '')})">✏️</button>
      </td>
      <td>${fmt(s.started_at)}</td>
      <td>${fmt(s.ended_at)}</td>
      <td><span class="badge badge-blue">${s.checkin_count}</span></td>
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
  calledTrafficCallsigns.clear();
  document.getElementById('expected-list').innerHTML = '<p class="text-muted" style="font-size:12px;margin:0">Set filter and click Load List to see previously active stations.</p>';
  document.getElementById('expected-panel-body').style.display = 'none';
  document.getElementById('expected-toggle-icon').textContent = '▶';
  document.getElementById('traffic-banner').style.display = 'none';
  try {
    const s = await apiFetch(`/sessions/${sessionId}`);
    const ended = !!s.ended_at;
    document.getElementById('live-session-panel').style.display = '';
    document.getElementById('session-status-dot').className = 'status-dot' + (ended ? ' ended' : '');
    const sessionLabel = s.name ? s.name : `Session ${sessionId}`;
    document.getElementById('session-status-label').textContent = `${sessionLabel} — ${ended ? 'Ended' : 'Live'}`;
    document.getElementById('session-meta').textContent = `Started ${fmt(s.started_at)}${ended ? ' · Ended ' + fmt(s.ended_at) : ''}`;
    document.getElementById('end-session-btn').style.display = ended ? 'none' : '';
    document.getElementById('checkin-form-area').style.display = ended ? 'none' : '';
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
    await loadSessions();
    await loadSessionLive(sid);
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
    <div style="background:var(--surface);border:2px solid var(--lc-orange);border-radius:12px;padding:28px;max-width:480px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,.6)">
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
