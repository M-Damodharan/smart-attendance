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
  }
});
