#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import sys
MODEL_ID='facebook/nllb-200-distilled-600M'
root=Path(__file__).resolve().parents[1]
out=root/'models'/'nllb-200-distilled-600M'
out.mkdir(parents=True,exist_ok=True)
try:
    from huggingface_hub import snapshot_download
except Exception:
    print('缺少 huggingface_hub，请先执行 INSTALL_LOCAL_MODEL.bat')
    raise
print(f'Downloading {MODEL_ID} -> {out}')
print('首次下载需要网络，完成后翻译过程可完全离线。模型体积约 2.5GB。')
snapshot_download(repo_id=MODEL_ID,local_dir=str(out))
print('Local model installed:',out)
