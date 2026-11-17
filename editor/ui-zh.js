// Translate only application-owned presentation messages. Never rewrite project
// text, prompts, file paths, model IDs, API arguments or stored job states.
const messages = {
  dissolve: "叠化", black: "淡入黑场", wipe: "擦除",
  "The fastest. Distilled to 6 steps.": "速度最快，蒸馏为 6 步。",
  "The original H3 with a 6-step Turbo LoRA. About as fast as HyperFlow.": "原版 H3 搭配 6 步 Turbo LoRA，速度接近 HyperFlow。",
  "The original H3 at 20 steps. About twice as slow as HyperFlow.": "原版 H3，20 步，耗时约为 HyperFlow 的两倍。",
  "A sharp, expressive H3 fine-tune, ref2va only. 20 steps, about twice as slow.": "清晰且富有表现力的 H3 微调模型，仅支持 ref2va。20 步，耗时约为两倍。",
  "A community H3 checkpoint, ref2va only. 20 steps, about twice as slow.": "社区 H3 检查点，仅支持 ref2va。20 步，耗时约为两倍。",
  "Its distilled version, 8 steps.": "对应的蒸馏版本，8 步。",
  "A merged community H3 checkpoint with its turbo built in, ref2va only. 6 steps for drafts, 8 for finals.": "内置 Turbo 的社区 H3 合并模型，仅支持 ref2va。草稿 6 步、成片 8 步。",
  "A community H3 merge with an 8-step turbo built in, one file for every clip. 6 steps for drafts, 8 for finals.": "内置 8 步 Turbo 的社区 H3 合并模型，各镜头共用一个文件。草稿 6 步、成片 8 步。",
  "Fast: seconds a clip, about 16 GB of GPU memory. Sharpens the edges that are there; it does not add new texture.": "快速：每个镜头数秒，约需 16 GB 显存。锐化已有边缘，不增加新纹理。",
  "Slow: minutes a clip, and far more GPU memory. Redraws fine texture, so it looks like a larger picture; it can change a face a little.": "较慢：每个镜头数分钟，显存需求更高。重绘细微纹理以提升分辨率感，面部可能略有变化。",
  simulation: "模拟", test: "测试", submitting: "正在提交", queued: "排队中", running: "运行中",
  succeeded: "已完成", failed: "失败", cancelled: "已取消", unknown: "结果未知",
  cancel_requested: "已请求取消", timed_out: "等待超时", configured: "已配置",
  supported: "支持", unsupported: "不支持",
  "MiniMax official": "MiniMax 官方",
  "implemented; live API unverified": "协议已实现，真实 API 尚未验证",
  "Administrator-approved relay; compatibility and billing unverified": "管理员批准的中转；兼容性及费用尚未验证",
  "Simulation only: no video and no charge.": "仅为模拟任务，不生成视频、不收费。",
  "Simulation only; no video or charge.": "仅为模拟任务，不生成视频、不收费。",
  "No charge": "不收费",
  "Paid MiniMax v1 Hailuo text-to-video. Server-configured quote in USD; actual billing can differ. No documented cancellation or submission idempotency. Cancelling a local wait is not a refund.": "MiniMax v1 Hailuo 付费文生视频。报价由服务器配置，以美元计价，实际账单可能不同。官方未确认取消和提交幂等能力；停止本地等待不代表退款。",
  "Submission interrupted. Reconcile the provider account; do not resubmit.": "提交被中断。请先核对供应商账户，不要重复提交。",
  "Submission outcome unknown. Do not resubmit; reconcile the provider account.": "提交结果未知。不要重复提交，请先核对供应商账户。",
  "Cancellation unconfirmed; poll again. Charges may continue.": "取消尚未确认，请继续查询状态，供应商可能仍会收费。",
  "Provider status unavailable; poll again. Do not submit again.": "暂时无法查询供应商状态。请稍后查询，不要重复提交。",
  "Provider outcome not confirmed. Charges may continue; keep polling.": "供应商结果尚未确认，可能继续收费，请持续查询状态。",
  "unknown provider": "未知供应商。",
  "prompt must contain 1 to 2000 characters": "提示词需包含 1 至 2000 个字符。",
  "duration outside provider limits": "时长超出供应商支持范围。",
  "unsupported model or resolution": "不支持此模型或分辨率。",
  "provider disabled or credential not configured": "供应商已禁用，或尚未配置凭据。",
  "verified price is unknown; paid submission disabled": "尚无已核实的报价，已禁用付费提交。",
  "acknowledge the provider cost notice first": "请先确认供应商费用说明。",
  "idempotency key already used for different input": "此提交标识已用于其他输入，请核对已有任务。",
  "job capacity reached; reconcile active jobs": "任务数量已达上限，请先核对尚未完成的任务。",
  "daily connection quota reached": "今日提交数量已达上限。",
  "configured lifetime reservation budget reached": "已达到配置的累计预留预算上限。",
  "unknown job": "未找到任务。",
  "provider unavailable; job retained": "供应商暂不可用，任务记录已保留。",
  "provider does not support cancellation; charges may continue": "此供应商不支持取消，可能继续收费。",
  "only unknown submissions can be reconciled": "仅可核对提交结果未知的任务。",
  "provider task already tracked": "此供应商任务已有关联记录。",
  "job has no downloadable output": "此任务没有可下载的结果。",
  "project revision changed": "项目已更改，请刷新后重新确认。",
  "project revision changed; read again": "项目版本已更改，请重新读取。",
  "project revision changed; preview again": "项目版本已更改，请重新预览并确认。",
  "project revision changed; review before importing": "项目版本已更改，导入前请重新核对。",
  "project revision changed; staged output kept for retry": "项目版本已更改，暂存结果已保留，请核对后重试。",
  "review and confirm the cost notice first": "请先核对请求及费用说明并确认。",
  "local editor approval required": "需要在本机编辑器中批准此操作。",
  "proposal expired; prepare it again": "请求已过期，请重新准备。",
  "approval does not match this action": "批准内容与此次操作不一致。",
  "approval requires the local editor": "请在本机编辑器中批准请求。",
  "this connection is read-only": "此连接仅有只读权限。",
  "project not allowed for this connection": "此连接无权访问该项目。",
  "connection rate limit reached": "请求过于频繁，请稍后重试。",
  "operation rejected; project unchanged": "操作被拒绝，项目未更改。",
  "this output is already imported": "此结果已导入，无需重复操作。",
  "stage this output before importing": "请先将结果下载至暂存区，再导入。",
  "staged output does not belong to this clip": "暂存结果不属于此镜头。",
  "output is not staged for this clip": "此镜头尚无对应的暂存结果。",
  "invalid or unreadable staged video": "暂存视频无效或无法读取。",
  "output download failed; project unchanged": "结果下载失败，项目未更改。",
  "remote outputs cannot satisfy local H3 linked-frame contracts": "远程结果不能满足本地 H3 关联镜头的帧衔接要求。",
  "remote generation cannot fulfill local H3 linked-frame contracts": "关联镜头需使用本地 H3 流程，无法改用此远程生成。",
  "export needs rendered media": "导出需要已渲染或已导入的媒体。",
  "editor operation failed; inspect local configuration": "编辑器操作失败，请检查本机配置。",
  "access denied": "无权访问。",
  "invalid request; no provider details disclosed": "请求无效，请检查输入。供应商私有信息未对外显示。"
};

export function uiMessage(value) {
  const text = String(value ?? "");
  if (Object.hasOwn(messages, text)) return messages[text];
  return text;
}

export function providerName(value) {
  return value === "simulation" ? "模拟服务" : value === "minimax" ? "MiniMax 官方" : String(value);
}

export function operationName(value) {
  return ({ add_clip: "新建镜头", update_clip: "修改镜头", set_project: "修改项目设置", update_item: "调整时间线片段",
    split_item: "分割片段", remove_item: "移除时间线片段", remove_clip: "删除镜头", place_clip: "放置镜头",
    choose_take: "选择素材版本", update_sound: "调整音频" })[value] || value;
}

export function fieldName(value) {
  return ({ name: "名称", action: "画面描述", camera: "运镜", dialogue: "台词", sound: "声音", seconds: "时长",
    clip: "镜头 ID", item: "片段 ID", job: "任务 ID", take: "素材版本 ID", start: "起始帧", in: "入点帧", out: "出点帧",
    frame: "帧", speed: "速度", transition: "转场", gain: "音量", lane: "轨道", target_revision: "要恢复的版本", as: "批次内引用名" })[value] || value;
}
