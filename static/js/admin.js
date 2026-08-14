// ============================================================
// ADMIN
// ============================================================
async function loadAdminUsers() {
  // Load email status and users in parallel
  const [users, emailStatus] = await Promise.all([
    apiFetch('/admin/users').catch(e => { toast(e.message, 'error'); return null; }),
    apiFetch('/admin/email-status').catch(() => null),
  ]);
  if (!users) return;

  // Render email status card
  const emailEl = document.getElementById('admin-email-status');
  if (emailStatus) {
    if (emailStatus.configured) {
      emailEl.innerHTML = `<span style="color:var(--lc-green)">✓ SMTP configured</span>
        <span class="text-muted" style="margin-left:10px;font-size:12px">From: ${esc(emailStatus.from_address || emailStatus.host)}</span>`;
      document.getElementById('admin-email-config-hint').style.display = 'none';
    } else {
      emailEl.innerHTML = `<span style="color:var(--lc-red)">✗ SMTP not configured</span>
        <span class="text-muted" style="margin-left:10px;font-size:12px">Emails will not be sent.</span>`;
    }
  }

  const pending = users.filter(u => !u.is_active);
  const pendingEl = document.getElementById('admin-pending-list');
  if (pending.length === 0) {
    pendingEl.innerHTML = '<p class="text-muted" style="font-size:13px">No pending registrations.</p>';
  } else {
    pendingEl.innerHTML = pending.map(u => `
      <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);flex-wrap:wrap">
        <span class="callsign">${esc(u.callsign)}</span>
        <span>${esc(u.name)}</span>
        <span class="text-muted" style="font-size:12px">${esc(u.email)}</span>
        <span class="text-muted" style="font-size:11px">Registered ${fmt(u.created_at)}</span>
        <div style="margin-left:auto;display:flex;gap:6px">
          <button class="btn btn-primary btn-sm" onclick="adminApprove(${u.id})">✓ Approve</button>
          <button class="btn btn-danger btn-sm" onclick="adminDelete(${u.id})">✕ Reject</button>
        </div>
      </div>
    `).join('');
  }

  const tbody = document.getElementById('admin-users-tbody');
  const empty = document.getElementById('admin-users-empty');
  if (users.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = users.map(u => {
    const isMe = u.id === currentUser.id;
    const roleBadge = u.is_admin
      ? '<span class="badge badge-blue">Admin</span>'
      : '<span class="badge badge-gray">Operator</span>';
    const statusBadge = u.is_active
      ? '<span class="badge badge-green">Active</span>'
      : '<span class="badge badge-gray">Pending</span>';

    // Notify toggle — only meaningful for admins
    const notifyCell = u.is_admin
      ? `<button class="btn btn-sm ${u.notify_new_registrations ? 'btn-primary' : 'btn-ghost'}"
           title="${u.notify_new_registrations ? 'Click to stop notifications' : 'Click to receive registration emails'}"
           onclick="adminToggleNotify(${u.id})" style="font-size:12px;padding:3px 8px">
           ${u.notify_new_registrations ? '📧 On' : '✉ Off'}
         </button>`
      : '<span class="text-muted" style="font-size:11px">—</span>';

    const actions = isMe
      ? '<span class="text-muted" style="font-size:11px">you</span>'
      : `<div style="display:flex;gap:4px;flex-wrap:wrap">
          ${!u.is_active ? `<button class="btn btn-primary btn-sm" onclick="adminApprove(${u.id})">Approve</button>` : ''}
          ${u.is_active  ? `<button class="btn btn-ghost btn-sm" onclick="adminDeactivate(${u.id})">Deactivate</button>` : ''}
          ${!u.is_admin  ? `<button class="btn btn-ghost btn-sm" onclick="adminMakeAdmin(${u.id})">Make Admin</button>` : ''}
          <button class="btn btn-danger btn-sm" onclick="adminDelete(${u.id})">Delete</button>
        </div>`;
    return `<tr>
      <td><span class="callsign">${esc(u.callsign)}</span></td>
      <td>${esc(u.name)}</td>
      <td class="text-muted" style="font-size:12px">${esc(u.email)}</td>
      <td>${roleBadge}</td>
      <td>${statusBadge}</td>
      <td style="text-align:center">${notifyCell}</td>
      <td class="text-muted" style="font-size:12px">${fmt(u.created_at)}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('');
}

async function adminToggleNotify(userId) {
  try {
    await apiFetch(`/admin/users/${userId}/notify`, { method: 'PATCH' });
    loadAdminUsers();
  } catch (e) { toast(e.message, 'error'); }
}

async function adminApprove(userId) {
  try {
    await apiFetch(`/admin/users/${userId}/approve`, { method: 'PATCH' });
    toast('Operator approved', 'success');
    loadAdminUsers();
  } catch (e) { toast(e.message, 'error'); }
}

async function adminDeactivate(userId) {
  if (!confirm('Deactivate this account? They will no longer be able to log in.')) return;
  try {
    await apiFetch(`/admin/users/${userId}/deactivate`, { method: 'PATCH' });
    toast('Account deactivated');
    loadAdminUsers();
  } catch (e) { toast(e.message, 'error'); }
}

async function adminMakeAdmin(userId) {
  if (!confirm('Grant admin privileges to this operator?')) return;
  try {
    await apiFetch(`/admin/users/${userId}/make-admin`, { method: 'PATCH' });
    toast('Admin access granted', 'success');
    loadAdminUsers();
  } catch (e) { toast(e.message, 'error'); }
}

async function adminDelete(userId) {
  if (!confirm('Permanently delete this account? This cannot be undone.')) return;
  try {
    await apiFetch(`/admin/users/${userId}`, { method: 'DELETE' });
    toast('Account deleted');
    loadAdminUsers();
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
// SUB-TABS (Sessions / Schedule)
// ============================================================
function switchSubTab(tab) {
  document.getElementById('sessions-panel').style.display      = tab === 'sessions' ? '' : 'none';
  document.getElementById('live-session-panel').style.display  = (tab === 'sessions' && currentSessionId) ? '' : 'none';
  document.getElementById('schedule-panel').style.display      = tab === 'schedule' ? '' : 'none';
  document.getElementById('sub-tab-sessions').classList.toggle('active', tab === 'sessions');
  document.getElementById('sub-tab-schedule').classList.toggle('active', tab === 'schedule');
  if (tab === 'schedule') loadScheduleView();
}

// ============================================================
// USER LIST (for assignment dropdown)
// ============================================================
let registeredUsers = [];

async function loadRegisteredUsers() {
  try { registeredUsers = await apiFetch('/users'); }
  catch { registeredUsers = []; }
}

