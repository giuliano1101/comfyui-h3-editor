# H3 Editor 供应商、语音与 Agent

2026-11-18。H3 Editor 与原时间线继续负责项目、镜头、素材版本、剪辑、历史和导出。本地 H3 保留原 ComfyUI 工作流；外部结果先暂存，再经确认导入为新 take。MakeMyClip 未作为编辑器或时间线依赖。

## 协议与模型能力

下表为**本项目实现的子集**，不代表供应商全部能力或真实 API 验收通过。当前测试状态见[验收矩阵](docs/ACCEPTANCE-20261118.md)。

| 协议 | 模型 | 文生 | 首帧图生 | 图片参考 | 时长 / 分辨率 | 取消 |
|---|---|---|---|---|---|---|
| 原本地 H3 工作流 | 已安装 H3 配置 | 原有能力 | 原有能力 | 原镜头/角色输入 | 原项目设置，受硬件限制 | 原 ComfyUI 取消 |
| simulation-v1 | simulation | 仅验证流程，无视频 | 无 | 无 | 1–10 秒测试值 | 本地模拟取消 |
| minimax-video-v1 | MiniMax-Hailuo-2.3、MiniMax-Hailuo-02 | 支持 | 一张 | 无多图参考 | 768P：6/10 秒；1080P：6 秒 | 未建立官方契约，禁用 |
| ark-video-v3 | doubao-seedance-2-5-260628 | 支持 | 一张 | 1–4 张 | 4–30 秒；480p/720p/1080p/4k | 自动 DELETE 禁用 |
| ark-video-v3 | doubao-seedance-2-5-flash-261115 | 支持 | 一张 | 1–4 张 | 4–30 秒；480p/720p | 自动 DELETE 禁用 |
| ark-video-v3 | doubao-seedance-2-0-260128 | 支持 | 一张 | 1–4 张 | 4–15 秒；480p/720p/1080p | 自动 DELETE 禁用 |

方舟关闭 `generate_audio`，保留水印。当前不接受视频/音频参考、任意素材 URL、回调、自动时长或视频编辑/延长。MiniMax 实现首帧输入，不传未实现的音频、尾帧或画幅参数。首帧模式不能称为通用参考接口。

图片只接受当前项目目录中的首帧、尾帧或角色首图；首帧模式把所选图片作为首帧。服务端校验 PNG/JPEG/WebP、短边 >300、长边 ≤6000、宽高比 0.4–2.5、每张 ≤4 MiB/合计 ≤8 MiB。审批显示文件名、大小和 SHA-256；执行前重新检查。换图、改价或 revision 改变均拒绝提交。base64 只在请求内存，不写任务数据库。引用其他项目的角色图片需先导入本项目目录。

## 官方契约

核对日期 2026-11-18。账户模型开放、价格及配额仍需实际确认。

- [MiniMax 文生](https://platform.minimax.io/docs/api-reference/video-generation-t2v)、[首帧图生](https://platform.minimax.io/docs/api-reference/video-generation-i2v)：`POST /v1/video_generation`，关闭提示词优化；`GET /v1/query/video_generation` 查询，`GET /v1/files/retrieve` 刷新下载地址。未实现 H3 v2 云端协议。
- [方舟创建任务](https://console.volcengine.com/ark/region%3Acn-beijing/docs/create-video-generation-task-api)：`POST /api/v3/contents/generations/tasks`，Bearer 认证；审核后的图片作为 data URL，角色为 `first_frame` / `reference_image`；参考任务类型为 `auto`。
- [方舟查询](https://docs.volcengine.com/docs/ark/get-video-generation-task-api?lang=zh)：按任务 ID 查询，签名 URL 在下载前刷新，不持久保存。
- [方舟取消与删除](https://docs.volcengine.com/docs/ark/cancel-or-delete-video-generation-tasks-api?lang=zh)：DELETE 对排队任务取消、对终态任务删除记录。先查询再 DELETE 仍有竞争，故本适配器不自动执行；停止等待不代表取消或退款。
- [方舟价格](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh)：按模型、尺寸、时长、输入和账户确认；本地预留不是供应商账单上限。

每任务至少间隔 10 秒查询，不自动重试。ID 不匹配、未知响应、HTTP/业务失败均脱敏。新增协议须实现 `validate/quote/submit/poll/cancel/download`，注册到 `protocol_registry()` 并补充能力与契约测试。仅修改 URL 不等于支持另一协议。

## 配置、密钥与费用

默认真实服务关闭、预算零、未知价格禁止提交。配置默认为 ComfyUI `user/h3-provider/providers.json`，可用 `H3_PROVIDER_CONFIG_PATH` 指向非公开私有目录。密钥来自服务端环境或 Windows 当前用户 DPAPI 注入，不放 JSON、项目、命令参数、日志或浏览器。

以下为关闭状态示例，**不是已核实报价或新的收费授权**。预算与报价使用同一币种的分；支持 USD/CNY。有已预留收费记录后，禁止换币种解释旧账。

```json
{
  "currency": "CNY",
  "budget_cents": 200,
  "max_active": 1,
  "daily_jobs": 20,
  "providers": [{
    "id": "seedance",
    "protocol": "ark-video-v3",
    "credential_env": "ARK_API_KEY",
    "enabled": false,
    "models": ["doubao-seedance-2-5-flash-261115"],
    "model_limits": {"doubao-seedance-2-5-flash-261115": {
      "modes": ["text"], "durations": [4], "resolutions": ["480p"]
    }},
    "quotes_cents": {"doubao-seedance-2-5-flash-261115:480p:4:text": 200},
    "download_hosts": []
  }]
}
```

先预览模型、提示词、图片与最高报价，再批准同一请求。UI/MCP 共用 `GenerationService`。累计预算预留不因失败、取消、超时或重启返还。单次预留设为整个测试预算可阻止第二次提交，但不能代替核实供应商实际报价。下载 CDN 必须在取到结果后精确审核批准，不允许任意主机。

中转使用独立 ID、明确实现过的协议和精确 HTTPS API/CDN 主机，默认关闭。例如 `relay-a + ark-video-v3` 仅表示 Ark v3 兼容配置，兼容性、模型、隐私及费用仍未验收，不称通用接口。`models` 只能选择已有实现，`model_limits` 只能收窄。禁止 URL 凭据、非 443 端口、私网/元数据地址、混合 DNS、重定向与代理；API 密钥不发给 CDN。

Windows：`tools/configure-provider-key.ps1 -Provider Ark` 隐藏输入，以 DPAPI 保存并限制文件 ACL；不调用 API。`-Remove` 删除本机加密副本，远端撤销需在供应商控制台完成。`tools/start-acceptance.ps1` 仅为隔离进程解密，退出后清理进程环境。

## 生命周期与结果

`submitting → queued → running → succeeded/failed/cancelled`，终态不可逆。提交网络请求前先提交 SQLite 记录；中断恢复为 `unknown`，保留预算、名额和幂等指纹，只允许关联账户已核实的任务 ID，不再次 POST。`cancel_requested/timed_out` 不表示远端已取消，仍可查询。网络不占数据库锁，同任务查询/取消互斥。

下载显式进入非公开暂存区，最多 256 MiB；验证 HTTPS、MIME/MP4 签名、尺寸/时长/帧率并实际解码，下载与转换均有时限。导入再审批，前后校验 revision，经原 `accept_take/commit` 增加新 take、保留旧版本。关联 H3 镜头拒绝不能满足帧衔接契约的远程结果。导入不自动替换整条时间线。项目历史恢复不能退款、取消远端任务或收回已发送数据。

## MCP

`tools/h3_mcp.py` 通过 stdio MCP 调用现有编辑器的受限 HTTP bridge。没有第二套编辑器，也不开放任意 ComfyUI 工作流执行、文件路径或代码运行。

配置服务器与客户端的 `H3_MCP_TOKEN`、`H3_EDITOR_PORT` 及服务器精确 `H3_MCP_PROJECTS`。默认只读，`H3_MCP_ALLOW_EDITS=1` 才能提案/执行。可另设不同的 `H3_MCP_READ_TOKEN` 保持同时在线的只读连接；重复读写令牌拒绝启动。Key/令牌只放环境，不写聊天或示例值。不修改用户全局 MCP 配置。

工具包括摘要、历史、渲染状态、费用/能力、供应商状态、工程检查，以及剪辑、字幕、恢复、导出、本地渲染/取消、外部生成、暂存和导入提案。所有修改需本地浏览器批准，绑定认证身份、具体内容和 revision，五分钟有效且只能使用一次；共享同一令牌的客户端属同一身份。Agent 无权自批、访问未列项目或绕到旧写路由。实际 SDK 连接验收见[复现步骤](docs/REPRODUCE-20261118.md)。

## 语音与对话

用户点击麦克风授权后最长录音 10 秒；文件上传最多 30 秒/8 MiB。转写还需勾选仅发送本机并点击确认，没有云端语音服务。`H3_ASR_MODEL` 指向已存在的离线 faster-whisper 目录，`H3_ASR_PYTHON` 指向可选隔离解释器（依赖见 `requirements-asr.txt`）。worker 禁用下载和遥测，CPU INT8、单并发、60 秒时限。取消会终止 worker；完成、失败、取消后删除临时音频。授权等待 15 秒超时，迟到授权会停止音轨。

`H3_CHAT_PORT` 只连接本机 `127.0.0.1` 的结构化文本模型。模型只提意图；代码校验真实对象、字段、范围与 revision，再进入原 bridge 审批。支持镜头增删改、分割、音轨移动/音量、字幕改字、撤销、本地渲染及取消、所选服务的生成费用预览。纠错使旧草稿失效，取消不撤销已经执行的修改。

录音 Blob 和对话保留在当前标签页；丢弃、关闭面板或页面后释放录音。待审批的具体内容在本机服务器内存最多五分钟，执行/拒绝/纠错会提前删除；确认的项目修改及字幕进入工程与历史。本地模型 RAM/KV 缓存可能留到后续覆盖或进程退出，运行器需关闭提示词日志。服务器音频临时文件在正常完成、失败、取消后删除；断电或强杀可能遗留系统 TEMP 中的 `h3-local-asr-*`，删除不是安全擦除。可先人工纠正转写，再预览剪辑或字幕。当前 tiny/base 中文合成语音实测有明显错字，不据此认定麦克风或识别质量通过。
