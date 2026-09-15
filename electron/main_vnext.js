// Keep the complete legacy runtime available while adding the vNext IPC surface.
// Phase 5 changes the default renderer experience, not the legacy backend contract.
const { app, BrowserWindow } = require('electron');
require('./main_with_glossary_resume');
require('./vnext_ipc');
require('./vnext_compat_ipc');

// Phase 7 Windows CI launches the real Electron entry point instead of only
// syntax-checking renderer files.  The smoke hook waits for the production
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
