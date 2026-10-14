// Shared helpers
function csrf() { return document.querySelector('meta[name="csrf-token"]').content; }

function startClock(el) {
  if (!el) return;
  const tick = () => { el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); };
  tick(); setInterval(tick, 1000);
}

async function postJSON(url, data) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(data) });
  let j = {}; try { j = await r.json(); } catch (e) {}
  return j;
}

// Theme Handling (Light / Dark)
function initTheme() {
  function getEffectiveTheme() {
    return document.documentElement.getAttribute('data-theme') || 'light';
  }

  function toggleTheme() {
    const current = getEffectiveTheme();
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) {}
  }

  document.querySelectorAll('.theme-toggle-btn, #theme-toggle, #theme-toggle-login').forEach(btn => {
    btn.removeEventListener('click', toggleTheme);
    btn.addEventListener('click', toggleTheme);
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initTheme);
} else {
  initTheme();
}

if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
