// Auto-login if token stored
if (token) enterApp();

// Enter to submit login
document.getElementById('login-pass').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
document.getElementById('login-user').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

// ============================================================
// KEYBOARD SHORTCUT  /  → focus callsign input
// ============================================================
document.addEventListener('keydown', e => {
  if (e.key !== '/') return;
  const active = document.activeElement;
  const tag = active ? active.tagName : '';
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  const ciCall = document.getElementById('ci-call');
  if (!ciCall || ciCall.offsetParent === null) return;  // not visible
  e.preventDefault();
  ciCall.focus();
});

