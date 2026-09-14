# updatefs 本地化边界（2026-09-11）

## 结论

本项目不能按文件或“含越南文”判断是否翻译。TSV 必须按文件与列判断；LUA 只翻译字符串字面量中标签外的自然语言；INI/TXT 只翻译明确的显示值或纯自然语言行。所有回写必须以官方 `_raw_reference` 为结构底稿，只替换已确认的文本单元格。

## 数据分布

以 `pak/v587+/_raw_reference/updatefs` 为基准：111 个 TSV、410 个 LUA、69 个 INI、17 个 TXT。全量审计得到：

- TSV 的主要可见文本列：`名称` 24,027、`说明文字` 23,973、`Desc` 14,268、`Name` 7,854、`STRING` 6,551、`Intro` 5,796、`SkillName` 2,134、`ItemName` 1,848 等自然语言单元格。
- LUA：8,384 个引号字符串，拆出 13,537 个标签外候选段；其中约 7,038 个越南文/混合文本段。8,327 个 `<...>` 标签保持不可编辑。
- INI：1,030 个显示型键值，约 517 个越南文、273 个中文值；其余 7,145 个键按配置/结构键保护。
- TXT：约 608 个越南文自然语言单元；键值结构默认保护，仅明确显示键允许翻译。

机器可读明细见 `docs/updatefs_localization_audit.json`，可用 `python backend/audit_updatefs_localization.py` 重跑。

## TSV 可翻译范围

通用显示列包括：

- 名称：`Name`、`npc_name`、`ItemName`、`SkillName`、`QuestName`、`RewardName`、`TitleName`、`PackName`、`DutyName`、`RightName`、`Map_name`、中文的名称/名字/任务名/技能名/物品名/装备名/地图名。
- 描述与正文：`Desc`、`Description`、`des`、`Intro`、`tip`、`SkillDesc`、`QuestDesc`、`RewardDescription`、`TaskContent`、`TaskInfo`、`TaskTips`、`RightDesc`、`DutyDesc`、`Memo`、`mission`、`tutorial`、`STRING` 及中文说明/描述/内容/文本/对话/提示。
- 少量歧义旧表头只按具体文件开放：`0030.../TenQua`、`0811.../Ghi chó`、`0814.../value`、`1021.../Note`、`2422.../Property`。

以下必须保护：ID/ResId/索引、类型/类别/属性数值、坐标、等级、数量、价格、时间、flag、脚本/函数/参数、图片/图标/动画/声音/文件/路径、技能 ID 和关联字段。`0553_38EBCB17.tsv` 与 `0999_64D8690E.tsv` 是模型、角色部件、动作和技能关联表，整表禁止翻译，包括看似自然语言的 `Skill1..4`、`人物名称`。

## LUA / INI / TXT

- LUA：仅处理引号字符串内部且 `<...>` 标签之外的自然语言。函数名、变量名、注释、资源名、脚本标识符不提取。`<pos=...>`、`<npcpos=...>` 内名称和坐标整体保护。
- INI：仅显示语义键（Name/Title/Text/Caption/Tip/Desc/Content/Message/Label 等）的值可翻译。section、key 本体，以及 Image/File/Path/Icon/Spr/Script/Function/Id/Index/Type/Map/X/Y 等键和值均保护。
- TXT：纯自然语言行或制表结构中经确认的文本可翻译；`key=value` 默认按 INI 显示键白名单处理，数字表、资源清单和脚本数据不翻译。

所有 `@`、`$`、`#`、格式串、转义换行、引号、花括号、资源路径及 `<...>` 标签先从模型输入中隔离并在本地复原；复原后还要做 token 与结构校验。

## 已有汉化迁移

迁移以新版官方 TSV 为底稿，对旧汉化与官方文件逐行逐列比对，只接受上述显示列、含中文、无乱码、非资源引用且通过 token/结构校验的目标文本。最新结果：59 个文件、95,084 个中文单元格安全迁移，4,104 个风险变更拒绝；两张高风险表整表隔离。输出位于 `pak/new/recovery/visible_tsv_salvage`，本轮未构建 PAK。

## 源码落点

- `backend/localization_analyzer.py`：统一文件+列判定、整表隔离、INI 显示键边界。
- `backend/tsv_localization.py`：Studio/XLSX 的实际提取入口复用统一规则。
- `backend/salvage_visible_tsv_cells.py`：旧汉化迁移复用同一边界，以官方字节为底稿。
- `backend/lua_localization.py`、`backend/protected_segments.py`、`backend/tsv_localization.py`：标签/路径/占位符拆分、恢复与校验。
