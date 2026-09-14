# Localization Structure Policy

## Default rule

Unknown files and unknown structures are never translated automatically. A format
must have both a parser and a round-trip structural validator before it can enter
the translation queue.

## Never translate

- Everything inside `<...>`, including `pos`, `npcpos`, color tags and names that
  look player-visible. These are runtime markup and must remain byte-identical.
- Resource paths and file names, including paths containing Vietnamese or Chinese.
- Lua code, function names, variables, table keys, comments and identifiers.
- INI section names, keys and equals signs.
- TSV headers, separators, IDs, numeric/config columns and resource-reference fields.
- Placeholders and control markers: printf tokens, escaped characters, `{...}`,
  `${...}`, `@`, `$`, `#`, symbol aliases and line/tab structure.
- Binary and asset bytes in BIN, SPR, BMP, JPG, JPEG and PNG files.

## Translate through a parser

| Suffix | Policy | Editable content |
| --- | --- | --- |
| `.tsv` | Mixed | Only allowlisted player-visible text columns, cell by cell |
| `.ini` | Mixed | Values belonging to visible text keys only |
| `.lua` | Mixed | Natural-language spans inside quoted strings, excluding every `<...>` tag, path and identifier |
| `.txt` | Conditional | Plain prose line by line; structured TXT uses key/value, tabular or markup parsing |

Text embedded in an image may need localization, but it must use an image-editing
workflow. It must never be handled by replacing bytes inside the asset.

## Current extracted project

| Suffix | Files | Classification |
| --- | ---: | --- |
| `.tsv` | 628 | Mixed |
| `.ini` | 711 | Mixed |
| `.lua` | 410 | Mixed |
| `.txt` | 62 | 25 plain text, 37 mixed |
| `.bin` | 1245 | Blocked |
| `.spr` | 765 | Blocked |
| `.bmp` | 325 | Blocked |
| `.jpg` | 22 | Blocked |
| `.json` | 6 | Blocked unless an explicit game schema is added |

## Update workflow

1. Run `python backend/resource_structure_audit.py <extracted-root> <report.json>`.
2. Review new suffixes and files whose policy changed.
3. Add a format-specific parser and structural tests before enabling a new format.
4. Translate only extracted text spans.
5. Materialize edits from the untouched extracted source.
6. Require structural validation and full PAK extraction round-trip before release.
