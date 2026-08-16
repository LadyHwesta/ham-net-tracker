// ============================================================
// AUTH
// ============================================================
function switchAuthTab(tab) {
  document.querySelectorAll('.auth-tab').forEach((el, i) => {
    el.classList.toggle('active', (i === 0 && tab === 'login') || (i === 1 && tab === 'register'));
  });
  document.getElementById('tab-login').style.display = tab === 'login' ? '' : 'none';
  document.getElementById('tab-register').style.display = tab === 'register' ? '' : 'none';
  document.getElementById('auth-error').style.display = 'none';
}

async function doLogin() {
  clearAuthError();
  const user = document.getElementById('login-user').value.trim();
  const pass = document.getElementById('login-pass').value;
  if (!user || !pass) return showAuthError('Fill in all fields');
  try {
    const form = new URLSearchParams({ username: user, password: pass });
    const res = await fetch(API + '/auth/login', {
      method: 'POST', body: form,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Login failed');
    token = data.access_token;
    currentUser = data.user;
    localStorage.setItem('nt_token', token);
    enterApp();
  } catch (e) { showAuthError(e.message); }
}

async function doRegister() {
  clearAuthError();
  const callsign = document.getElementById('reg-call').value.trim().toUpperCase();
  const name = document.getElementById('reg-name').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const password = document.getElementById('reg-pass').value;
  if (!callsign || !name || !email || !password) return showAuthError('Fill in all fields');
  try {
    const newUser = await apiFetch('/auth/register', { method: 'POST', body: JSON.stringify({ callsign, name, email, password }) });
    if (newUser.is_active) {
      // First user auto-approved — go straight to login
      toast('Account created — please log in', 'success');
      switchAuthTab('login');
      document.getElementById('login-user').value = callsign;
    } else {
      // Show a confirmation in place of the form
      document.getElementById('tab-register').innerHTML = `
        <div style="text-align:center;padding:12px 0">
          <div style="font-size:36px;margin-bottom:12px">✅</div>
          <h3 style="margin:0 0 10px;color:var(--lc-green)">Registration Submitted!</h3>
          <p style="font-size:13px;color:var(--text-muted);line-height:1.6;margin:0 0 16px">
            Your account request for <strong>${callsign}</strong> has been received.<br>
            An admin will review it and you will receive an email at<br>
            <strong>${email}</strong> once your account has been approved.
          </p>
          <button class="btn btn-ghost btn-sm" onclick="switchAuthTab('login')">← Back to Login</button>
        </div>`;
    }
  } catch (e) { showAuthError(e.message); }
}

function showAuthError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg;
  el.style.display = '';
}
function clearAuthError() { document.getElementById('auth-error').style.display = 'none'; }

function logout() {
  token = null;
  currentUser = null;
  localStorage.removeItem('nt_token');
  location.reload();
}

async function enterApp() {
  if (!currentUser) {
    try { currentUser = await apiFetch('/auth/me'); } catch { logout(); return; }
  }
  document.getElementById('auth-page').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  document.getElementById('header-callsign').textContent = currentUser.callsign;
  // Show admin nav link only for admins
  document.getElementById('nav-admin').style.display = currentUser.is_admin ? '' : 'none';
  // Mobile: show just the callsign badge in the header
  const shortEl = document.getElementById('header-callsign-short');
  if (shortEl) shortEl.textContent = currentUser.callsign;
  await loadBranding();
  await loadNets();
  showView('nets');
}

// Auto-login if token stored
