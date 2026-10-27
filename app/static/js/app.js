// Shared helpers
function csrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) return meta.content;
  const input = document.querySelector('input[name="csrf_token"], input[name="csrf"]');
  if (input && input.value) return input.value;
  return '';
}

function startClock(el) {
  if (!el) return;
  const tick = () => { el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); };
  tick(); setInterval(tick, 1000);
}

async function postJSON(url, data) {
  const tok = csrf();
  const bodyObj = Object.assign({}, data || {}, { csrf_token: tok });
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': tok }, body: JSON.stringify(bodyObj) });
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

// ==========================================
// AI Assistant Chat Controller
// ==========================================
function toggleAIChat(show) {
  const modal = document.getElementById('ai-assistant-modal');
  if (!modal) return;
  if (show === undefined) {
    show = modal.style.display === 'none' || modal.getAttribute('aria-hidden') === 'true';
  }
  if (show) {
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    const input = document.getElementById('ai-user-input');
    if (input) {
      setTimeout(() => input.focus(), 100);
    }
  } else {
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
  }
}

function clearAIChat() {
  const container = document.getElementById('ai-chat-messages');
  if (!container) return;
  container.innerHTML = `
    <div class="ai-msg ai-bot">
      <div class="ai-bubble">
        Conversation cleared. How can I assist you with your attendance, shifts, or working hours today?
      </div>
    </div>
  `;
}

function escapeHTML(str) {
  const p = document.createElement('p');
  p.textContent = str;
  return p.innerHTML;
}

function formatBotResponse(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  let safe = div.innerHTML;

  // Format bold **text**
  safe = safe.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

  // Format list items and paragraphs
  const lines = safe.split('\n');
  let inList = false;
  let formatted = [];
  for (let line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('* ') || trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
      if (!inList) {
        formatted.push('<ul class="ai-chat-ul">');
        inList = true;
      }
      formatted.push(`<li>${trimmed.substring(2)}</li>`);
    } else {
      if (inList) {
        formatted.push('</ul>');
        inList = false;
      }
      if (trimmed) {
        formatted.push(`<p>${trimmed}</p>`);
      }
    }
  }
  if (inList) formatted.push('</ul>');
  return formatted.join('');
}

function appendAIMessage(sender, htmlContent, isTyping = false) {
  const container = document.getElementById('ai-chat-messages');
  if (!container) return null;
  const msgDiv = document.createElement('div');
  msgDiv.className = `ai-msg ai-${sender}` + (isTyping ? ' ai-typing-msg' : '');
  const bubble = document.createElement('div');
  bubble.className = 'ai-bubble';
  bubble.innerHTML = htmlContent;
  msgDiv.appendChild(bubble);
  container.appendChild(msgDiv);
  container.scrollTop = container.scrollHeight;
  return msgDiv;
}

async function handleAIChatSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  const input = document.getElementById('ai-user-input');
  if (!input) return;
  const query = input.value.trim();
  if (!query) return;

  // Add user message bubble
  appendAIMessage('user', escapeHTML(query));
  input.value = '';

  // Show typing indicator
  const typingMsg = appendAIMessage('bot', '<span class="ai-dots"><span></span><span></span><span></span></span> Thinking...', true);

  try {
    const res = await postJSON('/api/ai/chat', { message: query });
    if (typingMsg && typingMsg.parentNode) {
      typingMsg.parentNode.removeChild(typingMsg);
    }
    if (res && res.ok && res.reply) {
      appendAIMessage('bot', formatBotResponse(res.reply));
    } else {
      const err = (res && res.error) || 'Sorry, I could not retrieve that attendance information right now.';
      appendAIMessage('bot', `⚠️ ${escapeHTML(err)}`);
    }
  } catch (err) {
    if (typingMsg && typingMsg.parentNode) {
      typingMsg.parentNode.removeChild(typingMsg);
    }
    appendAIMessage('bot', '⚠️ Connection error. Please check your network and try again.');
  }
}

function sendAIPrompt(text) {
  const input = document.getElementById('ai-user-input');
  if (input) {
    input.value = text;
    handleAIChatSubmit();
  }
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('ai-assistant-modal');
    if (modal && modal.style.display !== 'none' && modal.getAttribute('aria-hidden') === 'false') {
      toggleAIChat(false);
    }
    closeProfileModal();
    closeEditProfileModal();
    closeSwitchAccountModal();
  }
});

// Auto-expand AI textarea & Enter to submit
document.addEventListener('DOMContentLoaded', () => {
  const aiInput = document.getElementById('ai-user-input');
  if (aiInput) {
    aiInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleAIChatSubmit(e);
      }
    });
  }
});

// ==========================================
// Profile Dropdown & Account Switching
// ==========================================
function toggleProfileMenu(e) {
  if (e) e.stopPropagation();
  const m = document.getElementById('profile-dropdown-menu');
  if (!m) return;
  const isHidden = m.style.display === 'none';
  m.style.display = isHidden ? 'flex' : 'none';
}

document.addEventListener('click', (e) => {
  const m = document.getElementById('profile-dropdown-menu');
  const trigger = document.getElementById('profile-menu-trigger');
  if (m && m.style.display !== 'none' && trigger && !trigger.contains(e.target) && !m.contains(e.target)) {
    m.style.display = 'none';
  }
});

function openProfileModal() {
  const modal = document.getElementById('profile-modal') || document.getElementById('profile-view-modal');
  if (modal) modal.style.display = 'flex';
}
function closeProfileModal() {
  const modal = document.getElementById('profile-modal') || document.getElementById('profile-view-modal');
  if (modal) modal.style.display = 'none';
}

async function handleProfileUpdate(e) {
  if (e && e.preventDefault) e.preventDefault();
  const phone = document.getElementById('prof_phone')?.value.trim();
  const email = document.getElementById('prof_email')?.value.trim();
  const emergency_contact = document.getElementById('prof_emergency')?.value.trim();
  const status = document.getElementById('profile-update-status');
  const btn = document.getElementById('btn-save-profile');
  if (btn) btn.disabled = true;
  if (status) {
    status.style.display = 'block';
    status.className = 'muted small';
    status.textContent = 'Saving details...';
  }
  try {
    const res = await postJSON('/auth/update-profile', { phone, email, emergency_contact });
    if (res && res.ok) {
      if (status) {
        status.className = 'pill ok';
        status.textContent = '✓ Profile details saved successfully!';
      }
      setTimeout(() => location.reload(), 700);
    } else {
      if (status) {
        status.className = 'pill bad';
        status.textContent = (res && res.error) || 'Failed to update details.';
      }
      if (btn) btn.disabled = false;
    }
  } catch (err) {
    if (status) {
      status.className = 'pill bad';
      status.textContent = 'Connection error. Please try again.';
    }
    if (btn) btn.disabled = false;
  }
}

let switchableAccountsData = [];
async function openSwitchAccountModal() {
  const m = document.getElementById('profile-dropdown-menu');
  if (m) m.style.display = 'none';
  const modal = document.getElementById('switch-account-modal');
  if (modal) modal.style.display = 'flex';
  const list = document.getElementById('switchable-accounts-list');
  if (list) list.innerHTML = '<div class="muted small center" style="padding:1.5rem 0">Loading accounts...</div>';
  try {
    const res = await fetch('/auth/switchable-accounts').then(r => r.json());
    if (res && res.ok && res.accounts) {
      switchableAccountsData = res.accounts;
      renderSwitchableAccounts(switchableAccountsData);
    } else {
      if (list) list.innerHTML = '<div class="pill bad center">No switchable accounts available.</div>';
    }
  } catch (e) {
    if (list) list.innerHTML = '<div class="pill bad center">Failed to load accounts.</div>';
  }
}
function closeSwitchAccountModal() {
  const modal = document.getElementById('switch-account-modal');
  if (modal) modal.style.display = 'none';
}

function renderSwitchableAccounts(accs) {
  const list = document.getElementById('switchable-accounts-list');
  if (!list) return;
  if (!accs.length) {
    list.innerHTML = '<div class="muted small center" style="padding:1rem 0">No matching accounts found.</div>';
    return;
  }
  list.innerHTML = accs.map(a => `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:12px;gap:10px">
      <div style="display:flex;align-items:center;gap:10px;min-width:0">
        <div class="user-avatar" style="width:36px;height:36px;font-size:0.95rem;flex-shrink:0">${escapeHTML(a.name.charAt(0).toUpperCase())}</div>
        <div style="min-width:0">
          <strong style="display:block;font-size:0.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHTML(a.name)}</strong>
          <span class="muted small" style="display:block;font-size:0.75rem">${escapeHTML(a.emp_code)} &bull; ${escapeHTML(a.department || 'General')} &bull; <span class="badge sm" style="font-size:0.68rem;padding:0 5px">${a.role === 'hr' ? 'HR Admin' : (a.category_name || 'Staff')}</span></span>
        </div>
      </div>
      <button type="button" class="btn sm primary" style="flex-shrink:0" onclick="confirmSwitchToAccount(${a.id}, '${escapeHTML(a.name)}')">Switch &rarr;</button>
    </div>
  `).join('');
}

function filterSwitchableAccounts() {
  const q = (document.getElementById('switch-account-search')?.value || '').toLowerCase().trim();
  if (!q) {
    renderSwitchableAccounts(switchableAccountsData);
    return;
  }
  const filtered = switchableAccountsData.filter(a => 
    a.name.toLowerCase().includes(q) || a.emp_code.toLowerCase().includes(q) || (a.department && a.department.toLowerCase().includes(q))
  );
  renderSwitchableAccounts(filtered);
}

function confirmSwitchToAccount(id, name) {
  if (confirm(`Are you sure you want to switch your active session to ${name}?`)) {
    switchToAccount(id);
  }
}

async function switchToAccount(targetId) {
  try {
    const res = await postJSON('/auth/switch-account', { target_id: targetId });
    if (res && res.ok && res.redirect) {
      window.location.href = res.redirect;
    } else {
      alert((res && res.error) || 'Failed to switch account.');
    }
  } catch (err) {
    alert('Failed to connect to server for account switch.');
  }
}

// ==========================================
// Comments Engine (Requirement #9)
// ==========================================
async function loadComments(entityType, entityId, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '<div class="muted small" style="padding:0.5rem 0">Loading comments...</div>';
  try {
    const res = await fetch(`/api/comments/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}`).then(r => r.json());
    if (res && res.ok && res.comments) {
      renderCommentsList(res.comments, containerId);
    } else {
      container.innerHTML = '<div class="muted small" style="padding:0.5rem 0">No comments yet.</div>';
    }
  } catch (e) {
    container.innerHTML = '<div class="muted small" style="padding:0.5rem 0">Failed to load comments.</div>';
  }
}

function renderCommentsList(comments, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!comments.length) {
    container.innerHTML = '<div class="muted small" style="padding:0.5rem 0">No comments recorded yet. Add a note or remark below.</div>';
    return;
  }
  container.innerHTML = comments.map(c => `
    <div class="comment-item" style="padding:8px 12px;background:var(--bg-surface);border:1px solid var(--border-default);border-radius:10px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;font-size:0.75rem">
        <div>
          <b style="color:var(--text-main)">${escapeHTML(c.author_name)}</b>
          <span class="muted">(${escapeHTML(c.author_code)}) &bull; <span class="badge sm" style="font-size:0.65rem;padding:0 5px">${c.author_role === 'hr' ? 'HR' : 'Staff'}</span></span>
        </div>
        <span class="muted">${escapeHTML(c.created_at)}</span>
      </div>
      <div style="font-size:0.85rem;color:var(--text-main);line-height:1.4">${escapeHTML(c.content)}</div>
    </div>
  `).join('');
}

async function submitComment(e, entityType, entityId, inputId, containerId, statusId) {
  if (e && e.preventDefault) e.preventDefault();
  const input = document.getElementById(inputId);
  const statusEl = statusId ? document.getElementById(statusId) : null;
  if (!input) return;
  const content = input.value.trim();
  if (!content) {
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.className = 'pill bad';
      statusEl.textContent = 'Please enter comment text before submitting.';
    }
    return;
  }
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.className = 'muted small';
    statusEl.textContent = 'Posting comment...';
  }
  try {
    const res = await postJSON('/api/comments', { entity_type: entityType, entity_id: entityId, content });
    if (res && res.ok && res.comment) {
      input.value = '';
      if (statusEl) {
        statusEl.className = 'pill ok';
        statusEl.textContent = '✓ Comment posted!';
        setTimeout(() => { statusEl.style.display = 'none'; }, 2000);
      }
      loadComments(entityType, entityId, containerId);
    } else {
      if (statusEl) {
        statusEl.className = 'pill bad';
        statusEl.textContent = (res && res.error) || 'Failed to post comment.';
      }
    }
  } catch (err) {
    if (statusEl) {
      statusEl.className = 'pill bad';
      statusEl.textContent = 'Connection error. Please try again.';
    }
  }
}

// =========================================================================
// Real-Time Notifications & Pop Alert Engine
// =========================================================================
let seenNotifIds = new Set(JSON.parse(sessionStorage.getItem('seen_notifs') || '[]'));
let isNotifMenuOpen = false;

function playNotificationSound() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.14);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.32);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.32);
  } catch (e) {}
}

function showNotificationPopToast(notif) {
  const container = document.getElementById('notif-toast-container');
  if (!container) return;

  playNotificationSound();
  if (navigator.vibrate) {
    try { navigator.vibrate([100, 50, 100]); } catch (e) {}
  }

  const kindIcons = {
    leave: '🗓️',
    od: '💼',
    permission: '⏳',
    face_change: '👤',
    face: '👤',
    info: '🔔'
  };
  const icon = kindIcons[notif.kind] || '🔔';

  let toneClass = 'info';
  const tLower = (notif.title || '').toLowerCase();
  if (tLower.includes('approved')) toneClass = 'approved';
  else if (tLower.includes('rejected')) toneClass = 'rejected';
  else if (tLower.includes('new') || tLower.includes('request')) toneClass = 'request';

  const toast = document.createElement('div');
  toast.className = `notif-toast ${toneClass}`;
  toast.id = `notif-toast-${notif.id}`;
  toast.innerHTML = `
    <div class="notif-toast-icon">${icon}</div>
    <div class="notif-toast-body">
      <div class="notif-toast-title">${escapeHTML(notif.title)}</div>
      <div class="notif-toast-msg">${escapeHTML(notif.message)}</div>
      ${notif.link_url ? `<a href="${notif.link_url}" onclick="markNotificationRead(${notif.id})" class="notif-toast-action">View Details &rarr;</a>` : ''}
    </div>
    <button type="button" class="notif-toast-close" onclick="dismissToast(${notif.id})" title="Dismiss">&times;</button>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    dismissToast(notif.id);
  }, 9000);
}

function dismissToast(nid) {
  const toast = document.getElementById(`notif-toast-${nid}`);
  if (toast && !toast.classList.contains('dismissing')) {
    toast.classList.add('dismissing');
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }
}

function toggleNotificationMenu(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  const menu = document.getElementById('notif-dropdown-menu');
  if (!menu) return;

  const profMenu = document.getElementById('profile-dropdown-menu');
  if (profMenu) profMenu.style.display = 'none';

  isNotifMenuOpen = (menu.style.display === 'none' || !menu.style.display);
  menu.style.display = isNotifMenuOpen ? 'block' : 'none';

  if (isNotifMenuOpen) {
    fetchNotifications(true);
  }
}

async function fetchNotifications(updateList = false) {
  try {
    const res = await fetch('/api/notifications').then(r => r.json());
    if (!res || !res.ok) return;

    const badge = document.getElementById('notif-badge');
    const pill = document.getElementById('notif-unread-pill');

    if (res.unread_count > 0) {
      if (badge) {
        badge.textContent = res.unread_count > 99 ? '99+' : res.unread_count;
        badge.style.display = 'block';
      }
      if (pill) {
        pill.textContent = `${res.unread_count} unread`;
        pill.style.display = 'inline-block';
      }
    } else {
      if (badge) badge.style.display = 'none';
      if (pill) pill.style.display = 'none';
    }

    if (res.notifications && res.notifications.length) {
      res.notifications.forEach(n => {
        if (n.status === 'unread' && !seenNotifIds.has(n.id)) {
          seenNotifIds.add(n.id);
          sessionStorage.setItem('seen_notifs', JSON.stringify(Array.from(seenNotifIds)));
          showNotificationPopToast(n);
        }
      });
    }

    if (updateList || isNotifMenuOpen) {
      renderNotificationsList(res.notifications || []);
    }
  } catch (e) {}
}

function renderNotificationsList(notifs) {
  const listEl = document.getElementById('notif-dropdown-list');
  if (!listEl) return;

  if (!notifs.length) {
    listEl.innerHTML = '<div class="notif-empty-state">No notifications right now. You are all caught up!</div>';
    return;
  }

  const kindIcons = {
    leave: '🗓️',
    od: '💼',
    permission: '⏳',
    face_change: '👤',
    face: '👤',
    info: '🔔'
  };

  listEl.innerHTML = notifs.map(n => {
    const icon = kindIcons[n.kind] || '🔔';
    const isUnread = n.status === 'unread';
    return `
      <div class="notif-item ${isUnread ? 'unread' : ''}" onclick="onNotificationItemClick(${n.id}, '${n.link_url || ''}')">
        <div class="notif-icon-circle">${icon}</div>
        <div class="notif-content">
          <div class="notif-title">
            <span>${escapeHTML(n.title)}</span>
            ${isUnread ? '<span class="status-ping" style="width:7px;height:7px;margin-left:6px;"></span>' : ''}
          </div>
          <div class="notif-message">${escapeHTML(n.message)}</div>
          <span class="notif-time">${escapeHTML(n.created_at)}</span>
        </div>
      </div>
    `;
  }).join('');
}

async function markNotificationRead(nid) {
  try {
    await postJSON(`/api/notifications/read/${nid}`);
    fetchNotifications(false);
  } catch (e) {}
}

async function onNotificationItemClick(nid, linkUrl) {
  try {
    await postJSON(`/api/notifications/read/${nid}`);
  } catch (e) {}
  if (linkUrl) {
    window.location.href = linkUrl;
  } else {
    fetchNotifications(true);
  }
}

async function markAllNotificationsRead(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  try {
    await postJSON('/api/notifications/read-all');
    fetchNotifications(true);
  } catch (e) {}
}

document.addEventListener('click', function(e) {
  const notifMenu = document.getElementById('notif-dropdown-menu');
  const notifBtn = document.getElementById('notif-bell-btn');
  if (notifMenu && notifBtn && !notifMenu.contains(e.target) && !notifBtn.contains(e.target)) {
    notifMenu.style.display = 'none';
    isNotifMenuOpen = false;
  }
});

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    fetchNotifications(false);
    setInterval(() => fetchNotifications(false), 12000);
  });
} else {
  fetchNotifications(false);
  setInterval(() => fetchNotifications(false), 12000);
}

// ==========================================================================
// Instant Navigation & Link Prefetching Engine (Sub-Millisecond Page Clicks)
// ==========================================================================
(function() {
  const prefetchCache = new Map();
  let progressBar = null;
  let progressTimer = null;

  function getProgressBar() {
    if (!progressBar) {
      progressBar = document.getElementById('instant-progress-bar');
      if (!progressBar) {
        progressBar = document.createElement('div');
        progressBar.id = 'instant-progress-bar';
        document.body.appendChild(progressBar);
      }
    }
    return progressBar;
  }

  function startProgress() {
    const bar = getProgressBar();
    if (progressTimer) clearInterval(progressTimer);
    bar.classList.add('active');
    bar.style.width = '25%';

    let w = 25;
    progressTimer = setInterval(() => {
      w += (90 - w) * 0.2;
      bar.style.width = `${Math.min(w, 88)}%`;
    }, 100);
  }

  function finishProgress() {
    const bar = getProgressBar();
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    bar.style.width = '100%';
    setTimeout(() => {
      bar.classList.remove('active');
      setTimeout(() => {
        bar.style.width = '0%';
      }, 200);
    }, 150);
  }

  function isValidPrefetchUrl(urlStr) {
    if (!urlStr || urlStr.startsWith('#') || urlStr.startsWith('javascript:') || urlStr.startsWith('tel:') || urlStr.startsWith('mailto:')) {
      return false;
    }
    try {
      const u = new URL(urlStr, window.location.origin);
      if (u.origin !== window.location.origin) return false;
      const p = u.pathname;
      if (p.startsWith('/logout') || p.startsWith('/login') || p.includes('/download') || p.includes('/api/') || p === '/change-password') {
        return false;
      }
      return true;
    } catch (e) {
      return false;
    }
  }

  function prefetchUrl(urlStr) {
    if (!isValidPrefetchUrl(urlStr)) return;
    try {
      const u = new URL(urlStr, window.location.origin);
      const cleanUrl = u.pathname + u.search;
      if (cleanUrl === window.location.pathname + window.location.search) return;

      const cached = prefetchCache.get(cleanUrl);
      if (cached && (Date.now() - cached.ts < 30000)) return;

      const fetchPromise = fetch(cleanUrl, {
        priority: 'low',
        headers: { 'X-Requested-With': 'InstantNav' }
      }).then(r => r.ok ? r.text() : null).catch(() => null);

      prefetchCache.set(cleanUrl, { promise: fetchPromise, ts: Date.now() });
    } catch (e) {}
  }

  // Pre-warm primary navigation links 800ms after initial page load
  function prewarmNav() {
    document.querySelectorAll('nav.nav a[href], .brand[href]').forEach(a => {
      prefetchUrl(a.getAttribute('href'));
    });
  }

  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(prewarmNav, { timeout: 2500 });
  } else {
    setTimeout(prewarmNav, 900);
  }

  // Prefetch on hover (desktop) or touchstart (mobile / Android WebView)
  document.addEventListener('pointerenter', function(e) {
    const a = e.target.closest('a');
    if (a && a.href) prefetchUrl(a.getAttribute('href'));
  }, true);

  document.addEventListener('touchstart', function(e) {
    const a = e.target.closest('a');
    if (a && a.href) prefetchUrl(a.getAttribute('href'));
  }, { passive: true, capture: true });

  // Instant SPA Navigation
  async function navigateInstant(url, push = true) {
    startProgress();
    const mainWrap = document.querySelector('main.wrap');
    if (mainWrap) mainWrap.classList.add('instant-loading');

    const u = new URL(url, window.location.origin);
    const cleanUrl = u.pathname + u.search;

    let html = null;
    const cached = prefetchCache.get(cleanUrl);
    if (cached && (Date.now() - cached.ts < 30000)) {
      html = await cached.promise;
    }

    if (!html) {
      try {
        const resp = await fetch(cleanUrl, { headers: { 'X-Requested-With': 'InstantNav' } });
        if (!resp.ok) {
          window.location.href = url;
          return;
        }
        html = await resp.text();
      } catch (err) {
        window.location.href = url;
        return;
      }
    }

    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      const newMain = doc.querySelector('main.wrap');
      const oldMain = document.querySelector('main.wrap');

      if (!newMain || !oldMain) {
        window.location.href = url;
        return;
      }

      // Update content and title
      oldMain.innerHTML = newMain.innerHTML;
      oldMain.className = newMain.className;
      document.title = doc.title;

      // Update active navigation state
      const currentPath = u.pathname;
      document.querySelectorAll('nav.nav a').forEach(a => {
        const aHref = a.getAttribute('href');
        if (aHref) {
          const aPath = new URL(aHref, window.location.origin).pathname;
          if (aPath === currentPath) {
            a.classList.add('on');
          } else {
            a.classList.remove('on');
          }
        }
      });

      if (push) {
        window.history.pushState({ url }, '', url);
      }

      window.scrollTo({ top: 0, behavior: 'instant' });

      // Run any page-specific inline scripts in new content
      newMain.querySelectorAll('script').forEach(s => {
        const scr = document.createElement('script');
        if (s.src) {
          scr.src = s.src;
        } else {
          scr.textContent = s.textContent;
        }
        document.body.appendChild(scr);
      });

      // Reinitialize theme toggle & dynamic components
      initTheme();
      if (typeof fetchNotifications === 'function') fetchNotifications(false);

      if (mainWrap) mainWrap.classList.remove('instant-loading');
      finishProgress();
    } catch (e) {
      window.location.href = url;
    }
  }

  // Intercept clicks on links for instant response
  document.addEventListener('click', function(e) {
    const link = e.target.closest('a');
    if (!link || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (link.getAttribute('target') || link.getAttribute('download') !== null) return;

    const href = link.getAttribute('href');
    if (!isValidPrefetchUrl(href)) return;

    const targetUrl = new URL(href, window.location.origin);

    // Let hardware/biometric camera pages navigate natively with immediate visual feedback
    if (targetUrl.pathname === '/punch' || targetUrl.pathname === '/face-change' || targetUrl.pathname.includes('/onboarding-face')) {
      startProgress();
      return;
    }

    e.preventDefault();
    navigateInstant(targetUrl.href);
  });

  // Handle browser Back / Forward navigation
  window.addEventListener('popstate', function(e) {
    if (e.state && e.state.url) {
      navigateInstant(e.state.url, false);
    } else {
      navigateInstant(window.location.href, false);
    }
  });
})();

