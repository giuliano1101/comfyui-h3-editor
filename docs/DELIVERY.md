# Windows 改造交付记录

日期：2026-11-17。目标仓库：[giuliano1101/comfyui-h3-editor](https://github.com/giuliano1101/comfyui-h3-editor)。GitHub 连接身份已核对为 `giuliano1101`，仓库返回 admin/push 权限。使用现有仓库，不再另建 fork，不推送 `igorls` 上游。

起始本地树干净，HEAD 为任务书指定的 `0d2e0d1fad9218cafdb599902be252f3254b2fa1`，声明版本 0.9.1；目标远端 `main` 同为该基线。改造在 `feature/provider-foundation` 分支，交付提交以该分支/草稿 PR 的实际 head 为准。未修改 Registry 发布身份，没有发版、部署或合并主分支。

## 实际完成范围

| 阶段 / 能力 | 状态及证据 |
|---|---|
| 原型审查 | ZIP 只有 `provider_jobs.py`、`tests/test_provider_jobs.py`、`PROVIDERS.md`；无完整编辑器。按用户本轮要求仅选择性复用思路，没有将其作为底座或直接覆盖同名用户文件 |
| A：任务基础与模拟 | 已接入编辑器；SQLite、幂等、合法状态/终态、超时、取消竞态、配额、预算预留、重启恢复/未知提交；模拟不联网、不产视频、不收费 |
| A：真实协议 | MiniMax v1 Hailuo 两个模型的文生视频协议已实现并做离线/本机 HTTP 契约测试；默认关闭，真实认证、计费、生成、CDN 下载未验证。其他 API、新 H3 v2、图生/参考/音频未实现 |
| A：兼容中转 | 只允许管理员明确选协议和精确 HTTPS 主机；兼容性/费用未验证，未配置或测试真实中转 |
| A：结果导入 | 私有暂存、大小/媒体验证、受控路径、新 take、前后 revision 检查、保留原 take/历史；合成 MP4 测试通过 |
| B：MCP | stdio 2025-06-18，默认只读、项目白名单、独立 Token、严格 schema；编辑/批量/恢复/导入/导出/本地渲染取消须浏览器一次性批准；同一 store/锁/时间线。未安装到用户 Agent 的实际 MCP 配置 |
| B：协作冲突 | 本机 HTTP 测试确认过期 revision 为 409、批量不部分提交、审批不能重放、历史恢复新 revision 并保留 take；真实 ComfyUI websocket 协作未验证 |
| C：语音基础 | 可选本地录音控件、30 秒/8 MiB 上限、停止/丢弃和清晰保留说明；文本/粘贴稿预览再经同一桥新增镜头。麦克风硬件未实测；没有自动转写、语音聊天或网络音频服务 |
| C：剪辑增强设计 | 上游已有音轨、音量/裁剪、VFX 字幕/标题、速度/转场、批量 ops。优先补字幕时间码导入与编辑预览，再做音频整理，最后才评估转写/语音意图；不引入第二套编辑器，不承诺未实现效果 |
| Windows 安装 / H3 | 现有 0.37.0 低于上游 0.43+，未升级/部署/启动生成。相关模型只盘点，不下载或修改 |
| 安全审查 | 已做实现自查及攻击性测试；独立安全评审尚未完成，真实供应商保持默认禁用 |

## 修改文件

- `provider_jobs.py`：任务契约、状态、SQLite 持久化、幂等、限额和预算；适配器 I/O 不在全局锁内等待。
- `provider_adapters.py` / `provider_transport.py`：MiniMax v1 与显式 relay、按组合报价、仅服务端凭据、DNS/peer 固定、TLS、限流及有界下载。
- `provider_media.py`：私有暂存、实际媒体探测、转码、新 take、冲突和失败清理。
- `editor_security.py` / `editor_bridge.py` / `provider_api.py`：本机会话、同源/CSRF、MCP 身份/权限、一次性审批、同一项目机制。
- `editor_api.py` / `editor/`：现有路由挂接安全层，提取固定快照导出入口，增加任务/审批面板和本地语音笔记控件；取消媒体子进程会结束进程。
- `h3lora.py`：Windows LoRA 张量持有自己的存储，释放源文件映射。Windows 加载时会有一份 LoRA CPU 拷贝；没有复制完整 HyperFlow 检查点。
- `tests/test_h3lora.py`：一个上游测试把仍在使用的映射检查点提前删除，改为测试结束后清理，保留全部数值断言；其余新增测试见 `tests/test_provider*`、`tests/test_editor*`、`tests/test_h3_mcp.py`、`tests/provider_panel.test.mjs`。
- `tools/h3_mcp.py` / `tools/offline_editor.py` / `tools/run_tests.py`：MCP、隔离 UI harness 与 Windows 测试入口。原 `skills/h3-editor/scripts/h3.py` 通过内存会话继续支持可信本地 CLI。
- `PROVIDERS.md`、示例配置、Windows/安全/交付文档、README/NOTICE 和截图；无 Key、模型、真实素材、数据库或测试安装包提交。

## 测试结果

最终命令（仓库根目录）：

```powershell
& 'G:\ComfyUI_windows_portable\python_embeded\python.exe' tools/run_tests.py --dependencies .test-deps --report docs/acceptance/tests.json
node tests/provider_panel.test.mjs
```

Python **562 通过，0 失败，0 错误，0 跳过**；Node 前端确认流程 **4 通过，0 失败**。[机器可读计数](acceptance/tests.json)。无密钥或私有输入出现在测试报告。

基线真实记录：使用实际 portable Python 运行上游 497 项，491 通过、6 个 Windows 文件锁清理错误；5 个来自 LoRA 文件存储映射，一个来自仍持有映射模型的上游测试提前清理。已分别修复；没有把缺失依赖/权限异常标记为通过。早期系统 Python/受限沙箱运行存在导入/临时文件权限错误，未作为最终验收。最终测试使用正常宿主权限和工作区临时目录。GGUF 随机量化夹具发出数值 RuntimeWarning，测试断言通过；这不是 H3 实际渲染质量验证。

真实 HTTP 使用 127.0.0.1 的 mock 服务，覆盖提交/查询/取文件/无凭据下载，以及慢响应总超时；没有访问供应商公网。安全测试还覆盖地址/DNS/重定向、计费确认、恢复、异常脱敏和 revision 竞争。媒体测试使用完全合成的 48 帧短视频，实际转换、导入、导出并用 PyAV 解码。

浏览器实际检查：

- 新建 `Windows acceptance` 测试项目，添加镜头、写入文本、保存重载；费用/内容预览后运行模拟，确认成功且无媒体；文本预览确认后增加第二个镜头。
- 官方供应商显示未配置/默认关闭/真实 API 未验证；1080P 的时长下拉框只有 6 秒。
- 检查任务面板 1440、768、390 宽度，恢复浏览器原尺寸。已有桌面编辑器的全套窄屏布局没有重做。
- 创建 `Media acceptance`，以合成视频验证导入后的镜头、12 帧处分割、6 帧 dissolve 和保存重载。最初沙箱禁止 FFmpeg 子进程，已确认失败且项目未导入，再在正常权限下通过同一 HTTP/CLI 完成导入；浏览器文件选择动作尝试过，但不把第一次上传失败记为成功。
- 系统 FFmpeg 4.0.2 缺 `xfade`，转场导出实际失败；仅在仓库 `.test-deps` 安装上游要求的 imageio-ffmpeg 0.6.0（FFmpeg 7.1）后导出成功。
- 浏览器导出的 `Media_acceptance_001.mp4`：640×352、1.75 秒、readyState 4、媒体 error 为空，播放到末尾；合成素材没有需要人工判断的对白/音乐，不声明听感验收。

截图：[任务面板](acceptance/provider-desktop.png)、[真实服务默认关闭](acceptance/provider-disabled.png)、[平板](acceptance/provider-tablet.png)、[窄屏](acceptance/provider-mobile.png)、[导出成功](acceptance/export-success.png)、[成片播放](acceptance/export-playback.png)。截图是本轮隔离测试的证据；后续补充的预算显示和审批文案以代码为准。

UI 采用现有编辑器的色彩、字体和时间线。`design-taste-frontend` 抽取 `true-black-flat`（seed 994570274），保留现有品牌，仅借用按明度分层的做法，没有替换旧布局。面板首先回答“为哪个镜头、用哪个服务、花多少费用、目前是什么结果”；模拟项目和纸船文本都是测试数据。Impeccable 静态检测执行过，报告旧动态预览图缺初始 src，以及根路由样式引用不能在静态扫描中解析导致的字号警告；没有把这些既有/解析限制说成浏览器错误。实际截图已检查。没有启动另一套 headless 浏览器检查器；交互和视觉检查走本机 Chrome/CUA。Jev UI 状态已检查，因本任务没有实验收费授权，界面由 Codex 接手，没有发送 Jev 付费请求。

## 许可证和环境保护

保留 GPL-3.0、NOTICE 及 Apache-2.0/MIT/GPL 的原有归属；NOTICE 新增修改说明。无新增运行时第三方依赖，无 MakeMyClip 复制，无模型或服务授权推定。隔离 FFmpeg 仅用于测试且不随分支分发；未来打包须核查该构建 GPL 与来源义务。[完整安全/许可说明](SECURITY-PROVIDERS.md)。

现有 ComfyUI 的 output、user、工作流、模型、插件、启动脚本和系统 FFmpeg 均未修改。未部署，因此没有对真实素材做破坏性回归，也没有执行现有项目备份/恢复；部署前备份和回滚步骤见 [Windows 文档](WINDOWS.md)。测试安装和媒体留在被 Git 忽略的 `.test-deps`、`.ui-test`、`.test-tmp`，测试服务在交付前停止。

## 尚未完成的验收门槛

1. 独立安全审查和维护者代码评审；草稿 PR 不代表生产可用或已经批准合并。
2. 满足版本门槛的隔离 ComfyUI 安装、真实插件加载、同机用户/Agent websocket 协作、音轨浏览器交互和 GPU H3 实测。
3. 用户选择服务后，另行明确供应商/模型/输入/预算/费用，才可做真实 API、认证和下载测试；没有可靠报价或 CDN 白名单时继续禁用。
4. 自动语音转写/聊天及其服务、隐私/保留策略；麦克风硬件实测。当前只有可选的本地录音基础和文本预览，不能称为完整语音聊天。
