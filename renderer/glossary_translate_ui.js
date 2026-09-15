(function () {
  const button = document.getElementById('glossaryTranslate');
  if (!button) return;

  let running = false;
  let stopping = false;
  const normalLabel = '术语库翻译';

  function updateButtonState() {
    if (!running) {
      button.textContent = normalLabel;
      button.disabled = false;
      button.classList.remove('danger');
      return;
    }
    button.classList.add('danger');
    if (stopping) {
      button.textContent = '正在停止并保存…';
      button.disabled = true;
    } else {
      button.textContent = '停止术语库翻译';
      button.disabled = false;
    }
  }

  if (typeof setWorkflowDisabled === 'function') {
    const originalSetWorkflowDisabled = setWorkflowDisabled;
    setWorkflowDisabled = function (disabled) {
      originalSetWorkflowDisabled(disabled);
      if (running) updateButtonState();
      else button.disabled = !!disabled;
    };
  }

  function progressMessage(message) {
    const progressEl = document.getElementById('progress');
    if (progressEl) progressEl.textContent = message;
  }

  function progressValue(value, message, samples, metrics) {
    if (typeof setTranslationProgress === 'function') {
      setTranslationProgress(value, message, samples, metrics);
      return;
    }
    const progressBar = document.getElementById('translationProgress');
    if (progressBar) {
      progressBar.classList.remove('hidden');
      progressBar.value = value;
    }
    progressMessage(message);
  }

  function selectedPak() {
    if (typeof filters !== 'undefined' && filters && filters.pak && filters.pak !== 'all') return filters.pak;
    if (typeof selected !== 'undefined' && selected && selected.pak) return selected.pak;
    return null;
  }

  if (window.studio && typeof window.studio.onProgress === 'function') {
    window.studio.onProgress(d => {
      if (!d || d.phase !== 'glossary-translate') return;
      if (Array.isArray(d.updates) && typeof applyLiveTranslationUpdates === 'function') {
        applyLiveTranslationUpdates(d.updates);
      }
      if (d.stopping) {
        stopping = true;
        updateButtonState();
      }
      progressValue(
        Number(d.percent || 0),
        d.message || '正在应用术语库…',
        d.samples,
        { completed_rows: d.completed_rows, total_rows: d.total_rows }
      );
    });
  }

  button.onclick = async () => {
    if (running) {
      if (stopping) return;
      stopping = true;
      updateButtonState();
      progressMessage('正在停止术语库翻译并保存断点；已完成内容不会丢失…');
      try {
        const stopped = await window.studio.glossaryTranslateCancel();
        if (!stopped?.ok) {
          stopping = false;
          updateButtonState();
          alert(stopped?.error || '停止术语库翻译失败');
        }
      } catch (e) {
        stopping = false;
        updateButtonState();
        alert(e?.message || String(e));
      }
      return;
    }

    if (typeof project === 'undefined' || !project) {
      alert('请先打开或导入项目');
      return;
    }
    if (!window.studio || typeof window.studio.glossaryTranslate !== 'function') {
      alert('当前版本的 preload 未暴露术语库翻译接口，请重新启动 Studio。');
      return;
    }

    running = true;
    stopping = false;
    updateButtonState();
    try {
      if (typeof setWorkflowDisabled === 'function') setWorkflowDisabled(true);
      if (typeof startTranslationClock === 'function') startTranslationClock();
      progressValue(1, '请选择要应用术语库翻译的导出 XLSX，然后选择已翻译的术语库 XLSX…');

      const res = await window.studio.glossaryTranslate({ project, pak: selectedPak() });
      if (res?.canceled) {
        if (typeof refreshRecordsFromResult === 'function') refreshRecordsFromResult(res);
        const done = Number(res.processedRows ?? res.report?.processed_rows ?? 0);
        const total = Number(res.totalRows ?? res.report?.workbook_rows ?? 0);
        if (res.saved && res.resumable) {
          const msg = `术语库翻译已停止。已完成译文已经写回项目，断点保存为 ${done.toLocaleString()}/${total.toLocaleString()}。下次选择同一个导出 XLSX 和同一个术语库 XLSX，会从该断点继续，不会从 0 开始。`;
          progressValue(total ? done / total * 100 : 0, msg, null, { completed_rows: done, total_rows: total });
          alert(msg, { title: '已停止并保存断点', tone: 'success' });
        } else {
          progressValue(0, '已取消术语库翻译');
        }
        return;
      }
      if (!res?.ok) {
        progressValue(0, `术语库翻译失败：${res?.error || '未知错误'}`);
        alert(res?.error || '术语库翻译失败');
        return;
      }

      if (typeof refreshRecordsFromResult === 'function') refreshRecordsFromResult(res);
      else if (res.records && typeof records !== 'undefined') {
        records = res.records;
        if (typeof rebuildRecordIndexes === 'function') rebuildRecordIndexes();
        if (typeof renderFilters === 'function') renderFilters();
        if (typeof renderRows === 'function') renderRows();
      }

      const report = res.report || {};
      const imported = res.importReport || res.import || {};
      const sync = res.resourceSync?.report || res.sync || {};
      const partial = Number(report.partial_rows || 0);
      const resumed = Number(report.resumed_from || 0);
      const resumeText = resumed ? `，本次从 ${resumed.toLocaleString()} 行断点继续` : '';
      const message = `术语库翻译完成${resumeText}：完整中文 ${report.translated_rows || 0} 行，部分命中未写入 ${partial} 行，命中术语 ${report.matched_terms || 0} 次，导入变化 ${imported.changed || 0} 条，生成资源 ${sync.changed_file_count || 0} 个。现在可以直接构建 PAK。`;
      progressValue(100, message);
      alert(`${message}\n\n只有整段替换后不再残留越南文/拉丁字符的内容才会写回，避免中越混合译文进入 PAK。\n\n生成的可导入 XLSX：\n${res.translatedXlsx || ''}`);
    } finally {
      if (typeof stopTranslationClock === 'function') stopTranslationClock();
      running = false;
      stopping = false;
      if (typeof setWorkflowDisabled === 'function') setWorkflowDisabled(false);
      updateButtonState();
    }
  };
}());

// Phase 5 is loaded after the legacy renderer so the old tools stay intact.
(function loadVNextPhase5() {
  if (document.getElementById('vnextPhase5Script')) return;
  if (!document.querySelector('link[href="vnext.css"]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'vnext.css';
    document.head.appendChild(link);
  }
  const script = document.createElement('script');
  script.id = 'vnextPhase5Script';
  script.src = 'vnext_ui.js';
  document.body.appendChild(script);
}());
