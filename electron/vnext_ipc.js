const { BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');

let activeTranslate = null;
let activeBuild = null;

function currentWindow() {
  return BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0] || null;
}

function sendProgress(payload) {
  const win = currentWindow();
  if (win && !win.isDestroyed()) win.webContents.send('backend-progress', payload);
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

function spawnOptions() {
  const cpus = Math.max(1, (os.cpus()?.length || 1) - 2);
  return {
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
      OMP_NUM_THREADS: String(cpus),
      MKL_NUM_THREADS: String(cpus),
      OPENBLAS_NUM_THREADS: String(cpus),
    },
  };
}

function parseJsonOutput(stdout, stderr, code) {
  const text = String(stdout || '').trim();
  let parsed = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      const lines = text.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      for (let i = lines.length - 1; i >= 0; i--) {
        try { parsed = JSON.parse(lines[i]); break; } catch {}
      }
    }
  }
  if (parsed) return parsed;
  return { ok: false, error: String(stderr || `Backend exited ${code}`).trim() || `Backend exited ${code}` };
}

function runJson(script, args) {
  return new Promise(resolve => {
    const proc = spawn(pythonCommand(), [script, ...args], spawnOptions());
    let stdout = '';
    let stderr = '';
    proc.stdout.setEncoding('utf8');
    proc.stderr.setEncoding('utf8');
    proc.stdout.on('data', chunk => { stdout += chunk; });
    proc.stderr.on('data', chunk => { stderr += chunk; });
    proc.on('error', error => resolve({ ok: false, error: error.message || String(error) }));
    proc.on('close', code => resolve(parseJsonOutput(stdout, stderr, code)));
  });
}

function runStreaming(script, args, phase) {
  return new Promise(resolve => {
    const proc = spawn(pythonCommand(), [script, ...args], spawnOptions());
    activeTranslate = { proc, phase };
    let stdout = '';
    let stderr = '';
    let buffer = '';
    let lastObject = null;
    proc.stdout.setEncoding('utf8');
    proc.stderr.setEncoding('utf8');
    proc.stdout.on('data', chunk => {
      stdout += chunk;
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop();
      for (const raw of lines) {
        const line = raw.trim();
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          lastObject = obj;
          if (obj.event || obj.phase || obj.percent !== undefined) {
            sendProgress({ ...obj, phase, event: obj.event || 'progress' });
          }
        } catch {}
      }
    });
    proc.stderr.on('data', chunk => { stderr += chunk; });
    proc.on('error', error => {
      activeTranslate = null;
      resolve({ ok: false, error: error.message || String(error) });
    });
    proc.on('close', code => {
      if (buffer.trim()) {
        try { lastObject = JSON.parse(buffer.trim()); } catch {}
      }
      activeTranslate = null;
      const result = lastObject || parseJsonOutput(stdout, stderr, code);
      resolve(result);
    });
  });
}

const root = path.join(__dirname, '..');
const coreCli = path.join(root, 'backend', 'vnext_cli.py');
const uiCli = path.join(root, 'backend', 'vnext_ui_cli.py');

function requireWorkspace(payload) {
  const workspace = String(payload?.workspace || '').trim();
  if (!workspace) throw new Error('请先打开或导入项目');
  return workspace;
}

ipcMain.handle('vnext-dashboard', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    return await runJson(uiCli, ['dashboard', '--workspace', workspace]);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-ingest-workspace', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    sendProgress({ event: 'progress', phase: 'vnext-sync', percent: 10, message: '正在同步工作区到 vNext…' });
    const result = await runJson(coreCli, ['ingest-workspace', '--workspace', workspace]);
    sendProgress({ event: 'progress', phase: 'vnext-sync', percent: result.ok ? 100 : 0, message: result.ok ? 'vNext 工作区同步完成' : (result.error || '同步失败') });
    return result;
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-translate', async (_e, payload) => {
  try {
    if (activeTranslate) return { ok: false, error: 'vNext 智能翻译正在运行' };
    if (activeBuild) return { ok: false, error: '正在构建 PAK，请等待构建完成' };
    const workspace = requireWorkspace(payload);
    const args = [
      'translate', '--workspace', workspace,
      '--model', String(payload?.model || 'qwen3:14b'),
      '--ollama-base', String(payload?.baseUrl || 'http://127.0.0.1:11435'),
      '--batch-size', String(Math.max(1, Number(payload?.batchSize) || 16)),
      '--timeout', String(Math.max(30, Number(payload?.timeout) || 600)),
      '--temperature', String(Number.isFinite(Number(payload?.temperature)) ? Number(payload.temperature) : 0.1),
    ];
    if (Number(payload?.maxUnits) > 0) args.push('--max-units', String(Math.floor(Number(payload.maxUnits))));
    sendProgress({ event: 'progress', phase: 'vnext-translate', percent: 0, message: '正在准备 vNext 智能翻译…' });
    return await runStreaming(coreCli, args, 'vnext-translate');
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-stop-translate', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const result = await runJson(coreCli, ['stop-job', '--workspace', workspace]);
    sendProgress({ event: 'progress', phase: 'vnext-translate', stopping: true, message: result.ok ? '已请求停止；正在提交当前批次和断点…' : (result.error || '停止请求失败') });
    return result;
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-job-status', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const args = ['job-status', '--workspace', workspace];
    if (payload?.jobId) args.push('--job-id', String(payload.jobId));
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-review-list', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const args = ['review-list', '--workspace', workspace, '--state', String(payload?.state || 'pending'), '--limit', String(Math.max(1, Number(payload?.limit) || 200))];
    if (payload?.severity) args.push('--severity', String(payload.severity));
    if (payload?.query) args.push('--query', String(payload.query));
    if (payload?.noSync) args.push('--no-sync');
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-review-show', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    return await runJson(coreCli, ['review-show', '--workspace', workspace, '--unit-id', String(payload?.unitId || '')]);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-review-approve', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const args = ['review-approve', '--workspace', workspace, '--unit-id', String(payload?.unitId || '')];
    if (String(payload?.target || '').trim()) args.push('--target', String(payload.target));
    if (String(payload?.note || '').trim()) args.push('--note', String(payload.note));
    if (payload?.lock === false) args.push('--no-lock');
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-review-reject', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const args = ['review-reject', '--workspace', workspace, '--unit-id', String(payload?.unitId || '')];
    if (String(payload?.note || '').trim()) args.push('--note', String(payload.note));
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-review-state', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const command = payload?.state === 'pending' ? 'review-reopen' : 'review-defer';
    const args = [command, '--workspace', workspace, '--unit-id', String(payload?.unitId || '')];
    if (String(payload?.note || '').trim()) args.push('--note', String(payload.note));
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-review-stats', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const args = ['review-stats', '--workspace', workspace];
    if (payload?.sync) args.push('--sync');
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-knowledge-list', async (_e, payload) => {
  try {
    const args = ['knowledge-list', '--kind', String(payload?.kind || 'glossary'), '--limit', String(Math.max(1, Number(payload?.limit) || 200))];
    if (payload?.query) args.push('--query', String(payload.query));
    return await runJson(uiCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-glossary-save', async (_e, payload) => {
  try {
    const args = [
      'glossary-save', '--source', String(payload?.source || ''), '--target', String(payload?.target || ''),
      '--term-type', String(payload?.termType || 'general'), '--scope', String(payload?.scope || 'global'),
      '--status', String(payload?.status || 'approved'), '--priority', String(Number(payload?.priority) || 100),
    ];
    if (payload?.locked !== false) args.push('--locked');
    if (payload?.note) args.push('--note', String(payload.note));
    return await runJson(uiCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-glossary-delete', async (_e, payload) => {
  return await runJson(uiCli, ['glossary-delete', '--term-id', String(Number(payload?.termId) || 0)]);
});

ipcMain.handle('vnext-tm-save', async (_e, payload) => {
  try {
    const args = ['tm-save', '--source', String(payload?.source || ''), '--target', String(payload?.target || '')];
    if (payload?.locked !== false) args.push('--locked');
    return await runJson(uiCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-tm-delete', async (_e, payload) => {
  return await runJson(uiCli, ['tm-delete', '--tm-id', String(Number(payload?.tmId) || 0)]);
});

ipcMain.handle('vnext-build-preflight', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    const args = ['build-preflight', '--workspace', workspace];
    if (payload?.requireTranslated) args.push('--require-translated');
    if (payload?.failOnWarnings) args.push('--fail-on-warnings');
    return await runJson(coreCli, args);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});

ipcMain.handle('vnext-build-paks', async (_e, payload) => {
  try {
    if (activeBuild) return { ok: false, error: '已有 vNext 构建任务正在运行' };
    if (activeTranslate) return { ok: false, error: '智能翻译正在运行，请先停止翻译' };
    const workspace = requireWorkspace(payload);
    const args = ['build-paks', '--workspace', workspace, '--workers', String(Math.max(1, Number(payload?.workers) || 1))];
    for (const pak of Array.isArray(payload?.paks) ? payload.paks : []) args.push('--pak', String(pak));
    if (payload?.requireTranslated) args.push('--require-translated');
    if (payload?.failOnWarnings) args.push('--fail-on-warnings');
    sendProgress({ event: 'progress', phase: 'vnext-build', percent: 2, message: '正在执行最终 QA 和安全构建门禁…' });
    activeBuild = { workspace };
    const result = await runJson(coreCli, args);
    activeBuild = null;
    sendProgress({ event: 'progress', phase: 'vnext-build', percent: result.ok ? 100 : 0, message: result.ok ? '所有候选 PAK 已验证并发布' : (result.error || '构建失败') });
    return result;
  } catch (error) {
    activeBuild = null;
    return { ok: false, error: error.message || String(error) };
  }
});

ipcMain.handle('vnext-build-history', async (_e, payload) => {
  try {
    const workspace = requireWorkspace(payload);
    return await runJson(coreCli, ['build-history', '--workspace', workspace, '--limit', String(Math.max(1, Number(payload?.limit) || 20))]);
  } catch (error) { return { ok: false, error: error.message || String(error) }; }
});
