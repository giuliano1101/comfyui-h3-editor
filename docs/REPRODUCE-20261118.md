# Windows 隔离验收复现

全部命令在仓库 PowerShell 执行。只用 `.acceptance` 的源码、用户、输入、输出和新增依赖，不复制生产模型，不覆盖生产安装。已有同名测试项目时脚本会停止，复测应使用新的隔离 checkout；不要删除原工程清空证据。先确认 8190 属于本次隔离服务器，不把脚本指向生产端口。

## 1. 测试与隔离目录

实际环境：Windows、portable Python 3.13.14、ComfyUI 0.45.0、torch 2.13.0+cu130；双 RTX 3090，每卡 24 GB；FFmpeg 7.1 来自仓库 `.test-deps` 的 imageio-ffmpeg 0.6.0。源码与数据隔离，基础运行库通过只读 `site-packages` 路径复用生产 Python，所有入口 `-B`，没有在生产 Python 安装新增包。

```powershell
& 'G:/ComfyUI_windows_portable/python_embeded/python.exe' -B tools/run_tests.py --dependencies .test-deps --report docs/acceptance/tests-progress.json
node tests/provider_panel.test.mjs
node tests/ui_zh.test.mjs
& 'G:/ComfyUI_windows_portable/python_embeded/python.exe' -B tools/acceptance/prepare.py
& ./tools/start-acceptance.ps1
```

最后一条需要先建立指定测试项目；首次启动不传 MCP 参数，创建/导入测试项目后，再停掉隔离服务并按命令启用有限项目权限。默认不允许 Agent 编辑。端口占用会拒绝重复启动。`prepare.py` 只从现有 Git 安装 clone 隔离源码、复制单份插件与 GGUF 节点源码，并配置只读模型路径；不启动、不联网下载模型、不升级生产依赖。

实际启动日志：`.acceptance/startup.log`；页面 `http://127.0.0.1:8190/h3take/editor`。生产 8188 不被占用或替换。停止时 Ctrl+C，只停止该隔离进程。

## 2. 本机媒体、字幕和归档

```powershell
& '.acceptance/python/python.exe' -B tools/acceptance/editor_media.py
& '.acceptance/python/python.exe' -B tools/acceptance/composition.py
```

首个脚本新建 `H3_综合验收_20261118`，生成纯合成 4 秒测试图与 chirp 音频，导入原时间线；对齐偏移半秒的音轨（预期 12 帧），审批导入 SRT，导出、解码、检查中文字幕像素，再以 `?keep=all` 归档并恢复为新工程。最后提交真实 ComfyUI `EmptyImage → SaveImage` 工作流，读取 history 和媒体回传。该工作流不是 H3 生成。

第二个脚本只操作恢复出的 `_2` 工程：第 48 帧分割、第二段提前 6 帧并叠化，混合字幕/音轨导出；预期 640×352、89 帧、一个音频流。临时加入未生成特效，验证导出 HTTP 409 后明确移除该测试条目，保留原工程。

也可在“剪辑工具”粘贴 SRT/VTT 预览导入、按帧编辑文字/时间、整理音轨、预览波形对齐和工程检查。变速片段、低相似度、静音、超过四条同时重叠的音轨会拒绝自动对齐/整理，不静默移动。归档恢复仍使用原项目菜单。检查与听感分开：自动相关性、解码和字幕帧图不等于人工听感/口型验收。

## 3. 真实 MCP 客户端

本轮使用独立安装的官方 Python MCP SDK 2.3.0，不更改 Codex 全局 MCP 配置。安装在 `.acceptance/client-packages`，专用 `.acceptance/client-python` 的 `_pth` 指向该目录及只读基础依赖；脚本 `site.addsitedir` 处理 Windows pywin32 的 `.pth`。令牌在被 Git 忽略、当前用户 ACL 限制的 `.acceptance/mcp-private.json`，从环境传给 stdio 进程，不打印。首次建立私有令牌可在本机运行：

```powershell
& '.acceptance/python/python.exe' -B -c "import json,secrets,pathlib; p=pathlib.Path('.acceptance/mcp-private.json'); assert not p.exists(); p.write_text(json.dumps({'write':secrets.token_urlsafe(36),'read':secrets.token_urlsafe(36)}))"
& '.acceptance/python/python.exe' -B -m pip install --target .acceptance/client-packages mcp==2.3.0
# 停止首次隔离服务后，为已建立的测试项目启用受限 Agent 验收：
& ./tools/start-acceptance.ps1 -McpProject 'H3_综合验收_20261118' -AllowMcpEdits
```

令牌文件应收紧为当前用户可读，勿复制进公开项目/日志。专用解释器可按 `.acceptance/python` 的标准库与 DLL 布局复制小型运行时，再把 `_pth` 的私有包目录改为 `../client-packages`；不对生产解释器运行安装。

```powershell
& '.acceptance/client-python/python.exe' -B tools/acceptance/mcp_client.py inspect
& '.acceptance/client-python/python.exe' -B tools/acceptance/mcp_client.py edit
# 随后分别运行 generation、cancel、restore、export 阶段。
# 两客户端同时执行的冲突验收（两个提案都需分别在 UI 核对批准）：
& '.acceptance/client-python/python.exe' -B tools/acceptance/mcp_client.py concurrent
```

每个修改阶段先验证未批准执行被拒绝，再写 `mcp-ready-阶段.json` 等待最多 240 秒。浏览器打开“供应商与 Agent → 查看请求”，核对内容与费用、勾选并批准；**只有看到“查看已批准请求”后**，在本地写 `.acceptance/mcp-continue-阶段.txt` 通知测试客户端继续。信号文件不是授权，服务端仍校验真实审批。不要预先写信号文件；同名信号已存在则中止，不自动重复提交。

实际步骤覆盖只读工具隐藏、越界项目、未审批、重复执行、旧 revision 冲突、改名、零费用模拟生成、取消、恢复和导出。Agent 不打开浏览器自批，也不获得浏览器 Cookie。测试操作者在 UI 审批属于本次验收授权；收费服务另按具体授权执行。首次测试客户端字段与信号顺序错误均在执行前被拒绝，核对后才用同一批准请求继续，未重放未知提交。

并发阶段为同一镜头提出两个保持原名称不变的提案。两个 UI 审批完成后才写 `mcp-continue-concurrent.txt`；两个独立 stdio 客户端同时执行，实测只有一个提交（revision 9→10），另一个 409，名称未变。共用同一令牌的客户端属于同一认证身份。最终部署还可运行 `tools/acceptance/final_smoke.py`，验证两个独立导出、沿用面板提示词的明确时长预览和合成 WebM 转写；所有收费服务必须保持关闭。

## 4. 本地语音和对话

可选 worker：`.acceptance/asr-python` + `.acceptance/asr-packages`；实测 faster-whisper 1.2.1、CTranslate2 4.9.0、ONNX Runtime 1.32.0、基础 PyAV/numpy。通用可选依赖见 `requirements-asr.txt`，始终安装到专用目录。ASR 权重 tiny/base 的固定 revision、文件大小与 SHA-256 见 `speech-runtime.json` / `speech-base-runtime.json`，当前文件已经在隔离目录，不再下载。

意图运行器为 llama.cpp 官方 CPU 构建 b12359，压缩包 SHA-256 `837c1655e87df3b66989f4be33a792a1399c202477271407ae9a7f19afa52039cac8`。只读使用本机既有 Qwen GGUF：

```powershell
& '.acceptance/llama/llama-server.exe' -m 'G:/ComfyUI_windows_portable/ComfyUI/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf' --host 127.0.0.1 --port 8191 --alias h3-editing-intent -c 4096 -np 1 -t 4 -tb 4 --no-webui --log-disable
& '.acceptance/python/python.exe' -B tools/acceptance/intent_probe.py
& '.acceptance/python/python.exe' -B tools/acceptance/speech_safety.py
```

语音安全脚本只使用 `.acceptance/command-zh.wav` 合成语音（Windows Huihui 本机生成的“添加一个镜头，名称是纸船”），不是麦克风。它验证未同意拒绝、真实离线转写、取消 worker、临时文件清理和缺失音频阻止导出。tiny/base 转写均有错字，质量不算通过。

浏览器聊天验收：输入“把合成测试片段改名为纸船片段”，关闭预览后输入“不是纸船片段，改名为清晨片段”，核对新预览；取消草稿，再输入明确的新指令并确认。实际 revision 8→9，仅最终确认后改名，截图与 `actual-chat.json` 记录。

真实麦克风已获 10 秒本机测试授权，但本轮内置浏览器权限请求未返回，Chrome 自动化连接超时，未录到音频。后续应在可正常使用麦克风的本机浏览器打开页面，用户准备好后点录音，10 秒自动停止，勾选仅本机转写并人工校正，再走同一预览/确认。当前不能标麦克风或完整语音交互通过。关闭/丢弃即停止音轨；迟到授权释放另有回归测试。

## 5. GPU 与收费测试

```powershell
& '.acceptance/python/python.exe' -B tools/acceptance/mapped_loading.py
# 只有内存/模型许可条件已解决时才重新运行：
& '.acceptance/python/python.exe' -B tools/acceptance/gpu_render.py --run
```

映射测试使用 2 MiB 合成权重，先加载 CUDA DLL，再初始化 comfy-aimdo；实际补测 1 项通过。它不证明 H3 渲染通过。H3 原始尝试：第二张 3090、256×256、2 秒/56 帧、10 步、seed 20261118，无 LoRA；57.6 秒时系统可用内存最低 3479 MiB，连续低于 4 GiB后取消，未产生 take。重测脚本最多 900 秒，低内存取消，另写时间戳记录，不覆盖原证据。不要把两张 24 GB 显卡视作单卡 48 GB，也不要为通过验收停止用户其他应用。

真实收费授权仅覆盖方舟官方 `doubao-seedance-2-5-flash-261115`、480p、4 秒、关闭音频，文字“纸船在清澈水面上轻轻漂浮，白天，固定镜头，不含人物或文字”，一次且总预算人民币 2 元，不重试、不充值。当前无 Key/账户报价，未调用。先由用户运行本地隐藏输入脚本配置 Key，确认账户报价不超预算，再把 `PROVIDERS.md` 示例改为启用；总预算与单次预留均设 200 分，模型能力仅允许该次规格。执行完立刻禁用真实服务。下载主机在结果返回后精确审核，不以通配或绕过 SSRF 完成下载。不得用本段替代新的供应商/素材/追加费用授权。
