# V3-B Preview 3 Hotfix 1

UI-only hotfix for the unique-text translation queue.

Changes:
- Replaced the blocking queue modal with a collapsible right-side panel.
- Main workspace yields width to the queue panel instead of being covered.
- Closing the queue panel only hides it; background translation is not stopped.
- Queue list has independent scrolling.
- Kept Pending / Review / Failed / All filters.
- Lowered main table sticky header stacking level.
- Import PAK modal keeps an explicit high z-index.

Not changed:
- NLLB local model integration
- localization.db / Translation Memory
- queue backend/state
- CSV import/export
- PAK unpack/build / NRV2B
