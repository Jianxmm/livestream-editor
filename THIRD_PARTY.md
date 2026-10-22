# 外部工具与模型

分发 ZIP 不包含 FFmpeg、Python、numpy、sherpa-onnx 或任何模型权重。用户安装的这些组件与本包 MIT 许可分开计算，遵守各自许可。setup-speakers 的外部下载清单、大小和哈希保存在 assets/models.json；它不上传用户音视频。

- Python：见 https://www.python.org/psf/license/ 。
- FFmpeg：不同构建可能采用不同许可，见 https://ffmpeg.org/legal.html 。本包通过命令行调用用户已有程序。
- numpy：见 https://github.com/numpy/numpy/blob/main/LICENSE.txt 。
- sherpa-onnx：见 https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE 。
- segmentation-3.0：转换及模型来源见 https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html 及 https://huggingface.co/pyannote/segmentation-3.0 。
- 3D-Speaker：模型来源见 https://github.com/modelscope/3D-Speaker ，ONNX 发布资产由 sherpa-onnx 官方提供。

不要因本包采用 MIT 就将外部模型改标为 MIT，也不要把本地运行环境和模型缓存直接装入公开包。脚本与测试中的示例人物、对话均为测试用途，不能作为真实人物报道。
