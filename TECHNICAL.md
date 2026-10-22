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

原稿使用 UTF-8，格式见 [样例](examples/带时间码原稿.md)。目前规范输入是独立的“发言人1   00:00”时间码行；SRT/VTT 等格式先让 Agent 转成单独副本并核对，不覆盖原始导出。

主稿、待核清单、质检报告放在场次的 `outputs/`，证据与回听材料放在 `_work/`。单人还会提供整场 M4A。单人主稿内部保留兼容文件名 `01_公众号可发布稿.md`，人工终审通过后的发送副本为 `01_学员学习分享稿.md`。

Obsidian 不必安装。导出到其他目录使用 `export`，遇到已有不同内容即停止，避免覆盖人工稿。原始输入、AI 基线、人工终稿分别保留。

## 交接记录

发布或发送使用统一 `audit --release` / `audit --share`。P0/P1 记录具体的已回听、已当事人确认、已删除或已保守表达；“已解决”只兼容历史普通检查，不能作为交接依据。保守表达在处理建议中引用正文实际采用的最终原句。已回听项对应当前问题、音频与终审表核验内容；填写实际结论、真实姓名和有效日期。关闭问题时保留原问题行，汇总中的 P0/P1 填未确认数量，避免删掉可追溯记录。

旧场次不批量改状态或重写人工稿。新版指出记录缺口时保留原现场，按实际证据补充，不能替真人填写未发生的回听。

## 验证范围与更新

这是 0.2.1 公开试用版。技术测试与已知限制见 [VALIDATION.md](VALIDATION.md)；不能据安装成功宣称长场次编辑质量或所有客户端兼容。

更新时下载新版并解压，在新版目录运行 `python3 install.py --upgrade`（Windows 用 `py -3 install.py --upgrade`）。旧安装目录，包括人工修改和额外文件，会完整移到 `~/.agents/skill-backups/`，再安装新版；激活失败会恢复旧版。可用 `--backup-dir` 指定技能扫描目录之外的备份位置。普通安装遇到不同版本仍会停止，完全相同的版本可以重复运行。

案例和模型应放在安装目录之外，更新时无需搬动。不要把旧版备份放回技能扫描目录或同时启用来处理同一场素材。

维护者仅从本目录构建发布包：`python3 scripts/livestream.py package --output "<安装目录之外>/livestream-editor-0.2.1.zip"`。白名单由 `release-files.json` 管理，真实案例、缓存、模型及密钥不进入包。完整源码可作为独立 GitHub 仓库内容；本仓库父目录内的私人材料不属于本包。具体步骤见 [PUBLISHING.md](PUBLISHING.md)。

代码与原创规则采用 [MIT](LICENSE)。外部模型和工具分别遵守其原许可，见 [THIRD_PARTY.md](THIRD_PARTY.md)。
