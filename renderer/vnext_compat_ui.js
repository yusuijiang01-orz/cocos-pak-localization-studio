(function () {
  'use strict';

  if (!window.studio || typeof window.studio.vnextCompatStatus !== 'function') return;

  const byId = id => document.getElementById(id);
  const legacyMain = byId('legacyMain') || document.querySelector('.app > main');
  const nav = byId('vnextNav');
  if (!legacyMain || !nav) return;

  function currentWorkspace() {
    try { return String((typeof project !== 'undefined' && project?.workspace) || ''); } catch { return ''; }
  }
  function notify(message, options) {
    try {
      if (typeof showAppDialog === 'function') return showAppDialog(String(message), options || {});
    } catch {}
    window.alert(String(message));
    return Promise.resolve(true);
  }
  async function confirmCompat(message) {
    try {
      if (typeof appConfirm === 'function') return await appConfirm(String(message), { title: '旧版兼容工具', confirmText: '仍然继续' });
    } catch {}
    return window.confirm(String(message));
  }

  const badge = document.createElement('span');
  badge.id = 'vnCompatBadge';
  badge.className = 'vn-compat-badge unknown';
  badge.textContent = '兼容状态：未检查';
  const spacer = nav.querySelector('.vnext-spacer');
  if (spacer) nav.insertBefore(badge, spacer);
  else nav.appendChild(badge);

  const bar = document.createElement('section');
  bar.id = 'vnLegacyCompatBar';
  bar.className = 'vn-legacy-compat hidden';
  bar.innerHTML = `
    <div class="vn-compat-copy">
      <strong>旧版工具兼容区</strong>
      <span>导出类工具可继续使用；旧版翻译/导入会修改 text_records.json，并使 vNext 构建基线失效。旧版直接构建不会经过 vNext QA / TM / Build Gate。</span>
    </div>
    <div class="vn-compat-actions">
      <button id="vnCompatSync" class="secondary">仅同步源索引</button>
      <button id="vnCompatAdopt">吸收旧版当前译文</button>
    </div>`;
  legacyMain.insertAdjacentElement('beforebegin', bar);

  const readonlyIds = new Set([
    'exportXlsx', 'exportFullXlsx', 'exportGlossaryXlsx', 'exportUntranslated',
    'exportCsv', 'exportTsvCsv', 'mergeTsvCsv', 'splitMergedTsvCsv'
  ]);
  const mutationIds = new Set([
    'importXlsxPolish', 'importFullXlsx', 'importUntranslated', 'safePcMerge',
    'importCsv', 'importTsvCsv', 'glossaryTranslate', 'ollamaTranslate',
    'modelTranslateTsvCsv', 'apiTranslateMergedCsv', 'translateTsvCsv',
    'translationText', 'restoreOriginal'
  ]);
  const guardedIds = new Set([
    'glossaryTranslate', 'ollamaTranslate', 'modelTranslateTsvCsv',
    'apiTranslateMergedCsv', 'translateTsvCsv', 'buildPak', 'autoModelBuild'
  ]);
  const buildIds = new Set(['buildPak', 'autoModelBuild']);

  const labelMap = {
    glossaryTranslate: '旧版术语替换（不推荐）',
    ollamaTranslate: '旧版按文件 Ollama',
    buildPak: '旧版直接构建 PAK',
    autoModelBuild: '旧版 API 翻译并直构',
  };
  for (const [id, label] of Object.entries(labelMap)) {
    const el = byId(id);
    if (el) el.textContent = label;
  }
  for (const id of readonlyIds) byId(id)?.classList.add('vn-legacy-readonly');
  for (const id of mutationIds) byId(id)?.classList.add('vn-legacy-mutating');
  for (const id of buildIds) byId(id)?.classList.add('vn-legacy-build');

  let statusTimer = null;
  let assumedDirty = false;
  function setBadge(state, message) {
    badge.className = `vn-compat-badge ${state || 'unknown'}`;
    badge.textContent = message;
  }
  async function refreshStatus() {
    clearTimeout(statusTimer);
    const workspace = currentWorkspace();
    if (!workspace) {
      setBadge('unknown', '兼容状态：未加载项目');
      return null;
    }
    const res = await window.studio.vnextCompatStatus({ workspace });
    if (!res?.ok) {
      setBadge('bad', '兼容状态：检查失败');
      return res;
    }
    const report = res.report || {};
    assumedDirty = !report.synced;
    if (report.synced) setBadge('good', 'vNext 基线已同步');
    else if (report.state === 'never_synced') setBadge('warn', 'vNext 尚未同步');
    else setBadge('bad', '工作区已变化 · 构建已锁定');
    badge.title = report.message || '';
    return res;
  }
  function markDirty(reason) {
    assumedDirty = true;
    setBadge('warn', '旧版修改进行中 · vNext 将需重同步');
    badge.title = reason || '旧版工具可能修改 text_records.json';
    clearTimeout(statusTimer);
    statusTimer = setTimeout(refreshStatus, 1800);
  }

  byId('vnCompatSync').onclick = async () => {
    const workspace = currentWorkspace();
    if (!workspace) return notify('请先打开或导入项目');
    if (!await confirmCompat('“仅同步源索引”不会把旧版当前中文导入 vNext。若你想保留旧版已经翻译的中文，请使用“吸收旧版当前译文”。继续只同步原文索引吗？')) return;
    const button = byId('vnCompatSync');
    button.disabled = true;
    try {
      const res = await window.studio.vnextIngestWorkspace({ workspace });
      if (!res?.ok) return notify(res?.error || '同步失败');
      await refreshStatus();
      notify(`同步完成：${Number(res.report?.active_unique_units || 0).toLocaleString()} 个唯一文本。`, { title: 'vNext 已同步', tone: 'success' });
    } finally { button.disabled = false; }
  };

  byId('vnCompatAdopt').onclick = async () => {
    const workspace = currentWorkspace();
    if (!workspace) return notify('请先打开或导入项目');
    const ok = await confirmCompat(
      '将扫描当前旧版 text_records.json，把结构和 QA 均安全的旧版中文吸收到当前 vNext 项目。\n\n' +
      '这些译文只会标记为 legacy_candidate，不会自动写入全局 TM，也不会覆盖已存在的 vNext 译文。人工审核通过后才会晋升为可信 TM。'
    );
    if (!ok) return;
    const button = byId('vnCompatAdopt');
    button.disabled = true;
    try {
      const res = await window.studio.vnextAdoptLegacy({ workspace, overwrite: false });
      if (!res?.ok) return notify(res?.error || '吸收旧版译文失败');
      const s = res.stats || {};
      await refreshStatus();
      notify(
        `旧版译文吸收完成：\n` +
        `安全采用：${Number(s.adopted || 0).toLocaleString()}\n` +
        `保留已有 vNext：${Number(s.kept_existing || 0).toLocaleString()}\n` +
        `结构不匹配：${Number(s.structure_mismatch || 0).toLocaleString()}\n` +
        `QA 不安全：${Number(s.unsafe || 0).toLocaleString()}\n` +
        `同一原文冲突：${Number(s.conflict || 0).toLocaleString()}\n\n` +
        `${res.note || ''}`,
        { title: '旧版兼容迁移完成', tone: 'success' }
      );
    } finally { button.disabled = false; }
  };

  document.addEventListener('click', async event => {
    const target = event.target?.closest?.('button');
    if (!target || !guardedIds.has(target.id)) return;
    if (target.dataset.vnCompatAllow === '1') {
      delete target.dataset.vnCompatAllow;
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    const directBuild = buildIds.has(target.id);
    const message = directBuild
      ? '这是旧版直接构建流程，会绕过 vNext 的最终 QA、工作区指纹门禁、候选 PAK 重解包验证和统一构建历史。\n\n推荐返回“构建”页使用“验证并构建 PAK”。确实仍要运行旧版流程吗？'
      : '这是旧版翻译流程，不会自动使用 vNext 的统一 TM / Reference / 术语约束 / QA 流水线。运行后工作区会被视为已变化，之后必须同步或吸收旧版译文才能使用 vNext 构建。\n\n仍然继续吗？';
    if (!await confirmCompat(message)) return;
    target.dataset.vnCompatAllow = '1';
    markDirty(`运行旧版工具：${target.textContent || target.id}`);
    target.click();
  }, true);

  document.addEventListener('click', event => {
    const target = event.target?.closest?.('button');
    if (!target || guardedIds.has(target.id)) return;
    if (mutationIds.has(target.id)) markDirty(`运行旧版修改工具：${target.textContent || target.id}`);
  }, true);

  const observer = new MutationObserver(() => {
    const visible = !legacyMain.classList.contains('vnext-legacy-hidden');
    bar.classList.toggle('hidden', !visible);
    if (visible) refreshStatus();
  });
  observer.observe(legacyMain, { attributes: true, attributeFilter: ['class'] });

  nav.addEventListener('click', event => {
    const page = event.target?.closest?.('[data-vn-page]')?.dataset?.vnPage;
    if (page === 'legacy') refreshStatus();
  });

  badge.onclick = refreshStatus;
  refreshStatus();
}());
