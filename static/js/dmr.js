// ============================================================
// DMR INTEGRATION
// ============================================================
let currentDmrConfig = null;
let dmrHeardInterval = null;
let dmrPanelOpen = false;
let dmrHeardEntries = [];   // keyed by render index for onclick handlers

function onDmrSourceChange() {
  const src = document.getElementById('dmr-source').value;
  document.getElementById('dmr-hotspot-fields').style.display = (src === 'wpsd' || src === 'pistar') ? '' : 'none';
  document.getElementById('dmr-bm-fields').style.display      = src === 'brandmeister' ? '' : 'none';
}

async function loadDmrConfig(netId) {
  try {
    currentDmrConfig = await apiFetch(`/nets/${netId}/dmr/config`);
  } catch { currentDmrConfig = null; }
  // Show/hide DMR fields in net edit form
  const sec = document.getElementById('net-dmr-section');
  if (sec) sec.style.display = '';  // always show when editing own net
  if (currentDmrConfig) {
    document.getElementById('dmr-source').value = currentDmrConfig.source_type;
    onDmrSourceChange();
    document.getElementById('dmr-url').value       = currentDmrConfig.hotspot_url || '';
    document.getElementById('dmr-direct').checked  = !!currentDmrConfig.direct_mode;
    document.getElementById('dmr-tg').value        = currentDmrConfig.talkgroup_id || '';
    document.getElementById('dmr-filter').value    = currentDmrConfig.filter_callsign || '';
    document.getElementById('dmr-delete-btn').style.display = '';
    document.getElementById('dmr-relay-btn').style.display  = '';
    document.getElementById('dmr-relay-note').style.display = '';
  } else {
    document.getElementById('dmr-source').value = 'none';
    onDmrSourceChange();
    document.getElementById('dmr-url').value    = '';
    document.getElementById('dmr-direct').checked = false;
    document.getElementById('dmr-tg').value     = '';
    document.getElementById('dmr-filter').value = currentUser ? currentUser.callsign : '';
    document.getElementById('dmr-delete-btn').style.display = 'none';
    document.getElementById('dmr-relay-btn').style.display  = 'none';
    document.getElementById('dmr-relay-note').style.display = 'none';
  }
}

async function saveDmrConfig() {
  const src = document.getElementById('dmr-source').value;
  if (src === 'none') { await deleteDmrConfig(); return; }
  const payload = {
    source_type:     src,
    hotspot_url:     document.getElementById('dmr-url').value.trim() || null,
    talkgroup_id:    parseInt(document.getElementById('dmr-tg').value) || null,
    filter_callsign: document.getElementById('dmr-filter').value.trim().toUpperCase() || null,
    direct_mode:     document.getElementById('dmr-direct').checked,
  };
  try {
    currentDmrConfig = await apiFetch(`/nets/${editNetId}/dmr/config`, { method: 'PUT', body: JSON.stringify(payload) });
    document.getElementById('dmr-delete-btn').style.display = '';
    document.getElementById('dmr-relay-btn').style.display = '';
    document.getElementById('dmr-relay-note').style.display = '';
    toast('DMR config saved');
  } catch (e) { toast(e.message, 'error'); }
}


async function deleteDmrConfig() {
  try {
    await apiFetch(`/nets/${editNetId}/dmr/config`, { method: 'DELETE' });
    currentDmrConfig = null;
    document.getElementById('dmr-source').value = 'none';
    onDmrSourceChange();
    document.getElementById('dmr-url').value = '';
    document.getElementById('dmr-tg').value  = '';
    document.getElementById('dmr-delete-btn').style.display = 'none';
    toast('DMR integration removed');
  } catch (e) { toast(e.message, 'error'); }
}

async function initDmrForSession(netId) {
  // Load config; show/hide DMR panel and check-in fields accordingly
  try { currentDmrConfig = await apiFetch(`/nets/${netId}/dmr/config`); }
  catch { currentDmrConfig = null; }

  const panel = document.getElementById('dmr-heard-panel');
  const tgGrp = document.getElementById('ci-dmr-tg-group');
  const rgGrp = document.getElementById('ci-dmr-region-group');

  if (currentDmrConfig) {
    panel.style.display = '';
    tgGrp.style.display = '';
    rgGrp.style.display = '';
    startDmrPolling(netId);
  } else {
    panel.style.display = 'none';
    tgGrp.style.display = 'none';
    rgGrp.style.display = 'none';
    stopDmrPolling();
  }
}

function stopDmrPolling() {
  if (dmrHeardInterval) { clearInterval(dmrHeardInterval); dmrHeardInterval = null; }
}

function startDmrPolling(netId) {
  stopDmrPolling();
  refreshDmrHeard(netId);
  dmrHeardInterval = setInterval(() => refreshDmrHeard(netId), 30000);
}

async function refreshDmrHeard(netIdArg) {
  const netId = netIdArg || currentNetId;
  if (!currentDmrConfig) return;

  let entries = [];
  try {
    if (currentDmrConfig.direct_mode && currentDmrConfig.hotspot_url) {
      // Fetch directly from browser (local network access)
      let url = currentDmrConfig.hotspot_url.trim();
      if (currentDmrConfig.source_type === 'pistar' && !url.includes('lastheard')) {
        url = url.replace(/\/$/, '') + '/api/local/lastheard';
      } else if (currentDmrConfig.source_type === 'wpsd') {
        // Ensure URL ends at the /api/ path
        if (!url.includes('/api')) url = url.replace(/\/$/, '') + '/api/';
        url += (url.includes('?') ? '&' : '?') + 'limit=30&names=true&country=true';
      }
      let directOk = false;
      try {
        const r = await fetch(url, { mode: 'cors' });
        if (!r.ok) throw new Error(`HTTP ${r.status} from hotspot`);
        const raw = await r.json();
        entries = (Array.isArray(raw) ? raw : []).map(e => ({
          callsign:   (e.callsign || '').toUpperCase(),
          dmr_id:     String(e.src || e.id || ''),
          name:       e.name || null,
          talk_group: String(e.dst || ''),
          timeslot:   e.slot ? `TS${e.slot}` : null,
          region:     e.country || null,
          heard_at:   e.start || null,
          duration:   e.duration ? String(e.duration) : null,
        }));
        directOk = true;
      } catch (_directErr) {
        // Direct fetch failed (CORS, mixed content, unreachable) — fall back to backend proxy
      }

      if (!directOk) {
        let proxyOk = false;
        try {
          entries = await apiFetch(`/nets/${netId}/dmr/lastheard`);
          proxyOk = true;
        } catch (_proxyErr) { /* fall through to relay cache */ }

        if (!proxyOk) {
          // Final fallback: relay cache (populated by dmr_relay.py running on the LAN)
          try {
            const cached = await apiFetch(`/nets/${netId}/dmr/cache`);
            entries = cached.entries || [];
            const age = cached.age_seconds;
            // Render immediately with age note, then return
            if (entries.length === 0 || !entries.some(e => e.callsign)) {
              document.getElementById('dmr-last-refresh').textContent = 'Relay is running but no stations heard yet.';
            } else {
              const filter = (currentDmrConfig.filter_callsign || (currentUser && currentUser.callsign) || '').toUpperCase();
              if (filter) entries = entries.filter(e => e.callsign !== filter);
              renderDmrHeard(entries);
              const cnt = document.getElementById('dmr-heard-count');
              if (entries.length > 0) { cnt.textContent = entries.length; cnt.style.display = ''; }
              else cnt.style.display = 'none';
            }
            document.getElementById('dmr-last-refresh').textContent = `Via relay script (${age}s ago)`;
            return;
          } catch (_cacheErr) {
            // All three paths failed
            document.getElementById('dmr-last-refresh').innerHTML =
              '<span style="color:var(--lc-orange)">⚠ Could not reach hotspot</span> — ' +
              'direct fetch (CORS) and server proxy both failed, and no relay data is available. ' +
              'Run <strong>dmr_relay.py</strong> on a machine that can reach the hotspot ' +
              '(download it from the net\'s DMR config section).';
            return;
          }
        }
      }
    } else {
      // Proxy mode: server fetches the hotspot
      try {
        entries = await apiFetch(`/nets/${netId}/dmr/lastheard`);
      } catch (proxyErr) {
        const msg = proxyErr.message || 'Unknown error';
        document.getElementById('dmr-last-refresh').innerHTML =
          `<span style="color:var(--lc-orange)">⚠ Proxy fetch failed</span> — ${esc(msg)}. ` +
          'In proxy mode the <em>server</em> fetches your hotspot — it must be reachable from the server ' +
          '(local LAN addresses like 192.168.x.x won\'t work from a remote server). ' +
          'Enable <strong>Direct mode</strong> if your browser can reach the hotspot instead.';
        return;
      }
    }
  } catch (e) {
    document.getElementById('dmr-last-refresh').textContent = `Error: ${e.message}`;
    return;
  }

  // Filter out NCS callsign
  const skip = (currentDmrConfig.filter_callsign || (currentUser && currentUser.callsign) || '').toUpperCase();
  if (skip) entries = entries.filter(e => e.callsign !== skip);

  renderDmrHeard(entries);
  const now = new Date().toLocaleTimeString();
  document.getElementById('dmr-last-refresh').textContent = `Last refresh: ${now}`;
  const cnt = document.getElementById('dmr-heard-count');
  if (entries.length > 0) { cnt.textContent = entries.length; cnt.style.display = ''; }
  else cnt.style.display = 'none';
}

function renderDmrHeard(entries) {
  dmrHeardEntries = entries;
  const el = document.getElementById('dmr-heard-list');
  if (entries.length === 0) {
    el.innerHTML = '<p class="text-muted" style="font-size:12px;margin:0">No stations heard recently.</p>';
    return;
  }
  el.innerHTML = `<table class="tbl" style="font-size:12px"><thead><tr>
    <th>Callsign</th><th>Name</th><th>Talk Group</th><th>Region</th><th>Heard</th><th></th>
  </tr></thead><tbody>` +
  entries.map((e, i) => {
    const already = lastKnownCheckins && lastKnownCheckins.some(c => c.callsign === e.callsign);
    const btnClass = already ? 'btn-ghost' : 'btn-success';
    const btnText  = already ? '✓ Checked In' : '+ Check In';
    const dur = e.duration ? ` <span class="text-muted">${esc(e.duration)}s</span>` : '';
    return `<tr style="${already ? 'opacity:.55' : ''}">
      <td><span class="callsign">${esc(e.callsign)}</span>${e.dmr_id ? ` <span class="text-muted" style="font-size:10px">${esc(e.dmr_id)}</span>` : ''}</td>
      <td>${e.name ? esc(e.name) : '<span class="text-muted">—</span>'}</td>
      <td>${e.talk_group ? esc(e.talk_group) : '—'}${e.timeslot ? ` <span class="text-muted">${esc(e.timeslot)}</span>` : ''}</td>
      <td>${e.region ? esc(e.region) : '—'}</td>
      <td style="white-space:nowrap">${e.heard_at ? esc(String(e.heard_at).slice(11,19) || e.heard_at) : '—'}${dur}</td>
      <td><button class="btn ${btnClass} btn-sm" style="font-size:11px;padding:1px 8px" ${already ? 'disabled' : `onclick="dmrQuickCheckin(${i})"`}>${btnText}</button></td>
    </tr>`;
  }).join('') + '</tbody></table>';
}

function dmrQuickCheckin(index) {
  const entry = dmrHeardEntries[index];
  if (!entry) return;
  const net = nets.find(n => n.id === currentNetId);
  document.getElementById('ci-call').value        = entry.callsign || '';
  document.getElementById('ci-name').value        = entry.name || '';
  // Net-level TG is the canonical default; fall back to what the station was heard on
  document.getElementById('ci-dmr-tg').value      = (net && net.dmr_talkgroup) || entry.talk_group || '';
  document.getElementById('ci-dmr-region').value  = entry.region || '';
  document.getElementById('ci-sig').value         = '';
  document.getElementById('ci-call').focus();
  document.getElementById('checkin-form-area').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function toggleDmrPanel() {
  dmrPanelOpen = !dmrPanelOpen;
  document.getElementById('dmr-panel-body').style.display = dmrPanelOpen ? '' : 'none';
  document.getElementById('dmr-panel-toggle-icon').textContent = dmrPanelOpen ? '▼' : '▶';
  if (dmrPanelOpen) refreshDmrHeard();
}

onEnter(['dmr-url', 'dmr-tg', 'dmr-filter'], saveDmrConfig);

