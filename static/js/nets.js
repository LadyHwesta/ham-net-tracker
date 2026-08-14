// ============================================================
// NETS
// ============================================================
async function loadNets() {
  try { nets = await apiFetch('/nets'); } catch { nets = []; }
  renderNets();
}

function renderNets() {
  const el = document.getElementById('nets-list');
  if (nets.length === 0) {
    el.innerHTML = '<div class="empty"><p>No nets yet. Create your first net above.</p></div>';
    return;
  }
  el.innerHTML = nets.map(n => {
    const aresBadge = n.is_ares
      ? ' <span style="font-size:11px;background:var(--lc-orange);color:#000;border-radius:10px;padding:2px 8px;font-weight:700;vertical-align:middle">ARES</span>'
      : '';
    // Shared badge — shown for nets not owned by current user
    const sharedBadge = !n.is_owner
      ? ` <span style="font-size:11px;background:var(--lc-blue);color:#000;border-radius:10px;padding:2px 8px;font-weight:700;vertical-align:middle">Shared</span>`
      : '';
    // Share status line shown under the net name for owners
    let shareInfo = '';
    if (n.is_owner) {
      if (n.shared_with_all) {
        shareInfo = '<div style="font-size:11px;color:var(--lc-blue);margin-top:2px">🔗 Shared with all users</div>';
      } else if (n.shared_user_ids && n.shared_user_ids.length > 0) {
        shareInfo = `<div style="font-size:11px;color:var(--lc-blue);margin-top:2px">🔗 Shared with ${n.shared_user_ids.length} user${n.shared_user_ids.length > 1 ? 's' : ''}</div>`;
      }
    } else if (n.owner_callsign) {
      shareInfo = `<div style="font-size:11px;color:var(--lc-muted);margin-top:2px">Owner: ${esc(n.owner_callsign)}</div>`;
    }
    const editDeleteBtns = n.is_owner
      ? `<button class="btn btn-ghost btn-sm" onclick="editNet(${n.id})">Edit</button>
         <button class="btn btn-danger btn-sm" onclick="deleteNet(${n.id})">Delete</button>`
      : '';
    return `
    <div class="card" style="max-width:600px">
      <div class="card-header">
        <div>
          <h2>${esc(n.name)}${aresBadge}${sharedBadge}</h2>
          ${n.frequency ? `<span class="text-muted" style="font-size:12px">📡 ${esc(n.frequency)}</span>` : ''}
          ${shareInfo}
        </div>
        <div class="actions">
          <button class="btn btn-primary btn-sm" onclick="openNet(${n.id})">Open</button>
          ${editDeleteBtns}
        </div>
      </div>
      ${n.description ? `<p class="text-muted" style="font-size:13px">${esc(n.description)}</p>` : ''}
    </div>`;
  }).join('');
}

function showNetForm() {
  editNetId = null;
  document.getElementById('net-form-title').textContent = 'New Net';
  document.getElementById('net-name').value = '';
  document.getElementById('net-freq').value = '';
  document.getElementById('net-dmr-tg').value = '';
  document.getElementById('net-desc').value = '';
  document.getElementById('net-ares').checked = false;
  document.getElementById('net-sharing-section').style.display = 'none';
  document.getElementById('net-dmr-section').style.display = 'none';
  document.getElementById('net-form-card').style.display = '';
}

function cancelNetForm() {
  document.getElementById('net-form-card').style.display = 'none';
  document.getElementById('net-sharing-section').style.display = 'none';
  document.getElementById('net-dmr-section').style.display = 'none';
  editNetId = null;
  shareState = { share_with_all: false, user_ids: [] };
}

async function editNet(id) {
  const n = nets.find(x => x.id === id);
  if (!n) return;
  editNetId = id;
  document.getElementById('net-form-title').textContent = 'Edit Net';
  document.getElementById('net-name').value = n.name;
  document.getElementById('net-freq').value = n.frequency || '';
  document.getElementById('net-dmr-tg').value = n.dmr_talkgroup || '';
  document.getElementById('net-desc').value = n.description || '';
  document.getElementById('net-ares').checked = !!n.is_ares;
  document.getElementById('net-form-card').style.display = '';
  // Load sharing and DMR config if owner
  if (n.is_owner) {
    document.getElementById('net-sharing-section').style.display = '';
    await loadSharesForNet(id);
    await loadDmrConfig(id);
  } else {
    document.getElementById('net-sharing-section').style.display = 'none';
    document.getElementById('net-dmr-section').style.display = 'none';
  }
}

async function saveNet() {
  const name = document.getElementById('net-name').value.trim();
  const frequency = document.getElementById('net-freq').value.trim() || null;
  const dmr_talkgroup = document.getElementById('net-dmr-tg').value.trim() || null;
  const description = document.getElementById('net-desc').value.trim() || null;
  const is_ares = document.getElementById('net-ares').checked;
  if (!name) return toast('Net name is required', 'error');
  try {
    if (editNetId) {
      await apiFetch(`/nets/${editNetId}`, { method: 'PUT', body: JSON.stringify({ name, frequency, dmr_talkgroup, description, is_ares }) });
      toast('Net updated');
    } else {
      await apiFetch('/nets', { method: 'POST', body: JSON.stringify({ name, frequency, dmr_talkgroup, description, is_ares }) });
      toast('Net created');
    }
    cancelNetForm();
    await loadNets();
  } catch (e) { toast(e.message, 'error'); }
}

// --- Sharing helpers ---

async function loadSharesForNet(netId) {
  try {
    // Load users and current shares in parallel
    const [shares, users] = await Promise.all([
      apiFetch(`/nets/${netId}/shares`),
      apiFetch('/users'),
    ]);
    allUsers = users;
    shareState = {
      share_with_all: shares.share_with_all,
      user_ids: shares.user_ids || [],
    };
    document.getElementById('net-share-all').checked = shareState.share_with_all;
    renderShareUserList();
    document.getElementById('net-share-users').style.display = shareState.share_with_all ? 'none' : '';
  } catch (e) {
    console.warn('Could not load shares:', e);
  }
}

function renderShareUserList() {
  const el = document.getElementById('net-share-user-list');
  if (allUsers.length === 0) {
    el.innerHTML = '<div class="text-muted" style="font-size:12px">No other registered users found.</div>';
    return;
  }
  el.innerHTML = allUsers.map(u => `
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;padding:3px 0">
      <input type="checkbox" data-uid="${u.id}" style="accent-color:var(--lc-blue)"
        ${shareState.user_ids.includes(u.id) ? 'checked' : ''}
        onchange="toggleShareUser(${u.id}, this.checked)" />
      ${esc(u.callsign)} — ${esc(u.name)}
    </label>
  `).join('');
}

function onShareAllChanged() {
  shareState.share_with_all = document.getElementById('net-share-all').checked;
  document.getElementById('net-share-users').style.display = shareState.share_with_all ? 'none' : '';
}

function toggleShareUser(uid, checked) {
  if (checked) {
    if (!shareState.user_ids.includes(uid)) shareState.user_ids.push(uid);
  } else {
    shareState.user_ids = shareState.user_ids.filter(id => id !== uid);
  }
}

async function saveSharing() {
  if (!editNetId) return;
  try {
    await apiFetch(`/nets/${editNetId}/shares`, {
      method: 'PUT',
      body: JSON.stringify({ share_with_all: shareState.share_with_all, user_ids: shareState.user_ids }),
    });
    toast('Sharing saved');
    await loadNets();
    // Re-render to update share info on card without closing form
    const n = nets.find(x => x.id === editNetId);
    if (n) {
      shareState.share_with_all = n.shared_with_all;
      shareState.user_ids = n.shared_user_ids || [];
    }
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteNet(id) {
  if (!confirm('Delete this net and ALL its sessions and check-ins? This cannot be undone.')) return;
  try {
    await apiFetch(`/nets/${id}`, { method: 'DELETE' });
    toast('Net deleted');
    await loadNets();
  } catch (e) { toast(e.message, 'error'); }
}

