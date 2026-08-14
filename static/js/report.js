// ============================================================
// REPORT / REQUEST
// ============================================================
async function submitReport() {
  const type    = document.getElementById('report-type').value;
  const subject = document.getElementById('report-subject').value.trim();
  const body    = document.getElementById('report-body').value.trim();
  const statusEl = document.getElementById('report-status');

  if (!subject) { showReportStatus('Please enter a subject.', 'error'); return; }
  if (!body)    { showReportStatus('Please enter some details.', 'error'); return; }

  try {
    await apiFetch('/support/ticket', {
      method: 'POST',
      body: JSON.stringify({ type, subject, body }),
    });
    showReportStatus('Your message was sent successfully. Thank you!', 'success');
    document.getElementById('report-subject').value = '';
    document.getElementById('report-body').value = '';
    document.getElementById('report-type').value = 'Bug Report';
  } catch (e) {
    showReportStatus('Failed to send: ' + e.message, 'error');
  }
}

function clearReport() {
  document.getElementById('report-type').value = 'Bug Report';
  document.getElementById('report-subject').value = '';
  document.getElementById('report-body').value = '';
  document.getElementById('report-status').style.display = 'none';
}

function showReportStatus(msg, type) {
  const el = document.getElementById('report-status');
  el.textContent = msg;
  el.style.display = '';
  el.style.background = type === 'success' ? 'rgba(0,200,100,0.15)' : 'rgba(255,50,50,0.15)';
  el.style.border = type === 'success' ? '1px solid var(--lc-green)' : '1px solid var(--lc-red)';
  el.style.color = type === 'success' ? 'var(--lc-green)' : 'var(--lc-red)';
}

