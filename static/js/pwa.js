// ============================================================
// PWA — service worker registration
// ============================================================
// Loaded on every page so the app is installable everywhere, not just the
// check-in flow. No-op, no error surfaced, on browsers without support.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Registration failing (unsupported context, blocked, etc.) shouldn't
      // interrupt using the app — it's an enhancement, not a requirement.
    });
  });
}
