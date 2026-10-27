# 维护与 GitHub 分发

本目录就是可独立发布的源码根目录。对外发布时使用一个只含本目录内容的新仓库，不要把父项目整体上传。

## 推荐的交付方式

1. GitHub 仓库存放本目录内的源码、说明、模板、测试与 MIT 许可。
2. GitHub Releases 上传 `scripts/livestream.py package` 生成的 ZIP 和 SHA-256 文件，作为安装包下载入口。版本号同步 `VERSION` 与 Release 标题。
3. 新手优先下载 Release ZIP；GitHub 自动生成的 Source code ZIP 只用于源码获取，它的外层目录名可能不同。
4. Codex 用户解压后让 Agent 按 README 运行安装器；WorkBuddy 用户按客户端的“上传技能”导入 Release ZIP。尚未实测的客户端先运行环境检查和 self-test。

## 发布前的最低检查

- 更新 README、VALIDATION 与 VERSION，明确已经实测的平台、输入范围和仍需人工核验的环节。
- `python3 scripts/livestream.py self-test` 和 Skill 格式检查通过。
- 在全新目录从打包 ZIP 安装并运行 self-test，核对不依赖源码父目录。
- 检查白名单 `release-files.json`；新增模板或脚本时显式加入。真实案例、媒体、模型、账号、缓存不加入仓库和 ZIP。
- 只发布 `assets/profile/` 中的空白模板与个人资料使用说明，不把使用者填写的“我的文稿资料”或本地旧版归档加入白名单。
- 从源码根目录运行 `python3 scripts/livestream.py package --output "<根目录之外>/livestream-editor-版本.zip"`；同名文件存在时换新路径，避免覆盖正在分发的版本。
- 使用系统哈希工具或 Python 的 hashlib 计算 ZIP 的 SHA-256，随 Release 提供。

公开页面应写明“一次性准备 Python、FFmpeg，双人首次安装本地模型；需要本地回放与带时间码原稿，保留真人终审”。不要写成零依赖软件、自动发布工具或已替代人工团队。

维护者已授权以公开试用版分发。真实长场次验收、目标客户端体验和人工评审在使用中持续补充；这些结果用于判断质量和效率，不能因公开发布就宣称已替代人工团队。文稿正式发送仍保留真人终审。
