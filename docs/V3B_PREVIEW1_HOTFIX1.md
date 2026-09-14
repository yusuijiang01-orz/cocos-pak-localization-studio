# V3-B Preview 1 Hotfix 1

修复两个实机问题：

1. `ui.pak` 回包时遇到仍含越南文的部分翻译文本会因 `GBK` 无法编码而失败。
   - 回写器现支持游戏实际使用的 `GBK 中文 + TCVN3 越南文` 混合旧编码。
   - 导入 CSV 后，仅交换层格式变化（例如 `$` 前缀、`<\\n>` 与单元格换行）不再被误判为真实翻译修改。
   - 仍保留控制 Token 一致性门禁。

2. Electron 底部/右下角 Python 进度信息在部分中文 Windows 环境乱码。
   - Electron 启动 Python 时强制 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`。

验收：
- Python 核心测试：PASS
- Translation Memory / Token / NRV2B：PASS
- CSV exchange：PASS
- 使用真实 `ui.pak` + `ui_localization_zh_test.csv`：构建成功
- 重建后 216 / 216 Entry Round-trip：PASS
- 本次试译触发 59 个 Entry 重建，最终 PAK 可由当前解码器完整解回且逐 Entry 内容一致。
