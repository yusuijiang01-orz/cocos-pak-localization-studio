# V3-B Preview 3 本地模型

默认本地模型：`facebook/nllb-200-distilled-600M`。

## 安装

首次运行 `INSTALL_LOCAL_MODEL.bat`。安装过程会：

1. 安装 `torch / transformers / sentencepiece / huggingface_hub`；
2. 将模型下载到 `models/nllb-200-distilled-600M/`；
3. 下载完成后，翻译过程不再调用任何外部翻译 API，可断网运行。

模型约 2.5 GB，首次安装需要足够磁盘空间和网络。

## 翻译顺序

1. Translation Memory 精确匹配；
2. Glossary 精确匹配；
3. NLLB 本地模型处理剩余唯一文本；
4. `<c=g>`、`<c>`、`%d`、`%s`、`0/1`、换行等结构不会送入模型；
5. 输出先执行控制标记校验，通过后才写入 `localization.db`；
6. 失败或结构异常项目进入失败/需复核队列，不自动写回 PAK。

## 资源控制

CPU 推理默认最多使用约 75% 的逻辑线程，并限制上限；CUDA 可用时自动使用 GPU。暂停按钮会在当前批次结束后停止领取下一批任务。
