#!/usr/bin/env python3
"""Safely update only Studio's local Ollama profile for TranslateGemma."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ollama_batch_translate import CONFIG_PATH, TRANSLATEGEMMA_PROFILE_PROMPT


def main() -> None:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f'未找到 Studio 配置：{CONFIG_PATH}')
    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    profiles = config.setdefault('profiles', [])
    profile = next((item for item in profiles if item.get('id') == 'ollama'), None)
    if profile is None:
        profile = next((item for item in profiles if str(item.get('name', '')).strip().lower() == 'ollama'), None)
    if profile is None:
        profile = {
            'id': 'ollama', 'name': 'Ollama 本地',
            'baseUrl': 'http://127.0.0.1:11435/v1', 'apiKey': '', 'models': [],
        }
        profiles.append(profile)

    backup = CONFIG_PATH.with_name('api-translator-config.before-translategemma.json')
    if not backup.exists():
        shutil.copy2(CONFIG_PATH, backup)

    profile['model'] = 'translategemma:4b'
    profile['batchSize'] = 50
    profile['think'] = False
    profile['prompt'] = TRANSLATEGEMMA_PROFILE_PROMPT
    models = [str(value) for value in profile.get('models') or []]
    if 'translategemma:4b' not in models:
        models.insert(0, 'translategemma:4b')
    profile['models'] = models
    # This isolated Studio is intended to show and use the Ollama profile by
    # default; other profiles remain intact and can still be selected later.
    config['activeProfileId'] = profile['id']

    temp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + '.tmp')
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(CONFIG_PATH)
    print(json.dumps({
        'ok': True, 'config': str(CONFIG_PATH), 'backup': str(backup),
        'profile': profile.get('name'), 'model': profile['model'],
        'batchSize': profile['batchSize'], 'think': profile['think'],
        'promptLength': len(profile['prompt']),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
