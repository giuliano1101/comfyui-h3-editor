# Windows 本地检查与运行

以下为 2026-11-17 初始环境记录。2026-11-18 已按后续授权升级本机 ComfyUI 至 0.45.0，并在隔离副本实际加载 H3 插件、尝试 GPU 渲染及验证媒体/MCP；生产未安装本插件。最新状态、未通过项与复现步骤以[完整验收矩阵](ACCEPTANCE-20261118.md)为准。

## 已发现环境

| 项目 | 实际记录 |
|---|---|
| 工作副本 | `G:\Codex\AgentClipsDesk\comfyui-h3-editor` |
| 上游基线 | `0d2e0d1fad9218cafdb599902be252f3254b2fa1`，版本 0.9.1 |
| Windows | Windows 10 Pro for Workstations 22H2，19045.6466 |
| 现有 ComfyUI | `G:\ComfyUI_windows_portable\ComfyUI`，版本声明 0.37.0，Git `73c9bad` |
| 测试解释器 | `G:\ComfyUI_windows_portable\python_embeded\python.exe`，Python 3.13.14 |
| Torch / CUDA | 2.13.0+cu130 / 13.0；仅测试环境信息，非 H3 生成认证 |
| 系统内存 | Win32 内存 API 查询 63.7 GiB，总可用内存后续快照为 25.2 GiB；不是 H3 峰值测量 |
| 开发工具 | Git 2.40.0.windows.1，Node 24.22.1 |
| GPU | 两张 NVIDIA GeForce RTX 3090，各 24,576 MiB，驱动 596.49 |
| 开始检查时可用显存 | 分别约 17,249 / 24,326 MiB；仅当时快照，不把两卡显存相加当单卡容量 |
| 磁盘 G | 开始检查时约 1.03 TB 空闲；没有复制或下载大模型 |
| 系统 FFmpeg | 4.0.2，缺少 `xfade`，转场导出实际失败 |
| 隔离测试 FFmpeg | 仓库 `.test-deps` 中 `imageio-ffmpeg==0.6.0` 的 FFmpeg 7.1；转场导出通过 |
| 依赖 | 现有 portable 有 aiohttp、PyAV、numpy、torch、gguf；没有 imageio-ffmpeg，只补装到仓库隔离目录 |

现有启动脚本为 `run_nvidia_gpu.bat`，调用 portable 自带 Python 和 `ComfyUI/main.py --windows-standalone-build`。没有确认当前活动 ComfyUI 进程的实际解释器；CIM/进程盘点受系统访问限制。未发现该安装 `custom_nodes` 下名称匹配 H3/editor 的启用或停用副本，也未发现既有 `output/h3take_projects` 项目；这不替代其他磁盘、额外自定义插件路径或 Registry 来源的完整审计。

检测到 H3 视频/音频 VAE、32B NVFP4 和 8B INT8 文本编码器、pruned INT8 H3 检查点等相关文件；没有下载、加载或修改这些模型。上游要求 ComfyUI **0.43+**，因此现有 **0.37.0 不满足版本门槛**。模型文件存在、两张 3090 和单元测试通过都不能证明真实 H3 可运行。实际生成时的内存余量、完整模型清单、硬盘恢复权限和防火墙策略仍需部署前核验。

## 现在可复现的隔离验证

在仓库根目录用实际 portable 解释器执行，不要默认系统 `python` 是同一个环境：

```powershell
$h3Python = 'G:\ComfyUI_windows_portable\python_embeded\python.exe'
& $h3Python tools\run_tests.py --report docs\acceptance\tests.json
node tests\provider_panel.test.mjs
```

若缺 FFmpeg，先在**仓库隔离目录**安装上游依赖，不写现有环境：

```powershell
& $h3Python -m pip install --target .test-deps --no-deps imageio-ffmpeg==0.6.0
& $h3Python tools\run_tests.py --dependencies .test-deps --report docs\acceptance\tests.json
```

Portable 的 `_pth` 会忽略通常的当前目录/PYTHONPATH；测试入口已显式加入仓库路径。最终实际命令使用了 `--dependencies .test-deps`。受限运行环境若报临时文件/子进程 `PermissionError`，记录错误并通过宿主正常权限运行测试；不要把这类失败算作通过，也不要关闭系统保护或修改现有安装来规避。

需要查看界面时，仅启动测试 harness：

```powershell
& $h3Python -c "import sys,runpy; sys.path[:0]=['.test-deps','.']; sys.argv=['tools/offline_editor.py','--directory','.ui-test','--port','8199']; runpy.run_path('tools/offline_editor.py',run_name='__main__')"
```

访问 `http://127.0.0.1:8199/h3take/editor`。这是使用真实编辑器、项目、导入/导出代码的**隔离测试服务**，不是 ComfyUI：没有模型列表或生成能力，也不会加载任何自定义节点。输入、输出和私有状态都在仓库 `.ui-test`。退出用 Ctrl+C。录音不必授权；可以直接用文本预览。测试服务不需要 provider Key 或 MCP Token。

## 以后部署前的步骤（本次未执行）

1. 由维护者核对实际活动 ComfyUI 的目录、版本、Python、启动参数、额外模型/插件路径、Registry/Git 来源和用户修改。确认只会加载一份 `h3-editor` 或 `comfyui-h3-editor`；不要直接删除现有副本。
2. 先备份实际 `output`、`user`、工作流、启动脚本和已有插件配置到独立目录；核对容量、权限、文件数量、校验和及抽样恢复。可以用不带 `/MIR` 的 `robocopy /E /COPY:DAT /DCOPY:DAT /XJ /R:1 /W:1`，不得覆盖旧备份。备份/恢复方法未在现有环境实跑，不能称为已验证备份。
3. 当前 0.37.0 先由用户决定独立测试安装或升级方案。优先在另一个目录创建符合 0.43+ 的 ComfyUI 测试安装，使用自身 Python 安装上游 requirements；本次没有建立它，也不自动修改当前 portable。
4. 经用户授权后将本分支放到测试安装的单一 `custom_nodes` 目录，默认本机绑定，核对防火墙及日志只加载一份插件。私有 provider 状态在 `user/h3-provider`，不要放在 output。真实配置见 [PROVIDERS.md](../PROVIDERS.md)。默认无付费服务、MCP 无项目权限、无语音外发。
5. 先用合成或用户许可素材验证导入、移动/裁剪/分割、转场、音轨、保存重载、历史恢复和导出播放，再进行模型能力检查。只有版本/资源/模型许可满足且用户明确同意本地算力消耗后才生成一条 H3。真实供应商测试则另行明确模型、输入、报价、预算和外发数据，不能用“已配置 Key”替代授权。
6. 回滚时先停止测试安装，恢复经过验证的配置/插件备份；保留项目媒体和历史。provider 任务记录不得随代码回滚清空；先到供应商账户核对未决任务，恢复项目不撤销外部计费。没有实际替换安装，故本次不需要执行恢复。

## 已验证与未验证

完整 Python 回归、前端确认流程、本机 HTTP/MCP、合成视频导入/新 take/导出/解码有独立记录。浏览器新建项目、镜头编辑/保存重载、模拟任务、预览后新增镜头、素材导入结果、分割、6 帧 dissolve、1.75 秒 MP4 导出和浏览器播放已检查；1440、768、390 宽度检查了新任务面板。窄屏旧编辑器主界面仍是桌面布局，不承诺整套手机剪辑体验。

真实 ComfyUI 插件加载及 websocket 协作、GPU H3、付费 API、真实中转、麦克风硬件、自动转写、语音聊天、完整浏览器音轨交互和独立安全评审未验证或未实现，详见交付矩阵。没有部署到当前用户环境。
