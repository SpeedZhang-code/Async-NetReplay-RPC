let activeTab      = 'global';
let clients        = {};          
let openTabs       = ['global'];  
let globalSeq      = 0;
let clientSeqs     = {};          

const POLL_MS      = 1500;        

function tickClock() {
  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  document.getElementById('clock').textContent =
    `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}
setInterval(tickClock, 1000);
tickClock();

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function colorizeLabel(label) {
  let s = escHtml(label);
  s = s.replace(/\bACCEPTED\b/g,   '<span class="kw-accepted">ACCEPTED</span>');
  s = s.replace(/\bREJECTED\b/g,   '<span class="kw-rejected">REJECTED</span>');
  s = s.replace(/\b(連線建立)\b/g, '<span class="kw-connect">$1</span>');
  s = s.replace(/\b(斷線)\b/g,     '<span class="kw-disconnect">$1</span>');
  return s;
}

function srcClass(source) {
  if (!source) return 'system';
  const s = source.toUpperCase();
  if (s === 'RPC')    return 'rpc';
  if (s === 'SOCKET') return 'socket';
  if (s === 'ERROR')  return 'error';
  return 'system';
}

function appendLine(bodyEl, entry) {
  const line = document.createElement('div');
  line.className = 'log-line';
  const sc = srcClass(entry.source);
  line.innerHTML =
    `<span class="log-ts">${escHtml(entry.ts)}</span>` +
    `<span class="log-src ${sc}">[${escHtml(entry.source||'SYS')}]</span>` +
    `<span class="log-msg">${colorizeLabel(entry.label)}</span>`;
  bodyEl.appendChild(line);

  const NEAR = 80;
  const atBottom = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < NEAR;
  if (atBottom) bodyEl.scrollTop = bodyEl.scrollHeight;
}

function switchTab(tabId) {
  activeTab = tabId;
  document.querySelectorAll('.tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tabId);
  });
  document.querySelectorAll('.terminal').forEach(el => {
    el.classList.toggle('active', el.id === `term-${tabId}`);
  });
  
  // 同步讓左邊對應的項目顯示 active 狀態
  document.querySelectorAll('.client-item').forEach(el => {
    el.classList.toggle('active', el.dataset.cid === tabId);
  });
}

function openClientTab(clientId) {
  if (!openTabs.includes(clientId)) {
    openTabs.push(clientId);
    clientSeqs[clientId] = 0;

    const tab = document.createElement('div');
    tab.className = 'tab';
    tab.dataset.tab = clientId;
    tab.innerHTML =
      `<span onclick="switchTab('${CSS.escape(clientId)}')">${escHtml(clientId)}</span>` +
      `<span class="tab-close" onclick="closeClientTab('${CSS.escape(clientId)}')">✕</span>`;
    tab.onclick = (e) => { if (e.target.classList.contains('tab-close')) return; switchTab(clientId); };
    document.getElementById('tab-bar').appendChild(tab);

    const term = document.createElement('div');
    term.className = 'terminal';
    term.id = `term-${clientId}`;
    term.innerHTML =
      `<div class="terminal-body thin-scroll" id="body-${clientId}"></div>` +
      `<div class="terminal-status">` +
        `<span>CLIENT: ${escHtml(clientId)}</span>` +
        `<span class="cursor">▋</span>` +
      `</div>`;
    document.getElementById('terminal-wrap').appendChild(term);
  }
  switchTab(clientId);
}

function closeClientTab(clientId) {
  openTabs = openTabs.filter(t => t !== clientId);
  const tabEl = document.querySelector(`.tab[data-tab="${CSS.escape(clientId)}"]`);
  if (tabEl) tabEl.remove();
  const termEl = document.getElementById(`term-${clientId}`);
  if (termEl) termEl.remove();
  if (activeTab === clientId) switchTab('global');
}

function renderSidebar() {
  const listEl    = document.getElementById('client-list');
  const countEl   = document.getElementById('client-count');
  const ids       = Object.keys(clients);

  countEl.textContent = ids.length;

  if (ids.length === 0) {
    if (!document.getElementById('no-clients')) {
      listEl.innerHTML =
        `<div id="no-clients">` +
          `<div class="ascii">  ┌──────────────┐\n  │  NO CLIENTS  │\n  └──────────────┘</div>` +
          `<div style="margin-top:12px;font-size:11px;color:var(--text-muted)">等待連線入隊...</div>` +
        `</div>`;
    }
    return;
  }

  const placeholder = document.getElementById('no-clients');
  if (placeholder) placeholder.remove();

  const existing = new Set([...listEl.querySelectorAll('.client-item')].map(el => el.dataset.cid));

  ids.forEach(cid => {
    if (!existing.has(cid)) {
      const item = document.createElement('div');
      item.className = 'client-item';
      item.dataset.cid = cid;
      item.onclick = () => openClientTab(cid);
      listEl.appendChild(item);
    }
    const c    = clients[cid];
    const item = listEl.querySelector(`.client-item[data-cid="${CSS.escape(cid)}"]`);
    if (item) {
      const isPending = c.status === 'PENDING';
      item.innerHTML =
        `<div class="cid">` +
          `<span class="status-dot ${isPending ? 'pending' : 'connected'}"></span>` +
          `${escHtml(cid)}` +
        `</div>` +
        `<div class="cmeta">${escHtml(c.ip)}:${escHtml(String(c.port))} · ${escHtml(c.connected_at)}</div>`;
      if (activeTab === cid) item.classList.add('active');
      else item.classList.remove('active');
    }
  });

  existing.forEach(cid => {
    if (!clients[cid]) {
      const item = listEl.querySelector(`.client-item[data-cid="${CSS.escape(cid)}"]`);
      if (item) item.remove();
    }
  });
}

async function pollClients() {
  try {
    const r = await fetch('/api/monitor/clients');
    const arr = await r.json();
    const newClients = {};
    arr.forEach(c => { newClients[c.client_id] = c; });
    clients = newClients;
    renderSidebar();
  } catch(e) {}
}

async function pollGlobal() {
  try {
    const r = await fetch(`/api/monitor/log/global?since=${globalSeq}`);
    const events = await r.json();
    if (events.length > 0) {
      const bodyEl = document.getElementById('body-global');
      events.forEach(e => {
        appendLine(bodyEl, e);
        if (e.seq > globalSeq) globalSeq = e.seq;
      });
      const statEl = document.getElementById('global-stat');
      if (statEl) statEl.textContent = `EVENTS: ${globalSeq} // CLIENTS: ${Object.keys(clients).length}`;
    }
  } catch(e) {}
}

async function pollClientLog(clientId) {
  const since = clientSeqs[clientId] || 0;
  try {
    const r = await fetch(`/api/monitor/log/client/${encodeURIComponent(clientId)}?since=${since}`);
    const events = await r.json();
    if (events.length > 0) {
      const bodyEl = document.getElementById(`body-${clientId}`);
      if (!bodyEl) return;
      events.forEach(e => {
        appendLine(bodyEl, e);
        if (e.seq > (clientSeqs[clientId]||0)) clientSeqs[clientId] = e.seq;
      });
    }
  } catch(e) {}
}

async function pollLoop() {
  await pollClients();
  await pollGlobal();
  for (const tabId of openTabs) {
    if (tabId !== 'global') await pollClientLog(tabId);
  }
  setTimeout(pollLoop, POLL_MS);
}

pollLoop();