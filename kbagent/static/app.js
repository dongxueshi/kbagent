/* No external scripts: all user and model content is rendered as text. */
'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const state = {view: 'overview', spaces: [], spaceId: localStorage.getItem('kb-space'), documents: [], settings: {}, conversation: null, conversations: [], busy: false};
const labels = {overview: '工作台', documents: '知识文档', chat: '智能问答', settings: '模型与设置'};
const main = $('#main');
const dialog = $('#dialog');
let toastTimer;
let renderVersion = 0;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const date = value => new Date(value).toLocaleDateString('zh-CN', {month:'2-digit', day:'2-digit'});
const currentSpace = () => state.spaces.find(s => s.id === state.spaceId);

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const el = $('#toast');
  el.textContent = message;
  el.className = 'toast visible' + (error ? ' error' : '');
  toastTimer = setTimeout(() => el.classList.remove('visible'), 4500);
}

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {credentials: 'same-origin', ...options});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? '请检查输入内容和长度限制' : `请求失败 (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}
const jsonOptions = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const base = () => '/spaces/' + state.spaceId;

async function refreshSpaces() {
  state.spaces = await api('/spaces');
  if (!state.spaces.some(s => s.id === state.spaceId)) state.spaceId = state.spaces[0]?.id || null;
  if (state.spaceId) localStorage.setItem('kb-space', state.spaceId);
  else localStorage.removeItem('kb-space');
  renderSidebar();
}

function renderSidebar() {
  $('#space-list').innerHTML = state.spaces.map(s => `<button class="space-item ${s.id === state.spaceId ? 'active' : ''}" data-space="${s.id}" aria-pressed="${s.id === state.spaceId}"><span>${esc(s.name)}</span><b>${s.document_count}</b></button>`).join('') + '<button class="space-item" data-action="new-space"><span>新建知识库</span></button>';
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.view === state.view);
    if (el.dataset.view === state.view) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  $('#nav-count').textContent = currentSpace()?.document_count || 0;
  $('#breadcrumb').textContent = labels[state.view];
}

function heading(title, description, actions = '', eyebrow = 'YOUR KNOWLEDGE WORKSPACE') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="actions">${actions}</div></div>`;
}

function empty(title, description, actions = '') {
  return `<div class="empty"><div class="empty-icon">▧</div><h2>${esc(title)}</h2><p>${esc(description)}</p><div class="actions">${actions}</div></div>`;
}

async function render() {
  const version = ++renderVersion;
  renderSidebar();
  if (!state.spaceId && state.view !== 'settings') {
    main.innerHTML = heading('让知识成为你的工作伙伴', '从一个知识库开始，连接资料与答案。', '<button class="button primary" data-action="new-space">＋ 新建知识库</button>') + `<div class="panel">${empty('欢迎来到 KB Agent', '你可以从空白知识库开始，也可以加载三篇示例资料，体验导入、检索与问答。', '<button class="button primary" data-action="demo">加载示例知识库</button><button class="button" data-action="new-space">创建空白知识库</button>')}</div>`;
    return;
  }
  main.innerHTML = '<div class="loading">正在加载…</div>';
  try {
    if (state.spaceId) {
      const documents = await api(base() + '/documents');
      if (version !== renderVersion) return;
      state.documents = documents;
    }
    if (state.view === 'overview') renderOverview();
    if (state.view === 'documents') renderDocuments();
    if (state.view === 'settings') renderSettings();
    if (state.view === 'chat') {
      const conversations = await api(base() + '/conversations');
      if (version !== renderVersion) return;
      state.conversations = conversations;
      renderChat();
    }
  } catch (error) {
    if (version === renderVersion) main.innerHTML = empty('暂时无法加载', error.message, '<button class="button" data-action="retry">重新加载</button>');
  }
}

function renderOverview() {
  const space = currentSpace();
  const chars = state.documents.reduce((n, d) => n + d.character_count, 0);
  const totalChunks = state.documents.reduce((n, d) => n + d.chunk_count, 0);
  const actions = '<button class="button primary" data-action="new-doc">＋ 新建文档</button>';
  main.innerHTML = heading(space.name, space.description || '整理知识，发现关联，让每一个问题都有出处。', actions) + `
    <section class="hero"><div><div class="hero-label">KNOWLEDGE → INSIGHT</div><h2>不止是文档，更是你的知识伙伴。</h2><p>将零散的资料整理为可检索的知识库。让智能体帮你寻找答案，让每个结论都能回到原文。</p><div class="actions"><button class="button primary" data-view="chat">✧ 开始问答</button><button class="button" data-action="import">↑ 导入资料</button></div></div><div class="hero-art" aria-hidden="true"><div class="art-orbit"></div><div class="art-orbit second"></div><div class="art-book"><i></i><i></i></div><span class="art-spark">✦</span></div></section>
    <div class="metrics"><div class="metric"><div class="metric-icon">▤</div><div><span>知识文档</span><strong>${state.documents.length}<small>篇</small></strong></div></div><div class="metric"><div class="metric-icon">⌕</div><div><span>可检索片段</span><strong>${totalChunks}<small>个</small></strong></div></div><div class="metric"><div class="metric-icon">▧</div><div><span>累计知识字符</span><strong>${chars > 9999 ? (chars / 10000).toFixed(1) + '万' : chars.toLocaleString()}<small>字</small></strong></div></div></div>
    <div class="overview-grid"><section class="panel"><div class="panel-head"><h2>最近更新的文档</h2><button class="text-button" data-view="documents">查看全部 →</button></div>${state.documents.slice(0, 5).map(d => `<button class="doc-row" data-doc="${d.id}"><span class="file-icon">▤</span><div><strong>${esc(d.title)}</strong><small>${esc(d.source)} · ${date(d.updated_at)} 更新</small></div><span class="arrow">›</span></button>`).join('') || empty('知识库还是空的', '导入一份资料，开始积累你的知识。', '<button class="button" data-action="import">导入资料</button>')}</section>
    <section class="panel"><div class="panel-head"><h2>三步，开启知识之旅</h2><small>QUICK START</small></div><div class="step-list"><div class="step"><span class="step-num">01</span><div><h3>收集你的知识</h3><p>导入 Markdown、TXT、PDF 或 Word，也可以直接新建文档。</p></div></div><div class="step"><span class="step-num">02</span><div><h3>整理与检索</h3><p>按知识库分类管理，中文关键词即可找到相关原文。</p></div></div><div class="step"><span class="step-num">03</span><div><h3>带着问题探索</h3><p>检索原文片段；接入模型后，通过智能体获得带引用的回答。</p></div></div></div></section></div>
    <div class="section-note">${state.settings.mode === 'agent' ? '<strong>✧ 智能体模式已配置</strong> · DeepAgents 可规划、检索并阅读当前知识库资料。' : '<strong>◈ 当前为本地检索模式</strong> · 无需模型密钥即可管理文档、检索原文。前往「模型与设置」了解如何启用智能体。'}</div>`;
}

function documentRows(documents) {
  if (!documents.length) return empty('没有匹配的文档', '创建新文档或导入文件，也可以试试其他标题关键词。');
  return `<div class="table-wrap"><table><thead><tr><th>文档名称</th><th>索引状态</th><th>字符 / 片段</th><th>最近更新</th><th>操作</th></tr></thead><tbody>${documents.map(d => `<tr><td><div class="title-cell"><span class="file-icon">▤</span><button data-doc="${d.id}">${esc(d.title)}</button></div></td><td><span class="status-pill">已索引</span></td><td>${d.character_count.toLocaleString()} / ${d.chunk_count}</td><td>${date(d.updated_at)}</td><td><button class="text-button" data-edit="${d.id}">编辑</button><button class="text-button" data-delete-doc="${d.id}" aria-label="删除 ${esc(d.title)}">删除</button></td></tr>`).join('')}</tbody></table></div>`;
}

function renderDocuments() {
  main.innerHTML = heading('知识文档', `「${currentSpace().name}」中的所有资料，自动分片并建立检索索引。`, '<button class="button" data-action="import">↑ 导入文件</button><button class="button primary" data-action="new-doc">＋ 新建文档</button>', 'KNOWLEDGE LIBRARY') + `
    <section class="panel"><div class="toolbar"><form id="search-form" class="search-box"><span>⌕</span><input id="search-input" placeholder="输入关键词，按 Enter 搜索全文" aria-label="搜索文档"><button class="text-button" type="submit">搜索</button></form><button class="text-button" data-action="clear-search">全部文档</button><small>${state.documents.length} 篇文档 · 支持中文全文检索</small></div><div id="document-results">${documentRows(state.documents)}</div></section><div class="section-note">支持 .md / .txt / .pdf / .docx，单文件最大 10 MB。PDF 需含可提取文字，扫描文件暂不支持 OCR。</div>`;
}

function renderSettings() {
  main.innerHTML = heading('模型与设置', '本地优先，按需连接你的模型服务。', '', 'WORKSPACE SETTINGS') + `
    <div class="settings-grid"><section class="panel"><div class="panel-head"><h2>运行状态</h2><span class="status-pill">服务已连接</span></div><div class="settings-body"><div class="settings-row"><span>当前模式</span><strong>${state.settings.mode === 'agent' ? 'DeepAgents 智能体' : '本地文档检索'}</strong></div><div class="settings-row"><span>模型</span><strong>${esc(state.settings.model || '尚未配置')}</strong></div><div class="settings-row"><span>智能体引擎</span><strong>DeepAgents SDK / LangGraph</strong></div><div class="settings-row"><span>检索方式</span><strong>中文分词 + FTS5 / BM25</strong></div><div class="settings-row"><span>数据存储</span><strong>本地 SQLite</strong></div><p>没有模型密钥时，问答页只展示检索到的原文，不生成 AI 答案。无需外部数据库或嵌入服务。</p></div></section>
    <section class="panel"><div class="panel-head"><h2>连接模型服务</h2><small>可稍后配置</small></div><div class="settings-body"><p>在项目根目录创建 <code>.env</code>，填写兼容 OpenAI 且支持工具调用的模型配置，然后重启服务：</p><pre class="code-block">KBAGENT_API_KEY=你的密钥\nKBAGENT_MODEL=你的模型名称\nKBAGENT_BASE_URL=https://你的服务地址/v1</pre><p>密钥仅由服务端读取，浏览器不会接收密钥。配置状态不代表模型连通性，发起一次智能体问答即可验证。</p><p>此版本用于本地单用户开发，默认仅监听本机。团队登录、权限和公开 Wiki 发布尚未提供。</p></div></section></div>
    ${currentSpace() ? `<section class="danger-zone"><div><h3>删除当前知识库</h3><p>「${esc(currentSpace().name)}」的文档、索引和所有对话将一并删除。</p></div><button class="button danger" data-action="delete-space">删除知识库</button></section>` : ''}`;
}

function messageHTML(message) {
  const user = message.role === 'user';
  return `<div class="message ${user ? 'user' : ''}"><span class="message-avatar">${user ? '本' : '✧'}</span><div class="message-body"><div class="message-label">${user ? '你' : message.mode === 'retrieval' ? 'KB Agent · 原文检索' : 'KB Agent · 智能体'}</div><div class="message-text">${esc(message.content)}</div>${!user && message.sources?.length ? `<div class="sources"><span class="source-label">参考来源 · 点击查看原文</span>${message.sources.map((s, i) => `<button class="source-chip" data-doc="${s.document_id}">[${i + 1}] ${esc(s.title)}</button>`).join('')}</div>` : ''}</div></div>`;
}

function renderChat() {
  main.innerHTML = heading('与知识对话', `仅检索「${currentSpace().name}」中的资料，每一次引用都能追溯。`, '', 'ASK YOUR KNOWLEDGE') + `
    <section class="chat-layout"><aside class="history-panel"><button class="button" data-action="new-chat">＋ 新对话</button><div class="history-label">最近对话</div><div id="history-list">${state.conversations.map(c => `<button class="history-item ${state.conversation?.id === c.id ? 'active' : ''}" data-conversation="${c.id}">${esc(c.title)}</button>`).join('') || '<p class="muted" style="padding:10px;font-size:11px">还没有对话记录</p>'}</div></aside><div class="chat-panel"><div class="chat-messages" id="chat-messages">${state.conversation?.messages?.length ? state.conversation.messages.map(messageHTML).join('') : `<div class="chat-welcome"><div class="chat-logo">✧</div><h2>从一个好问题开始</h2><p>${state.settings.mode === 'agent' ? '智能体将规划、检索和阅读资料，结合文档为你解答。' : '当前为原文检索模式。输入关键词，找到知识库中的相关资料。'}</p><div class="suggestions"><button class="suggestion" data-question="支持哪些文档格式？">支持哪些文档格式？</button><button class="suggestion" data-question="如何配置模型？">如何配置模型？</button><button class="suggestion" data-question="知识库检索">了解知识库检索</button></div></div>`}</div><form class="chat-input-wrap" id="chat-form"><div class="chat-input"><textarea id="question" placeholder="输入你的问题，探索知识库…" aria-label="问答问题" maxlength="4000" required></textarea><button class="send-button" id="send" type="submit" aria-label="发送问题">↑</button></div><p>Enter 发送 · Shift + Enter 换行 · ${state.settings.mode === 'agent' ? 'AI 回答请结合来源核对' : '检索结果为原文片段，并非 AI 生成答案'}</p></form></div></section>`;
  $('#question').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $('#chat-form').requestSubmit();
    }
  });
  scrollChat();
}

function scrollChat() { const el = $('#chat-messages'); if (el) el.scrollTop = el.scrollHeight; }
function showDialog(html, wide = false) {
  dialog.className = wide ? 'wide' : '';
  $('#dialog-content').innerHTML = html;
  if (!dialog.open) dialog.showModal();
}
function dialogHead(title) { return `<div class="dialog-head"><h2>${esc(title)}</h2><button type="button" class="icon-button" data-action="close-dialog" aria-label="关闭">×</button></div>`; }

function newSpaceDialog() {
  showDialog(`<form id="space-form">${dialogHead('新建知识库')}<div class="dialog-body"><label class="field">知识库名称<input name="name" placeholder="例如：产品文档、团队手册" maxlength="100" required></label><label class="field">简介<textarea name="description" placeholder="简单描述这个知识库的主题" maxlength="500"></textarea></label></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">创建知识库</button></div></form>`);
}

async function editor(documentId) {
  const documentData = documentId ? await api(base() + '/documents/' + documentId) : {title:'', content:''};
  showDialog(`<form id="document-form" data-id="${documentId || ''}">${dialogHead(documentId ? '编辑文档' : '新建文档')}<div class="dialog-body"><label class="field">文档标题<input name="title" value="${esc(documentData.title)}" placeholder="给文档一个清晰的标题" maxlength="200" required></label><div class="editor-tabs"><button type="button" class="active" data-action="editor-write">Markdown 编辑</button><button type="button" data-action="editor-preview">预览</button><small>保存后自动更新索引</small></div><textarea class="editor" id="editor-content" name="content" placeholder="# 文档标题&#10;&#10;在这里记录你的知识…" maxlength="200000" required>${esc(documentData.content)}</textarea><div class="markdown" id="editor-preview" hidden></div></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">保存文档</button></div></form>`, true);
}

function markdown(text, target) {
  target.replaceChildren();
  let code = null;
  for (const line of text.split('\n')) {
    if (line.startsWith('```')) {
      if (code) { target.append(code); code = null; }
      else code = document.createElement('pre');
      continue;
    }
    if (code) { code.textContent += line + '\n'; continue; }
    const heading = line.match(/^(#{1,3})\s+(.*)/);
    const el = document.createElement(heading ? 'h' + heading[1].length : line.startsWith('> ') ? 'blockquote' : 'p');
    el.textContent = heading ? heading[2] : line.startsWith('> ') ? line.slice(2) : line;
    if (/^([-*]|\d+\.)\s/.test(line)) el.classList.add('list-line');
    target.append(el);
  }
  if (code) target.append(code);
}

async function viewDocument(id) {
  const doc = await api(base() + '/documents/' + id);
  showDialog(`${dialogHead(doc.title)}<div class="dialog-body"><div class="muted" style="font-size:11px;margin-bottom:20px">${esc(doc.source)} · ${date(doc.updated_at)} 更新</div><article class="markdown" id="document-preview"></article></div><div class="dialog-footer"><a class="button" href="/api${base()}/documents/${id}/export" download>↓ 导出 Markdown</a><button class="button primary" data-edit="${id}">编辑文档</button></div>`, true);
  markdown(doc.content, $('#document-preview'));
}

async function searchDocuments() {
  const query = $('#search-input').value.trim();
  if (!query) { $('#document-results').innerHTML = documentRows(state.documents); return; }
  const sid = state.spaceId;
  const hits = await api(base() + '/search?q=' + encodeURIComponent(query));
  if (state.spaceId !== sid || !$('#document-results')) return;
  $('#document-results').innerHTML = hits.length ? hits.map(h => `<button class="search-hit" data-doc="${h.document_id}"><h3>▤ ${esc(h.title)} <small>· 片段 ${h.position + 1}</small></h3><p>${esc(h.content.slice(0, 400))}</p></button>`).join('') : empty('没有找到相关内容', '试试更具体的关键词，或导入相关文档。');
}

async function sendQuestion() {
  if (state.busy) return;
  const input = $('#question');
  const question = input.value.trim();
  if (!question) return;
  state.busy = true;
  $('#send').disabled = true;
  input.disabled = true;
  const chatBase = base();
  const messagesEl = $('#chat-messages');
  const originalMessages = state.conversation?.messages ? [...state.conversation.messages] : [];
  const welcome = $('.chat-welcome');
  if (welcome) welcome.remove();
  messagesEl.insertAdjacentHTML('beforeend', messageHTML({role:'user', content:question}));
  const progress = document.createElement('div');
  progress.className = 'progress';
  progress.textContent = '正在准备…';
  messagesEl.append(progress);
  input.value = '';
  scrollChat();
  let completed = false;
  try {
    if (!state.conversation) {
      state.conversation = {...await api(chatBase + '/conversations', {method:'POST'}), messages:[]};
    }
    const response = await fetch('/api' + chatBase + '/conversations/' + state.conversation.id + '/chat', jsonOptions('POST', {question}));
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(typeof err.detail === 'string' ? err.detail : '问答请求失败');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const consume = raw => {
      if (!raw.startsWith('data: ')) return;
      const event = JSON.parse(raw.slice(6));
      if (event.type === 'status') { progress.textContent = event.message; scrollChat(); }
      if (event.type === 'error') throw new Error(event.message);
      if (event.type === 'answer') {
        completed = true;
        const assistant = {role:'assistant', content:event.content, sources:event.sources, mode:event.mode};
        state.conversation.messages.push({role:'user', content:question}, assistant);
        messagesEl.insertAdjacentHTML('beforeend', messageHTML(assistant));
        progress.remove();
        scrollChat();
      }
    };
    while (true) {
      const {done, value} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream:!done});
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        consume(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
      }
      if (done) break;
    }
    if (!completed) throw new Error('连接已结束，但未收到完整回答，请重试');
    state.conversations = await api(chatBase + '/conversations');
    $('#history-list').innerHTML = state.conversations.map(c => `<button class="history-item ${state.conversation.id === c.id ? 'active' : ''}" data-conversation="${c.id}">${esc(c.title)}</button>`).join('');
  } catch (error) {
    progress.remove();
    toast(error.message, true);
    if (!completed) {
      if (state.conversation) state.conversation.messages = originalMessages;
      messagesEl.querySelector('.message.user:last-of-type')?.remove();
      // Restore the exact persisted history rather than leave a failed question as saved.
      messagesEl.innerHTML = originalMessages.map(messageHTML).join('');
      input.value = question;
    }
  } finally {
    state.busy = false;
    $('#send').disabled = false;
    input.disabled = false;
    input.focus();
  }
}

async function changed() { await refreshSpaces(); await render(); }

document.addEventListener('click', async event => {
  const target = event.target.closest('button');
  if (!target) return;
  // A running turn is scoped to its base and conversation; keep that context stable.
  if (state.busy && (target.dataset.view || target.dataset.space || target.dataset.conversation || ['new-chat', 'new-space', 'delete-space'].includes(target.dataset.action))) {
    toast('请等待当前问答完成'); return;
  }
  try {
    if (target.dataset.view) { state.view = target.dataset.view; await render(); }
    if (target.dataset.space) {
      state.spaceId = target.dataset.space;
      localStorage.setItem('kb-space', state.spaceId);
      state.conversation = null;
      await render();
    }
    if (target.dataset.doc) await viewDocument(target.dataset.doc);
    if (target.dataset.edit) await editor(target.dataset.edit);
    if (target.dataset.deleteDoc) {
      if (!confirm('确定删除这篇文档？相关检索片段也会删除。')) return;
      await api(base() + '/documents/' + target.dataset.deleteDoc, {method:'DELETE'});
      toast('文档已删除'); await changed();
    }
    if (target.dataset.conversation) {
      state.conversation = await api(base() + '/conversations/' + target.dataset.conversation);
      renderChat();
    }
    if (target.dataset.question) { $('#question').value = target.dataset.question; $('#question').focus(); }
    const action = target.dataset.action;
    if (action === 'new-space') newSpaceDialog();
    if (action === 'close-dialog') dialog.close();
    if (action === 'new-doc') await editor();
    if (action === 'import') { $('#file-input').value = ''; $('#file-input').click(); }
    if (action === 'new-chat') { state.conversation = null; renderChat(); }
    if (action === 'retry') await render();
    if (action === 'clear-search') { $('#search-input').value = ''; $('#document-results').innerHTML = documentRows(state.documents); }
    if (action === 'editor-preview' || action === 'editor-write') {
      const preview = action === 'editor-preview';
      $('#editor-content').hidden = preview;
      $('#editor-preview').hidden = !preview;
      document.querySelectorAll('.editor-tabs button').forEach(b => b.classList.toggle('active', b === target));
      if (preview) markdown($('#editor-content').value, $('#editor-preview'));
    }
    if (action === 'demo') {
      target.disabled = true;
      try {
        const space = await api('/demo', {method:'POST'});
        state.spaceId = space.id; state.view = 'overview';
        await changed(); toast('已创建示例知识库，包含 3 篇可编辑文档');
      } finally { target.disabled = false; }
    }
    if (action === 'delete-space') {
      if (!confirm(`确定删除「${currentSpace().name}」及全部文档和对话？此操作不可撤销。`)) return;
      await api(base(), {method:'DELETE'});
      state.conversation = null;
      await changed(); toast('知识库已删除');
    }
  } catch (error) { toast(error.message, true); }
});

document.addEventListener('submit', async event => {
  if (!['space-form', 'document-form', 'search-form', 'chat-form'].includes(event.target.id)) return;
  event.preventDefault();
  const form = event.target;
  const submit = $('button[type="submit"]', form);
  try {
    if (form.id === 'search-form') { await searchDocuments(); return; }
    if (form.id === 'chat-form') { await sendQuestion(); return; }
    submit.disabled = true;
    if (form.id === 'space-form') {
      const data = Object.fromEntries(new FormData(form));
      const space = await api('/spaces', jsonOptions('POST', data));
      state.spaceId = space.id; state.conversation = null;
      dialog.close(); await changed(); toast('知识库已创建');
    }
    if (form.id === 'document-form') {
      const id = form.dataset.id;
      await api(base() + '/documents' + (id ? '/' + id : ''), jsonOptions(id ? 'PUT' : 'POST', Object.fromEntries(new FormData(form))));
      dialog.close(); await changed(); toast('文档已保存，检索索引已更新');
    }
  } catch (error) { toast(error.message, true); }
  finally { if (submit) submit.disabled = false; }
});

$('#file-input').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > state.settings.max_upload_bytes) { toast('文件超过 10 MB 限制', true); return; }
  const data = new FormData(); data.append('file', file);
  const sid = state.spaceId;
  toast('正在读取文件并建立索引…');
  try {
    await api('/spaces/' + sid + '/import', {method:'POST', body:data});
    await changed(); toast('文件已导入并完成索引');
  } catch (error) { toast(error.message, true); }
});
$('#add-space').addEventListener('click', () => {
  if (state.busy) { toast('请等待当前问答完成'); return; }
  newSpaceDialog();
});
dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });

(async () => {
  try {
    state.settings = await api('/settings');
    $('#mode-badge').textContent = state.settings.mode === 'agent' ? '✧ 智能体模式' : '◈ 本地检索模式';
    await refreshSpaces();
    await render();
  } catch (error) { main.innerHTML = empty('无法连接服务', error.message, '<button class="button" onclick="location.reload()">重新连接</button>'); }
})();
