// Keep the complete legacy runtime available while adding the vNext IPC surface.
// vNext must also boot on older local Studio baselines that predate the optional
// glossary wrapper modules. The first available legacy bootstrap is loaded.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

function tryLegacyBootstrap(moduleName) {
  const absolute = path.join(__dirname, `${moduleName}.js`);
  if (!fs.existsSync(absolute)) return false;
  try {
    require(`./${moduleName}`);
    console.log(`[VNEXT] legacy bootstrap: ${moduleName}`);
    return true;
  } catch (error) {
    // Some older overlays may contain main_with_glossary_resume.js while its
    // own dependency main_with_glossary.js is absent. Treat that exact missing
    // wrapper as an optional-baseline mismatch and continue to the next layer.
    if (error && error.code === 'MODULE_NOT_FOUND' && /main_with_glossary/.test(String(error.message || ''))) {
      console.warn(`[VNEXT] optional legacy bootstrap unavailable: ${moduleName}: ${error.message}`);
      return false;
    }
    throw error;
  }
}

let legacyBootstrap = '';
for (const candidate of ['main_with_glossary_resume', 'main_with_glossary', 'main']) {
  if (tryLegacyBootstrap(candidate)) {
    legacyBootstrap = candidate;
    break;
  }
}
if (!legacyBootstrap) {
  throw new Error('Cannot find a usable Electron legacy bootstrap (main.js)');
}

// If an older baseline had to fall back below the resumable glossary wrapper,
// keep the legacy toolbar from producing an unhandled IPC exception. The new
// vNext smart-translation workflow remains fully available; users who need the
// old glossary-only tool can install the cumulative runtime overlay later.
if (legacyBootstrap !== 'main_with_glossary_resume') {
  ipcMain.handle('glossary-translate-v2', async () => ({
    ok: false,
    error: '当前本地基线缺少旧版术语库翻译运行模块。vNext 智能翻译可正常使用；旧版术语库工具需要累计兼容补丁。',
  }));
  ipcMain.handle('glossary-translate-cancel', async () => ({
    ok: true,
    count: 0,
    message: '当前未加载旧版术语库翻译运行模块',
  }));
}

require('./vnext_ipc');
require('./vnext_compat_ipc');

// Phase 7 Windows CI launches the real Electron entry point instead of only
// syntax-checking renderer files. The smoke hook waits for the production
// renderer to finish loading, verifies the preload bridge and vNext shell, then
// exits deterministically so a headless CI runner cannot hang indefinitely.
if (process.env.STUDIO_VNEXT_SMOKE === '1') {
  const fail = (message, code = 2) => {
    try { console.error(`[VNEXT_SMOKE_FAIL] ${message}`); } catch {}
    setTimeout(() => app.exit(code), 0);
  };
  const deadline = setTimeout(() => fail('Electron renderer did not become ready within 45s', 3), 45_000);

  app.whenReady().then(() => {
    const waitForWindow = () => {
      const win = BrowserWindow.getAllWindows()[0];
      if (!win || win.isDestroyed()) {
        setTimeout(waitForWindow, 50);
        return;
      }
      const inspect = async () => {
        try {
          const result = await win.webContents.executeJavaScript(`(() => {
            const nav = document.getElementById('vnextNav');
            const shell = document.getElementById('vnextShell');
            const api = window.studio || {};
            return {
              title: document.title,
              readyState: document.readyState,
              hasNav: !!nav,
              hasShell: !!shell,
              hasDashboardApi: typeof api.vnextDashboard === 'function',
              hasTranslateApi: typeof api.vnextTranslate === 'function',
              hasReviewApi: typeof api.vnextReviewList === 'function',
              hasBuildApi: typeof api.vnextBuildPaks === 'function',
              hasCompatApi: typeof api.vnextCompatStatus === 'function',
              pages: nav ? Array.from(nav.querySelectorAll('[data-vn-page]')).map(x => x.dataset.vnPage) : [],
              legacyBootstrap: ${JSON.stringify(legacyBootstrap)},
            };
          })()`, true);
          const requiredPages = ['overview', 'translate', 'review', 'knowledge', 'build', 'legacy'];
          const ok = result && result.readyState === 'complete'
            && result.hasNav && result.hasShell
            && result.hasDashboardApi && result.hasTranslateApi && result.hasReviewApi
            && result.hasBuildApi && result.hasCompatApi
            && requiredPages.every(page => result.pages.includes(page));
          console.log(`[VNEXT_SMOKE_RESULT] ${JSON.stringify({ ...result, ok })}`);
          clearTimeout(deadline);
          app.exit(ok ? 0 : 4);
        } catch (error) {
          clearTimeout(deadline);
          fail(error?.stack || error?.message || String(error), 5);
        }
      };
      if (win.webContents.isLoadingMainFrame()) win.webContents.once('did-finish-load', inspect);
      else inspect();
    };
    waitForWindow();
  }).catch(error => {
    clearTimeout(deadline);
    fail(error?.stack || error?.message || String(error), 6);
  });
}
