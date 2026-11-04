// Keep the standalone tab aligned with the dashboard's persisted theme.
(() => {
  const key = 'runvard_ui_theme';
  function syncTheme() {
    let saved;
    try { saved = localStorage.getItem(key); } catch (_) { /* Use the dashboard default. */ }
    document.documentElement.dataset.uiTheme =
      saved === 'original' || saved === 'tech' ? 'tech' : 'modern';
  }
  syncTheme();
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) syncTheme();
  });
  window.addEventListener('focus', syncTheme);
  window.addEventListener('pageshow', syncTheme);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) syncTheme();
  });
})();
