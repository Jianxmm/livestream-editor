# 安装与操作说明，供 AI 助手和维护者阅读

普通使用者先看 [第一次使用](START_HERE.md)。以下命令由 AI 助手按实际路径执行；遇到权限或依赖问题时，用日常语言向使用者解释具体缺项和下一步。

## 安装

**Codex**：在解压目录运行 `python3 install.py`，Windows 可用 `py -3 install.py`。安装到个人 `~/.agents/skills/livestream-editor`，不改其他设置。也可让 Codex 读取本说明并代为执行。新建对话后用 `$livestream-editor`；没有显示时重启客户端。自定义位置用 `--target`。[Codex 官方技能目录说明](https://learn.chatgpt.com/docs/build-skills)

**WorkBuddy**：技能 → 添加技能 → 上传技能，选择发布的 ZIP；不要只上传 `SKILL.md`。若当前客户端未识别包结构，可以解压后让 Agent 读取 `SKILL.md`，在独立工作区运行。支持导入不代表当前版本的音频理解、Python 和外部程序权限已经可用，先运行环境检查。[WorkBuddy 官方导入说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)

**其他 Agent**：需要支持本地文件、脚本执行和 Agent Skills。能读规则不等于能执行所有工具；未验证的客户端先跑自检。

## 一次性环境准备

基础流程需要 Python 3.10+、FFmpeg 和 ffprobe。Python 脚本本身使用标准库；不要把项目包误当成自带 Python 的独立应用。

```text
python3 "<解压或安装目录>/scripts/livestream.py" doctor
python3 "<解压或安装目录>/scripts/livestream.py" self-test
```

请让 Agent 将尖括号替换成实际绝对路径；Windows 把 `python3` 换成 `py -3` 或已有 Python 解释器。自检只使用临时合成音调，不读取你的真实案例。

缺 FFmpeg 时，macOS 已安装 Homebrew 可用 `brew install ffmpeg`；Windows 有 winget 可用 `winget install --id Gyan.FFmpeg -e`。没有包管理器时从 [FFmpeg 官方下载页](https://ffmpeg.org/download.html) 选择本机版本，并将含 ffmpeg、ffprobe 的目录加入 PATH；安装后重新打开 Agent。宿主若自带工具，先检查，避免重复安装。

双人流程首次使用还需独立声学运行时：

```text
python3 "<Skill目录>/scripts/livestream.py" setup-speakers
```

该命令创建隔离环境，下载约 46 MB 的模型及 Python 依赖，不修改系统 Python。声学后端使用 Python 3.10–3.13，推荐 3.12；工具会优先寻找已有的 3.12/3.11，找不到时给出明确说明。可用 `--python "解释器路径"` 指定，或用 `--runtime "目录"` 自定义缓存位置。默认缓存为 macOS/Linux 的 `~/.cache/livestream-editor` 或 Windows 的 `%LOCALAPPDATA%/livestream-editor`。离线机器可事先准备通过哈希校验的模型与对应系统的隔离环境；虚拟环境不可直接跨系统复制。

身份声音锚点必须来自每场真实回放。脚本自动处理全场时间轴和对齐；没有真人或可用音频工具确认时，会保留待核状态。详见 [双人工具说明](references/speaker-tools.md)。

## 素材与输出

个人资料为可选项：运行 `python3 "<Skill目录>/scripts/livestream.py" profile init "<工作区>/我的文稿资料"` 补齐模板，再由 Agent 按 [个人资料说明](references/personalization.md) 协助填写。重复执行只补缺失文件，不覆盖人工内容。`profile check "<资料目录>"` 检查结构与确认依据是否填写，不验证事实真假。

新场次初始化可加 `--profile "<资料目录>"`。系统保存 `_work/个人资料/` 快照与 `个人资料使用记录.json`；未另传 `--people` 时把已确认且有依据的词条带入专名表；已传人物表则逐字保留。案例与偏好供 Agent 读取，脚本不会替用户编造事实或生成文稿。资料后来更新不影响本场快照，审计会拒绝被改过的快照。已有场次不自动改写或附加资料。

三份正文/检查文件完成后运行 `python3 "<Skill目录>/scripts/livestream.py" reading-index "<场次>"`，生成 `outputs/00_从这里开始.md`。此页只提供阅读链接，不代表已经人工核对或可以发送。

原稿使用 UTF-8，格式见 [样例](examples/带时间码原稿.md)。规范输入是独立的“发言人1   00:00”时间码行；`init --transcript "字幕.srt"` 或 `"字幕.vtt"` 也可直接导入常见完整字幕。支持 UTF-8/BOM、UTF-16 BOM、普通样式和 VTT 单声音标签；原始文件逐字节保存到 `inputs/05_平台原始导出.srt` 或 `.vtt`，规范副本放在原稿位置，`_work/字幕导入记录.json` 保留每条原文、原始毫秒、声音标签与映射。规范副本起点向下取整到秒，误差小于 1 秒。同一秒内互不重叠且标签相同（包括均未标注）的字幕，按原顺序逐条换行放进一个时间索引块；转换记录中的 `cues` 保留每条边界，`blocks` 记录该块包含哪些条目。该分组不合并句意、不删字或去重，也不证明是同一人。未提供声音标签的条目仍为“发言人未标注”，须由回放核验；一个块实际跨人时按说话人冲突流程处理。

重叠/滚动字幕、同秒条目的声音标签不同、单条字幕内混合多个声音、注音及 `X-TIMESTAMP-MAP` 分段字幕会明确报错。同秒声音标签不同的情况不能强行并成一个署名块，应保留毫秒与声音边界供核对；其他不支持格式请从原工具另导出互不重叠且与完整回放对齐的转写，不让 AI 临时猜测、删短句或移动时间。随后运行 `doctor --case "<场次>"` 检查回放范围和同秒分组提示；原字幕、规范副本和转换记录发生变化均会阻断统一审计。格式依据：[SubRip 描述](https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml)、[W3C WebVTT](https://www.w3.org/TR/webvtt1/)。

如果已知字幕是转写工具导出的完整逐段原话，例如本地 Whisper 输出，而非滚动字幕，导入时可加 `--subtitle-source full-transcript`。这会原样保留起点有序的重叠条目和重复文字，不自动修正时间或去重；`timing_overlaps` 记录每处精确位置，doctor 会列出需回放核对的时间码。AI 应把这些位置加入待核清单，核对原话与声音边界。来源不明时保持默认模式，不能仅为消除报错选择该选项；起点倒序、同秒声音标签不同等仍拒绝。已有场次不会重新转换或自动迁移。

输入质量线索：`python3 "<Skill目录>/scripts/livestream.py" source-preflight "<场次>"` 只读输出 JSON，列出回放首、中、尾的附近原稿块和疑似长重复。若字幕导入记录经来源核验且含真实起止时间，也会列出30秒及以上的空白候选。候选可由静音或音乐造成，不能直接判成漏稿；普通原稿只有起点，不计算时间空白。每类最多显示前50项，同时报告总数。核对实际语言及静音、音乐附近的识别结果；已有稿件不重新整场转写，疑点按授权局部复核。此命令不写文件、不关闭核验问题，也不代表已回听。

主稿、待核清单、质检报告放在场次的 `outputs/`，证据与回听材料放在 `_work/`。单人还会提供整场 M4A。单人主稿内部保留兼容文件名 `01_公众号可发布稿.md`，人工终审通过后的发送副本为 `01_学员学习分享稿.md`。

Obsidian 不必安装。导出到其他目录使用 `export`；正文及所引用的原文摘录、回听片段一并放入独立阅读包，副本链接指向包内的“核对材料”。不需要复制整场回放或整个工作目录。遇到已有不同内容即停止，避免覆盖人工稿。原始输入、AI 基线、人工终稿分别保留。

## 交接记录

分流先运行 `probe "<场次>"`，按实际核验填写生成的 `_work/分流试听/核验记录.json`，再运行 `route "<场次>" --acoustic-speakers 1 --observations "<场次>/_work/分流试听/核验记录.json"`（双人用 2）。记录须覆盖五个真实位置和不同时间窗口，并绑定当前回放与试听文件。无法判断时使用 `uncertain` 和实际缺口说明；五条不同文字不构成五位置证据。旧分流普通预检保留兼容，正式交接前需真实补核，不自动追认。

回听包支持增量更新：问题、输入未变时保留原编号、音频、结论和签字；新问题追加，改变的问题重新核验，删除的记录归档。正文有修改仍需整体通读和重算最终修改统计。有效内容排除沿用编辑台账的可选字段，详见 [有效内容与保留率](references/content-scope.md)。

发布或发送使用统一 `audit --release` / `audit --share`。P0/P1 记录具体的已回听、已当事人确认、已删除或已保守表达；“已解决”只兼容历史普通检查，不能作为交接依据。保守表达在处理建议中引用正文实际采用的最终原句。已回听项对应当前问题、音频与终审表核验内容；填写实际结论、真实姓名和有效日期。关闭问题时保留原问题行，汇总中的 P0/P1 填未确认数量，避免删掉可追溯记录。

旧场次不批量改状态或重写人工稿。新版指出记录缺口时保留原现场，按实际证据补充，不能替真人填写未发生的回听。

## 验证范围与更新

这是 0.4.1 公开试用版。技术测试与已知限制见 [VALIDATION.md](VALIDATION.md)；不能据安装成功宣称长场次编辑质量或所有客户端兼容。

更新时下载新版并解压，在新版目录运行 `python3 install.py --upgrade`（Windows 用 `py -3 install.py --upgrade`）。旧安装目录，包括人工修改和额外文件，会完整移到 `~/.agents/skill-backups/`，再安装新版；激活失败会恢复旧版。可用 `--backup-dir` 指定技能扫描目录之外的备份位置。普通安装遇到不同版本仍会停止，完全相同的版本可以重复运行。

案例和模型应放在安装目录之外，更新时无需搬动。不要把旧版备份放回技能扫描目录或同时启用来处理同一场素材。

维护者仅从本目录构建发布包：`python3 scripts/livestream.py package --output "<安装目录之外>/livestream-editor-0.4.1.zip"`。白名单由 `release-files.json` 管理，真实案例、缓存、模型及密钥不进入包。完整源码可作为独立 GitHub 仓库内容；本仓库父目录内的私人材料不属于本包。具体步骤见 [PUBLISHING.md](PUBLISHING.md)。

代码与原创规则采用 [MIT](LICENSE)。外部模型和工具分别遵守其原许可，见 [THIRD_PARTY.md](THIRD_PARTY.md)。


## 秒级逐段原稿中的同秒发言

普通Markdown/TXT逐段原稿若时间不倒退、但同一秒有多条发言，可明确使用 `init --transcript-contract ordered-turns`。此选项仅用于原逐段转写，不能改变SRT/VTT合同，也不证明人物身份或精确语音边界。默认导入仍要求严格递增，单人审计仍保持严格递增。

原文件逐字节复制；`_work/原稿逐块索引.json`为每条发言保存独立编号、原时间、标签、原顺序、行号和内容指纹，并由输入来源记录绑定。不得把时间顺延一秒、补造毫秒、合并不同说话人，或删除短回应。来源或索引变化会阻断审计。

`source-index`为各块生成独立原文锚点。同秒块在声音时间轴中保留独立编号和待核冲突；自动检索区间只是寻找相应音频的候选范围，不是该句话的真实起止。即使该范围只检出一个声音，也不自动确认同秒各块身份。每块的实际核对和冲突结论必须独立保留，不能以同一时间或一条确认覆盖多条发言。
