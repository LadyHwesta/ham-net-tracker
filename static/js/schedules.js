// ============================================================
// SCHEDULES
// ============================================================
let schedules = [];

async function loadScheduleView() {
  await Promise.all([loadSchedules(), loadRegisteredUsers()]);
  await loadUpcoming();
}

async function loadSchedules() {
  try { schedules = await apiFetch(`/nets/${currentNetId}/schedules`); }
  catch { schedules = []; }
  renderSchedules();
}

function renderSchedules() {
  const el = document.getElementById('schedules-list');
  if (schedules.length === 0) { el.innerHTML = '<p class="text-muted" style="font-size:13px">No schedules yet.</p>'; return; }
  el.innerHTML = schedules.map(s => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-top:1px solid var(--border)">
      <span style="font-size:13px"><strong>${esc(s.day_name)}s</strong> at ${esc(s.start_time)} ${esc(s.timezone)}</span>
      ${s.notes ? `<span class="text-muted" style="font-size:12px">— ${esc(s.notes)}</span>` : ''}
      <button class="btn btn-danger btn-sm" style="margin-left:auto" onclick="deleteSchedule(${s.id})">Delete</button>
    </div>
  `).join('');
}

function toggleScheduleForm() {
  const f = document.getElementById('schedule-form');
  f.style.display = f.style.display === 'none' ? '' : 'none';
  // Pre-fill callsign from current user
  if (currentUser) document.getElementById('signup-callsign').value = currentUser.callsign;
}

async function saveSchedule() {
  const day_of_week = parseInt(document.getElementById('sched-day').value);
  const start_time  = document.getElementById('sched-time').value;
  const timezone    = document.getElementById('sched-tz').value.trim() || 'UTC';
  const notes       = document.getElementById('sched-notes').value.trim() || null;
  if (!start_time) return toast('Start time required', 'error');
  try {
    await apiFetch(`/nets/${currentNetId}/schedules`, {
      method: 'POST',
      body: JSON.stringify({ day_of_week, start_time, timezone, notes }),
    });
    toast('Schedule added');
    document.getElementById('schedule-form').style.display = 'none';
    document.getElementById('sched-notes').value = '';
    await loadScheduleView();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteSchedule(id) {
  if (!confirm('Delete this schedule and all its sign-ups?')) return;
  try {
    await apiFetch(`/schedules/${id}`, { method: 'DELETE' });
    toast('Schedule deleted');
    await loadScheduleView();
  } catch (e) { toast(e.message, 'error'); }
}


// ============================================================
// UPCOMING SLOTS
// ============================================================
async function loadUpcoming() {
  const empty = document.getElementById('upcoming-empty');
  const grid  = document.getElementById('upcoming-slots');
  if (schedules.length === 0) { grid.innerHTML = ''; empty.style.display = ''; return; }
  empty.style.display = 'none';

  let slots = [];
  try { slots = await apiFetch(`/nets/${currentNetId}/upcoming?weeks=8`); }
  catch { slots = []; }

  // Build a lookup of scheduleId → schedule for time display
  const schedMap = Object.fromEntries(schedules.map(s => [s.id, s]));

  grid.innerHTML = slots.map(slot => {
    const sched = schedMap[slot.schedule_id] || {};
    const dateObj = new Date(slot.slot_date + 'T00:00:00');
    const dateStr = dateObj.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
    const isPast  = slot.slot_date < new Date().toISOString().slice(0, 10);

    let statusHtml;
    if (slot.signup) {
      const s = slot.signup;
      const canRemove = s.is_mine || (currentUser && currentNetOwnerId === currentUser.id);
      statusHtml = `
        <span class="slot-claimed">
          <span class="callsign">${esc(s.callsign)}</span>
          ${s.name ? `<span class="text-muted"> — ${esc(s.name)}</span>` : ''}
        </span>
        ${canRemove ? `<button class="btn btn-ghost btn-sm" onclick="removeSignup(${s.id})" style="margin-left:8px">✕ Remove</button>` : ''}
      `;
    } else if (isPast) {
      statusHtml = '<span class="lookup-notfound">Past — no sign-up</span>';
    } else {
      const isOwner = currentUser && currentNetOwnerId === currentUser.id;
      statusHtml = `
        <button class="btn btn-success btn-sm" onclick="openSignupModal(${slot.schedule_id}, '${slot.slot_date}', '${esc(dateStr)}')">+ I'll run control</button>
        ${isOwner ? `<button class="btn btn-ghost btn-sm" style="margin-left:6px" onclick="openAssignModal(${slot.schedule_id}, '${slot.slot_date}', '${esc(dateStr)}')">👤 Assign</button>` : ''}
      `;
    }

    return `<div class="slot-row${isPast ? '" style="opacity:.5' : ''}">
      <span class="slot-date">${dateStr}</span>
      <span class="slot-time text-muted">${esc(sched.start_time || '')} ${esc(sched.timezone || '')}</span>
      <span class="slot-status">${statusHtml}</span>
    </div>`;
  }).join('');
}

// ============================================================
// SIGNUP MODAL
// ============================================================
function openSignupModal(scheduleId, slotDate, dateLabel) {
  document.getElementById('signup-schedule-id').value = scheduleId;
  document.getElementById('signup-slot-date').value   = slotDate;
  document.getElementById('signup-date-label').textContent = dateLabel;
  // Pre-fill from current user
  if (currentUser) {
    document.getElementById('signup-callsign').value = currentUser.callsign;
    document.getElementById('signup-name').value     = currentUser.name || '';
    document.getElementById('signup-email').value    = currentUser.email || '';
  }
  const modal = document.getElementById('signup-modal');
  modal.style.display = 'flex';
}

function closeSignupModal() {
  document.getElementById('signup-modal').style.display = 'none';
}

async function submitSignup() {
  const schedule_id = parseInt(document.getElementById('signup-schedule-id').value);
  const slot_date   = document.getElementById('signup-slot-date').value;
  const callsign    = document.getElementById('signup-callsign').value.trim().toUpperCase();
  const name        = document.getElementById('signup-name').value.trim() || null;
  const email       = document.getElementById('signup-email').value.trim() || null;
  const notes       = document.getElementById('signup-notes').value.trim() || null;
  if (!callsign) return toast('Callsign required', 'error');
  try {
    await apiFetch(`/nets/${currentNetId}/signups`, {
      method: 'POST',
      body: JSON.stringify({ schedule_id, slot_date, callsign, name, email, notes }),
    });
    toast(`${callsign} signed up for net control`, 'success');
    closeSignupModal();
    await loadUpcoming();
  } catch (e) { toast(e.message, 'error'); }
}

async function removeSignup(id) {
  if (!confirm('Remove this sign-up?')) return;
  try {
    await apiFetch(`/signups/${id}`, { method: 'DELETE' });
    toast('Sign-up removed');
    await loadUpcoming();
  } catch (e) { toast(e.message, 'error'); }
}

// Close modals on backdrop click
document.getElementById('signup-modal').addEventListener('click', function(e) {
  if (e.target === this) closeSignupModal();
});
document.getElementById('assign-modal').addEventListener('click', function(e) {
  if (e.target === this) closeAssignModal();
});

// ============================================================
// ASSIGN MODAL
// ============================================================
function openAssignModal(scheduleId, slotDate, dateLabel) {
  document.getElementById('assign-schedule-id').value = scheduleId;
  document.getElementById('assign-slot-date').value   = slotDate;
  document.getElementById('assign-date-label').textContent = dateLabel;
  document.getElementById('assign-notes').value = '';
  document.getElementById('assign-preview').style.display = 'none';

  // Populate user dropdown
  const sel = document.getElementById('assign-user-select');
  sel.innerHTML = '<option value="">— choose a registered operator —</option>' +
    registeredUsers.map(u =>
      `<option value="${u.id}">${esc(u.callsign)} — ${esc(u.name)}</option>`
    ).join('');

  document.getElementById('assign-modal').style.display = 'flex';
}

function closeAssignModal() {
  document.getElementById('assign-modal').style.display = 'none';
}

function onAssignUserChange() {
  const sel = document.getElementById('assign-user-select');
  const userId = parseInt(sel.value);
  const preview = document.getElementById('assign-preview');
  if (!userId) { preview.style.display = 'none'; return; }
  const user = registeredUsers.find(u => u.id === userId);
  if (!user) { preview.style.display = 'none'; return; }
  document.getElementById('assign-preview-call').textContent = user.callsign;
  document.getElementById('assign-preview-name').textContent = ' — ' + user.name;
  preview.style.display = '';
}

async function submitAssign() {
  const schedule_id      = parseInt(document.getElementById('assign-schedule-id').value);
  const slot_date        = document.getElementById('assign-slot-date').value;
  const assigned_user_id = parseInt(document.getElementById('assign-user-select').value);
  const notes            = document.getElementById('assign-notes').value.trim() || null;
  if (!assigned_user_id) return toast('Please select an operator', 'error');
  try {
    await apiFetch(`/nets/${currentNetId}/signups`, {
      method: 'POST',
      body: JSON.stringify({ schedule_id, slot_date, assigned_user_id, notes }),
    });
    const user = registeredUsers.find(u => u.id === assigned_user_id);
    toast(`${user ? user.callsign : 'Operator'} assigned as net control`, 'success');
    closeAssignModal();
    await loadUpcoming();
  } catch (e) { toast(e.message, 'error'); }
}

