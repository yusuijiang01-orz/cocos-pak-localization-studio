// Keep the complete legacy runtime available while adding the vNext IPC surface.
// Phase 5 changes the default renderer experience, not the legacy backend contract.
require('./main_with_glossary_resume');
require('./vnext_ipc');
require('./vnext_compat_ipc');
