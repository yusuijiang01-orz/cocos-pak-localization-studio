"""Keep resource syntax out of model output; assemble translated spans locally."""
import re
from localization_tm import TOKEN_RE, RESOURCE_EXTENSIONS, RESOURCE_PATH_PATTERN

RESOURCE_REF = (
    r'(?:' + RESOURCE_PATH_PATTERN + r')'
    r'|\b[^\s<>"\'|,;()\[\]{}\\/]+\.(?:' + RESOURCE_EXTENSIONS + r')\b'
    r'|\b(?:' + RESOURCE_EXTENSIONS + r')\b'
)

# Keep this deliberately narrow. Broad "looks like an identifier" rules used
# here can swallow ordinary Vietnamese names or UI words and leave nothing for
# the model to translate.
PATTERN = re.compile(
    r'^\s*[-+]?\d+\s*='
    r'|#[A-Za-z]◈\s*(?:P\s*)?\d+\s*◈[-+]?%?'
    r'|#[A-Za-z]\d+[-+]?%?'
    r'|^[#$=]+'
    r'|◈\s*(?:P\s*)?\d+\s*◈'
    r'|#[dsif]'
    r'|<[^<>\r\n]{1,160}>'
    r'|[\r\n\t]+'
    r'|(?:' + RESOURCE_REF + ')'
    r'|(?:' + TOKEN_RE.pattern + ')',
    re.I,
)

NATURAL_LATIN_RE = re.compile(r'[A-Za-z\u00c0-\u024f\u1e00-\u1eff]')
LATIN_RUN_RE = re.compile(r'[A-Za-z\u00c0-\u024f\u1e00-\u1eff]+')
HAN_RE = re.compile(r'[\u3400-\u9fff]')
PARTIAL_LEADING_STEM_RE = re.compile(r'^([A-Za-z]{1,3})(?=[\u3400-\u9fff])')
TECH_ACRONYMS = {'NPC', 'PK', 'PVP', 'PVE', 'VIP', 'HP', 'MP', 'EXP', 'ID', 'UI', 'URL'}
EXACT_TRANSLATIONS = {
    'Hoàn thành nhiệm vụ': '任务完成',
    'Hoàn thành nhiệm vụ.': '任务完成。',
    'Thỉnh giáo': '请教',
    'Kháng tất cả +10%': '所有抗性 +10%',
    'Phòng ngự tăng 140 điểm': '防御增加140点',
    'Phòng ngự tăng 180 điểm': '防御增加180点',
}

def split_rows(rows, compact=False, marker_style='diamond'):
    requests, layouts = [], {}
    for row in rows:
        text = str(row['text'])
        if compact:
            markers = {}
            def protect(match):
                marker = (
                    f'<x{90000000 + len(markers)}/>'
                    if marker_style == 'xml'
                    else f'◈{90000000 + len(markers)}◈'
                )
                markers[marker] = match.group(0)
                return marker
            masked = PATTERN.sub(protect, text)
            if not NATURAL_LATIN_RE.search(masked):
                layouts[row['id']] = [(None, text)]
                continue
            key = f's{len(requests)}'
            requests.append({'id': key, 'text': masked})
            layouts[row['id']] = {'key': key, 'markers': markers}
            continue
        parts, pos = [], 0
        for match in PATTERN.finditer(text):
            if match.start() > pos:
                parts.append((False, text[pos:match.start()]))
            parts.append((True, match.group()))
            pos = match.end()
        parts.append((False, text[pos:]))
        layout = []
        for protected, value in parts:
            if protected or not value.strip() or not NATURAL_LATIN_RE.search(value):
                layout.append((None, value))
            else:
                stripped = value.strip()
                exact = EXACT_TRANSLATIONS.get(stripped)
                if exact is not None:
                    leading = value[:len(value)-len(value.lstrip())]
                    trailing = value[len(value.rstrip()):]
                    layout.append((None, leading + exact + trailing))
                    continue
                key = f's{len(requests)}'
                requests.append({'id': key, 'text': stripped})
                leading = value[:len(value)-len(value.lstrip())]
                trailing = value[len(value.rstrip()):]
                layout.extend([(None, leading), (key, ''), (None, trailing)])
        layouts[row['id']] = layout
    return requests, layouts

def assemble(layouts, results):
    values = {}
    duplicates = set()
    for item in results:
        key = str(item.get('id', ''))
        if key in values:
            duplicates.add(key)
        value = str(item.get('text', ''))
        # Model output belongs only to natural-language spans. Runtime tokens
        # were removed before the request, so Latin fragments beside Chinese
        # (K系统连接 / Ph附近 / Ho完成任务) are always incomplete Vietnamese,
        # never identifiers. Remove them before reattaching protected content.
        # Keep residual words intact so validation can retry instead of silently
        # deleting untranslated meaning.
        # Some models keep the first one or two letters of a Vietnamese word
        # and glue them to Chinese (K系统 / Ph附近). This is never a valid
        # translation, while known game acronyms remain intact.
        stem = PARTIAL_LEADING_STEM_RE.match(value)
        if stem and stem.group(1).upper() not in TECH_ACRONYMS:
            value = value[stem.end():]
        values[key] = value
    output = []
    for rid, layout in layouts.items():
        if isinstance(layout, dict):
            key = layout['key']
            value = values.get(key, '')
            if key in duplicates or not value.strip():
                continue
            markers = layout.get('markers', {})
            if any(value.count(marker) != 1 for marker in markers):
                continue
            for marker, original in markers.items():
                value = value.replace(marker, original)
            output.append({'id': rid, 'text': value})
            continue
        keys = [key for key, _ in layout if key is not None]
        if any(key in duplicates or not values.get(key, '').strip() for key in keys):
            continue
        output.append({'id': rid, 'text': ''.join(value if key is None else values[key] for key, value in layout)})
    return output
