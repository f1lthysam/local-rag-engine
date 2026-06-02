/**
 * chatbot.js — Alian Software RAG Chatbot Widget
 * ================================================
 * Self-contained embeddable widget. Injects its own HTML and CSS.
 *
 * Embed on any site with:
 *   <script src="https://your-server.com/widget/chatbot.js"></script>
 *
 * Override defaults before the script tag:
 *   <script>
 *     window.AlianChatConfig = {
 *       apiBase: 'https://your-server.com',
 *       welcomeMessage: 'Custom welcome text',
 *     };
 *   </script>
 */

(function () {
  'use strict';

  /* ── Detect own URL so CSS loads from same server ─────────────────────── */
  const _scriptEl = document.currentScript ||
    Array.from(document.getElementsByTagName('script')).find((script) =>
      /\/widget\/chatbot\.js(?:[?#].*)?$/.test(script.src || '')
    );
  const _scriptBase = _scriptEl && _scriptEl.src
    ? new URL('.', _scriptEl.src).href
    : new URL('/widget/', window.location.origin).href;

  /* ── Configuration (override via window.AlianChatConfig) ───────────────── */
  const CFG = Object.assign(
    {
      apiBase:        'http://localhost:8000',
      cssUrl:         _scriptBase + 'chatbot.css',
      siteName:       'Alian Software',
      assistantName:  'Alian Assistant',
      placeholder:    'Ask anything about Alian Software…',
      welcomeTitle:   'Hi, I\'m the Alian Assistant',
      welcomeMessage: 'Ask me about our services, technologies, pricing, or anything on the Alian Software website.',
      hints: [
        'What services do you offer?',
        'How can I get started?',
        'What technologies do you use?',
        'Tell me about your team',
      ],
    },
    window.AlianChatConfig || {}
  );

  /* ── State ───────────────────────────────────────────────────────────────── */
  const S = {
    open:      false,
    sessionId: null,
    sessions:  [],
    busy:      false,
    loadedFromHistory: false,
  };

  /* ── SVG icons ───────────────────────────────────────────────────────────── */
  const ICON = {
    chat: `<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/></svg>`,
    close: `<svg viewBox="0 0 24 24"><path d="M18.3 5.71a1 1 0 0 0-1.41 0L12 10.59 7.11 5.7A1 1 0 0 0 5.7 7.11L10.59 12 5.7 16.89a1 1 0 1 0 1.41 1.41L12 13.41l4.89 4.89a1 1 0 0 0 1.41-1.41L13.41 12l4.89-4.89a1 1 0 0 0 0-1.4z"/></svg>`,
    send: `<svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>`,
    bot:  `<svg viewBox="0 0 24 24"><path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 0 1 7 7h1a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1H2a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h1a7 7 0 0 1 7-7h1V5.73A2 2 0 0 1 10 4a2 2 0 0 1 2-2zm-3 9a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg>`,
    plus: `<svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>`,
    trash: `<svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM8 9h8v10H8V9zm7.5-5-1-1h-5l-1 1H5v2h14V4z"/></svg>`,
  };

  /* ── CSS injection ───────────────────────────────────────────────────────── */
  function injectCSS() {
    if (!document.querySelector('style[data-rc-base-css]')) {
      const base = document.createElement('style');
      base.setAttribute('data-rc-base-css', '1');
      base.textContent = `
        #rc-root{position:relative;z-index:2147483647;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
        #rc-root,#rc-root *{box-sizing:border-box}
        #chatbot-fab{position:fixed;right:28px;bottom:28px;width:56px;height:56px;border-radius:50%;border:1px solid rgba(255,255,255,.18);background:#2563eb;color:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 8px 28px rgba(29,78,216,.45),0 2px 8px rgba(0,0,0,.4);cursor:pointer;z-index:2147483647;padding:0}
        #chatbot-fab svg{width:24px;height:24px;fill:currentColor}
        #chatbot-fab .rc-fab-icon{position:absolute;width:24px;height:24px;display:flex;align-items:center;justify-content:center}
        #chatbot-fab .rc-fab-icon-close{opacity:0}
        #chatbot-fab.rc-open .rc-fab-icon-chat{opacity:0}
        #chatbot-fab.rc-open .rc-fab-icon-close{opacity:1}
        #chatbot-panel{position:fixed;right:28px;bottom:100px;width:min(780px,calc(100vw - 32px));height:min(610px,calc(100vh - 128px));display:none;overflow:hidden;border-radius:16px;background:#0b1629;color:#dce8f5;border:1px solid #1c2e47;box-shadow:0 32px 80px rgba(0,0,0,.65);z-index:2147483646}
        #chatbot-panel.rc-open{display:flex}
        #chatbot-panel svg{width:18px;height:18px;fill:currentColor;flex:0 0 auto}
        #chatbot-panel button{font:inherit}
        #chatbot-panel .rc-sidebar{width:220px;min-width:220px;background:#060d1a;border-right:1px solid #152033;display:flex;flex-direction:column}
        #chatbot-panel .rc-sidebar-header{height:56px;display:flex;align-items:center;justify-content:space-between;padding:12px;border-bottom:1px solid #152033}
        #chatbot-panel .rc-sidebar-actions{display:flex;gap:6px;align-items:center}
        #chatbot-panel .rc-new-chat-btn,
        #chatbot-panel .rc-clear-history-btn{display:inline-flex;gap:6px;align-items:center;border:1px solid #243857;background:#0f1d33;color:#dce8f5;border-radius:7px;padding:7px 10px;cursor:pointer}
        #chatbot-panel .rc-clear-history-btn{color:#fda4af}
        #chatbot-panel .rc-clear-history-btn:hover{background:#162a40}
        #chatbot-panel .rc-clear-history-btn:disabled{opacity:.35;cursor:wait}
        #chatbot-panel .rc-session-list{flex:1;padding:8px;overflow:auto;font-size:13px;color:#6e90b8}
        #chatbot-panel .rc-date-group{padding:10px 6px 4px}
        #chatbot-panel .rc-date-label{font-size:10px;font-weight:700;color:#304565;text-transform:uppercase;letter-spacing:.08em}
        #chatbot-panel .rc-session-item{padding:8px 9px;border-radius:7px;border:1px solid transparent;cursor:pointer;margin-bottom:2px;min-width:0}
        #chatbot-panel .rc-session-item:hover{background:#0f1d33;border-color:#1c2e47}
        #chatbot-panel .rc-session-item.rc-active{background:rgba(29,78,216,.18);border-color:#1d4ed8}
        #chatbot-panel .rc-session-row{display:flex;align-items:center;gap:6px;min-width:0}
        #chatbot-panel .rc-session-text{min-width:0;flex:1}
        #chatbot-panel .rc-session-title{font-size:12px;color:#dce8f5;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.4}
        #chatbot-panel .rc-session-meta{font-size:10px;color:#304565;margin-top:2px}
        #chatbot-panel .rc-session-delete{width:24px;height:24px;border:0;border-radius:6px;background:transparent;color:#6e90b8;display:flex;align-items:center;justify-content:center;cursor:pointer;opacity:.75;flex:0 0 auto}
        #chatbot-panel .rc-session-delete:hover{background:rgba(220,38,38,.14);color:#fca5a5;opacity:1}
        #chatbot-panel .rc-session-delete:disabled{opacity:.35;cursor:wait}
        #chatbot-panel .rc-session-delete svg{width:14px;height:14px}
        #chatbot-panel .rc-session-empty{padding:24px 10px;text-align:center;color:#6e90b8;font-size:12px;line-height:1.55}
        #chatbot-panel .rc-main{min-width:0;flex:1;display:flex;flex-direction:column;background:#0b1629}
        #chatbot-panel .rc-header{height:64px;display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border-bottom:1px solid #152033;background:#0f1d33}
        #chatbot-panel .rc-header-left{display:flex;align-items:center;gap:10px}
        #chatbot-panel .rc-header-avatar,#chatbot-panel .rc-welcome-icon,#chatbot-panel .rc-avatar-bot{width:34px;height:34px;min-width:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#2563eb;color:#fff}
        #chatbot-panel .rc-header-name{font-weight:600;color:#fff}
        #chatbot-panel .rc-header-status{font-size:12px;color:#6e90b8}
        #chatbot-panel .rc-close-btn{width:32px;height:32px;border:0;border-radius:8px;background:#121f38;color:#dce8f5;display:flex;align-items:center;justify-content:center;cursor:pointer}
        #chatbot-panel .rc-messages{flex:1;overflow:auto;padding:18px;display:flex;flex-direction:column;gap:14px}
        #chatbot-panel .rc-welcome{margin:auto;text-align:center;max-width:430px;display:flex;flex-direction:column;align-items:center;gap:10px}
        #chatbot-panel .rc-welcome-title{font-size:20px;font-weight:600;color:#fff}
        #chatbot-panel .rc-welcome-sub{font-size:14px;color:#9db3cc;line-height:1.5}
        #chatbot-panel .rc-hints{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:8px}
        #chatbot-panel .rc-hint-btn{border:1px solid #243857;background:#0f1d33;color:#dce8f5;border-radius:999px;padding:7px 10px;cursor:pointer}
        #chatbot-panel .rc-input-area{display:flex;gap:10px;align-items:flex-end;padding:14px;border-top:1px solid #152033;background:#0f1d33}
        #chatbot-panel .rc-textarea{flex:1;min-height:42px;max-height:120px;resize:none;border:1px solid #243857;border-radius:10px;background:#0c1525;color:#fff;padding:11px 12px;font:inherit;outline:none}
        #chatbot-panel .rc-send-btn{width:42px;height:42px;border:0;border-radius:10px;background:#2563eb;color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer}
        #chatbot-panel .rc-footer{padding:8px 14px;border-top:1px solid #152033;color:#6e90b8;font-size:11px;text-align:center}
        #chatbot-panel .rc-row,#chatbot-panel .rc-typing-row{display:flex;gap:10px;max-width:90%;align-items:flex-start}
        #chatbot-panel .rc-row-user{align-self:flex-end;flex-direction:row-reverse}
        #chatbot-panel .rc-avatar{width:28px;height:28px;min-width:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#2563eb;color:#fff;font-size:12px;font-weight:700}
        #chatbot-panel .rc-avatar svg{width:14px;height:14px}
        #chatbot-panel .rc-bubble{padding:10px 12px;border-radius:12px;background:#0c1b33;border:1px solid #172845;color:#dce8f5;line-height:1.45;font-size:14px;overflow-wrap:anywhere}
        #chatbot-panel .rc-bubble-user{background:#162f62;border-color:#1d50c0;color:#fff}
        #chatbot-panel .rc-typing-bubble{display:flex;gap:5px;padding:12px;border-radius:12px;background:#0c1b33;border:1px solid #172845}
        #chatbot-panel .rc-dot{width:6px;height:6px;border-radius:50%;background:#6e90b8}
        #chatbot-panel .rc-error-msg{padding:10px 12px;border-radius:10px;background:rgba(220,38,38,.12);border:1px solid rgba(220,38,38,.3);color:#fca5a5;font-size:13px}
        @media(max-width:720px){#chatbot-panel{right:12px;bottom:84px;width:calc(100vw - 24px);height:min(620px,calc(100vh - 104px))}#chatbot-panel .rc-sidebar{display:none}}
      `;
      document.head.appendChild(base);
    }
    if (document.querySelector('[data-rc-css]')) return;

    fetch(CFG.cssUrl, {
      headers: { 'ngrok-skip-browser-warning': 'true' },
      mode: 'cors',
    })
      .then((res) => {
        if (!res.ok) throw new Error(`CSS HTTP ${res.status}`);
        return res.text();
      })
      .then((css) => {
        if (/^\s*</.test(css)) throw new Error('CSS response was HTML');
        const style = document.createElement('style');
        style.setAttribute('data-rc-css', '1');
        style.textContent = css;
        document.head.appendChild(style);
      })
      .catch((err) => {
        console.warn('[AlianChat] CSS fetch failed, falling back to stylesheet link:', err);
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = CFG.cssUrl;
        link.crossOrigin = 'anonymous';
        link.setAttribute('data-rc-css', '1');
        document.head.appendChild(link);
      });
  }

  /* ── HTML structure ──────────────────────────────────────────────────────── */
  function buildHTML() {
    return `
      <button id="chatbot-fab" aria-label="Open chat with ${CFG.assistantName}" aria-expanded="false">
        <span class="rc-fab-icon rc-fab-icon-chat" aria-hidden="true">${ICON.chat}</span>
        <span class="rc-fab-icon rc-fab-icon-close" aria-hidden="true">${ICON.close}</span>
      </button>

      <div id="chatbot-panel" role="dialog" aria-label="${CFG.assistantName}" aria-hidden="true">
        <aside class="rc-sidebar">
          <div class="rc-sidebar-header">
            <div>
              <div class="rc-sidebar-title">Conversation History</div>
            </div>
            <div class="rc-sidebar-actions">
              <button class="rc-new-chat-btn" id="rc-new-chat" type="button" aria-label="Start new chat">
                ${ICON.plus}<span>New</span>
              </button>
              <button class="rc-clear-history-btn" id="rc-clear-history" type="button" aria-label="Clear chat history">
                ${ICON.trash}<span>Clear</span>
              </button>
            </div>
          </div>
          <div class="rc-session-list" id="rc-sessions"></div>
        </aside>

        <main class="rc-main">
          <header class="rc-header">
            <div class="rc-header-left">
              <div class="rc-header-avatar" aria-hidden="true">${ICON.bot}</div>
              <div>
                <div class="rc-header-name">${CFG.assistantName}</div>
                <div class="rc-header-status">
                  <span class="rc-status-dot" aria-hidden="true"></span>
                  <span>Online · RAG powered</span>
                </div>
              </div>
            </div>
            <button class="rc-close-btn" id="rc-close" aria-label="Close chat">
              ${ICON.close}
            </button>
          </header>

          <div class="rc-messages" id="rc-messages" role="log" aria-live="polite" aria-label="Conversation"></div>

          <div class="rc-input-area">
            <textarea
              class="rc-textarea"
              id="rc-input"
              rows="1"
              placeholder="${CFG.placeholder}"
              aria-label="Type your message"
            ></textarea>
            <button class="rc-send-btn" id="rc-send" aria-label="Send message">
              ${ICON.send}
            </button>
          </div>

          <div class="rc-footer">
            <span>Powered by ${CFG.siteName} RAG · Read-only knowledge base</span>
          </div>
        </main>
      </div>`;
  }

  /* ── Inject into DOM ─────────────────────────────────────────────────────── */
  function injectHTML() {
    if (document.getElementById('rc-root')) return;
    const target = document.getElementById('rag-chatbot') || document.body;
    const wrapper = document.createElement('div');
    wrapper.id = 'rc-root';
    wrapper.innerHTML = buildHTML();
    target.appendChild(wrapper);
  }

  /* ── Events ──────────────────────────────────────────────────────────────── */
  function bindEvents() {
    const fab     = document.getElementById('chatbot-fab');
    const close   = document.getElementById('rc-close');
    const newChat = document.getElementById('rc-new-chat');
    const send    = document.getElementById('rc-send');
    const input   = document.getElementById('rc-input');
    const panel   = document.getElementById('chatbot-panel');

    const clearHistoryBtn = document.getElementById('rc-clear-history');
    fab?.addEventListener('click', togglePanel);
    close?.addEventListener('click', closePanel);
    newChat?.addEventListener('click', startNewChat);
    clearHistoryBtn?.addEventListener('click', clearHistoryAll);
    send?.addEventListener('click', handleSend);
    panel?.addEventListener('click', (e) => e.stopPropagation());

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });

    /* Auto-resize textarea */
    input?.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });

    /* Click outside to close */
    document.addEventListener('click', (e) => {
      const panelNow = document.getElementById('chatbot-panel');
      const root = document.getElementById('rc-root');
      if (
        S.open &&
        panelNow &&
        !panelNow.contains(e.target) &&
        !(fab && fab.contains(e.target)) &&
        !(root && root.contains(e.target))
      ) {
        closePanel();
      }
    });

    /* Escape key */
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && S.open) closePanel();
    });
  }

  /* ── Panel open / close ──────────────────────────────────────────────────── */
  function togglePanel() { S.open ? closePanel() : openPanel(); }

  async function openPanel() {
    S.open = true;
    const fab   = document.getElementById('chatbot-fab');
    const panel = document.getElementById('chatbot-panel');
    fab.classList.add('rc-open');
    fab.setAttribute('aria-expanded', 'true');
    panel.classList.add('rc-open');
    panel.setAttribute('aria-hidden', 'false');

    if (!S.sessionId) {
      await startNewChat();
    }
    await loadHistory();
    setActiveSession(S.sessionId);
    setTimeout(() => document.getElementById('rc-input')?.focus(), 280);
  }

  function closePanel() {
    S.open = false;
    const fab   = document.getElementById('chatbot-fab');
    const panel = document.getElementById('chatbot-panel');
    fab.classList.remove('rc-open');
    fab.setAttribute('aria-expanded', 'false');
    panel.classList.remove('rc-open');
    panel.setAttribute('aria-hidden', 'true');
    endCurrentSession();
  }

  /* ── Session management ──────────────────────────────────────────────────── */
  async function startNewChat() {
    try {
      const res  = await apiFetch('/new-session', { method: 'POST' });
      const data = await res.json();
      S.sessionId = data.session_id;
    } catch {
      S.sessionId = genId();
    }
    S.loadedFromHistory = false;
    clearMessages();
    showWelcome();
    setActiveSession(null);
  }

  function endCurrentSession() {
    S.sessionId = null;
    S.loadedFromHistory = false;
    clearMessages();
    showWelcome();
    setActiveSession(null);
    loadHistory();
  }

  async function loadHistory() {
    const el = document.getElementById('rc-sessions');
    if (!el) return;

    try {
      const res = await apiFetch('/history');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      S.sessions = normalizeSessions(data.sessions || []);
      renderSidebar(false);
    } catch (e) {
      console.warn('[AlianChat] Load history failed:', e);
      S.sessions = [];
      renderSidebar(true);
    }
  }

  async function loadSession(sid) {
    try {
      const res  = await apiFetch(`/history/${sid}`);
      const data = await res.json();
      S.sessionId = sid;
      S.loadedFromHistory = true;
      clearMessages();
      (data.messages || []).forEach((m) => appendMessage(m.role, m.content, false));
      scrollBottom();
      setActiveSession(sid);
    } catch (e) {
      console.warn('[AlianChat] Session load failed:', e);
    }
  }

  /* ── Send message ────────────────────────────────────────────────────────── */
  async function handleSend() {
    if (S.busy) return;

    const input    = document.getElementById('rc-input');
    const question = (input.value || '').trim();
    if (!question) return;

    /* Remove welcome screen on first message */
    const welcome = document.getElementById('rc-welcome');
    if (welcome) welcome.remove();

    input.value = '';
    input.style.height = 'auto';

    appendMessage('user', question, true);
    showTyping();
    setBusy(true);
    const requestSessionId = S.sessionId;

    try {
      const res = await apiFetch('/chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ question, session_id: requestSessionId }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      if (!S.open || S.sessionId !== requestSessionId) {
        await loadHistory();
        return;
      }
      S.sessionId = data.session_id;
      hideTyping();
      appendMessage('assistant', data.answer, true);
      await loadHistory();
      setActiveSession(S.sessionId);
    } catch (e) {
      console.warn('[AlianChat] Chat error:', e);
      hideTyping();
      appendError(e.message || 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
      document.getElementById('rc-input')?.focus();
    }
  }

  /* ── Rendering helpers ───────────────────────────────────────────────────── */
  function showWelcome() {
    const el = document.getElementById('rc-messages');
    const hints = CFG.hints
      .map((h) => `<button class="rc-hint-btn">${escHtml(h)}</button>`)
      .join('');
    el.innerHTML = `
      <div class="rc-welcome" id="rc-welcome">
        <div class="rc-welcome-icon">${ICON.bot}</div>
        <div class="rc-welcome-title">${escHtml(CFG.welcomeTitle)}</div>
        <div class="rc-welcome-sub">${escHtml(CFG.welcomeMessage)}</div>
        <div class="rc-hints">${hints}</div>
      </div>`;
    el.querySelectorAll('.rc-hint-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const input = document.getElementById('rc-input');
        if (input) { input.value = btn.textContent; handleSend(); }
      });
    });
  }

  function clearMessages() {
    const el = document.getElementById('rc-messages');
    if (el) el.innerHTML = '';
  }

  function appendMessage(role, content, animate = true) {
    const el = document.getElementById('rc-messages');
    if (!el) return;

    const isUser = role === 'user';
    const row    = document.createElement('div');
    row.className = isUser ? 'rc-row rc-row-user' : 'rc-row';
    if (!animate) row.style.animation = 'none';

    const avatarHtml = isUser
      ? `<div class="rc-avatar rc-avatar-user">U</div>`
      : `<div class="rc-avatar rc-avatar-bot">${ICON.bot}</div>`;

    const bubbleClass = isUser ? 'rc-bubble rc-bubble-user' : 'rc-bubble rc-bubble-bot';

    row.innerHTML = `
      ${avatarHtml}
      <div class="${bubbleClass}">${formatMd(content)}</div>`;

    el.appendChild(row);
    scrollBottom();
  }

  function appendError(msg) {
    const el = document.getElementById('rc-messages');
    if (!el) return;
    const div = document.createElement('div');
    div.className = 'rc-error-msg';
    div.textContent = '⚠ ' + msg;
    el.appendChild(div);
    scrollBottom();
  }

  function showTyping() {
    const el = document.getElementById('rc-messages');
    if (!el || document.getElementById('rc-typing')) return;
    const row = document.createElement('div');
    row.className = 'rc-typing-row';
    row.id = 'rc-typing';
    row.innerHTML = `
      <div class="rc-avatar rc-avatar-bot">${ICON.bot}</div>
      <div class="rc-typing-bubble">
        <span class="rc-dot"></span>
        <span class="rc-dot"></span>
        <span class="rc-dot"></span>
      </div>`;
    el.appendChild(row);
    scrollBottom();
  }

  function hideTyping() {
    document.getElementById('rc-typing')?.remove();
  }

  function renderSidebar(error) {
    const el = document.getElementById('rc-sessions');
    if (!el) return;

    const sessions = getSidebarSessions();

    if (error && !sessions.length) {
      el.innerHTML = '<div class="rc-session-empty">History could not be loaded.<br>Try again after the API tunnel is ready.</div>';
      return;
    }

    if (!sessions.length) {
      el.innerHTML = '<div class="rc-session-empty">No conversations yet.<br>Start a chat below!</div>';
      return;
    }

    const groups = groupByDate(sessions);
    const order  = ['Today', 'Yesterday', 'This week', 'Older'];
    let html = '';
    order.forEach((label) => {
      const items = groups[label] || [];
      if (!items.length) return;
      html += `<div class="rc-date-group"><div class="rc-date-label">${label}</div></div>`;
      items.forEach((s) => {
        const active = s.session_id === S.sessionId ? ' rc-active' : '';
        const msgWord = s.message_count === 1 ? 'message' : 'messages';
        const canDelete = !s.localOnly && s.message_count > 0;
        const deleteButton = canDelete
          ? `<button class="rc-session-delete" type="button" data-id="${escAttr(s.session_id)}" aria-label="Delete chat history">${ICON.trash}</button>`
          : '';
        html += `
          <div class="rc-session-item${active}" data-id="${escAttr(s.session_id)}">
            <div class="rc-session-row">
              <div class="rc-session-text">
                <div class="rc-session-title">${escHtml(s.title || 'Untitled')}</div>
                <div class="rc-session-meta">${s.message_count} ${msgWord}</div>
              </div>
              ${deleteButton}
            </div>
          </div>`;
      });
    });
    el.innerHTML = html;
    el.querySelectorAll('.rc-session-item').forEach((item) => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        loadSession(item.dataset.id);
      });
    });
    el.querySelectorAll('.rc-session-delete').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (btn.disabled) return;
        btn.disabled = true;
        deleteSession(btn.dataset.id).finally(() => {
          btn.disabled = false;
        });
      });
    });
  }

  function setActiveSession(sid) {
    document.querySelectorAll('.rc-session-item').forEach((el) => {
      el.classList.toggle('rc-active', el.dataset.id === sid);
    });
  }

  function normalizeSessions(data) {
    const list = Array.isArray(data)
      ? data
      : Array.isArray(data?.sessions)
        ? data.sessions
        : [];

    return list
      .filter((s) => s && s.session_id)
      .map((s) => ({
        session_id: String(s.session_id),
        title: s.title || 'Untitled Chat',
        created_at: s.created_at || s.updated_at || new Date().toISOString(),
        updated_at: s.updated_at || s.created_at || new Date().toISOString(),
        message_count: Number(s.message_count || 0),
      }));
  }

  function getSidebarSessions() {
    const sessions = [...S.sessions];
    if (S.sessionId && !sessions.some((s) => s.session_id === S.sessionId)) {
      sessions.unshift({
        session_id: S.sessionId,
        title: S.loadedFromHistory ? 'Loaded chat' : 'Current chat',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        message_count: 0,
        localOnly: true,
      });
    }
    return sessions;
  }

  /* ── Markdown formatter ──────────────────────────────────────────────────── */
  async function deleteSession(sid) {
    if (!sid) return;

    try {
      const res = await apiFetch(`/history/${encodeURIComponent(sid)}`, { method: 'DELETE' });
      if (!res.ok) {
        const errorText = await res.text().catch(() => '');
        throw new Error(errorText || `HTTP ${res.status}`);
      }

      if (S.sessionId === sid) {
        await startNewChat();
      }
    } catch (e) {
      console.warn('[AlianChat] Delete session failed:', e);
    } finally {
      await loadHistory();
      setActiveSession(S.sessionId);
    }
  }

  async function clearHistoryAll() {
    const clearHistoryBtn = document.getElementById('rc-clear-history');
    if (clearHistoryBtn) clearHistoryBtn.disabled = true;

    try {
      const res = await apiFetch('/history', { method: 'DELETE' });
      if (!res.ok) {
        const errorText = await res.text().catch(() => '');
        throw new Error(errorText || `HTTP ${res.status}`);
      }
      endCurrentSession();
    } catch (e) {
      console.warn('[AlianChat] Clear all history failed:', e);
      // Fallback: try deleting each session via the POST delete endpoint
      try {
        const ids = S.sessions.map((s) => s.session_id).filter(Boolean);
        for (const sid of ids) {
          try {
            await apiFetch(`/history/${encodeURIComponent(sid)}/delete`, { method: 'POST' });
          } catch (_) {}
        }
        // If still not cleared, attempt Flask fallback on :5000
        try {
          const flaskBase = (window.AlianChatConfig?.flaskBase || 'http://localhost:5000').replace(/\/$/, '');
          await fetch(flaskBase + '/history/clear-all', { method: 'POST' });
        } catch (_) {}
      } catch (_) {}
    } finally {
      if (clearHistoryBtn) clearHistoryBtn.disabled = false;
      await loadHistory();
      setActiveSession(S.sessionId);
    }
  }

  function formatMd(raw) {
    if (!raw) return '';

    /* 1. Protect fenced code blocks */
    const codeBlocks = [];
    let s = raw.replace(/```([\w]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      codeBlocks.push({ lang, code: code.trim() });
      return `\x00CB${codeBlocks.length - 1}\x00`;
    });

    /* 2. Protect inline code */
    const inlineCode = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => {
      inlineCode.push(c);
      return `\x00IC${inlineCode.length - 1}\x00`;
    });

    /* 3. HTML-escape the rest */
    s = escHtml(s);

    /* 4. Apply markdown */
    s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    s = s.replace(/^#{1,3}\s+(.+)$/gm, '<strong>$1</strong>');
    s = s.replace(/^[-•]\s+(.+)$/gm, '<li>$1</li>');
    s = s.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/---/g, '<hr>');

    /* Wrap consecutive <li> in <ul> */
    s = s.replace(/(<li>.*?<\/li>\n?)+/gs, (m) => `<ul>${m}</ul>`);

    /* Paragraphs (split on blank lines) */
    s = s.split(/\n{2,}/).map((p) => {
      p = p.trim();
      if (!p) return '';
      if (/^<(ul|ol|pre|hr|strong)/.test(p)) return p;
      return `<p>${p.replace(/\n/g, '<br>')}</p>`;
    }).filter(Boolean).join('');

    /* 5. Restore inline code */
    inlineCode.forEach((c, i) => {
      s = s.replace(
        `\x00IC${i}\x00`,
        `<code>${escHtml(c)}</code>`
      );
    });

    /* 6. Restore fenced code blocks */
    codeBlocks.forEach(({ code }, i) => {
      s = s.replace(
        `\x00CB${i}\x00`,
        `<pre><code>${escHtml(code)}</code></pre>`
      );
    });

    return s || `<p>${escHtml(raw)}</p>`;
  }

  /* ── Utilities ───────────────────────────────────────────────────────────── */
  function scrollBottom() {
    const el = document.getElementById('rc-messages');
    if (el) el.scrollTop = el.scrollHeight;
  }

  function setBusy(val) {
    S.busy = val;
    const btn = document.getElementById('rc-send');
    if (btn) btn.disabled = val;
  }

  function apiFetch(path, opts = {}) {
    const url = CFG.apiBase.replace(/\/$/, '') + path;
    const headers = Object.assign(
      {
        Accept: 'application/json',
        'ngrok-skip-browser-warning': 'true',
      },
      opts.headers || {}
    );
    return fetch(url, Object.assign({}, opts, { headers }));
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;');
  }

  function escAttr(str) {
    return String(str).replace(/[^a-zA-Z0-9-_]/g, '');
  }

  function genId() {
    return 'xxxx-xxxx-4xxx-yxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function groupByDate(sessions) {
    const groups = { Today: [], Yesterday: [], 'This week': [], Older: [] };
    const now    = new Date();
    const today  = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yest   = new Date(today - 864e5);
    const week   = new Date(today - 7 * 864e5);

    sessions.forEach((s) => {
      const d   = new Date(s.updated_at || s.created_at || 0);
      const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      if (day >= today)      groups.Today.push(s);
      else if (day >= yest)  groups.Yesterday.push(s);
      else if (d >= week)    groups['This week'].push(s);
      else                   groups.Older.push(s);
    });
    return groups;
  }

  /* ── Init ────────────────────────────────────────────────────────────────── */
  function init() {
    if (!document.head || !document.body) {
      setTimeout(init, 0);
      return;
    }
    if (window.__AlianChatReady) return;
    window.__AlianChatReady = true;
    injectCSS();
    injectHTML();
    bindEvents();
    showWelcome();
    loadHistory();
    // Ensure FAB exists and is visible — defensive fix for environments
    (function ensureFab(){
      try {
        const fab = document.getElementById('chatbot-fab');
        if (!fab) {
          const btn = document.createElement('button');
          btn.id = 'chatbot-fab';
          btn.setAttribute('aria-label', `Open chat with ${CFG.assistantName}`);
          btn.setAttribute('aria-expanded', 'false');
          btn.innerHTML = `<span class="rc-fab-icon rc-fab-icon-chat" aria-hidden="true">${ICON.chat}</span><span class="rc-fab-icon rc-fab-icon-close" aria-hidden="true">${ICON.close}</span>`;
          document.body.appendChild(btn);
          btn.addEventListener('click', togglePanel);
        }
        const f = document.getElementById('chatbot-fab');
        if (f) f.style.display = 'flex';
      } catch (e) {
        console.warn('[AlianChat] ensureFab failed', e);
      }
    })();
  }

  /* ── Boot ────────────────────────────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }

})();
