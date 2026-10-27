# 双人声音工具

先在本场 `_work/说话人校准.json` 填两位人物的姓名、角色、默认标签映射及每人两段以上不重叠、累计至少30秒的声音锚点。模板由 `templates --mode dual` 创建。依据必须是清晰自我介绍、可核对画面/口型、物理分轨或真人确认；禁止仅依据原稿标签。确认后更新 `status` 和各段 `review_status`，保留实际依据。

将 `<Skill目录>` 和 `<场次>` 替换成真实绝对路径：

```text
python3 "<Skill目录>/scripts/livestream.py" setup-speakers
python3 "<Skill目录>/scripts/livestream.py" speakers "<场次>"
```

首次联网安装后可以离线处理声音；不上传回放。脚本分块运行声学模型，将每段声音与已核身份锚点比对，再对齐全部原稿块；不将原稿标签作为声学模型输入。固定两人聚类以分流已确认两人为前提，不能拿它证明整场只有两人。

生成 `_work/说话人时间轴.json`、`说话人冲突清单.md`、`说话人待核项.md`。低相似度、短插话、混合讲话及无声学覆盖均进入待核；相似度和差值仅为未经业务标定的保守筛查阈值，不能当作正确率。将待核项合并到输出待核清单，不覆盖既有处理结论，再执行：

```text
python3 "<Skill目录>/scripts/livestream.py" speakers "<场次>" --check
```

预检存在 ERROR 时不写正文。即使没有 ERROR，未关闭冲突也仍需回听，不能凭机器分数改成“真人已确认”。正式发送或发布前使用统一 `audit --share/--release`。

已有时间轴或冲突文件时脚本拒绝覆盖。人物校准或输入变化后，先比较、归档旧三件套和人工结论，再重新生成并核对；不把旧结论自动搬到新音频上。机器运行失败时保留校准及原始素材，修正依赖后可重试。

模型来自 [sherpa-onnx 官方预训练模型说明](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html)，通过 `assets/models.json` 固定下载和文件 SHA-256。默认运行时只下载模型，不将它们装进分发包。无需购买第三方转写服务。
