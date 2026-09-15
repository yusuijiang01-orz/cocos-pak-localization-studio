(function () {
  const button = document.getElementById('glossaryTranslate');
  if (!button) return;

  if (typeof setWorkflowDisabled === 'function') {
    const originalSetWorkflowDisabled = setWorkflowDisabled;
    setWorkflowDisabled = function (disabled) {
      originalSetWorkflowDisabled(disabled);
      const glossaryButton = document.getElementById('glossaryTranslate');
      if (glossaryButton) glossaryButton.disabled = !!disabled;
    };
  }

  function progressMessage(message) {
    const progressEl = document.getElementById('progress');
    if (progressEl) progressEl.textContent = message;
  }

  function progressValue(value, message) {
    if (typeof setTranslationProgress === 'function') {
      setTranslationProgress(value, message);
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

  button.onclick = async () => {
    if (typeof project === 'undefined' || !project) {
      alert('请先打开或导入项目');
      return;
    }
    if (!window.studio || typeof window.studio.glossaryTranslate !== 'function') {
      alert('当前版本的 preload 未暴露术语库翻译接口，请重新启动 Studio。');
      return;
    }

    try {
      if (typeof setWorkflowDisabled === 'function') setWorkflowDisabled(true);
      else button.disabled = true;
      if (typeof startTranslationClock === 'function') startTranslationClock();
      progressValue(1, '请选择要应用术语库翻译的导出 XLSX，然后选择已翻译的术语库 XLSX…');

      const res = await window.studio.glossaryTranslate({ project, pak: selectedPak() });
      if (res?.canceled) {
        progressValue(0, '已取消术语库翻译');
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
        if (typeof render === 'function') render();
      }

      const report = res.report || {};
      const imported = res.importReport || res.import || {};
      const sync = res.resourceSync?.report || res.sync || {};
      const message = `术语库翻译完成：改写 ${report.translated_rows || 0} 行，命中术语 ${report.matched_terms || 0} 次，导入变化 ${imported.changed || 0} 条，生成资源 ${sync.changed_file_count || 0} 个。现在可以直接构建 PAK。`;
      progressValue(100, message);
      alert(`${message}\n\n生成的可导入 XLSX：\n${res.translatedXlsx || ''}`);
    } finally {
      if (typeof stopTranslationClock === 'function') stopTranslationClock();
      if (typeof setWorkflowDisabled === 'function') setWorkflowDisabled(false);
      else button.disabled = false;
    }
  };
}());
