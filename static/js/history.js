// ============================================================
// HISTORY
// ============================================================
async function loadHistory(netId) {
  // Populate the net selector with all known nets
  const sel = document.getElementById('history-net-select');
  sel.innerHTML = nets.map(n =>
    `<option value="${n.id}" ${n.id === netId ? 'selected' : ''}>${esc(n.name)}</option>`
  ).join('');

  currentNetId = netId;
  document.getElementById('history-search').value = '';
  document.getElementById('history-filter').value = 'all';
  try {
    historyData = await apiFetch(`/nets/${netId}/history?limit=1000`);
  } catch { historyData = []; }
  renderHistory(historyData);
}

function onHistoryNetChange() {
  const netId = parseInt(document.getElementById('history-net-select').value);
  if (netId) loadHistory(netId);
}

const historyLookupCache = {};  // separate cache for history view

function renderHistory(rows) {
  const tbody = document.getElementById('history-tbody');
  const empty = document.getElementById('history-empty');
  if (rows.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = rows.map(r => {
    const cached = historyLookupCache[r.callsign];
    const licenseCell = cached
      ? buildLicensePills(cached)
      : `<button class="btn btn-ghost btn-sm" onclick="lookupHistoryRow('${esc(r.callsign)}')">🔍</button>`;
    const lastNetBadge = r.checked_in_last_session
      ? `<span class="badge badge-green">✓</span>`
      : `<span class="badge badge-gray">—</span>`;
    const recent2wBadge = r.recent_checkins > 0
      ? `<span class="badge badge-green">${r.recent_checkins}</span>`
      : `<span class="text-muted" style="font-size:11px">—</span>`;
    const recent4wBadge = r.recent_4w_checkins > 0
      ? `<span class="badge badge-blue">${r.recent_4w_checkins}</span>`
      : `<span class="text-muted" style="font-size:11px">—</span>`;
    return `<tr id="hist-row-${esc(r.callsign)}">
      <td><span class="callsign">${esc(r.callsign)}</span></td>
      <td id="hist-name-${esc(r.callsign)}">${esc(r.name || '—')}</td>
      <td id="hist-lic-${esc(r.callsign)}">${licenseCell}</td>
      <td style="text-align:center">${lastNetBadge}</td>
      <td style="text-align:center">${recent2wBadge}</td>
      <td style="text-align:center">${recent4wBadge}</td>
      <td style="text-align:center"><span class="badge badge-orange">${r.total_checkins}</span></td>
      <td class="text-muted" style="font-size:11px">${fmt(r.last_checkin)}</td>
    </tr>`;
  }).join('');
}

function buildLicensePills(result) {
  if (result.status !== 'found') return '<span class="lookup-notfound" style="font-size:11px">Not found</span>';
  const parts = [];
  if (result.license_class) parts.push(`<span class="lookup-pill lookup-pill-class">${esc(result.license_class)}</span>`);
  if (result.state)         parts.push(`<span class="lookup-pill lookup-pill-state">${esc(result.state)}</span>`);
  if (result.grid)          parts.push(`<span class="lookup-pill lookup-pill-grid">${esc(result.grid)}</span>`);
  return parts.join(' ') || '<span class="text-muted" style="font-size:11px">—</span>';
}

async function lookupHistoryRow(callsign) {
  const licEl = document.getElementById(`hist-lic-${callsign}`);
  const nameEl = document.getElementById(`hist-name-${callsign}`);
  if (!licEl) return;
  licEl.innerHTML = '<span class="lookup-spinner"></span>';
  try {
    const result = await apiFetch(`/callsign/${encodeURIComponent(callsign)}/lookup`);
    historyLookupCache[callsign] = result;
    licEl.innerHTML = buildLicensePills(result);
    if (result.status === 'found' && result.name && nameEl && nameEl.textContent === '—') {
      nameEl.textContent = result.name;
    }
  } catch {
    licEl.innerHTML = '<span class="lookup-notfound" style="font-size:11px">Error</span>';
  }
}

async function lookupAllHistory() {
  for (const r of historyData) {
    if (!historyLookupCache[r.callsign]) {
      await lookupHistoryRow(r.callsign);
      await new Promise(res => setTimeout(res, 150)); // small delay between requests
    }
  }
}

function filterHistory() {
  const q      = document.getElementById('history-search').value.toLowerCase();
  const filter = document.getElementById('history-filter').value;

  let rows = historyData;

  // Apply dropdown filter
  switch (filter) {
    case 'last_net':    rows = rows.filter(r => r.checked_in_last_session); break;
    case 'missed_last': rows = rows.filter(r => !r.checked_in_last_session); break;
    case 'active_2w':   rows = rows.filter(r => r.recent_checkins > 0); break;
    case 'regular_4w':  rows = rows.filter(r => r.recent_4w_checkins >= 2); break;
    case 'frequent_4w': rows = rows.filter(r => r.recent_4w_checkins >= 3); break;
    case 'inactive_4w': rows = rows.filter(r => r.recent_4w_checkins === 0); break;
  }

  // Apply text search on top of dropdown filter
  if (q) {
    rows = rows.filter(r =>
      r.callsign.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q)
    );
  }

  renderHistory(rows);
}

function downloadHistoryCSV() {
  if (!historyData.length) return toast('No history to download', 'error');
  const net = nets.find(n => n.id === currentNetId);
  const netName = net ? net.name : 'net';

  // Include FCC lookup data if already fetched
  const rows = [
    ['Callsign', 'Name', 'License Class', 'State', 'Grid', 'Past 14 Days', 'Total Check-ins', 'Last Check-in']
  ];
  for (const r of historyData) {
    const cached = historyLookupCache[r.callsign];
    rows.push([
      r.callsign,
      r.name || '',
      cached?.license_class || '',
      cached?.state || '',
      cached?.grid || '',
      r.recent_checkins,
      r.total_checkins,
      r.last_checkin ? new Date(r.last_checkin).toISOString() : '',
    ]);
  }

  const csv = rows.map(row =>
    row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')
  ).join('\r\n');

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `history_${netName.replace(/\s+/g, '_')}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function historyBack() {
  if (currentNetId) {
    openNet(currentNetId);
  } else {
    showView('nets');
  }
}

// ============================================================
// CSV EXPORT
// ============================================================
function exportSession() {
  if (!currentSessionId) return;
  window.open(`${API}/sessions/${currentSessionId}/export?token=${token}`, '_blank');
  // Workaround: fetch with auth header and trigger download
  apiFetch(`/sessions/${currentSessionId}/export`).catch(() => {});
  triggerDownload(`${API}/sessions/${currentSessionId}/export`);
}

function exportNet() {
  if (!currentNetId) return;
  triggerDownload(`${API}/nets/${currentNetId}/export`);
}

