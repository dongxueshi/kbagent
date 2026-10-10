/* Repository UI uses text rendering for all source code. */
let repositoryPoll;
async function renderRepositories(version) {
  clearTimeout(repositoryPoll);
  const sid = state.spaceId;
  const repos = await api(base() + '/repositories');
  if (version !== renderVersion || sid !== state.spaceId) return;
  const status = r => ({new:'尚未同步',queued:'等待同步',running:'正在同步',ready:'已同步',error:'同步失败'}[r.sync_status]);
  main.innerHTML = heading('代码 Wiki', '连接自建 GitLab 或服务器上的 Git 目录，将代码变成可检索、可追溯的知识。', '<button class="button primary" data-action="add-repository">＋ 添加仓库</button>', 'CODE → KNOWLEDGE') + '<div class="repository-grid">' + (repos.map(r => `<section class="panel repository-card"><h2>${esc(r.name)}</h2><p class="repository-path">${esc(r.source)}</p><div class="muted">${r.kind === 'local' ? '本地目录' : 'GitLab'} · ${esc(r.ref || '默认分支')} · ${esc(status(r))}</div><p>${r.file_count} 个文件 · ${r.skipped_count} 个已过滤 ${r.commit_sha ? '· ' + esc(r.commit_sha.slice(0,12)) : ''}</p>${r.error || r.wiki_error ? `<p class="repository-error">${esc(r.error || r.wiki_error)}</p>` : ''}<p class="muted">${['queued','running'].includes(r.wiki_status) ? 'AI Wiki 生成中…' : r.wiki_status === 'ready' ? 'AI Wiki 已生成' : '结构 Wiki（无需模型）'}</p><div class="actions"><button class="button primary" data-repository-open="${r.id}" ${!r.snapshot_id ? 'disabled' : ''}>打开 Wiki</button><button class="button" data-repository-job="sync" data-repository-id="${r.id}" ${repositoryBusy(r) ? 'disabled' : ''}>同步</button><button class="button" data-repository-job="wiki" data-repository-id="${r.id}" ${repositoryBusy(r) || !r.snapshot_id || state.settings.mode !== 'agent' ? 'disabled' : ''}>生成 AI Wiki</button><button class="text-button" data-repository-delete="${r.id}" ${repositoryBusy(r) ? 'disabled' : ''}>删除</button></div></section>`).join('') || empty('添加一个代码仓库', '同步后自动生成结构 Wiki；配置模型后可生成架构与关键流程说明。')) + '</div>';
  if (repos.some(repositoryBusy)) repositoryPoll = setTimeout(() => {
    if (state.view === 'repositories' && sid === state.spaceId) renderRepositories(version).catch(e => toast(e.message, true));
  }, 1200);
}
function repositoryBusy(r) { return [r.sync_status,r.wiki_status].some(s => ['queued','running'].includes(s)); }
async function addRepositoryDialog() {
  const config = await api('/repository-settings');
  showDialog(`<form id="repository-form">${dialogHead('添加代码仓库')}<div class="dialog-body"><label class="field">仓库名称<input name="name" required maxlength="100" placeholder="例如：订单服务"></label><label class="field">来源类型<select name="kind"><option value="gitlab">自建 GitLab</option><option value="local">已 clone 的本地目录</option></select></label><label class="field">仓库 URL 或服务器绝对路径<input name="source" required maxlength="1024" placeholder="https://gitlab.example.com/team/service.git 或 /srv/repos/service"></label><label class="field">分支<input name="ref" maxlength="200" placeholder="留空使用默认分支"></label><p class="muted">GitLab ${config.gitlab_configured ? '已配置' : '未配置'} · 本地目录允许范围${config.local_configured ? '已配置' : '未配置'}。在服务端 .env 中配置地址、Token 和允许目录。</p><p class="muted">仅导入已提交的文本文件；默认最多 ${config.max_files} 个文件。自动跳过常见密钥文件、依赖和生成目录。</p></div><div class="dialog-footer"><button type="button" class="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">添加并同步</button></div></form>`);
}
async function openRepository(id) {
  const sid = state.spaceId;
  const repo = await api(base() + '/repositories/' + id);
  if (sid !== state.spaceId) return;
  const wiki = repo.files.filter(f => f.kind !== 'code');
  const files = repo.files.filter(f => f.kind === 'code');
  showDialog(`${dialogHead(repo.name + ' · 代码 Wiki')}<div class="dialog-body"><p class="muted repository-path">${esc(repo.ref || '默认分支')} · ${esc(repo.commit_sha)}</p><h3>Wiki 页面</h3><div class="repository-wiki">${wiki.map(f => `<button class="button" data-doc="${f.document_id}">${esc(f.path)}</button>`).join('')}</div><h3>源码文件</h3><input id="repository-file-filter" class="repository-filter" placeholder="按文件路径筛选" aria-label="按文件路径筛选"><div class="repository-file-list">${files.map(f => `<button class="doc-row" data-repository-code="${esc(JSON.stringify({repository_id:id,path:f.path,snapshot_id:f.snapshot_id}))}"><strong>${esc(f.path)}</strong><small>${f.line_count} 行</small></button>`).join('')}</div></div>`, true);
  $('#repository-file-filter').addEventListener('input', event => {
    const query = event.target.value.toLowerCase();
    document.querySelectorAll('[data-repository-code]').forEach(el => {el.hidden = !JSON.parse(el.dataset.repositoryCode).path.toLowerCase().includes(query);});
  });
}
async function viewSource(meta, start = meta.start_line || 1, end = meta.end_line || start + 199) {
  const sid = state.spaceId;
  const params = new URLSearchParams({path:meta.path,start_line:String(start),end_line:String(end)});
  if (meta.snapshot_id) params.set('snapshot_id', meta.snapshot_id);
  const source = await api(base() + '/repositories/' + meta.repository_id + '/source?' + params);
  if (sid !== state.spaceId) return;
  showDialog(`${dialogHead(source.path)}<div class="dialog-body"><p class="muted repository-path">提交 ${esc(source.commit_sha)} · ${source.line_count} 行</p><div class="source-code" role="region" aria-label="带行号的源码">${source.content.split('\n').map((line,i) => `<div class="source-line"><span>${source.start_line+i}</span><code>${esc(line)}</code></div>`).join('')}</div><div class="actions source-pagination">${source.start_line > 1 ? '<button class="button" id="source-previous">上一段</button>' : ''}${source.end_line < source.line_count ? '<button class="button" id="source-next">下一段</button>' : ''}</div></div>`, true);
  const current = {...meta,snapshot_id:source.snapshot_id};
  $('#source-previous')?.addEventListener('click', () => viewSource(current, Math.max(1,source.start_line-200),source.start_line-1).catch(e => toast(e.message,true)));
  $('#source-next')?.addEventListener('click', () => viewSource(current,source.end_line+1,source.end_line+200).catch(e => toast(e.message,true)));
}
document.addEventListener('click', async event => {
  const el = event.target.closest('button');
  if (!el) return;
  try {
    if (el.dataset.action === 'add-repository') await addRepositoryDialog();
    if (el.dataset.repositoryOpen) await openRepository(el.dataset.repositoryOpen);
    if (el.dataset.repositoryCode) await viewSource(JSON.parse(el.dataset.repositoryCode));
    if (el.dataset.repositoryJob) {
      await api(base() + '/repositories/' + el.dataset.repositoryId + '/' + el.dataset.repositoryJob,{method:'POST'});
      await render();
    }
    if (el.dataset.repositoryDelete && confirm('删除此仓库的代码、Wiki 和所有历史快照？')) {
      await api(base() + '/repositories/' + el.dataset.repositoryDelete,{method:'DELETE'});
      await changed();
    }
  } catch (e) {toast(e.message,true);}
});
document.addEventListener('submit', async event => {
  if (event.target.id !== 'repository-form') return;
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    await api(base() + '/repositories', jsonOptions('POST', Object.fromEntries(new FormData(form))));
    dialog.close(); await render();
  } catch (e) {toast(e.message,true); button.disabled=false;}
});
