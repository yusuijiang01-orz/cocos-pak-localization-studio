(function () {
  'use strict';

  if (!window.studio || typeof window.studio.vnextDashboard !== 'function') return;
  if (document.getElementById('vnextNav')) return;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = value => Number(value || 0).toLocaleString();
  const pct = (a, b) => b ? `${(Number(a || 0) / Number(b) * 100).toFixed(1)}%` : '0.0%';
  const byId = id => document.getElementById(id);

  function currentProject() {
    try { return typeof project !== 'undefined' ? project : null; } catch { return null; }
  }
  function workspace() { return String(currentProject()?.workspace || ''); }
  function notify(message, options) {
    try {
      if (typeof showAppDialog === 'function') return showAppDialog(String(message), options || {});
    } catch {}
    window.alert(String(message));
    return Promise.resolve(true);
  }
  async function confirmAction(message) {
    try {
      if (typeof appConfirm === 'function') return await appConfirm(String(message));
    } catch {}
    return window.confirm(String(message));
  }

  const state = {
    page: 'overview',
    workspace: '',
    dashboard: null,
    translating: false,
    stopping: false,
    reviewItems: [],
    reviewSelected: '',
    reviewDetail: null,
    knowledgeKind: 'glossary',
    knowledgeItems: [],
    knowledgeSelected: null,
    selectedPaks: new Set(),
    syncedWorkspace: '',
  };

  const header = document.querySelector('.app > header');
  const legacyMain = document.querySelector('.app > main');
  if (!header || !legacyMain) return;
  legacyMain.id = legacyMain.id || 'legacyMain';

  const nav = document.createElement('nav');
  nav.id = 'vnextNav';
  nav.className = 'vnext-nav';
  nav.innerHTML = `
    <button data-vn-page="overview" class="active">项目概览</button>
    <button data-vn-page="translate">智能翻译</button>
    <button data-vn-page="review">审核</button>
    <button data-vn-page="knowledge">术语与 TM</button>
    <button data-vn-page="build">构建</button>
    <span class="vnext-spacer"></span>
    <button data-vn-page="legacy" class="legacy-entry">旧版工具</button>
  `;
  header.insertAdjacentElement('afterend', nav);

  const shell = document.createElement('section');
  shell.id = 'vnextShell';
  shell.className = 'vnext-shell';
  shell.innerHTML = `
    <section class="vnext-page active" data-vn-section="overview">
      <div class="vnext-page-head"><div><h2>项目概览</h2><p>vNext 以唯一文本为核心，复用 TM，只翻新增内容，并在构建前重新执行安全门禁。</p></div><div class="vnext-actions"><button id="vnSync">同步当前项目</button><button id="vnRefresh" class="secondary">刷新</button></div></div>
      <div id="vnProjectBanner" class="vnext-banner">请先通过右上角“打开项目”或“导入 PAK”加载工作区。</div>
      <div class="vnext-grid">
        <div class="vnext-card info"><div class="k">唯一文本</div><div class="v" id="vnUnique">0</div><div class="s" id="vnOccurrences">0 个出现位置</div></div>
        <div class="vnext-card good"><div class="k">已安全翻译</div><div class="v" id="vnTranslated">0</div><div class="s" id="vnTranslatedRate">0.0%</div></div>
        <div class="vnext-card warn"><div class="k">待翻译</div><div class="v" id="vnPending">0</div><div class="s">TM 未命中且尚未生成安全中文</div></div>
        <div class="vnext-card bad"><div class="k">待审核</div><div class="v" id="vnReviewPending">0</div><div class="s" id="vnReviewSeverity">高风险 0</div></div>
      </div>
      <div class="vnext-two">
        <div class="vnext-panel"><div class="vnext-panel-head"><h3>翻译来源</h3></div><div class="vnext-panel-body"><div id="vnOrigins" class="vnext-chip-row"><span class="vnext-muted">暂无数据</span></div></div></div>
        <div class="vnext-panel"><div class="vnext-panel-head"><h3>长期知识库</h3></div><div class="vnext-panel-body"><div class="vnext-kv"><div class="k">TM</div><div id="vnTmCount">0</div><div class="k">锁定 TM</div><div id="vnTmLocked">0</div><div class="k">有效术语</div><div id="vnGlossaryCount">0</div><div class="k">Canonical Reference</div><div id="vnReferenceCount">0</div></div></div></div>
      </div>
      <div class="vnext-panel"><div class="vnext-panel-head"><h3>最近任务</h3></div><div class="vnext-panel-body"><div id="vnRecentStatus" class="vnext-result">尚无 vNext 任务。</div></div></div>
    </section>

    <section class="vnext-page" data-vn-section="translate">
      <div class="vnext-page-head"><div><h2>智能翻译</h2><p>优先级：人工锁定 → TM → 中文参考 → 模型缓存 → Ollama。术语只约束专名，不再逐词拼接。</p></div><div class="vnext-actions"><button id="vnTranslateStart">开始智能翻译</button></div></div>
      <div class="vnext-grid">
        <div class="vnext-card info"><div class="k">唯一文本</div><div class="v" id="vnTUnique">0</div></div>
        <div class="vnext-card good"><div class="k">已完成</div><div class="v" id="vnTDone">0</div></div>
        <div class="vnext-card warn"><div class="k">仍需处理</div><div class="v" id="vnTLeft">0</div></div>
        <div class="vnext-card bad"><div class="k">拒绝 / 失败</div><div class="v" id="vnTRejected">0</div></div>
      </div>
      <div class="vnext-panel"><div class="vnext-panel-head"><h3>Ollama 设置</h3></div><div class="vnext-panel-body">
        <div class="vnext-inline">
          <div class="vnext-field"><label>模型</label><input id="vnModel" class="vnext-input" value="qwen3:14b" /></div>
          <div class="vnext-field"><label>Ollama Base URL</label><input id="vnBaseUrl" class="vnext-input" value="http://127.0.0.1:11435" /></div>
          <div class="vnext-field compact"><label>批量条数</label><input id="vnBatch" type="number" min="1" max="128" class="vnext-input" value="16" /></div>
          <div class="vnext-field compact"><label>超时（秒）</label><input id="vnTimeout" type="number" min="30" class="vnext-input" value="600" /></div>
        </div>
        <div class="vnext-muted" style="margin-top:9px">每批成功后立即提交 SQLite。停止、崩溃或重新启动后，已完成内容会继续复用。</div>
      </div></div>
      <div class="vnext-panel"><div class="vnext-panel-head"><h3>当前翻译任务</h3><span id="vnTranslateState" class="vnext-muted">空闲</span></div><div class="vnext-panel-body">
        <progress id="vnTranslateProgress" class="vnext-progress" max="100" value="0"></progress>
        <div id="vnTranslateMessage" class="vnext-progress-text">尚未开始。</div>
        <div id="vnTranslateLog" class="vnext-log" style="margin-top:10px">TM 命中的文本不会发送给 Ollama。</div>
      </div></div>
    </section>

    <section class="vnext-page" data-vn-section="review">
      <div class="vnext-page-head"><div><h2>审核队列</h2><p>只展示需要人工判断的风险内容。人工修改并批准后会晋升为锁定 TM，以后永久复用。</p></div><div class="vnext-actions"><button id="vnReviewRefresh" class="secondary">刷新队列</button></div></div>
      <div class="vnext-panel"><div class="vnext-panel-body"><div class="vnext-inline">
        <div class="vnext-field compact"><label>状态</label><select id="vnReviewState" class="vnext-select"><option value="pending">待审核</option><option value="deferred">稍后处理</option><option value="rejected">已拒绝</option><option value="approved">已批准</option><option value="all">全部</option></select></div>
        <div class="vnext-field compact"><label>严重度</label><select id="vnReviewSeverity" class="vnext-select"><option value="">全部</option><option value="fatal">Fatal</option><option value="error">Error</option><option value="warning">Warning</option><option value="info">Info</option></select></div>
        <div class="vnext-field"><label>搜索</label><input id="vnReviewQuery" class="vnext-input" placeholder="搜索原文、译文或风险代码" /></div>
      </div></div></div>
      <div class="vnext-review-layout">
        <div class="vnext-panel"><div class="vnext-panel-head"><h3>风险文本</h3><span id="vnReviewCount" class="vnext-muted">0 条</span></div><div class="vnext-table-wrap"><table class="vnext-table"><thead><tr><th>等级</th><th>原文</th><th>当前译文</th><th>原因</th><th>影响</th></tr></thead><tbody id="vnReviewRows"></tbody></table></div></div>
        <div class="vnext-panel vnext-review-detail"><div class="vnext-panel-head"><h3>审核详情</h3></div><div class="vnext-panel-body" id="vnReviewDetail"><div class="vnext-empty">选择左侧一条风险文本。</div></div></div>
      </div>
    </section>

    <section class="vnext-page" data-vn-section="knowledge">
      <div class="vnext-page-head"><div><h2>术语与翻译记忆</h2><p>术语用于约束专有名词；TM 保存完整句子。人工确认内容优先于任何模型结果。</p></div><div class="vnext-actions"><button id="vnKnowledgeRefresh" class="secondary">刷新</button></div></div>
      <div class="vnext-panel"><div class="vnext-panel-head"><div class="vnext-tabs"><button data-vn-knowledge="glossary" class="active">术语库</button><button data-vn-knowledge="tm">TM</button><button data-vn-knowledge="reference">中文参考</button></div><span id="vnKnowledgeCount" class="vnext-muted">0 条</span></div><div class="vnext-panel-body"><div class="vnext-inline"><div class="vnext-field"><label>搜索</label><input id="vnKnowledgeQuery" class="vnext-input" placeholder="搜索越南文或中文" /></div></div></div><div class="vnext-table-wrap"><table class="vnext-table"><thead id="vnKnowledgeHead"></thead><tbody id="vnKnowledgeRows"></tbody></table></div></div>
      <div class="vnext-panel" id="vnKnowledgeEditorPanel"><div class="vnext-panel-head"><h3 id="vnKnowledgeEditorTitle">新增 / 修改术语</h3></div><div class="vnext-panel-body"><div id="vnKnowledgeEditor" class="vnext-editor">
        <div class="vnext-field"><label>原文</label><textarea id="vnKSource" class="vnext-textarea"></textarea></div>
        <div class="vnext-field"><label>中文</label><textarea id="vnKTarget" class="vnext-textarea"></textarea></div>
        <div class="vnext-field"><label>类型</label><input id="vnKType" class="vnext-input" value="general" /></div>
        <div class="vnext-field"><label>范围</label><input id="vnKScope" class="vnext-input" value="global" /></div>
        <div class="vnext-field"><label>状态</label><select id="vnKStatus" class="vnext-select"><option value="approved">已确认</option><option value="locked">锁定</option><option value="candidate">候选</option><option value="disabled">禁用</option><option value="conflict">冲突</option></select></div>
        <div class="vnext-field"><label>优先级</label><input id="vnKPriority" type="number" class="vnext-input" value="100" /></div>
        <label class="vnext-check wide"><input id="vnKLocked" type="checkbox" checked /> 锁定：机器翻译不得覆盖</label>
        <div class="vnext-editor-actions"><button id="vnKClear" class="secondary">清空</button><button id="vnKDelete" class="danger">删除所选</button><button id="vnKSave">保存</button></div>
      </div></div></div>
    </section>

    <section class="vnext-page" data-vn-section="build">
      <div class="vnext-page-head"><div><h2>安全构建</h2><p>只从原版 PAK 重建。候选包必须通过结构、编码、压缩自校验和完整重解包验证后才会发布。</p></div><div class="vnext-actions"><button id="vnPreflight" class="secondary">仅运行构建前检查</button><button id="vnBuild">验证并构建 PAK</button></div></div>
      <div class="vnext-two">
        <div class="vnext-panel"><div class="vnext-panel-head"><h3>构建范围</h3></div><div class="vnext-panel-body"><div id="vnPakList" class="vnext-pak-list"><div class="vnext-muted">尚未加载项目。</div></div><div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap"><label class="vnext-check"><input id="vnRequireTranslated" type="checkbox" /> 未翻译文本也阻止构建</label><label class="vnext-check"><input id="vnFailWarnings" type="checkbox" /> Warning 也视为失败</label></div></div></div>
        <div class="vnext-panel"><div class="vnext-panel-head"><h3>门禁结果</h3></div><div class="vnext-panel-body"><div id="vnBuildResult" class="vnext-result">尚未执行检查。</div></div></div>
      </div>
      <div class="vnext-panel"><div class="vnext-panel-head"><h3>最近构建</h3><button id="vnBuildHistoryRefresh" class="secondary mini">刷新</button></div><div class="vnext-table-wrap"><table class="vnext-table"><thead><tr><th>时间</th><th>状态</th><th>Build ID</th><th>验证</th></tr></thead><tbody id="vnBuildHistory"></tbody></table></div></div>
    </section>
  `;
  legacyMain.insertAdjacentElement('beforebegin', shell);
  legacyMain.classList.add('vnext-legacy-hidden');

  function setPage(page) {
    state.page = page;
    nav.querySelectorAll('button[data-vn-page]').forEach(btn => btn.classList.toggle('active', btn.dataset.vnPage === page));
    if (page === 'legacy') {
      shell.classList.add('hidden');
      legacyMain.classList.remove('vnext-legacy-hidden');
      return;
    }
    legacyMain.classList.add('vnext-legacy-hidden');
    shell.classList.remove('hidden');
    shell.querySelectorAll('[data-vn-section]').forEach(section => section.classList.toggle('active', section.dataset.vnSection === page));
    if (page === 'overview' || page === 'translate' || page === 'build') refreshDashboard({ autoIngest: true });
    if (page === 'review') loadReviewList();
    if (page === 'knowledge') loadKnowledge();
    if (page === 'build') loadBuildHistory();
  }
  nav.addEventListener('click', event => {
    const button = event.target.closest('button[data-vn-page]');
    if (button) setPage(button.dataset.vnPage);
  });

  function setBanner(text, tone = '') {
    const el = byId('vnProjectBanner');
    el.textContent = text;
    el.className = `vnext-banner${tone ? ` ${tone}` : ''}`;
  }

  function renderDashboard(report) {
    state.dashboard = report;
    const total = Number(report.unique_units || 0);
    const translated = Number(report.translated_unique || 0);
    byId('vnUnique').textContent = num(total);
    byId('vnOccurrences').textContent = `${num(report.active_occurrences)} 个出现位置`;
    byId('vnTranslated').textContent = num(translated);
    byId('vnTranslatedRate').textContent = `${pct(translated, total)} · 只统计当前安全译文`;
    byId('vnPending').textContent = num(report.pending_unique);
    byId('vnReviewPending').textContent = num(report.review?.pending || 0);
    byId('vnReviewSeverity').textContent = `Error/Fatal ${num((report.review_severity?.error || 0) + (report.review_severity?.fatal || 0))}`;
    byId('vnTUnique').textContent = num(total);
    byId('vnTDone').textContent = num(translated);
    byId('vnTLeft').textContent = num(report.pending_unique);
    byId('vnTRejected').textContent = num(report.rejected_unique);
    byId('vnTmCount').textContent = num(report.knowledge?.tm);
    byId('vnTmLocked').textContent = num(report.knowledge?.tm_locked);
    byId('vnGlossaryCount').textContent = `${num(report.knowledge?.glossary_active)} / ${num(report.knowledge?.glossary)}`;
    byId('vnReferenceCount').textContent = num(report.knowledge?.reference);

    const originEntries = Object.entries(report.target_origin || {}).sort((a, b) => b[1] - a[1]);
    byId('vnOrigins').innerHTML = originEntries.length
      ? originEntries.map(([key, value]) => `<span class="vnext-chip">${esc(key)} · ${num(value)}</span>`).join('')
      : '<span class="vnext-muted">尚无译文来源数据</span>';

    const job = report.latest_job;
    const build = report.latest_build;
    const recent = [];
    if (job) recent.push(`翻译任务：${job.status} · ${num(job.completed_unique)}/${num(job.total_unique)} · 模型 ${job.model || '-'}`);
    if (build) recent.push(`最近构建：${build.status} · ${build.created_at || ''} · ${build.build_id || ''}`);
    byId('vnRecentStatus').textContent = recent.length ? recent.join('\n') : '尚无 vNext 翻译或构建任务。';

    const paks = Array.isArray(report.paks) ? report.paks : [];
    if (!state.selectedPaks.size) paks.forEach(item => state.selectedPaks.add(item.pak));
    renderPakList(paks);
    setBanner(`已连接工作区：${report.workspace} · vNext ${num(total)} 个唯一文本`, 'ok');
  }

  async function refreshDashboard({ autoIngest = false } = {}) {
    const ws = workspace();
    if (!ws) {
      state.dashboard = null;
      setBanner('请先通过右上角“打开项目”或“导入 PAK”加载工作区。');
      return null;
    }
    state.workspace = ws;
    let res = await window.studio.vnextDashboard({ workspace: ws });
    if (!res?.ok) {
      setBanner(`vNext 状态读取失败：${res?.error || '未知错误'}`, 'error');
      return null;
    }
    if (autoIngest && Number(res.report?.unique_units || 0) === 0 && res.report?.has_records && state.syncedWorkspace !== ws) {
      state.syncedWorkspace = ws;
      setBanner('首次进入 vNext：正在建立 Translation Unit / occurrence 索引…');
      const ingest = await window.studio.vnextIngestWorkspace({ workspace: ws });
      if (!ingest?.ok) {
        setBanner(`vNext 工作区同步失败：${ingest?.error || '未知错误'}`, 'error');
        return null;
      }
      res = await window.studio.vnextDashboard({ workspace: ws });
    }
    if (res?.ok) renderDashboard(res.report || {});
    return res?.report || null;
  }

  byId('vnRefresh').onclick = () => refreshDashboard();
  byId('vnSync').onclick = async () => {
    const ws = workspace();
    if (!ws) return notify('请先打开或导入项目');
    byId('vnSync').disabled = true;
    try {
      const res = await window.studio.vnextIngestWorkspace({ workspace: ws });
      if (!res?.ok) return notify(res?.error || '同步失败');
      state.syncedWorkspace = ws;
      await refreshDashboard();
      notify(`vNext 同步完成：${num(res.report?.active_unique_units)} 个唯一文本，${num(res.report?.active_occurrences)} 个出现位置。`, { title: '同步完成', tone: 'success' });
    } finally { byId('vnSync').disabled = false; }
  };

  function loadTranslatePrefs() {
    try {
      const cfg = JSON.parse(localStorage.getItem('vnext.translate.config') || '{}');
      if (cfg.model) byId('vnModel').value = cfg.model;
      if (cfg.baseUrl) byId('vnBaseUrl').value = cfg.baseUrl;
      if (cfg.batchSize) byId('vnBatch').value = cfg.batchSize;
      if (cfg.timeout) byId('vnTimeout').value = cfg.timeout;
    } catch {}
  }
  function saveTranslatePrefs() {
    localStorage.setItem('vnext.translate.config', JSON.stringify({
      model: byId('vnModel').value.trim(), baseUrl: byId('vnBaseUrl').value.trim(),
      batchSize: Number(byId('vnBatch').value) || 16, timeout: Number(byId('vnTimeout').value) || 600,
    }));
  }
  loadTranslatePrefs();

  function setTranslateButton() {
    const btn = byId('vnTranslateStart');
    btn.classList.toggle('danger', state.translating);
    btn.disabled = state.stopping;
    btn.textContent = state.stopping ? '正在停止并保存…' : state.translating ? '停止并保存' : '开始智能翻译';
    byId('vnTranslateState').textContent = state.stopping ? '正在提交断点' : state.translating ? '运行中' : '空闲';
  }
  function appendTranslateLog(text) {
    const el = byId('vnTranslateLog');
    const stamp = new Date().toLocaleTimeString();
    el.textContent = `${el.textContent}\n[${stamp}] ${text}`.trim().split('\n').slice(-80).join('\n');
    el.scrollTop = el.scrollHeight;
  }
  byId('vnTranslateStart').onclick = async () => {
    const ws = workspace();
    if (!ws) return notify('请先打开或导入项目');
    if (state.translating) {
      if (state.stopping) return;
      state.stopping = true;
      setTranslateButton();
      const stopped = await window.studio.vnextStopTranslate({ workspace: ws });
      if (!stopped?.ok) {
        state.stopping = false;
        setTranslateButton();
        notify(stopped?.error || '停止请求失败');
      }
      return;
    }
    saveTranslatePrefs();
    const ingest = await window.studio.vnextIngestWorkspace({ workspace: ws });
    if (!ingest?.ok) return notify(ingest?.error || '开始翻译前同步工作区失败');
    state.syncedWorkspace = ws;
    state.translating = true;
    state.stopping = false;
    setTranslateButton();
    byId('vnTranslateProgress').value = 0;
    byId('vnTranslateMessage').textContent = '正在准备智能翻译…';
    appendTranslateLog(`开始：${byId('vnModel').value.trim()} · batch ${byId('vnBatch').value}`);
    try {
      const res = await window.studio.vnextTranslate({
        workspace: ws,
        model: byId('vnModel').value.trim() || 'qwen3:14b',
        baseUrl: byId('vnBaseUrl').value.trim() || 'http://127.0.0.1:11435',
        batchSize: Number(byId('vnBatch').value) || 16,
        timeout: Number(byId('vnTimeout').value) || 600,
      });
      if (!res?.ok) notify(res?.error || '智能翻译失败');
      else appendTranslateLog(`任务结束：${res.report?.status || 'completed'}`);
    } finally {
      state.translating = false;
      state.stopping = false;
      setTranslateButton();
      await refreshDashboard();
      if (state.page === 'review') await loadReviewList();
    }
  };

  async function loadReviewList() {
    const ws = workspace();
    if (!ws) {
      byId('vnReviewRows').innerHTML = '<tr><td colspan="5"><div class="vnext-empty">请先打开项目。</div></td></tr>';
      return;
    }
    const res = await window.studio.vnextReviewList({
      workspace: ws,
      state: byId('vnReviewState').value,
      severity: byId('vnReviewSeverity').value,
      query: byId('vnReviewQuery').value.trim(),
      limit: 300,
    });
    if (!res?.ok) return notify(res?.error || '读取审核队列失败');
    state.reviewItems = res.items || [];
    byId('vnReviewCount').textContent = `${num(res.count)} 条`;
    byId('vnReviewRows').innerHTML = state.reviewItems.length ? state.reviewItems.map(item => `
      <tr data-unit="${esc(item.unit_id)}" class="${state.reviewSelected === item.unit_id ? 'active' : ''}">
        <td class="sev-${esc(item.severity)}">${esc(item.severity)}</td>
        <td class="source">${esc(item.source_snapshot)}</td>
        <td class="target">${esc(item.target_snapshot || '')}</td>
        <td class="small">${esc(item.reason_code)}${Array.isArray(item.qa_codes) && item.qa_codes.length ? `<br>${esc(item.qa_codes.join(', '))}` : ''}</td>
        <td class="small">${num(item.occurrence_count)}</td>
      </tr>`).join('') : '<tr><td colspan="5"><div class="vnext-empty">当前筛选条件下没有风险文本。</div></td></tr>';
    byId('vnReviewRows').querySelectorAll('tr[data-unit]').forEach(row => row.onclick = () => loadReviewDetail(row.dataset.unit));
  }
  async function loadReviewDetail(unitId) {
    const ws = workspace(); if (!ws) return;
    const res = await window.studio.vnextReviewShow({ workspace: ws, unitId });
    if (!res?.ok) return notify(res?.error || '读取审核详情失败');
    state.reviewSelected = unitId;
    state.reviewDetail = res.item;
    byId('vnReviewRows').querySelectorAll('tr[data-unit]').forEach(row => row.classList.toggle('active', row.dataset.unit === unitId));
    const item = res.item || {}, unit = item.unit || {}, target = item.target || {}, review = item.review || {};
    const findings = (item.findings || []).map(f => `<div class="vnext-finding"><b class="sev-${esc(f.severity)}">${esc(f.code)}</b><br>${esc(f.message)}</div>`).join('');
    const occurrences = (item.occurrences || []).map(o => `${o.pak_name} · ${o.source_file} · ${o.record_id}`).join('\n');
    byId('vnReviewDetail').innerHTML = `
      <div class="vnext-field"><label>原文</label><textarea id="vnReviewSource" class="vnext-textarea" readonly>${esc(unit.source_text || '')}</textarea></div>
      <div class="vnext-field" style="margin-top:10px"><label>中文译文（可直接修改）</label><textarea id="vnReviewTarget" class="vnext-textarea">${esc(target.target_text || review.target_snapshot || '')}</textarea></div>
      <div class="vnext-field" style="margin-top:10px"><label>审核备注</label><input id="vnReviewNote" class="vnext-input" value="${esc(review.note || '')}" /></div>
      <div class="vnext-findings">${findings || '<div class="vnext-muted">当前没有未解决 QA finding。</div>'}</div>
      <div class="vnext-muted" style="margin-top:12px">出现位置</div><div class="vnext-occurrences">${esc(occurrences || '无')}</div>
      <div class="vnext-actions" style="margin-top:14px"><button id="vnReviewDefer" class="secondary">稍后处理</button><button id="vnReviewReject" class="danger">拒绝当前译文</button><button id="vnReviewApprove">批准并锁定 TM</button></div>`;
    byId('vnReviewApprove').onclick = () => reviewAction('approve');
    byId('vnReviewReject').onclick = () => reviewAction('reject');
    byId('vnReviewDefer').onclick = () => reviewAction('defer');
  }
  async function reviewAction(action) {
    const ws = workspace(); if (!ws || !state.reviewSelected) return;
    const target = byId('vnReviewTarget')?.value || '';
    const note = byId('vnReviewNote')?.value || '';
    let res;
    if (action === 'approve') res = await window.studio.vnextReviewApprove({ workspace: ws, unitId: state.reviewSelected, target, note, lock: true });
    else if (action === 'reject') {
      if (!await confirmAction('拒绝后，同一个错误译文将被当前项目永久屏蔽。继续吗？')) return;
      res = await window.studio.vnextReviewReject({ workspace: ws, unitId: state.reviewSelected, note });
    } else res = await window.studio.vnextReviewState({ workspace: ws, unitId: state.reviewSelected, state: 'deferred', note });
    if (!res?.ok) return notify(res?.error || '审核操作失败');
    state.reviewSelected = '';
    state.reviewDetail = null;
    byId('vnReviewDetail').innerHTML = '<div class="vnext-empty">处理完成。请选择下一条。</div>';
    await loadReviewList();
    await refreshDashboard();
  }
  byId('vnReviewRefresh').onclick = loadReviewList;
  byId('vnReviewState').onchange = loadReviewList;
  byId('vnReviewSeverity').onchange = loadReviewList;
  let reviewSearchTimer = null;
  byId('vnReviewQuery').oninput = () => { clearTimeout(reviewSearchTimer); reviewSearchTimer = setTimeout(loadReviewList, 300); };

  function knowledgeColumns(kind) {
    if (kind === 'glossary') return '<tr><th>原文</th><th>中文</th><th>类型</th><th>状态</th><th>优先级</th></tr>';
    if (kind === 'tm') return '<tr><th>原文</th><th>中文</th><th>质量</th><th>锁定</th><th>使用</th></tr>';
    return '<tr><th>别名</th><th>权威中文</th><th>类型</th><th>状态</th><th>来源</th></tr>';
  }
  async function loadKnowledge() {
    const kind = state.knowledgeKind;
    const res = await window.studio.vnextKnowledgeList({ kind, query: byId('vnKnowledgeQuery').value.trim(), limit: 500 });
    if (!res?.ok) return notify(res?.error || '读取知识库失败');
    const report = res.report || {};
    state.knowledgeItems = report.items || [];
    state.knowledgeSelected = null;
    byId('vnKnowledgeCount').textContent = `${num(report.total)} 条`;
    byId('vnKnowledgeHead').innerHTML = knowledgeColumns(kind);
    byId('vnKnowledgeRows').innerHTML = state.knowledgeItems.length ? state.knowledgeItems.map((item, index) => {
      if (kind === 'glossary') return `<tr data-k-index="${index}"><td class="source">${esc(item.source_text)}</td><td class="target">${esc(item.target_text)}</td><td>${esc(item.term_type)}</td><td>${item.locked ? '锁定' : esc(item.status)}</td><td>${num(item.priority)}</td></tr>`;
      if (kind === 'tm') return `<tr data-k-index="${index}"><td class="source">${esc(item.source_text)}</td><td class="target">${esc(item.target_text)}</td><td>${esc(item.quality)}</td><td>${item.locked ? '是' : '否'}</td><td>${num(item.usage_count)}</td></tr>`;
      return `<tr data-k-index="${index}"><td class="source">${esc(item.source_alias)}</td><td class="target">${esc(item.canonical_zh)}</td><td>${esc(item.entity_type)}</td><td>${esc(item.status)}</td><td>${esc(item.provenance)}</td></tr>`;
    }).join('') : '<tr><td colspan="5"><div class="vnext-empty">没有匹配内容。</div></td></tr>';
    byId('vnKnowledgeRows').querySelectorAll('tr[data-k-index]').forEach(row => row.onclick = () => selectKnowledge(Number(row.dataset.kIndex)));
    configureKnowledgeEditor();
  }
  function configureKnowledgeEditor() {
    const kind = state.knowledgeKind;
    const panel = byId('vnKnowledgeEditorPanel');
    panel.classList.toggle('hidden', kind === 'reference');
    byId('vnKnowledgeEditorTitle').textContent = kind === 'tm' ? '新增 / 修改锁定 TM' : '新增 / 修改术语';
    byId('vnKType').parentElement.classList.toggle('hidden', kind === 'tm');
    byId('vnKScope').parentElement.classList.toggle('hidden', kind === 'tm');
    byId('vnKStatus').parentElement.classList.toggle('hidden', kind === 'tm');
    byId('vnKPriority').parentElement.classList.toggle('hidden', kind === 'tm');
    clearKnowledgeEditor();
  }
  function clearKnowledgeEditor() {
    state.knowledgeSelected = null;
    byId('vnKSource').value = '';
    byId('vnKTarget').value = '';
    byId('vnKType').value = 'general';
    byId('vnKScope').value = 'global';
    byId('vnKStatus').value = 'approved';
    byId('vnKPriority').value = '100';
    byId('vnKLocked').checked = true;
    byId('vnKDelete').disabled = true;
    byId('vnKnowledgeRows').querySelectorAll('tr').forEach(row => row.classList.remove('active'));
  }
  function selectKnowledge(index) {
    const item = state.knowledgeItems[index]; if (!item) return;
    state.knowledgeSelected = item;
    byId('vnKnowledgeRows').querySelectorAll('tr[data-k-index]').forEach(row => row.classList.toggle('active', Number(row.dataset.kIndex) === index));
    if (state.knowledgeKind === 'reference') return;
    byId('vnKSource').value = item.source_text || '';
    byId('vnKTarget').value = item.target_text || '';
    byId('vnKLocked').checked = Boolean(item.locked);
    if (state.knowledgeKind === 'glossary') {
      byId('vnKType').value = item.term_type || 'general';
      byId('vnKScope').value = item.scope || 'global';
      byId('vnKStatus').value = item.status || 'approved';
      byId('vnKPriority').value = item.priority ?? 100;
    }
    byId('vnKDelete').disabled = false;
  }
  shell.querySelectorAll('[data-vn-knowledge]').forEach(button => button.onclick = () => {
    state.knowledgeKind = button.dataset.vnKnowledge;
    shell.querySelectorAll('[data-vn-knowledge]').forEach(x => x.classList.toggle('active', x === button));
    loadKnowledge();
  });
  byId('vnKnowledgeRefresh').onclick = loadKnowledge;
  let knowledgeSearchTimer = null;
  byId('vnKnowledgeQuery').oninput = () => { clearTimeout(knowledgeSearchTimer); knowledgeSearchTimer = setTimeout(loadKnowledge, 300); };
  byId('vnKClear').onclick = clearKnowledgeEditor;
  byId('vnKSave').onclick = async () => {
    const source = byId('vnKSource').value.trim(), target = byId('vnKTarget').value.trim();
    if (!source || !target) return notify('原文和中文不能为空');
    let res;
    if (state.knowledgeKind === 'tm') res = await window.studio.vnextTmSave({ source, target, locked: byId('vnKLocked').checked });
    else res = await window.studio.vnextGlossarySave({
      source, target, termType: byId('vnKType').value.trim() || 'general', scope: byId('vnKScope').value.trim() || 'global',
      status: byId('vnKStatus').value, priority: Number(byId('vnKPriority').value) || 100, locked: byId('vnKLocked').checked,
    });
    if (!res?.ok) return notify(res?.error || '保存失败');
    await loadKnowledge();
    await refreshDashboard();
  };
  byId('vnKDelete').onclick = async () => {
    const item = state.knowledgeSelected; if (!item) return;
    if (!await confirmAction('删除所选知识条目？此操作不会删除项目中的当前译文。')) return;
    const res = state.knowledgeKind === 'tm'
      ? await window.studio.vnextTmDelete({ tmId: item.tm_id })
      : await window.studio.vnextGlossaryDelete({ termId: item.term_id });
    if (!res?.ok) return notify(res?.error || '删除失败');
    await loadKnowledge();
    await refreshDashboard();
  };

  function renderPakList(paks) {
    const el = byId('vnPakList');
    if (!paks.length) { el.innerHTML = '<div class="vnext-muted">当前项目没有 PAK。</div>'; return; }
    el.innerHTML = paks.map(item => `<label class="vnext-pak"><input type="checkbox" data-vn-pak="${esc(item.pak)}" ${state.selectedPaks.has(item.pak) ? 'checked' : ''}/><div><div class="name">${esc(item.pak)}</div><div class="path">${esc(item.path || '')}</div></div></label>`).join('');
    el.querySelectorAll('input[data-vn-pak]').forEach(input => input.onchange = () => input.checked ? state.selectedPaks.add(input.dataset.vnPak) : state.selectedPaks.delete(input.dataset.vnPak));
  }
  function buildOptions() {
    return { requireTranslated: byId('vnRequireTranslated').checked, failOnWarnings: byId('vnFailWarnings').checked };
  }
  function renderPreflight(report) {
    const el = byId('vnBuildResult');
    const blockers = report?.blockers || [], warnings = report?.warnings || [];
    el.className = `vnext-result ${report?.ok ? 'ok' : 'error'}`;
    const details = [];
    details.push(report?.ok ? '✓ 构建前 QA 通过' : `✗ 构建被阻止：${num(blockers.length)} 个 blocker`);
    details.push(`已翻译唯一文本：${num(report?.translated)} · 未翻译：${num(report?.untranslated)} · Warning：${num(warnings.length)}`);
    blockers.slice(0, 12).forEach((item, i) => details.push(`${i + 1}. ${item.code} · ${(item.source || '').slice(0, 100)}`));
    warnings.slice(0, 6).forEach((item, i) => details.push(`W${i + 1}. ${item.code} · ${(item.source || '').slice(0, 100)}`));
    el.textContent = details.join('\n');
  }
  async function syncBeforeBuild() {
    const ws = workspace(); if (!ws) throw new Error('请先打开或导入项目');
    const sync = await window.studio.vnextIngestWorkspace({ workspace: ws });
    if (!sync?.ok) throw new Error(sync?.error || '构建前工作区同步失败');
    state.syncedWorkspace = ws;
  }
  byId('vnPreflight').onclick = async () => {
    const ws = workspace(); if (!ws) return notify('请先打开或导入项目');
    const btn = byId('vnPreflight'); btn.disabled = true;
    try {
      await syncBeforeBuild();
      const res = await window.studio.vnextBuildPreflight({ workspace: ws, ...buildOptions() });
      renderPreflight(res?.report || { ok: false, blockers: [{ code: res?.error || 'PREFLIGHT_FAILED' }] });
    } catch (error) { byId('vnBuildResult').className = 'vnext-result error'; byId('vnBuildResult').textContent = error.message || String(error); }
    finally { btn.disabled = false; }
  };
  byId('vnBuild').onclick = async () => {
    const ws = workspace(); if (!ws) return notify('请先打开或导入项目');
    const paks = [...state.selectedPaks];
    if (!paks.length) return notify('至少选择一个 PAK');
    if (!await confirmAction(`将从原始 PAK 重建并完整验证：\n${paks.join('\n')}\n\n只有全部通过才会发布。继续吗？`)) return;
    const btn = byId('vnBuild'); btn.disabled = true; btn.textContent = '正在验证构建…';
    byId('vnBuildResult').className = 'vnext-result'; byId('vnBuildResult').textContent = '正在同步工作区并运行最终 Build Gate…';
    try {
      await syncBeforeBuild();
      const preflight = await window.studio.vnextBuildPreflight({ workspace: ws, ...buildOptions() });
      renderPreflight(preflight?.report || { ok: false, blockers: [{ code: preflight?.error || 'PREFLIGHT_FAILED' }] });
      if (!preflight?.ok) return;
      const res = await window.studio.vnextBuildPaks({ workspace: ws, paks, workers: 1, ...buildOptions() });
      if (!res?.ok) {
        byId('vnBuildResult').className = 'vnext-result error';
        byId('vnBuildResult').textContent = res?.error || '构建失败';
        return;
      }
      byId('vnBuildResult').className = 'vnext-result ok';
      byId('vnBuildResult').textContent = `✓ 已通过验证并发布\n输出：${res.output_dir || res.output || 'workspace/build-vnext'}\nBuild ID：${res.build_id || ''}`;
      await loadBuildHistory();
      await refreshDashboard();
    } catch (error) {
      byId('vnBuildResult').className = 'vnext-result error';
      byId('vnBuildResult').textContent = error.message || String(error);
    } finally { btn.disabled = false; btn.textContent = '验证并构建 PAK'; }
  };
  async function loadBuildHistory() {
    const ws = workspace(); if (!ws) return;
    const res = await window.studio.vnextBuildHistory({ workspace: ws, limit: 30 });
    if (!res?.ok) return;
    const items = res.items || [];
    byId('vnBuildHistory').innerHTML = items.length ? items.map(item => {
      let verification = '';
      try { const v = JSON.parse(item.verification_json || '{}'); verification = v.ok === false ? '失败' : v.ok === true ? '通过' : (v.status || ''); } catch {}
      return `<tr><td>${esc(item.created_at)}</td><td>${esc(item.status)}</td><td class="mono">${esc(item.build_id)}</td><td>${esc(verification)}</td></tr>`;
    }).join('') : '<tr><td colspan="4"><div class="vnext-empty">暂无构建历史。</div></td></tr>';
  }
  byId('vnBuildHistoryRefresh').onclick = loadBuildHistory;

  if (typeof window.studio.onProgress === 'function') {
    window.studio.onProgress(data => {
      if (!data) return;
      if (data.phase === 'vnext-translate') {
        const completed = Number(data.completed ?? data.completed_unique ?? data.completed_rows ?? 0);
        const total = Number(data.total ?? data.total_unique ?? data.total_rows ?? 0);
        const value = Number.isFinite(Number(data.percent)) ? Number(data.percent) : total ? completed / total * 100 : 0;
        byId('vnTranslateProgress').value = Math.max(0, Math.min(100, value));
        byId('vnTranslateMessage').textContent = data.message || `${num(completed)} / ${num(total)}`;
        if (data.message) appendTranslateLog(data.message);
        if (data.stopping) { state.stopping = true; setTranslateButton(); }
      } else if (data.phase === 'vnext-build') {
        byId('vnBuildResult').textContent = data.message || '正在构建…';
      } else if (data.phase === 'vnext-sync' && state.page === 'overview') {
        setBanner(data.message || '正在同步工作区…', data.percent >= 100 ? 'ok' : '');
      }
    });
  }

  let lastSeenWorkspace = '';
  setInterval(() => {
    const ws = workspace();
    if (ws !== lastSeenWorkspace) {
      lastSeenWorkspace = ws;
      state.workspace = ws;
      state.syncedWorkspace = '';
      state.selectedPaks.clear();
      if (state.page !== 'legacy') refreshDashboard({ autoIngest: true });
    }
  }, 900);

  setTranslateButton();
  refreshDashboard({ autoIngest: true });
}());
