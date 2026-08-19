// ============================================================
// THEME
// ============================================================
const THEME_STORAGE_KEY = 'nt_theme';

function resolveSystemTheme() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

// Applies a (possibly 'system') theme value to the document.
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === 'system' ? resolveSystemTheme() : theme;
  const sel = document.getElementById('theme-select');
  if (sel) sel.value = theme;
}

// Called once currentUser is known (from login response or /auth/me) --
// reconciles the DB source of truth with the local cache/DOM.
function syncThemeFromUser(user) {
  const theme = (user && user.theme) || 'lcars';
  localStorage.setItem(THEME_STORAGE_KEY, theme);
  applyTheme(theme);
  watchSystemTheme(theme);
}

// Live-update only while the active preference is 'system'.
let _systemThemeMql = null;
function watchSystemTheme(activeTheme) {
  if (_systemThemeMql) {
    _systemThemeMql.removeEventListener('change', _onSystemThemeChange);
    _systemThemeMql = null;
  }
  if (activeTheme !== 'system' || !window.matchMedia) return;
  _systemThemeMql = window.matchMedia('(prefers-color-scheme: dark)');
  _systemThemeMql.addEventListener('change', _onSystemThemeChange);
}
function _onSystemThemeChange() {
  document.documentElement.dataset.theme = resolveSystemTheme();
}

// User picked a new theme from the <select> -- persist to DB, then re-apply.
async function saveTheme(theme) {
  try {
    const updated = await apiFetch('/auth/theme', { method: 'PATCH', body: JSON.stringify({ theme }) });
    currentUser = updated;
    localStorage.setItem(THEME_STORAGE_KEY, theme);
    applyTheme(theme);
    watchSystemTheme(theme);
  } catch (e) {
    toast('Could not save theme: ' + e.message, 'error');
  }
}
