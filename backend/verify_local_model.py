#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from local_model import NLLBEngine, model_status


def main() -> int:
    model_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "models" / "nllb-200-distilled-600M"
    status = model_status(model_dir)
    if not status["installed"]:
        raise RuntimeError(status["message"])
    engine = NLLBEngine(model_dir)
    translated = engine.translate_batch(["Thiết lập tự động"], batch_size=1)[0]
    print(f"Device: {engine.device}")
    print(f"Translation test: {translated}")
    if engine.torch.cuda.is_available():
        print(f"GPU: {engine.torch.cuda.get_device_name(0)}")
        print(f"VRAM: {engine.torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
