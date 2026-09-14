# Cocos2D PAK Localization Studio V3-A Hotfix 2

本版本针对实机反馈修复 UI 性能、资源路径噪声与中越混合旧编码。

## Hotfix 2 变更

- 表格改为每页 200 条，避免一次创建数千 DOM 节点导致 Electron/Windows 卡顿。
- 搜索增加 220ms 防抖；选择记录、输入译文不再整表重绘。
- 修复中央内容区 `min-height` 与底部状态栏布局，状态栏不再出现在表格中间。
- 自动排除 `Image=\Spr\...spr`、BMP/PNG/SPR 等资源引用路径，不再当作待翻译文本。
- INI 注释行默认不提取。
- 新增 GBK 中文 + TCVN3 越南文同一行的混合解码，可恢复例如 `Uy Phong Lẫm Liệt` 与中文说明混排的内容。
- 新增“中越混合”语言标记，避免强行把混合行归为纯中文或纯越南文。
- 保持 PAK 解包核心不变；本版本仍不回写、不重打包。

> 已有 V3-A 工作区使用旧 `text_records.json`。要应用新的路径过滤与混合编码修复，请重新“导入 PAK”分析一次。

# Cocos2D PAK Localization Studio V3-A

这是第一阶段的桌面本地化查看器，针对当前已经验证的 Kingsoft/Cocos2D `PACK` PAK 家族。

## V3-A 已实现

- Electron 桌面 UI。
- 一次选择多个 `.pak`。
- 使用已验证的 32-byte Header + 16-byte Entry + Method 1 / NRV2B 解包核心。
- 原始资源字节无损导出，不强制转码。
- 支持 `.lua / .ini / .tsv / .csv / .json / .xml / .txt` 的文本抽取。
- 面向老游戏的混合编码分析：UTF-8、GB18030/GBK、Windows-1258、TCVN3/ABC。
- 特别处理“同一 TSV 中中文表头 + TCVN3 越南文内容”的混合编码情况：按单元格而不是整文件判断编码。
- 初步分类：武器、技能、任务、道具、装备、NPC 对话、UI 文字、系统提示、其他文本。
- 按分类、语言、PAK 筛选；全文搜索；上下文查看。
- 导出当前筛选结果 CSV。
- 工作区自动生成完整 CSV 与分类 CSV。

## 本阶段明确不包含

- 不会写回原始资源。
- 不会重新打包 PAK。
- 不会覆盖任何输入 PAK。
- 分类是启发式结果，不应视为 100% 准确；V3-B 会基于真实表头/脚本结构继续提高分类精度。
- VNI 等更特殊越南旧编码尚未进入正式解码器；发现实际样本后再加入，避免错误转换。

## Windows 使用

你的机器已经确认 `python` 可用而 `py -3` 损坏，因此本项目只调用 `python`，不调用 Python Launcher。

1. 安装 Node.js LTS（如果已有可跳过）。
2. 双击 `安装依赖.bat`，只需第一次执行。
3. 双击 `启动V3-A.bat`。
4. 点击右上角 **导入 PAK**。
5. 选择 `ui.pak / settings.pak / script.pak` 等一个或多个 PAK。
6. 选择一个空目录作为工作区。
7. 等待解包和文本分析完成。

> `settings.pak` 和 `script.pak` 文本量很大，首次分析可能需要几十秒到数分钟，取决于 CPU 和磁盘速度。

## 工作区结构

```text
workspace/
├─ project.json
├─ extracted/
│  ├─ ui/
│  ├─ settings/
│  └─ script/
└─ localization/
   ├─ text_records.json
   ├─ localization_all.csv
   ├─ analysis_report.json
   └─ categories/
      ├─ 武器.csv
      ├─ 技能.csv
      ├─ 任务.csv
      ├─ 道具.csv
      ├─ 装备.csv
      ├─ NPC对话.csv
      ├─ UI文字.csv
      ├─ 系统提示.csv
      └─ 其他文本.csv
```

CSV 核心追踪字段包含：`id / category / pak / hash / source_file / line / column / key / encoding / language / original / translation / context / status`。

这些定位字段会作为后续 V3-C “安全回写”使用，不能随意改动。

## 下一阶段计划

V3-B 将重点做分类准确率与本地化工作流：识别 TSV 表头语义、任务/技能/物品数据表，单独的“仅越南文”队列，重复文本合并、术语表、翻译状态与 CSV 导入。

V3-C 才加入按定位字段写回 Lua/TSV/INI/JSON/XML，并做语法/列数/编码验证。

V3-D 最后加入 Method 1 重压缩、PAK 重建和 round-trip 校验。


## Hotfix 1 - Windows CMD launcher

If the original Chinese BAT files printed broken commands such as `on`, `cho.` or mojibake, use the new ASCII launchers:

```text
INSTALL_DEPS.bat
START_V3A.bat
```

Both files use Windows CRLF line endings and contain ASCII commands only. They call `python` directly and never call `py -3`. The Chinese-named BAT files are now only tiny wrappers around these launchers.


## Hotfix 3：并行处理与系统负载保护

- PAK 内 NRV2B 解压改为 `ProcessPoolExecutor` 多进程处理，绕开 Python GIL。
- 文本文件扫描/GBK/TCVN3/Windows-1258 编码分析按文件并行。
- 默认 worker 数 = `floor(逻辑 CPU × 75%)`，严格低于 80% 配额；高核心数机器额外限制最多 16 个 worker，防止进程风暴。
- 只有 1 个逻辑 CPU 时退化为单 worker。
- 后台分析进程采用较低调度优先级（Windows `BELOW_NORMAL_PRIORITY_CLASS`，类 Unix `nice +5`），减少对其他前台程序的影响。
- 多个 PAK 仍顺序处理；只在单个 PAK 内并行，避免并发数乘法膨胀。
- 并行输出仍按原 PAK Entry / 文件顺序收集，保持 manifest 与文本记录顺序稳定。
- 导入时底部状态会显示实际 worker 数与逻辑 CPU 数。

### 验收

使用实际 `ui.pak`：216/216 Entry 解包成功，最终有效文本 14,532 条。串行与并行解包后的 216 个资源逐文件 SHA-256 一致。

使用实际 `script.pak` 做解包性能测试：本测试环境串行约 5.83 秒，并行 8 workers 约 2.45 秒，约 2.38×；实际速度取决于 CPU、磁盘和资源大小。

文本分析对 400 个真实脚本文件抽样：串行约 4.22 秒，并行 8 workers 约 1.75 秒，约 2.41×；15,798 条记录内容与顺序完全一致。

## Hotfix 4：按 PAK 分离的精简本地化表

- 左侧 `PAK` 列表用于明确选择 `ui.pak / settings.pak / script.pak`；列表只显示当前选择 PAK 的记录。
- 用户交换 CSV 不再包含分类、Hash、文件、行列、编码、上下文等重复元数据，固定只有两列：`id,original`。
- **导出所选 PAK CSV**：必须先选择一个具体 PAK；每个 PAK 单独生成，例如 `ui_localization.csv`、`settings_localization.csv`、`script_localization.csv`。
- **导入所选 PAK CSV**：同样先选择对应 PAK。导入只按稳定 `id` 匹配，不依赖 CSV 行号或排序；`original` 列中的编辑后文本成为该 ID 的当前本地化文本。
- 为未来安全回写，`pak/hash/source_file/line/column/encoding/category` 等定位数据仍保存在工作区内部 `text_records.json`，但不暴露到翻译 CSV。
- 页面主表简化为两列：`ID` 和 `Original`。
- 不再自动生成巨大的 `localization_all.csv` 和 `categories/*.csv`；已有工作区重新导入分析后会移除这些旧格式文件。
- 内部 `text_records.json` 改为紧凑 JSON，减少工作区体积。
- 当前导入 CSV 只更新工作区数据库，**仍不会修改或重新打包原始 PAK**；PAK 回写将在后续阶段接入同一 ID 映射。
