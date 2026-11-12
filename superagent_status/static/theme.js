"use strict";

// Applied synchronously in <head> so the saved theme is in place before first paint.
(() => {
  const STORAGE_KEY = "superagent-theme";
  const DEFAULT_THEME = "dark";
  let theme = DEFAULT_THEME;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") {
      theme = saved;
    }
  } catch (error) {
    // Storage can be unavailable; keep the default theme.
  }
  document.documentElement.dataset.theme = theme;
})();
