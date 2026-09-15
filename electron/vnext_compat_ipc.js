const { ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

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

function runJson(args) {
  const script = path.join(__dirname, '..', 'backend', 'vnext_compat_cli.py');
  return new Promise(resolve => {
    const proc = spawn(pythonCommand(), [script, ...args], {
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    });
    let stdout = '';
    let stderr = '';
    proc.stdout.setEncoding('utf8');
    proc.stderr.setEncoding('utf8');
    proc.stdout.on('data', chunk => { stdout += chunk; });
    proc.stderr.on('data', chunk => { stderr += chunk; });
    proc.on('error', error => resolve({ ok: false, error: error.message || String(error) }));
    proc.on('close', code => {
      const text = stdout.trim();
      let parsed = null;
      if (text) {
        const lines = text.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
        for (let i = lines.length - 1; i >= 0; i--) {
          try { parsed = JSON.parse(lines[i]); break; } catch {}
        }
      }
      resolve(parsed || { ok: false, error: stderr.trim() || `Backend exited ${code}` });
    });
  });
}

function workspaceOf(payload) {
  const workspace = String(payload?.workspace || '').trim();
  if (!workspace) throw new Error('请先打开或导入项目');
  return workspace;
}

ipcMain.handle('vnext-compat-status', async (_e, payload) => {
  try {
    return await runJson(['status', '--workspace', workspaceOf(payload)]);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
});

ipcMain.handle('vnext-adopt-legacy', async (_e, payload) => {
  try {
    const args = ['adopt-legacy', '--workspace', workspaceOf(payload)];
    if (payload?.overwrite) args.push('--overwrite');
    return await runJson(args);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
});
