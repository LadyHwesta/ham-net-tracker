// ============================================================
// BRANDING
// ============================================================
let currentBranding = {};

async function loadBranding() {
  try { currentBranding = await apiFetch('/branding'); } catch { return; }
  applyBranding(currentBranding);
}

function applyBranding(b) {
  // Header title
  const titleEl = document.getElementById('header-title');
  if (b.org_name) {
    titleEl.innerHTML = `<span>${esc(b.org_name)}</span>`;
    document.title = b.org_name + ' — Net Tracker';
  } else {
    titleEl.innerHTML = '<span>Net</span> Tracker';
    document.title = 'Ham Radio Net Tracker';
  }
  // Tagline
  const tagEl = document.getElementById('header-tagline');
  if (b.tagline) { tagEl.textContent = b.tagline; tagEl.style.display = ''; }
  else { tagEl.style.display = 'none'; }
  // Logo
  const logoEl = document.getElementById('header-logo');
  const antennaEl = document.getElementById('header-antenna');
  if (b.has_logo) {
    logoEl.src = '/logo?' + Date.now(); // cache-bust
    antennaEl.style.display = 'none';
  } else {
    logoEl.style.display = 'none';
    antennaEl.style.display = '';
  }
  // Website link on title
  if (b.website_url) {
    titleEl.style.cursor = 'pointer';
    titleEl.onclick = () => window.open(b.website_url, '_blank');
    titleEl.title = b.website_url;
  } else {
    titleEl.style.cursor = '';
    titleEl.onclick = null;
    titleEl.title = '';
  }
}

async function loadAdminBranding() {
  try {
    const b = await apiFetch('/branding');
    document.getElementById('branding-org-name').value = b.org_name || '';
    document.getElementById('branding-tagline').value = b.tagline || '';
    document.getElementById('branding-website-url').value = b.website_url || '';
    const deleteBtn = document.getElementById('branding-logo-delete-btn');
    const preview = document.getElementById('branding-logo-preview');
    if (b.has_logo) {
      preview.src = '/logo?' + Date.now();
      deleteBtn.style.display = '';
    } else {
      preview.style.display = 'none';
      deleteBtn.style.display = 'none';
    }
  } catch {}
}

function previewLogo(input) {
  const file = input.files[0];
  if (!file) return;
  const preview = document.getElementById('branding-logo-preview');
  preview.src = URL.createObjectURL(file);
}

async function saveBranding() {
  const org_name = document.getElementById('branding-org-name').value.trim() || null;
  const tagline  = document.getElementById('branding-tagline').value.trim() || null;
  const website_url = document.getElementById('branding-website-url').value.trim() || null;
  try {
    // Save text settings
    await apiFetch('/admin/branding', { method: 'PUT', body: JSON.stringify({ org_name, tagline, website_url }) });
    // Upload logo if a file was selected
    const fileInput = document.getElementById('branding-logo-file');
    if (fileInput.files[0]) {
      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      await fetch('/admin/branding/logo', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: fd,
      }).then(r => { if (!r.ok) throw new Error('Logo upload failed'); });
      fileInput.value = '';
    }
    toast('Branding saved');
    await loadBranding();
    await loadAdminBranding();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteLogo() {
  if (!confirm('Remove the current logo?')) return;
  try {
    await apiFetch('/admin/branding/logo', { method: 'DELETE' });
    toast('Logo removed');
    await loadBranding();
    await loadAdminBranding();
  } catch (e) { toast(e.message, 'error'); }
}

