# Cocos PAK Localization Studio — 文件队列版

此目录是原版 Studio 的独立副本，保留原来的三栏界面、文件汉化率、文本详情、人工编辑、XLSX、翻译设置和构建体验。

## 普通工作流

1. 导入 PAK。
2. 在左侧按文件检查提取结果。`text_records.json` 是后台结构分析器生成的玩家展示字段白名单；界面、XLSX 导出、导入、Ollama、资源写回和构建均使用这一个范围，不再各自猜测。
3. 点击“导出逐文件 XLSX”会为白名单内每个 TSV/INI/TXT/LUA 生成独立 XLSX，不创建文件名子目录。输出位于 `xlsx_export/<pak>_player_visible`；`xlsx` 文件夹只放工作簿，映射和报告统一放入旁边的 `config` 文件夹。旧版导出目录不会被新版首次导出覆盖。
4. Google 处理完成后，点击“导入 XLSX 文件夹”只选择 `xlsx` 目录；Studio 自动寻找同级 `config`，再按文件名依次校验并一次性导回。
   如果 Google 把译后 XLSX 下载到了其他目录，Studio 会回退使用当前项目原导出位置的 `config`；配置文件无需上传。
5. “导出全部 PAK XLSX”会把项目内所有 PAK 合并成一个工作簿，放在 `xlsx_export/all_paks_player_visible/full_xlsx`，列为 `id / pak / source_file / text`；一次 Google 翻译后，用“导入全部 PAK XLSX”统一回写。`pak` 与 `source_file` 只供查看，定位只信任稳定 ID 和同级 `config`。
   v7 完整表不再把 `◈` 或任何替代占位符交给 Google：标签、`$` 变量、格式符、路径、数字等只保存在本地精确骨架里。导入时逐片段重建；缺行或危险译文自动使用原文片段补位，绝不写入残缺结构。汉化率只统计，不作为构建条件。
   每次完整表导入后会自动生成 `xlsx_export/all_paks_player_visible/remaining_xlsx/all_paks_player_visible_remaining_localization.xlsx`。它只含仍有越南文的去重片段；下一轮只翻译这份小表再用同一个“导入全部 PAK XLSX”按钮导回，未列出的已有中文会保持不变。弹窗分别显示去重片段数与资源引用处数，避免把同一句被复用数千次误报成数千项翻译工作。
5. 点击“按文件 Ollama”后只选择 `xlsx` 文件夹；Ollama 自动完成一个文件再进入下一个，同一请求绝不混入其他文件，大文件内部自动分桶。
   也可以直接选择“导出未翻译 XLSX”生成的 `remaining_xlsx` 文件夹；Studio 会自动识别多 PAK v7 映射，完成后统一导入涉及的 PAK，无需切换为另一种导入方式。
6. 每桶立即保存检查点；检查点、备份、控制文件和翻译报告全部写入 `config`，可停止后续跑。
7. “构建全部 PAK”会依次从各自官方 raw 结构构建项目内所有 PAK。官方包里的 TSV、CSV、INI、TXT、LUA 无论是否翻译，都会统一转为严格 UTF-8；不再按原文件回写 GBK/ANSI/Windows-1258/TCVN3。结构修复后还会再次转码并执行 UTF-8、标签、路径、Lua 代码骨架及完整解包回读闸门。未翻译文本保留原文并允许构建；任何非 UTF-8、结构损坏或新增 `�` 都会停止对应 PAK。

“玩家展示字段”指名称、说明、对话、提示等设计为呈现给玩家的字段。它排除 ID、路径、资源名、数值、坐标、脚本标识和配置注释；不承诺每条旧/废弃数据都能在当前版本运行时到达。

## 可见按钮

- 翻译设置、打开项目、导入 PAK
- 导出逐文件 XLSX、导入 XLSX 文件夹、导出全部 PAK XLSX、导出未翻译 XLSX、导入全部 PAK XLSX
- 按文件 Ollama（选择一次文件夹，自动翻译并导回）
- 构建全部 PAK

API 审校、国际版迁移、API 一键构建以及旧 CSV/分片/脚本工具不再显示在普通界面。

副本内已复制当前翻译记忆数据库，已有可信译文可以继续命中；后续写入只影响这个副本。

## 启动

双击项目根目录的 `启动文件队列Studio.bat`，或在本目录运行 `npm start`。
