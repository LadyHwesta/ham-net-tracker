// ============================================================
// VIEWS
// ============================================================
function showView(view, netId) {
  closeSidebar();
  activeView = view;
  document.getElementById('view-nets').style.display    = view === 'nets'    ? '' : 'none';
  document.getElementById('view-session').style.display = view === 'session' ? '' : 'none';
  document.getElementById('view-history').style.display = view === 'history' ? '' : 'none';
  document.getElementById('view-admin').style.display   = view === 'admin'   ? '' : 'none';
  document.getElementById('view-help').style.display    = view === 'help'    ? '' : 'none';
  document.getElementById('view-report').style.display  = view === 'report'  ? '' : 'none';
  document.getElementById('view-tokens').style.display  = view === 'tokens'  ? '' : 'none';
  // schedule-panel lives outside view-session in the DOM — hide it explicitly when leaving session view
  if (view !== 'session') document.getElementById('schedule-panel').style.display = 'none';

  document.querySelectorAll('[id^="nav-"]').forEach(el => el.classList.remove('active'));
  if (view === 'nets')    document.getElementById('nav-nets').classList.add('active');
  if (view === 'admin')   document.getElementById('nav-admin').classList.add('active');
  if (view === 'history') document.getElementById('nav-history').classList.add('active');
  if (view === 'report')  document.getElementById('nav-report').classList.add('active');
  if (view === 'tokens')  document.getElementById('nav-tokens').classList.add('active');
  if (view === 'help') {
    document.getElementById('nav-help').classList.add('active');
    const isAdmin = currentUser && currentUser.is_admin;
    document.getElementById('help-admin-section').style.display = isAdmin ? '' : 'none';
    document.getElementById('help-db-section').style.display    = isAdmin ? '' : 'none';
  }

  if (view === 'history') {
    const id = netId || currentNetId || (nets.length ? nets[0].id : null);
    if (id) loadHistory(id);
  }
  if (view === 'admin')  { loadAdminUsers(); loadAdminBranding(); }
  if (view === 'tokens') { loadApiTokens(); }
}

