const { contextBridge, ipcRenderer } = require('electron');

async function guardedVnextBuild(channel, payload) {
  const status = await ipcRenderer.invoke('vnext-compat-status', payload);
  if (!status?.ok) return status;
  if (!status?.report?.synced) {
    return {
      ok: false,
      error: status?.report?.message || '旧版工作区在上次 vNext 同步后已经变化；请先同步或吸收旧版译文。',
      compatibility: status.report,
    };
  }
  return ipcRenderer.invoke(channel, payload);
}

contextBridge.exposeInMainWorld('studio', {
  choosePaks: () => ipcRenderer.invoke('choose-paks'),
  chooseWorkspace: () => ipcRenderer.invoke('choose-workspace'),
  importPaks: (payload) => ipcRenderer.invoke('import-paks', payload),
  loadProject: () => ipcRenderer.invoke('load-project'),
  saveCsv: (csvText, defaultName) => ipcRenderer.invoke('save-csv', { csvText, defaultName }),
  loadCsv: () => ipcRenderer.invoke('load-csv'),
  exportTsvCsv: (payload) => ipcRenderer.invoke('export-tsv-csv', payload),
  exportXlsx: (payload) => ipcRenderer.invoke('export-xlsx', payload),
  importXlsxPolish: (payload) => ipcRenderer.invoke('import-xlsx-polish', payload),
  exportFullXlsx: (payload) => ipcRenderer.invoke('export-full-xlsx', payload),
  exportGlossaryXlsx: (payload) => ipcRenderer.invoke('export-glossary-xlsx', payload),
  glossaryTranslate: (payload) => ipcRenderer.invoke('glossary-translate-v2', payload),
  glossaryTranslateCancel: () => ipcRenderer.invoke('glossary-translate-cancel'),
  importFullXlsx: (payload) => ipcRenderer.invoke('import-full-xlsx', payload),
  exportUntranslatedXlsx: (payload) => ipcRenderer.invoke('export-untranslated-xlsx', payload),
  ollamaTranslate: (payload) => ipcRenderer.invoke('ollama-translate', payload),
  ollamaControl: (payload) => ipcRenderer.invoke('ollama-control', payload),
  importUntranslatedXlsx: (payload) => ipcRenderer.invoke('import-untranslated-xlsx', payload),
  safePcMerge: (payload) => ipcRenderer.invoke('safe-pc-merge', payload),
  apiReview: (payload) => ipcRenderer.invoke('api-review', payload),
  apiReviewStatus: (payload) => ipcRenderer.invoke('api-review-status', payload),
  mergeTsvCsv: (payload) => ipcRenderer.invoke('merge-tsv-csv', payload),
  splitMergedTsvCsv: (payload) => ipcRenderer.invoke('split-merged-tsv-csv', payload),
  apiTranslatorConfig: () => ipcRenderer.invoke('api-translator-config'),
  saveApiTranslatorConfig: (payload) => ipcRenderer.invoke('save-api-translator-config', payload),
  fetchApiModels: (payload) => ipcRenderer.invoke('fetch-api-models', payload),
  apiTranslateMergedCsv: (payload) => ipcRenderer.invoke('api-translate-merged-csv', payload),
  cancelCurrentTask: () => ipcRenderer.invoke('cancel-current-task'),
  translateTsvCsv: (payload) => ipcRenderer.invoke('translate-tsv-csv', payload),
  modelTranslateTsvCsv: (payload) => ipcRenderer.invoke('model-translate-tsv-csv', payload),
  importTsvCsv: (payload) => ipcRenderer.invoke('import-tsv-csv', payload),
  buildPak: (payload) => ipcRenderer.invoke('build-pak', payload),
  buildPaks: (payload) => ipcRenderer.invoke('build-paks', payload),
  pushGithubPak: (payload) => ipcRenderer.invoke('push-github-pak', payload),
  persistRecords: (payload) => ipcRenderer.invoke('persist-records', payload),
  persistRecordEdit: (payload) => ipcRenderer.invoke('persist-record-edit', payload),
  syncRecords: (payload) => ipcRenderer.invoke('sync-records', payload),
  batchTranslate: (payload) => ipcRenderer.invoke('batch-translate', payload),
  learnRecord: (payload) => ipcRenderer.invoke('learn-record', payload),
  learnModified: (payload) => ipcRenderer.invoke('learn-modified', payload),
  queuePrepare: (payload) => ipcRenderer.invoke('queue-prepare', payload),
  queueStats: (payload) => ipcRenderer.invoke('queue-stats', payload),
  queueList: (payload) => ipcRenderer.invoke('queue-list', payload),
  queueApply: (payload) => ipcRenderer.invoke('queue-apply', payload),
  tmStats: () => ipcRenderer.invoke('tm-stats'),
  modelStatus: () => ipcRenderer.invoke('model-status'),
  modelTranslate: (payload) => ipcRenderer.invoke('model-translate', payload),

  vnextDashboard: (payload) => ipcRenderer.invoke('vnext-dashboard', payload),
  vnextIngestWorkspace: (payload) => ipcRenderer.invoke('vnext-ingest-workspace', payload),
  vnextTranslate: (payload) => ipcRenderer.invoke('vnext-translate', payload),
  vnextStopTranslate: (payload) => ipcRenderer.invoke('vnext-stop-translate', payload),
  vnextJobStatus: (payload) => ipcRenderer.invoke('vnext-job-status', payload),
  vnextReviewList: (payload) => ipcRenderer.invoke('vnext-review-list', payload),
  vnextReviewShow: (payload) => ipcRenderer.invoke('vnext-review-show', payload),
  vnextReviewApprove: (payload) => ipcRenderer.invoke('vnext-review-approve', payload),
  vnextReviewReject: (payload) => ipcRenderer.invoke('vnext-review-reject', payload),
  vnextReviewState: (payload) => ipcRenderer.invoke('vnext-review-state', payload),
  vnextReviewStats: (payload) => ipcRenderer.invoke('vnext-review-stats', payload),
  vnextKnowledgeList: (payload) => ipcRenderer.invoke('vnext-knowledge-list', payload),
  vnextGlossarySave: (payload) => ipcRenderer.invoke('vnext-glossary-save', payload),
  vnextGlossaryDelete: (payload) => ipcRenderer.invoke('vnext-glossary-delete', payload),
  vnextTmSave: (payload) => ipcRenderer.invoke('vnext-tm-save', payload),
  vnextTmDelete: (payload) => ipcRenderer.invoke('vnext-tm-delete', payload),
  vnextCompatStatus: (payload) => ipcRenderer.invoke('vnext-compat-status', payload),
  vnextAdoptLegacy: (payload) => ipcRenderer.invoke('vnext-adopt-legacy', payload),
  vnextBuildPreflight: (payload) => guardedVnextBuild('vnext-build-preflight', payload),
  vnextBuildPaks: (payload) => guardedVnextBuild('vnext-build-paks', payload),
  vnextBuildHistory: (payload) => ipcRenderer.invoke('vnext-build-history', payload),

  onProgress: (cb) => ipcRenderer.on('backend-progress', (_e, data) => cb(data))
});
