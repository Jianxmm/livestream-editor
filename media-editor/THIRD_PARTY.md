# 外部工具与许可

文稿整理通过命令行调用使用者自己安装的工具，不在本包中分发这些工具或模型。它们的许可、系统要求和更新应以各自项目及具体发行版本为准：

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：按需获取用户有权限访问的音视频或字幕。
- [FFmpeg](https://ffmpeg.org/legal.html)：探测和处理音视频；不同构建的许可可能不同。
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)：可选本地转写工具；语音模型需单独准备，并遵守该模型自己的使用条件。

内置直播核心的依赖、模型来源与许可另见 [.livestream-core/THIRD_PARTY.md](.livestream-core/THIRD_PARTY.md)。

设计方法参考[音视频转录与长文整理](https://scys.com/skillhub/skills/skl_media-transcriber?from=copy_link&v=1.1.0)；本包不包含该 Skill 的源码或正文。外部作品的权利归其权利人，不能因本包提供 MIT 许可而推断外部模型、素材或其他 Skill 的许可。
