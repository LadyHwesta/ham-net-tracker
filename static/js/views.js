// ============================================================
// VIEWS  (admin / tokens / help / report are standalone pages)
// ============================================================
function showView(view, netId) {
  closeSidebar();
  activeView = view;
  document.getElementById('view-nets').style.display    = view === 'nets'    ? '' : 'none';
  document.getElementById('view-session').style.display = view === 'session' ? '' : 'none';
  document.getElementById('view-history').style.display = view === 'history' ? '' : 'none';
  // schedule-panel lives outside view-session in the DOM — hide it explicitly when leaving session view
  if (view !== 'session') document.getElementById('schedule-panel').style.display = 'none';

  document.querySelectorAll('[id^="nav-"]').forEach(el => el.classList.remove('active'));
  if (view === 'nets')    document.getElementById('nav-nets').classList.add('active');
  if (view === 'history') document.getElementById('nav-history').classList.add('active');

  if (view === 'history') {
    const id = netId || currentNetId || (nets.length ? nets[0].id : null);
    if (id) loadHistory(id);
  }
}
