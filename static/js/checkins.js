// ============================================================
// CALLSIGN LOOKUP
// ============================================================
const lookupCache = {};  // callsign → result, avoids repeat API calls per session
let lastLookedUpCallsign = null;  // guards against a redundant re-lookup wiping an open remark editor

async function lookupCallsign(callsign) {
  if (!callsign || callsign.length < 3) { clearLookupInfo(); return; }
  lastLookedUpCallsign = callsign;
  if (lookupCache[callsign]) { applyLookupResult(lookupCache[callsign]); return; }

  setLookupInfo('<span class="lookup-spinner"></span><span class="text-muted" style="font-size:11px">Looking up…</span>');
  try {
    const result = await apiFetch(`/callsign/${encodeURIComponent(callsign)}/lookup`);
    lookupCache[callsign] = result;
    applyLookupResult(result);
  } catch {
    clearLookupInfo();
  }
}

function remarkPillText(data) {
  const parts = [];
  if (data && data.preferred_name) parts.push('👤 ' + data.preferred_name);
  if (data && data.remark) parts.push('📝 ' + data.remark);
  return parts.length ? parts.join('   ') : '+ Name/Remark';
}

function renderRemarkPill(callsign, data) {
  const info = document.getElementById('ci-lookup-info');
  if (!info) return;
  const existing = document.getElementById('remark-pill');
  if (existing) existing.remove();
  const pill = document.createElement('span');
  pill.id = 'remark-pill';
  pill.style.cssText = 'display:inline-flex;align-items:center;gap:5px;margin-left:6px;font-size:11px;cursor:pointer;background:rgba(255,204,0,0.15);border:1px solid rgba(255,204,0,0.4);border-radius:4px;padding:1px 7px;color:var(--lc-orange)';
  pill.title = 'Click to set a preferred name and/or remark for this station';
  pill.innerHTML = `<span>${esc(remarkPillText(data))}</span>`;
  pill.onclick = () => showRemarkEditor(callsign, data);
  info.appendChild(pill);
}

function applyLookupResult(result) {
  if (result.status !== 'found') {
    const notFoundMsg = currentNetIsGmrs
      ? 'Not found in GMRS database'
      : 'Not found in FCC database';
    setLookupInfo(`<span class="lookup-notfound">${notFoundMsg}</span>`);
    // Preferred name/remark isn't tied to a successful FCC/GMRS lookup — always
    // offer the pill so a station missing from the database can still get one.
    const cs = document.getElementById('ci-call').value.trim().toUpperCase();
    if (cs) loadStationRemarks(cs).then(data => renderRemarkPill(cs, data));
    return;
  }

  // Auto-fill name if empty (from the FCC/GMRS lookup — preferred name doesn't
  // override this; it only overrides Expected Stations and reports)
  const nameEl = document.getElementById('ci-name');
  const noteEl = document.getElementById('ci-name-autofill-note');
  if (result.name && !nameEl.value.trim()) {
    nameEl.value = result.name;
    noteEl.style.display = '';
  }

  // Build info pills
  const parts = [];
  if (result.name) parts.push(`<span class="lookup-name">${esc(result.name)}</span>`);
  if (result.license_class) parts.push(`<span class="lookup-pill lookup-pill-class">${esc(result.license_class)}</span>`);
  if (result.state)         parts.push(`<span class="lookup-pill lookup-pill-state">${esc(result.state)}</span>`);
  if (result.grid)          parts.push(`<span class="lookup-pill lookup-pill-grid">${esc(result.grid)}</span>`);
  setLookupInfo(parts.join(' '));
  // Load and display station remark / preferred name
  const callsign = result.callsign || document.getElementById('ci-call').value.trim().toUpperCase();
  loadStationRemarks(callsign).then(data => renderRemarkPill(callsign, data));
}

function setLookupInfo(html) {
  document.getElementById('ci-lookup-info').innerHTML = html;
}

function clearLookupInfo() {
  document.getElementById('ci-lookup-info').innerHTML = '';
  document.getElementById('ci-name-autofill-note').style.display = 'none';
  lastLookedUpCallsign = null;
}

// ============================================================
// CHECKINS
// ============================================================
async function addCheckin() {
  const callsign = document.getElementById('ci-call').value.trim().toUpperCase();
  const name = document.getElementById('ci-name').value.trim() || null;
  const signal_report = document.getElementById('ci-sig').value.trim() || null;
  const comments = document.getElementById('ci-comments').value.trim() || null;
  const has_traffic = document.getElementById('ci-traffic').checked;
  const evac_zone = currentNetIsAres ? (document.getElementById('ci-zone').value.trim() || null) : null;
  const currentNet = nets.find(n => n.id === currentNetId);
  const dmr_talkgroup = document.getElementById('ci-dmr-tg').value.trim()
    || (currentNet && currentNet.dmr_talkgroup)
    || null;
  const dmr_region = document.getElementById('ci-dmr-region').value.trim() || null;
  if (!callsign) { toast('Callsign required', 'error'); document.getElementById('ci-call').focus(); return; }
  const payload = { callsign, name, signal_report, comments, has_traffic, evac_zone, dmr_talkgroup, dmr_region };
  try {
    const created = await apiFetch(`/sessions/${currentSessionId}/checkins`, { method: 'POST', body: JSON.stringify(payload) });
    _clearCheckinForm();
    toast(`${callsign} checked in`, 'success');
    markRecentCheckin(created.id);
    await loadCheckins();
    renderExpectedList();   // refresh expected list to show new checkin state
  } catch (e) {
    if (e instanceof TypeError) {
      // fetch couldn't reach the server at all — offline. Queue it rather
      // than lose the check-in; static/js/offline-queue.js replays it
      // automatically once back online (see the online listener below).
      await queueCheckin(currentSessionId, payload, token);
      _registerBackgroundSync();
      _clearCheckinForm();
      toast(`${callsign} queued — offline, will send automatically`, 'success');
      await refreshOfflineQueueBanner();
    } else {
      toast(e.message, 'error');
    }
  }
}

function _clearCheckinForm() {
  document.getElementById('ci-call').value = '';
  document.getElementById('ci-name').value = '';
  document.getElementById('ci-sig').value = '';
  document.getElementById('ci-comments').value = '';
  document.getElementById('ci-traffic').checked = false;
  if (currentNetIsAres) document.getElementById('ci-zone').value = '';
  // Keep TG populated (usually same for whole session), clear region
  document.getElementById('ci-dmr-region').value = '';
  clearLookupInfo();
  document.getElementById('ci-call').focus();
}

// ============================================================
// OFFLINE CHECK-IN QUEUE — banner + retry wiring (issue #9)
// ============================================================
async function _registerBackgroundSync() {
  // Best-effort extra layer, not the primary guarantee — see static/sw.js
  // header comment. Silently skipped on browsers without Background Sync
  // (notably Safari/iOS); the online-listener below covers everyone.
  if (!('serviceWorker' in navigator) || !('SyncManager' in window)) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    await reg.sync.register('sync-checkins');
  } catch { /* unsupported or registration failed — foreground retry still works */ }
}

async function refreshOfflineQueueBanner() {
  const banner = document.getElementById('offline-queue-banner');
  if (!banner || !currentSessionId) return;
  const pending = await getPendingCheckins(currentSessionId);
  if (pending.length === 0) { banner.style.display = 'none'; return; }
  banner.style.display = '';
  const failedCount = pending.filter(p => p.status === 'failed').length;
  const waitingCount = pending.length - failedCount;
  document.getElementById('offline-queue-summary').textContent =
    (waitingCount ? `⏳ ${waitingCount} check-in${waitingCount !== 1 ? 's' : ''} waiting to sync` : '')
    + (failedCount ? `${waitingCount ? ' · ' : ''}⚠ ${failedCount} failed` : '');
  document.getElementById('offline-queue-list').innerHTML = pending.map(p => {
    if (p.status === 'failed') {
      return `<div style="display:flex;align-items:center;gap:8px;margin-top:3px">
        <span class="callsign" style="color:var(--lc-red)">${esc(p.payload.callsign)}</span>
        <span class="text-muted" style="font-size:11px">${esc(p.last_error || 'failed')}</span>
        <button class="btn btn-ghost btn-sm" onclick="dismissFailedCheckin('${p.id}')" style="margin-left:auto">Dismiss</button>
      </div>`;
    }
    return `<div style="display:flex;align-items:center;gap:8px;margin-top:3px">
      <span class="callsign">${esc(p.payload.callsign)}</span>
      <span class="text-muted" style="font-size:11px">queued ${fmt(p.queued_at)}</span>
    </div>`;
  }).join('');
}

async function retryOfflineQueue() {
  const changed = await flushCheckinQueue();
  await refreshOfflineQueueBanner();
  if (changed) {
    const beforeIds = new Set(lastKnownCheckins.map(c => c.id));
    await loadCheckins();
    // Newly-appeared rows just landed via the offline queue — highlight them
    // like a fresh check-in so a late sync doesn't look like old news.
    lastKnownCheckins.filter(c => !beforeIds.has(c.id)).forEach(c => markRecentCheckin(c.id));
    renderExpectedList();
  }
}

async function dismissFailedCheckin(id) {
  await removeFailedCheckin(id);
  await refreshOfflineQueueBanner();
}

// Foreground retry — the guarantee that works on every browser including
// iOS Safari, unlike the Background Sync API used in static/sw.js.
window.addEventListener('online', retryOfflineQueue);
setInterval(() => {
  const panel = document.getElementById('live-session-panel');
  if (panel && panel.style.display !== 'none') retryOfflineQueue();
}, 15000);

// ── Callsign search helpers ──────────────────────────────────
// US amateur pattern: 1-2 prefix letters + digit + 1-3 suffix letters  (e.g. W1AW, KD9XYZ)
const HAM_CS_RE  = /^[A-Z]{1,2}[0-9][A-Z]{1,3}$/;
// GMRS pattern: letter + 2-3 letters + 4 digits  (e.g. WQXH7777, KAB1234)
const GMRS_CS_RE = /^[A-Z]{3,4}\d{3,4}$/;

function isLikelyFullCallsign(val) {
  if (val.length < 4) return false;
  return HAM_CS_RE.test(val) || GMRS_CS_RE.test(val);
}

function clearCallsignDropdown() {
  const dd = document.getElementById('cs-dropdown');
  dd.style.display = 'none';
  dd.innerHTML = '';
}

function showCallsignDropdown(results) {
  const dd = document.getElementById('cs-dropdown');
  dd.innerHTML = results.map(r =>
    `<div onmousedown="selectCallsign('${esc(r.callsign)}')"
          style="padding:7px 12px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px"
          onmouseover="this.style.background='#1a1a1a'" onmouseout="this.style.background=''">
       <span class="callsign" style="min-width:90px;font-size:13px">${esc(r.callsign)}</span>
       <span style="flex:1;font-size:12px;color:var(--text)">${esc(r.name || '')}</span>
       <span style="font-size:11px;color:var(--lc-blue)">${esc(r.license_class || '')}</span>
     </div>`
  ).join('');
  dd.style.display = results.length ? '' : 'none';
}

function selectCallsign(callsign) {
  document.getElementById('ci-call').value = callsign;
  clearCallsignDropdown();
  clearLookupInfo();
  lookupCallsign(callsign);
}

async function searchCallsigns(q) {
  setLookupInfo('<span class="lookup-spinner"></span><span class="text-muted" style="font-size:11px">Searching…</span>');
  try {
    const netParam = currentNetId ? `&net_id=${currentNetId}` : '';
    const results = await apiFetch(`/callsign/search?q=${encodeURIComponent(q)}${netParam}`);
    clearLookupInfo();
    if (results.length === 0) {
      setLookupInfo('<span class="lookup-notfound">No matches found</span>');
    } else if (results.length === 1 && results[0].callsign === q) {
      // Exact single match — auto-select
      selectCallsign(results[0].callsign);
    } else {
      showCallsignDropdown(results);
    }
  } catch (err) {
    setLookupInfo(`<span class="lookup-notfound">Search error: ${esc(err.message)}</span>`);
  }
}

// Lookup on blur (tab away) or after a short pause while typing
let lookupTimer = null;
document.getElementById('ci-call').addEventListener('blur', e => {
  // Small delay so onmousedown on a dropdown item fires first
  setTimeout(() => {
    const cs = e.target.value.trim().toUpperCase();
    clearCallsignDropdown();
    // Skip re-looking-up a callsign already displayed — this field loses focus
    // whenever the remark pill/editor is clicked, and a redundant lookup here
    // would wipe out the just-opened editor via setLookupInfo()'s innerHTML reset.
    if (cs && isLikelyFullCallsign(cs) && cs !== lastLookedUpCallsign) lookupCallsign(cs);
  }, 150);
});
document.getElementById('ci-call').addEventListener('input', e => {
  clearLookupInfo();
  clearCallsignDropdown();
  clearTimeout(lookupTimer);
  const cs = e.target.value.trim().toUpperCase();
  if (cs.length < 2) return;
  lookupTimer = setTimeout(() => {
    if (isLikelyFullCallsign(cs)) {
      lookupCallsign(cs);
    } else {
      searchCallsigns(cs);
    }
  }, 600);
});

// Submit checkin on Enter in callsign field
document.getElementById('ci-call').addEventListener('keydown', e => {
  if (e.key === 'Escape') { clearCallsignDropdown(); return; }
  if (e.key === 'Enter') addCheckin();
});
onEnter(['ci-name', 'ci-sig', 'ci-comments', 'ci-zone', 'ci-dmr-tg', 'ci-dmr-region'], addCheckin);
onEnter(['tm-dest', 'tm-notes'], addTrafficMessage);

// Close dropdown on click outside
document.addEventListener('click', e => {
  if (!document.getElementById('ci-call').contains(e.target) &&
      !document.getElementById('cs-dropdown').contains(e.target)) {
    clearCallsignDropdown();
  }
});

async function loadCheckins() {
  const checkins = await apiFetch(`/sessions/${currentSessionId}/checkins`).catch(() => []);
  if (currentNetIsAres) {
    try {
      const zones = await apiFetch(`/nets/${currentNetId}/evac-zones`);
      evacZones = {};
      zones.forEach(z => { evacZones[z.callsign] = z.zone; });
      populateKnownZonesList();
      renderZoneRoster();
    } catch {}
  }
  renderCheckins(checkins);
}

async function markTrafficCalled(checkinId) {
  if (checkinId == null) return; // pending (not checked in yet) — nothing to persist
  try {
    const updated = await apiFetch(`/checkins/${checkinId}/traffic-called`, { method: 'PATCH' });
    const idx = lastKnownCheckins.findIndex(c => c.id === checkinId);
    if (idx !== -1) lastKnownCheckins[idx] = updated;
    updateTrafficBanner(lastKnownCheckins);
  } catch (e) { toast(e.message, 'error'); }
}

function updateTrafficBanner(checkins) {
  const confirmedTraffic = checkins.filter(c => c.has_traffic);
  const confirmedCallsigns = new Set(checkins.map(c => c.callsign));
  // Pending: flagged in expected list but not yet checked in
  const pendingTraffic = [...pendingTrafficCallsigns].filter(cs => !confirmedCallsigns.has(cs));
  const banner = document.getElementById('traffic-banner');
  if (confirmedTraffic.length === 0 && pendingTraffic.length === 0) {
    banner.style.display = 'none';
    return;
  }
  banner.style.display = '';

  // called is persisted server-side (Checkin.traffic_called) so it survives a
  // session close/reopen; pending stations have no checkin row yet so there's
  // nothing to persist for them.
  const chips = [
    ...confirmedTraffic.map(c => ({ id: c.id, label: c.callsign + (c.name ? ` (${c.name})` : ''), pending: false, called: !!c.traffic_called })),
    ...pendingTraffic.map(cs => ({ id: null, label: `${cs} ⏳`, pending: true, called: false })),
  ];

  document.getElementById('traffic-callsigns').innerHTML = chips.map(({ id, label, pending, called }) => {
    return `<label style="display:inline-flex;align-items:center;gap:5px;cursor:pointer;
                background:rgba(0,0,0,0.18);border-radius:5px;padding:2px 8px;
                ${called ? 'opacity:0.45;text-decoration:line-through;' : ''}
                font-weight:700;white-space:nowrap">
      <input type="checkbox" ${called ? 'checked' : ''} ${pending ? 'disabled title="Not checked in yet"' : ''}
        onchange="markTrafficCalled(${id})"
        style="accent-color:#000;width:13px;height:13px;cursor:pointer" />
      ${esc(label)}
    </label>`;
  }).join('');
}

// Last 5 check-in ids added/synced, newest last — each highlighted for 20s
// (issue #18). A station drops out of the highlight the moment either the
// 20s window elapses or a 6th newer check-in bumps it off the list.
let recentCheckins = [];

function markRecentCheckin(id) {
  recentCheckins.push({ id, at: Date.now() });
  if (recentCheckins.length > 5) recentCheckins.shift();
  renderCheckins(lastKnownCheckins);
  setTimeout(() => renderCheckins(lastKnownCheckins), 20000);
}

function isRecentCheckin(id) {
  const entry = recentCheckins.find(r => r.id === id);
  return !!entry && (Date.now() - entry.at) < 20000;
}

function renderCheckins(checkins) {
  lastKnownCheckins = checkins;
  const list = document.getElementById('checkins-list');
  const empty = document.getElementById('checkins-empty');
  const count = document.getElementById('checkin-count-label');
  count.textContent = `${checkins.length} check-in${checkins.length !== 1 ? 's' : ''}`;

  updateTrafficBanner(checkins);

  if (checkins.length === 0) {
    list.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  const hasDmr = !!currentDmrConfig;
  list.innerHTML = checkins.map(c => {
    const details = [
      c.signal_report ? `Signal: ${c.signal_report}` : null,
      c.comments ? `Comments: ${c.comments}` : null,
      currentNetIsAres && c.evac_zone ? `Zone: ${c.evac_zone}` : null,
      hasDmr && c.dmr_talkgroup ? `TG: ${c.dmr_talkgroup}` : null,
      hasDmr && c.dmr_region ? `Region: ${c.dmr_region}` : null,
      `Checked in: ${fmt(c.checked_in_at)}`,
    ].filter(Boolean).join(' · ');
    return `<div class="checkin-row${isRecentCheckin(c.id) ? ' checkin-recent' : ''}" title="${esc(details)}">
      <span class="callsign">${esc(c.callsign)}</span>
      <span class="checkin-name">${esc(c.name || '—')}</span>
      <button class="btn btn-sm ${c.has_traffic ? 'btn-danger' : 'btn-ghost'}"
        style="font-size:14px;padding:2px 8px" title="${c.has_traffic ? 'Traffic — click to clear' : 'Click to flag traffic'}"
        onclick="toggleTraffic(${c.id})">${c.has_traffic ? '📢' : '○'}</button>
      <button class="btn btn-danger btn-sm" onclick="removeCheckin(${c.id})">✕</button>
    </div>`;
  }).join('');
}

async function removeCheckin(id) {
  if (!confirm('Remove this check-in?')) return;
  try {
    await apiFetch(`/checkins/${id}`, { method: 'DELETE' });
    toast('Check-in removed');
    await loadCheckins();
    renderExpectedList();
  } catch (e) { toast(e.message, 'error'); }
}

async function toggleTraffic(checkinId) {
  try {
    const updated = await apiFetch(`/checkins/${checkinId}/traffic`, { method: 'PATCH' });
    // Reload checkins and re-render (simplest approach for consistency)
    await loadCheckins();
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// EXPECTED STATIONS
// ============================================================
let expectedStations = [];        // loaded from /nets/{id}/expected
let lastKnownCheckins = [];       // most recent checkin list for banner refresh
const pendingTrafficCallsigns = new Set();  // traffic flagged in expected list before check-in

function toggleExpectedPanel() {
  const body = document.getElementById('expected-panel-body');
  const icon = document.getElementById('expected-toggle-icon');
  const open = body.style.display === 'none';
  body.style.display = open ? '' : 'none';
  icon.textContent = open ? '▼' : '▶';
}

async function loadExpectedStations() {
  const minCheckins = parseInt(document.getElementById('exp-min').value) || 2;
  const weeks = parseInt(document.getElementById('exp-weeks').value) || 4;
  const listEl = document.getElementById('expected-list');
  listEl.innerHTML = '<p class="text-muted" style="font-size:12px;margin:0">Loading…</p>';
  try {
    expectedStations = await apiFetch(`/nets/${currentNetId}/expected?min_checkins=${minCheckins}&weeks=${weeks}`);
    renderExpectedList();
  } catch (e) {
    listEl.innerHTML = `<p class="text-muted" style="font-size:12px;margin:0;color:var(--lc-red)">${esc(e.message)}</p>`;
  }
}

// Get set of callsigns currently checked into the session (from the DOM)
function checkedInCallsigns() {
  return new Set(lastKnownCheckins.map(c => c.callsign));
}

function renderExpectedList() {
  const listEl = document.getElementById('expected-list');
  if (!expectedStations.length) {
    listEl.innerHTML = '<p class="text-muted" style="font-size:12px;margin:0">No matching stations found.</p>';
    return;
  }
  const alreadyIn = checkedInCallsigns();
  listEl.innerHTML = expectedStations.map(st => {
    const checked = alreadyIn.has(st.callsign);
    const checkboxGroup = checked
      ? `<label style="display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700;color:var(--lc-orange);white-space:nowrap">
           <input type="checkbox" checked disabled style="accent-color:var(--lc-orange);width:15px;height:15px" />
           Check In
         </label>
         <span style="font-size:11px;color:var(--text-muted);white-space:nowrap">—</span>`
      : `<label style="display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700;color:var(--lc-orange);cursor:pointer;white-space:nowrap">
           <input type="checkbox"
             style="accent-color:var(--lc-orange);width:15px;height:15px;cursor:pointer"
             data-callsign="${esc(st.callsign)}" data-name="${esc(st.name || '')}"
             onchange="if(this.checked) checkInExpected(this, this.dataset.callsign, this.dataset.name)" />
           Check In
         </label>
         <label style="display:flex;align-items:center;gap:5px;font-size:11px;font-weight:700;color:var(--lc-red);cursor:pointer;white-space:nowrap">
           <input type="checkbox" id="exp-traffic-${esc(st.callsign)}"
             style="accent-color:var(--lc-red);width:15px;height:15px;cursor:pointer"
             data-callsign="${esc(st.callsign)}"
             onchange="toggleExpectedTraffic(this.dataset.callsign, this.checked)" />
           Traffic
         </label>`;
    const knownZone = evacZones[st.callsign];
    const zoneBadge = currentNetIsAres
      ? `<span style="font-size:11px;color:var(--lc-blue);white-space:nowrap;min-width:60px;text-align:right"
              title="Last known zone">${knownZone ? '📍 ' + esc(knownZone) : ''}</span>`
      : '';
    return `<div class="exp-row" style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid var(--border);flex-wrap:wrap;${checked ? 'opacity:.45' : ''}">
      ${checkboxGroup}
      <span class="callsign" style="min-width:80px">${esc(st.callsign)}</span>
      <span style="flex:1;display:flex;align-items:center;gap:6px;min-width:120px">
        <span class="exp-name-display" style="color:var(--text-muted);font-size:12px">${esc(st.name || '')}</span>
        <button type="button" title="Set preferred name / remark for this station"
          onclick="toggleExpectedRemarkEditor(this, '${esc(st.callsign)}')"
          style="background:none;border:none;color:var(--lc-orange);cursor:pointer;font-size:11px;padding:0 2px;opacity:0.7">✏️</button>
      </span>
      ${zoneBadge}
      <span style="font-size:11px;color:var(--lc-blue);white-space:nowrap" title="Check-ins in window">${st.checkin_count}✓</span>
    </div>`;
  }).join('');
}

function toggleExpectedTraffic(callsign, checked) {
  if (checked) {
    pendingTrafficCallsigns.add(callsign);
  } else {
    pendingTrafficCallsigns.delete(callsign);
  }
  updateTrafficBanner(lastKnownCheckins);
}

// ── Zone roster (ARES nets) ──────────────────────────────────
function populateKnownZonesList() {
  const dl = document.getElementById('known-zones-list');
  if (!dl) return;
  const distinctZones = [...new Set(Object.values(evacZones))].sort();
  dl.innerHTML = distinctZones.map(z => `<option value="${esc(z)}">`).join('');
}

function toggleZoneRoster() {
  const body = document.getElementById('zone-roster-body');
  const icon = document.getElementById('zone-roster-toggle-icon');
  const open = body.style.display === 'none';
  body.style.display = open ? '' : 'none';
  icon.textContent = open ? '▼' : '▶';
}

function callsignSuffix(cs) {
  const m = cs.toUpperCase().match(/\d([A-Z]+)$/);
  return m ? m[1] : cs;
}

function renderZoneRoster() {
  const listEl = document.getElementById('zone-roster-list');
  if (!listEl) return;
  const entries = Object.entries(evacZones); // [callsign, zone]
  if (entries.length === 0) {
    listEl.innerHTML = '<p class="text-muted" style="font-size:12px;margin:0">No zones recorded yet.</p>';
    return;
  }
  // Group by zone
  const byZone = {};
  entries.forEach(([cs, zone]) => {
    if (!byZone[zone]) byZone[zone] = [];
    byZone[zone].push(cs);
  });
  const sortedZones = Object.keys(byZone).sort();
  listEl.innerHTML = sortedZones.map(zone => {
    const callsigns = byZone[zone].slice().sort((a, b) => callsignSuffix(a).localeCompare(callsignSuffix(b)));
    return `<div style="margin-bottom:8px">
      <div style="font-weight:700;color:var(--lc-orange);font-size:12px;margin-bottom:4px;letter-spacing:.05em">
        📍 ${esc(zone)}
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;padding-left:12px">
        ${callsigns.map(cs => `<span class="callsign" style="font-size:12px;background:rgba(255,153,0,0.12);padding:2px 7px;border-radius:4px">${esc(cs)}</span>`).join('')}
      </div>
    </div>`;
  }).join('');
}

async function checkInExpected(checkbox, callsign, name) {
  const trafficEl = document.getElementById(`exp-traffic-${callsign}`);
  const has_traffic = trafficEl ? trafficEl.checked : false;
  const evac_zone = currentNetIsAres ? (evacZones[callsign] || null) : null;
  checkbox.disabled = true;
  try {
    const created = await apiFetch(`/sessions/${currentSessionId}/checkins`, {
      method: 'POST',
      body: JSON.stringify({ callsign, name: name || null, has_traffic, evac_zone })
    });
    pendingTrafficCallsigns.delete(callsign);  // now confirmed in DB, remove from pending
    toast(`${callsign} checked in`, 'success');
    markRecentCheckin(created.id);
    await loadCheckins();
    renderExpectedList();
  } catch (e) {
    toast(e.message, 'error');
    checkbox.checked = false;
    checkbox.disabled = false;
  }
}


// ============================================================
// TRAFFIC MESSAGE LOG
// ============================================================
let trafficMessages = [];

function toggleTrafficLog() {
  const body = document.getElementById('traffic-log-body');
  const icon = document.getElementById('traffic-log-toggle-icon');
  const open = body.style.display === 'none';
  body.style.display = open ? '' : 'none';
  icon.textContent = open ? '▼' : '▶';
}

async function loadTrafficMessages() {
  if (!currentSessionId) return;
  try {
    trafficMessages = await apiFetch(`/sessions/${currentSessionId}/traffic-messages`);
    renderTrafficMessages();
  } catch {}
}

function renderTrafficMessages() {
  const listEl = document.getElementById('traffic-log-list');
  const countEl = document.getElementById('traffic-log-count');
  countEl.textContent = trafficMessages.length ? `(${trafficMessages.length})` : '';
  if (!trafficMessages.length) {
    listEl.innerHTML = '<p class="text-muted" style="margin:0;font-size:12px">No messages logged yet.</p>';
    return;
  }
  const statusColors = { received:'var(--lc-blue)', relayed:'var(--lc-orange)', delivered:'var(--success)', undeliverable:'var(--lc-red)' };
  listEl.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px">
    <thead><tr style="border-bottom:1px solid var(--border)">
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">#</th>
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">Msg #</th>
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">Origin</th>
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">Destination</th>
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">Type</th>
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">Status</th>
      <th style="text-align:left;padding:4px 6px;color:var(--text-muted);font-weight:600">Notes</th>
      <th></th>
    </tr></thead>
    <tbody>
    ${trafficMessages.map((m, i) => `<tr style="border-bottom:1px solid var(--border)">
      <td style="padding:4px 6px;color:var(--text-muted)">${i+1}</td>
      <td style="padding:4px 6px;font-family:monospace">${esc(m.msg_number || '—')}</td>
      <td style="padding:4px 6px"><span class="callsign" style="font-size:11px">${esc(m.origin_callsign)}</span></td>
      <td style="padding:4px 6px">${esc(m.dest_info || '—')}</td>
      <td style="padding:4px 6px">${esc(m.msg_type.replace('_',' '))}</td>
      <td style="padding:4px 6px">
        <select style="font-size:11px;background:var(--bg);color:${statusColors[m.status]||'inherit'};border:1px solid var(--border);border-radius:4px;padding:2px 4px"
          onchange="updateTrafficStatus(${m.id}, this.value)">
          <option value="received" ${m.status==='received'?'selected':''}>Received</option>
          <option value="relayed" ${m.status==='relayed'?'selected':''}>Relayed</option>
          <option value="delivered" ${m.status==='delivered'?'selected':''}>Delivered</option>
          <option value="undeliverable" ${m.status==='undeliverable'?'selected':''}>Undeliverable</option>
        </select>
      </td>
      <td style="padding:4px 6px;color:var(--text-muted)">${esc(m.notes || '')}</td>
      <td style="padding:4px 6px">
        <button class="btn btn-danger btn-sm" onclick="deleteTrafficMessage(${m.id})" style="padding:1px 6px;font-size:11px">✕</button>
      </td>
    </tr>`).join('')}
    </tbody></table>`;
}

async function addTrafficMessage() {
  const origin = document.getElementById('tm-origin').value.trim().toUpperCase();
  if (!origin) { toast('Origin callsign required', 'error'); return; }
  try {
    await apiFetch(`/sessions/${currentSessionId}/traffic-messages`, {
      method: 'POST',
      body: JSON.stringify({
        origin_callsign: origin,
        dest_info: document.getElementById('tm-dest').value.trim() || null,
        msg_number: document.getElementById('tm-number').value.trim() || null,
        msg_type: document.getElementById('tm-type').value,
        notes: document.getElementById('tm-notes').value.trim() || null,
      })
    });
    document.getElementById('tm-origin').value = '';
    document.getElementById('tm-dest').value = '';
    document.getElementById('tm-number').value = '';
    document.getElementById('tm-notes').value = '';
    toast('Message logged', 'success');
    await loadTrafficMessages();
  } catch (e) { toast(e.message, 'error'); }
}

async function updateTrafficStatus(msgId, status) {
  try {
    await apiFetch(`/traffic-messages/${msgId}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    await loadTrafficMessages();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteTrafficMessage(msgId) {
  if (!confirm('Remove this message?')) return;
  try {
    await apiFetch(`/traffic-messages/${msgId}`, { method: 'DELETE' });
    await loadTrafficMessages();
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// STATION REMARKS & PREFERRED NAME
// ============================================================
// A preferred name overrides the FCC/GMRS-looked-up name in the Expected
// Stations list and net reports (ICS-205, CSV exports) — not the live
// check-in form or the stored check-in record itself.

async function loadStationRemarks(callsign) {
  if (!currentNetId || !callsign) return null;
  try {
    return await apiFetch(`/nets/${currentNetId}/stations/${encodeURIComponent(callsign)}/remark`);
  } catch { return null; }
}

async function saveStationRemark(callsign, remark, preferredName) {
  if (!currentNetId) return null;
  return await apiFetch(`/nets/${currentNetId}/stations/${encodeURIComponent(callsign)}/remark`, {
    method: 'PUT', body: JSON.stringify({ remark: remark.trim() || null, preferred_name: preferredName.trim() || null }),
  });
}

function showRemarkEditor(callsign, current) {
  const existing = document.getElementById('remark-editor');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.id = 'remark-editor';
  // Positioned absolutely (like #cs-dropdown) rather than in normal flow —
  // the callsign field's column is only ~140px wide, which was forcing every
  // item onto its own line no matter how the flex layout was tuned. Wraps
  // and caps its own width to the viewport so it can never run off the
  // right edge of a narrow phone screen regardless of where the callsign
  // field itself sits horizontally.
  div.style.cssText = 'position:absolute;top:100%;left:0;z-index:150;margin-top:4px;display:flex;flex-wrap:wrap;gap:6px;align-items:center;background:var(--surface);border:1px solid var(--lc-orange);border-radius:8px;padding:8px;box-shadow:0 4px 12px rgba(0,0,0,.5);max-width:min(360px, calc(100vw - 32px))';
  div.innerHTML = `
    <input id="remark-preferred-name-input" class="form-control" style="width:130px;font-size:12px"
      placeholder="Preferred name" value="${esc((current && current.preferred_name) || '')}" />
    <input id="remark-input" class="form-control" style="flex:1;min-width:140px;font-size:12px"
      placeholder="Notes about this station…" value="${esc((current && current.remark) || '')}" />
    <button class="btn btn-primary btn-sm" onclick="submitRemark('${esc(callsign)}')">Save</button>
    <button class="btn btn-ghost btn-sm" onclick="document.getElementById('remark-editor').remove()">✕</button>`;
  const lookupInfo = document.getElementById('ci-lookup-info');
  lookupInfo.appendChild(div);
  onEnter(['remark-preferred-name-input', 'remark-input'], () => submitRemark(callsign));
  div.querySelector('#remark-preferred-name-input').focus();
}

async function submitRemark(callsign) {
  const remarkVal = document.getElementById('remark-input')?.value || '';
  const preferredNameVal = document.getElementById('remark-preferred-name-input')?.value || '';
  try {
    const saved = await saveStationRemark(callsign, remarkVal, preferredNameVal);
    toast(saved ? 'Saved' : 'Cleared', 'success');
    document.getElementById('remark-editor')?.remove();
    // Refresh the remark pill in lookup info
    const pill = document.getElementById('remark-pill');
    if (pill) {
      if (saved) { pill.querySelector('span').textContent = remarkPillText(saved); }
      else { pill.remove(); }
    }
  } catch (e) { toast(e.message, 'error'); }
}

// Inline preferred name / remark editor for a row in the Expected Stations
// list — the check-in form's pill only appears once a callsign is typed
// there, so this gives a second, more discoverable entry point.
async function toggleExpectedRemarkEditor(btn, callsign) {
  const row = btn.closest('.exp-row');
  const existing = row.querySelector('.exp-remark-editor');
  if (existing) { existing.remove(); return; }

  const current = await loadStationRemarks(callsign);
  const editor = document.createElement('div');
  editor.className = 'exp-remark-editor';
  editor.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap';
  editor.innerHTML = `
    <input class="form-control exp-pref-input" style="width:120px;font-size:12px"
      placeholder="Preferred name" value="${esc((current && current.preferred_name) || '')}" />
    <input class="form-control exp-remark-input" style="width:140px;font-size:12px"
      placeholder="Notes" value="${esc((current && current.remark) || '')}" />
    <button class="btn btn-primary btn-sm" type="button">Save</button>
    <button class="btn btn-ghost btn-sm" type="button">✕</button>`;
  const prefInput = editor.querySelector('.exp-pref-input');
  const remarkInput = editor.querySelector('.exp-remark-input');
  const [saveBtn, cancelBtn] = editor.querySelectorAll('button');
  const doSave = async () => {
    try {
      await saveStationRemark(callsign, remarkInput.value, prefInput.value);
      toast('Saved', 'success');
      await loadExpectedStations();
    } catch (e) { toast(e.message, 'error'); }
  };
  saveBtn.onclick = doSave;
  cancelBtn.onclick = () => editor.remove();
  [prefInput, remarkInput].forEach(el => el.addEventListener('keydown', e => { if (e.key === 'Enter') doSave(); }));
  row.appendChild(editor);
  prefInput.focus();
}

