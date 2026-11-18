# 本地自用许可与服务核查

2026-11-18；用户确认仅本人本地使用、实际地点中国大陆。范围为本轮编辑器代码、隔离运行依赖和实际用到/拟接入的模型与服务，不代表对生产目录全部模型或所有潜在中转的许可保证。未打包分发运行库和模型，未部署公网服务。

| 层次 | 来源与核对 | 本轮结论 / 限制 |
|---|---|---|
| H3 Editor | 仓库 `LICENSE`：GPL-3.0；`NOTICE` 保存改造与来源说明 | 修改源码沿用 GPL-3.0，修改日期及来源已补充；模型许可另算 |
| ComfyUI | 隔离与生产均为 0.45.0；源码 GPL-3.0-or-later | 仅本机运行；生产安装未添加本插件 |
| 内嵌代码 | `NOTICE`、`LICENSES/`：Turbo Apache-2.0、HyperFlow GPL-3.0、GGUF Apache-2.0、ClipProj 代码 MIT、universal-llm-client MIT | 原声明保留；没有复用 MakeMyClip 源码、素材或时间线 |
| Python / 核心库 | Python PSF；aiohttp、numpy、Pillow、PyAV、torch 等实际 wheel 元数据 | 具体版本及复合许可见[依赖清单](acceptance/20261118/dependency-licenses.json)，不能把 torch/numpy 的全部组成简写为单一许可 |
| 视频工具 | imageio-ffmpeg 0.6.0 BSD-2-Clause；实际 FFmpeg 7.1 带 `--enable-gpl --enable-version3`、libx264、libass | 包装器与二进制许可不同；本轮只本机运行，未将二进制提交。再分发须分别满足[FFmpeg 许可](https://ffmpeg.org/legal.html)及各组成条件 |
| 可选语音 / Agent 库 | faster-whisper 1.2.1、CTranslate2 4.9.0、ONNX Runtime 1.32.0、MCP SDK 2.3.0 等 | 新增依赖仅在隔离目录；完整元数据清单含传递依赖。gguf 0.19.0 未提供 License 字段，但安装包 `dist-info/licenses/LICENSE` 实际为 MIT，单独核对，未把空字段视为通过 |
| 本地 H3 权重 / VAE / 编码器 | [MiniMax H3 官方许可](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) | 中国大陆不在排除地区；仍受用途、输出、衍生与再分发条款约束。欧盟、英国、韩国、美国被排除，本地自用也不豁免。没有作全球可用或商用授权承诺 |
| 小编码器投影 | [ClipProj 模型页](https://huggingface.co/NicoLab28/ClipProj-MiniMax-H3)、仓库 `docs/models.md` | 代码 MIT 不等于投影权重许可；作者对权重法律状态有保留。本轮没有下载投影以绕过内存问题，亦未下载额外 H3 大模型 |
| 本地意图模型 | 既有 Qwen3-4B-Instruct-2507-Q4_K_M.gguf 元数据为 Apache-2.0，来源指向 Qwen 基模 / unsloth 量化 | [Qwen 官方 LICENSE](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/blob/main/LICENSE)已核对；仅只读使用原文件，不重新分发模型 |
| 语音权重与运行器 | [Systran faster-whisper tiny](https://huggingface.co/Systran/faster-whisper-tiny)、[base](https://huggingface.co/Systran/faster-whisper-base)：MIT；[llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE)：MIT | 仅隔离下载公开的小型 ASR 权重、官方 CPU 运行器，固定 revision/hash 见证据；没有云端语音调用。Windows 字体和系统语音依本机系统许可，不打包字体 |
| 火山方舟官方 API | [平台专用条款](https://docs.volcengine.com/docs/ark/volcengine-ark-platform-terms?lang=zh)、[创建任务](https://console.volcengine.com/ark/region%3Acn-beijing/docs/create-video-generation-task-api)、[价格](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh) | 与本地 H3 权重许可分开。个人本地使用不能代替账号开通、模型资格、内容权利和账户计费确认。已授权单次纸船文字测试、预算 2 元；尚缺本机 Key/账户报价，未调用。没有以中转价格替代官方报价 |
| MiniMax 官方 API | [平台服务条款](https://platform.minimax.io/protocol/terms-of-service)、[付费协议](https://platform.minimax.io/protocol/paid-agreement) | 对输入权利、账号使用及付费另有要求；当前适配国际 `api.minimax.io`，账号可用地域、数据位置与价格未实测，默认关闭；不因自用就推定可调用 |
| 兼容中转 | 用户尚未选择 | 无法核查未知经营主体、数据保留、模型授权与结算；仅交付受限协议框架，不给未知中转通用或合规结论 |

官方价格/条款页面本轮有访问超时；可检索到的官方条款摘要用于定位要求，不据此确认用户账户资格或最终价格。运行前应以账户实际展示为准，未经核对的服务保持关闭。以上是工程交付中的来源核查，自查不冒充独立法律意见或完整软件供应链审计。

本轮真实传出给 Jev 的内容仅为合成验收项目的必要控件名称与目标；8 次新请求按 usage 估算 0.000142422 美元，账单未核对。此前中文界面任务的 0.002688 美元未知预留保守结转保留；无自动重试、没有重置累计预算，见[费用记录](acceptance/20261118/jev-accounting.json)。真实视频服务仍为 0 次调用。
