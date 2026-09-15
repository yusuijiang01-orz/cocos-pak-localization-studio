// Bootstrap the existing Studio and legacy glossary handler, then register the
// resumable v2 channel used by the current preload/UI.
require('./main_with_glossary');

const { BrowserWindow, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');

let activeRun = null;

function currentWindow() {
  return BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0] || null;
}

function sendProgress(obj) {
  const win = currentWindow();
  if (win && !win.isDestroyed()) win.webContents.send('backend-progress', obj);
}

function pythonCommand() {
  const root = path.join(__dirname, '..');
  const isolated = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python');
  const parent = process.platform === 'win32'
    ? path.join(root, '..', '.venv', 'Scripts', 'python.exe')
    : path.join(root, '..', '.venv', 'bin', 'python');
  return fs.existsSync(isolated) ? isolated : fs.existsSync(parent) ? parent : (process.platform === 'win32' ? 'python' : 'python3');
}

function pythonSpawnOptions() {
  const usableCpus = Math.max(1, (os.cpus()?.length || 1) - 4);
  return {
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
      OMP_NUM_THREADS: String(usableCpus),
      MKL_NUM_THREADS: String(usableCpus),
      OPENBLAS_NUM_THREADS: String(usableCpus),
      NUMEXPR_NUM_THREADS: String(usableCpus),
      VECLIB_MAXIMUM_THREADS: String(usableCpus),
    },
  };
}

function runPython(scriptPath, args, options = {}) {
  return new Promise((resolve) => {
    const proc = spawn(pythonCommand(), [scriptPath, ...args], pythonSpawnOptions());
    const run = options.run || activeRun;
    if (run) run.processes.add(proc);
    let stderr = '';
    let done = null;
    let buf = '';
    proc.stdout.setEncoding('utf8');
    proc.stderr.setEncoding('utf8');
    proc.stdout.on('data', chunk => {
      buf += chunk;
      const lines = buf.split(/\r?\n/);
      buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.event === 'progress' && !options.suppressProgress) sendProgress(obj);
          else if (obj.event === 'done') done = obj;
          else if (obj.event === 'error') { done = obj; sendProgress(obj); }
        } catch {
          sendProgress({ event: 'progress', phase: 'glossary-translate', message: line });
        }
      }
    });
    proc.stderr.on('data', x => { stderr += x; });
    proc.on('error', error => {
      if (run) run.processes.delete(proc);
      resolve({ ok: false, error: error.message || String(error) });
    });
    proc.on('close', code => {
      if (run) run.processes.delete(proc);
      if (proc.__cancelled) resolve({ ok: false, canceled: true, error: '任务已停止' });
      else if (code !== 0 || !done || done.event === 'error') resolve({ ok: false, error: done?.message || stderr || `Backend exited ${code}`, trace: done?.trace });
      else resolve({ ok: true, ...done });
    });
  });
}

function runCli(args, options = {}) {
  return runPython(path.join(__dirname, '..', 'backend', 'studio_cli.py'), args, options);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, ''));
}

function readRecords(workspace) {
  return readJson(path.join(workspace, 'localization', 'text_records.json'));
}

function pakItem(project, pakName) {
  return (project.paks || []).find(x => x.pak === pakName) || (project.paks || [])[0];
}

function recordsOutputDir(project, pak) {
  const base = pak.replace(/\.pak$/i, '');
  const modified = path.join(project.workspace, 'modified', base);
  const legacy = path.join(project.workspace, 'tsv_after', base);
  if (fs.existsSync(modified)) return modified;
  if (fs.existsSync(legacy)) return legacy;
  return modified;
}

async function syncRecordsToResources(project, pak, run) {
  const item = pakItem(project, pak);
  if (!item?.extracted) throw new Error(`没有找到 ${pak} 的解包目录`);
  const recordsPath = path.join(project.workspace, 'localization', 'text_records.json');
  const outputDir = recordsOutputDir(project, pak);
  const result = await runCli(['materialize-records', item.extracted, recordsPath, pak, outputDir], { suppressProgress: true, run });
  if (!result.ok) throw new Error(result.error || '本地化文本同步失败');
  return { outputDir, report: result.report };
}

async function syncRecordsWithAutoReject(project, pak, run) {
  const recordsPath = path.join(project.workspace, 'localization', 'text_records.json');
  let sync = await syncRecordsToResources(project, pak, run);
  let autoRejected = 0;
  const repairBackups = [];
  for (let round = 0; round < 10 && Number(sync?.report?.skipped_count || 0) > 0; round++) {
    const reportPath = path.join(sync.outputDir, '_records_materialize_report.json');
    const repaired = await runCli(['restore-materialize-rejections', recordsPath, reportPath], { suppressProgress: true, run });
    if (!repaired.ok) throw new Error(repaired.error || '自动恢复结构错误译文失败');
    const count = Number(repaired.report?.restored || 0);
    if (!count) break;
    autoRejected += count;
    if (repaired.report?.backup) repairBackups.push(repaired.report.backup);
    sync = await syncRecordsToResources(project, pak, run);
  }
  return { ...sync, autoRejected, repairBackups };
}

function multiPakXlsxRoot(workspace) {
  return path.join(workspace, 'xlsx_export', 'all_paks_player_visible');
}

function workbookStemCandidates(xlsxPath) {
  const stem = path.basename(xlsxPath, '.xlsx');
  const stems = [stem];
  let stripped = stem;
  for (const suffix of [/_term_translated$/i, /_translated$/i, /_google(?:_translated)?$/i, /\s*-\s*translated$/i, /\s*\(\d+\)$/]) {
    stripped = stripped.replace(suffix, '');
    if (stripped && !stems.includes(stripped)) stems.push(stripped);
  }
  return stems;
}

function candidateConfigDirs(project, xlsxPath, pak) {
  const dirs = [
    path.join(path.dirname(xlsxPath), 'config'),
    path.join(path.dirname(path.dirname(xlsxPath)), 'config'),
    path.dirname(xlsxPath),
  ];
  if (project?.workspace) {
    dirs.push(path.join(multiPakXlsxRoot(project.workspace), 'config'));
    const base = (pak || '').replace(/\.pak$/i, '');
    if (base) dirs.push(path.join(project.workspace, 'xlsx_export', `${base}_player_visible`, 'config'));
  }
  return [...new Set(dirs.filter(Boolean))];
}

function findMappingForXlsx(project, xlsxPath, pak, preferredMode = '') {
  const names = workbookStemCandidates(xlsxPath).map(stem => `${stem}_mapping.json`);
  const dirs = candidateConfigDirs(project, xlsxPath, pak);
  for (const dir of dirs) {
    for (const name of names) {
      const candidate = path.join(dir, name);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  if (preferredMode) {
    for (const dir of dirs) {
      if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) continue;
      for (const name of fs.readdirSync(dir).filter(n => /_mapping\.json$/i.test(n))) {
        const candidate = path.join(dir, name);
        try { if (readJson(candidate).mode === preferredMode) return candidate; } catch {}
      }
    }
  }
  return '';
}

function copyMappingForOutput(sourceMapping, outputXlsx, outputConfigDir) {
  fs.mkdirSync(outputConfigDir, { recursive: true });
  const target = path.join(outputConfigDir, `${path.basename(outputXlsx, '.xlsx')}_mapping.json`);
  if (!fs.existsSync(target) || !fs.readFileSync(target).equals(fs.readFileSync(sourceMapping))) fs.copyFileSync(sourceMapping, target);
  return target;
}

function fileIdentity(filePath) {
  const st = fs.statSync(filePath);
  return `${path.resolve(filePath)}\0${st.size}\0${Math.round(st.mtimeMs)}`;
}

function sessionKey(paths) {
  const hash = crypto.createHash('sha256');
  for (const p of paths.filter(Boolean)) hash.update(fileIdentity(p)).update('\0');
  return hash.digest('hex').slice(0, 24);
}

function uniqueStamp() {
  return new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function writeControl(controlPath, stop) {
  fs.mkdirSync(path.dirname(controlPath), { recursive: true });
  fs.writeFileSync(controlPath, JSON.stringify({ stop: !!stop, at: new Date().toISOString() }), 'utf8');
}

ipcMain.handle('glossary-translate-cancel', async () => {
  if (!activeRun) return { ok: true, count: 0, message: '当前没有术语库翻译任务' };
  activeRun.cancelRequested = true;
  try { writeControl(activeRun.controlPath, true); } catch {}
  sendProgress({
    event: 'progress', phase: 'glossary-translate',
    percent: activeRun.lastPercent || 0,
    message: '正在停止术语库翻译并保存断点；已完成内容不会丢失…',
    stopping: true,
  });
  return { ok: true, count: 1, message: '已请求停止并保存断点' };
});

ipcMain.handle('glossary-translate-v2', async (_e, { project, pak }) => {
  if (activeRun) return { ok: false, error: '已有术语库翻译任务正在运行' };
  let run = null;
  try {
    if (!project?.workspace) throw new Error('请先打开项目');
    const availablePaks = [...new Set((project.paks || []).map(item => item.pak).filter(Boolean))];
    if (!availablePaks.length) throw new Error('当前项目没有可用的 PAK');
    const fallbackPak = pak || availablePaks[0];
    const exportDir = multiPakXlsxRoot(project.workspace);
    const win = currentWindow();

    const sourcePick = await dialog.showOpenDialog(win, {
      title: '选择要应用术语库翻译的导出 XLSX',
      defaultPath: path.join(exportDir, 'remaining_xlsx'),
      properties: ['openFile'], filters: [{ name: 'Excel 工作簿', extensions: ['xlsx'] }],
    });
    if (sourcePick.canceled || !sourcePick.filePaths.length) return { ok: false, canceled: true };
    const sourceXlsx = sourcePick.filePaths[0];
    const sourceMapping = findMappingForXlsx(project, sourceXlsx, fallbackPak);
    if (!sourceMapping) throw new Error('找不到导出 XLSX 的配套 mapping.json。请保留 Studio 导出时生成的 config 文件夹。');

    const glossaryPick = await dialog.showOpenDialog(win, {
      title: '选择已翻译的术语库 XLSX',
      defaultPath: path.join(exportDir, 'term_glossary_xlsx'),
      properties: ['openFile'], filters: [{ name: 'Excel 工作簿', extensions: ['xlsx'] }],
    });
    if (glossaryPick.canceled || !glossaryPick.filePaths.length) return { ok: false, canceled: true };
    const glossaryXlsx = glossaryPick.filePaths[0];
    const glossaryMapping = findMappingForXlsx(project, glossaryXlsx, fallbackPak, 'multi-pak-safe-term-glossary');

    const key = sessionKey([sourceXlsx, sourceMapping, glossaryXlsx, glossaryMapping]);
    const runDir = path.join(project.workspace, 'xlsx_glossary_translate', 'sessions', key);
    const translatedDir = path.join(runDir, 'xlsx');
    const outputConfigDir = path.join(runDir, 'config');
    const outputXlsx = path.join(translatedDir, path.basename(sourceXlsx));
    const outputMapping = copyMappingForOutput(sourceMapping, outputXlsx, outputConfigDir);
    const checkpointPath = path.join(runDir, 'checkpoint.json');
    const controlPath = path.join(runDir, 'control.json');
    const recordsPath = path.join(project.workspace, 'localization', 'text_records.json');
    writeControl(controlPath, false);

    run = { key, runDir, controlPath, checkpointPath, cancelRequested: false, processes: new Set(), lastPercent: 0 };
    activeRun = run;
    const sourceMeta = readJson(sourceMapping);

    sendProgress({ event: 'progress', phase: 'glossary-translate', percent: 0, message: '正在检查术语库翻译断点…' });
    const translated = await runPython(path.join(__dirname, '..', 'backend', 'glossary_xlsx_translate_resumable.py'), [
      sourceXlsx, outputMapping, glossaryXlsx, glossaryMapping || '', outputXlsx,
      recordsPath, checkpointPath, controlPath,
    ], { run });
    if (!translated.ok) return translated;
    const tr = translated.report || {};
    const stopped = Boolean(tr.canceled || run.cancelRequested);

    let imported = { report: { changed: 0, paks: tr.paks || [] } };
    let syncs = [];
    let autoRejected = 0;
    let resourceSync = { report: { modified_records: 0, changed_file_count: 0, skipped_count: 0 } };
    let remaining = null;

    if (Number(tr.translated_rows || 0) > 0) {
      sendProgress({
        event: 'progress', phase: 'glossary-translate', percent: stopped ? 92 : 91,
        message: stopped ? '翻译扫描已停止，正在把已完成译文写回项目…' : '术语匹配完成，正在把译文写回项目…',
        completed_rows: tr.processed_rows, total_rows: tr.workbook_rows, stopping: stopped,
      });
      const isMulti = sourceMeta.mode === 'multi-pak-out-of-band-skeleton' && Number(sourceMeta.version) === 7;
      if (isMulti) imported = await runCli(['apply-xlsx-multi-records', recordsPath, outputXlsx, outputMapping], { run });
      else imported = await runCli(['apply-xlsx-records', recordsPath, outputXlsx, outputMapping, sourceMeta.pak || fallbackPak], { run });
      if (!imported.ok) return imported;

      const paksToSync = isMulti ? (imported.report?.paks || []) : [sourceMeta.pak || fallbackPak];
      for (const pakName of paksToSync) {
        const sync = await syncRecordsWithAutoReject(project, pakName, run);
        syncs.push({ pak: pakName, ...sync.report, outputDir: sync.outputDir });
        autoRejected += Number(sync.autoRejected || 0);
      }
      resourceSync = {
        report: {
          modified_records: syncs.reduce((n, x) => n + Number(x.modified_records || 0), 0),
          changed_file_count: syncs.reduce((n, x) => n + Number(x.changed_file_count || 0), 0),
          skipped_count: syncs.reduce((n, x) => n + Number(x.skipped_count || 0), 0),
        },
      };
    }

    if (stopped) {
      sendProgress({
        event: 'progress', phase: 'glossary-translate', percent: 100,
        message: `已停止并保存：断点 ${Number(tr.processed_rows || 0).toLocaleString()}/${Number(tr.workbook_rows || 0).toLocaleString()}；下次选择同一两个 XLSX 将继续`,
        completed_rows: tr.processed_rows, total_rows: tr.workbook_rows, stopped: true,
      });
      return {
        ok: false, canceled: true, saved: true, resumable: true,
        report: tr, importReport: imported.report, import: imported.report,
        resourceSync, sync: resourceSync.report, syncs, autoRejected,
        records: readRecords(project.workspace), sourceXlsx, glossaryXlsx,
        translatedXlsx: outputXlsx, checkpoint: checkpointPath,
        processedRows: tr.processed_rows, totalRows: tr.workbook_rows,
      };
    }

    const isMulti = sourceMeta.mode === 'multi-pak-out-of-band-skeleton' && Number(sourceMeta.version) === 7;
    if (isMulti && Number(tr.translated_rows || 0) > 0) {
      const remainingDir = path.join(exportDir, 'remaining_xlsx');
      const remainingBase = `all_paks_player_visible_remaining_${uniqueStamp()}`;
      remaining = await runCli(['export-xlsx-multi-remaining', recordsPath, remainingDir, remainingBase, path.join(exportDir, 'config'), ...(imported.report?.paks || [])], { run });
      if (!remaining.ok) return { ok: false, error: `译文已写回，但生成剩余未翻译 XLSX 失败：${remaining.error || '未知错误'}`, records: readRecords(project.workspace) };
    }

    try { if (fs.existsSync(checkpointPath)) fs.unlinkSync(checkpointPath); } catch {}
    try { if (fs.existsSync(controlPath)) fs.unlinkSync(controlPath); } catch {}
    sendProgress({ event: 'progress', phase: 'glossary-translate', percent: 100, message: '术语库翻译已完成并写回资源' });
    return {
      ok: true, glossaryTranslate: true, report: tr,
      importReport: imported.report, import: imported.report,
      resourceSync, sync: resourceSync.report, syncs, autoRejected,
      records: readRecords(project.workspace), sourceXlsx, glossaryXlsx,
      translatedXlsx: outputXlsx, checkpoint: checkpointPath,
      remaining: remaining?.report, remainingXlsx: remaining?.report?.xlsx,
    };
  } catch (e) {
    return { ok: false, error: e.message };
  } finally {
    if (activeRun === run) activeRun = null;
  }
});
