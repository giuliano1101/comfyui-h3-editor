// All dynamic content uses textContent. Provider keys and signed URLs stay server-side.
import { uiMessage, providerName, operationName, fieldName } from "./ui-zh.js";
export function installProviderPanel({ state, api, postJson, toast, adopt, flush }) {
  const $ = (id) => document.getElementById(id);
  const panel = $("providersPanel");
  let providers = {}, review = null, busy = false, refreshTimer = 0, selectedClip = null;
  let recorder = null, stream = null, recordingURL = null, recordingTimer = 0, recordingEpoch = 0;
  const money = (cents) => cents == null ? "价格未知，已禁用付费生成。" : cents === 0 ? "模拟任务：不生成视频、不收费。" : `服务器报价：$${(cents / 100).toFixed(2)} 美元/次。实际账单可能不同。`;
  const chosen = () => providers[$("providerSelect").value];
  const actionNames = { ops: "编辑镜头", restore: "恢复历史版本", import: "导入新素材版本", export: "导出剪辑", cancel: "取消本地渲染" };
  const estimate = () => {
    const provider = chosen();
    return provider?.simulated ? 0 : provider?.quotes_cents?.[`${$("providerModel").value}:${$("providerResolution").value}:${$("providerSeconds").value}`] ?? null;
  };
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const error = (message, target = "providerError") => {
    $(target).textContent = uiMessage(message);
    $(target).hidden = !message;
  };
  const run = (action) => async () => {
    if (busy) return;
    busy = true;
    panel.setAttribute("aria-busy", "true");
    error("");
    try { await action(); } catch (caught) { error(caught.message); }
    finally { busy = false; panel.removeAttribute("aria-busy"); }
  };
  function options(id, values, current) {
    $(id).replaceChildren(...values.map((value) => {
      const option = el("option", value === "simulation" ? "模拟模型" : value === "test" ? "测试" : value);
      option.value = value;
      return option;
    }));
    if (values.includes(current)) $(id).value = current;
  }
  function updateDuration() {
    const provider = chosen();
    const durations = provider.protocol === "minimax-video-v1" && $("providerResolution").value === "1080P" ? [6] : provider.durations;
    options("providerSeconds", durations.map(String), $("providerSeconds").value);
    updateQuote();
  }
  function updateQuote() {
    $("providerCost").textContent = money(estimate());
    setClip();
  }
  function updateCapabilities() {
    const provider = chosen();
    if (!provider) return;
    options("providerModel", provider.models, $("providerModel").value);
    options("providerResolution", provider.resolutions, $("providerResolution").value);
    updateDuration();
    $("providerStatus").textContent = provider.simulated ? "模拟服务可用，不会生成媒体文件。" : `${uiMessage(provider.service)}：${uiMessage(provider.status)}。凭据：${provider.configured ? "已配置" : "未配置"}；${provider.enabled ? "管理员已启用" : "管理员已禁用"}。取消能力：${provider.cancellation ? "支持" : "不支持"}。`;
    $("providerCost").textContent = money(estimate());
    setClip();
  }
  function setClip() {
    const clip = state.project?.clips.find((row) => row.id === state.clip);
    $("providerClip").textContent = clip ? `当前镜头：${clip.name || clip.id}` : "请先在编辑器中选择一个镜头。";
    if (selectedClip !== clip?.id) {
      selectedClip = clip?.id;
      $("providerPrompt").value = clip?.action || "";
    }
    $("providerPreview").disabled = !chosen()?.available || estimate() == null || !clip || Boolean(clip.link);
    if (clip?.link) $("providerClip").textContent += "关联镜头继续使用本地 H3 生成流程。";
  }
  const bridge = (name, args) => postJson("/bridge/call", { name, arguments: args });
  async function refresh() {
    if (!state.folder) return;
    const folder = state.folder;
    const [jobs, approvals] = await Promise.all([api("/provider-jobs?project=" + encodeURIComponent(folder)), api("/approvals")]);
    if (folder !== state.folder || panel.hidden) return;
    setClip();
    const usage = jobs.usage;
    $("providerLimits").textContent = `${usage.active}/${usage.max_active} 个活动任务 · ${usage.connection_today}/${usage.connection_daily_limit} 次今日提交。预留预算余额：$${(usage.remaining_cents / 100).toFixed(2)} 美元。此为本地限制，实际供应商收费可能不同。`;
    renderJobs(jobs.jobs);
    renderApprovals(approvals.proposals.filter((p) => p.payload.project === folder && p.action !== "provider_submit"));
  }
  function action(label, callback, disabled = false) {
    const button = el("button", label, "quiet");
    button.disabled = disabled;
    button.addEventListener("click", run(callback));
    return button;
  }
  function renderJobs(jobs) {
    const list = $("providerJobs");
    list.replaceChildren();
    if (!jobs.length) list.append(el("p", "此项目暂无供应商任务。可先运行模拟任务，验证流程且不生成视频。", "hint"));
    for (const job of jobs) {
      const row = el("article", null, "provider-job");
      const heading = el("div", null, "providers-head");
      heading.append(el("b", `${providerName(job.provider)} · ${uiMessage(job.model)}`), el("span", uiMessage(job.state), "provider-state"));
      row.append(heading, el("p", `${job.seconds} 秒 · ${uiMessage(job.resolution)} · ${job.simulated ? "模拟任务" : "真实 API 未验证"} · ${job.id.slice(0, 8)}`, "hint"));
      if (job.notice) row.append(el("p", uiMessage(job.notice), "hint"));
      const buttons = el("div", null, "provider-actions");
      const call = async (verb) => { await postJson(`/provider-jobs/${encodeURIComponent(job.id)}/${verb}`, {}); await refresh(); };
      if (!["succeeded", "failed", "cancelled", "unknown", "submitting"].includes(job.state)) {
        buttons.append(action("查询状态", () => call("poll")));
        if (providers[job.provider]?.cancellation) buttons.append(action("请求取消", () => call("cancel")));
      }
      if (job.state === "succeeded" && !job.has_output) row.append(el("p", "模拟已完成，未生成媒体，也未向项目添加素材。", "hint"));
      if (job.has_output && job.state === "succeeded") {
        if (!job.staged) buttons.append(action("下载到暂存区", () => call("stage")));
        else buttons.append(action("预览为新素材版本", async () => {
          await flush();
          const proposal = await bridge("propose_import", { project: state.folder, clip: job.clip, job: job.id, revision: state.project.revision });
          openReview(proposal, "导入为新素材版本，保留已有版本。如项目已更改，导入会被拒绝。", true);
        }));
      }
      if (job.state === "unknown") {
        const details = el("details");
        details.append(el("summary", "核对供应商任务"), el("p", "请先在供应商账户核对任务，再粘贴现有任务 ID。此操作不会再次提交生成。", "hint"));
        const input = el("input");
        input.maxLength = 128;
        input.setAttribute("aria-label", "已核实的供应商任务 ID");
        details.append(input, action("关联已核实的任务 ID", async () => {
          await postJson(`/provider-jobs/${job.id}/reconcile`, { handle: input.value });
          await refresh();
        }));
        row.append(details);
      }
      row.append(buttons);
      list.append(row);
    }
  }
  function renderApprovals(proposals) {
    const list = $("providerApprovals");
    list.replaceChildren();
    if (!proposals.length) list.append(el("p", "暂无待审批请求。", "hint"));
    for (const proposal of proposals) {
      const row = el("div", null, "provider-job");
      row.append(el("b", `${actionNames[proposal.action] || proposal.action} · 修订版本 ${proposal.payload.revision}`), action(proposal.approved ? "查看已批准请求" : "查看请求", async () => openReview(proposal, "Agent 只能执行一次已批准的具体操作。恢复历史无法撤销外部收费或已经发送的数据。", false)));
      list.append(row);
    }
  }
  function openReview(proposal, notice, executeHere) {
    review = { proposal, executeHere };
    $("providerReviewAck").checked = false;
    $("providerConfirm").disabled = true;
    $("providerConfirm").textContent = proposal.action === "provider_submit" ? (proposal.simulated ? "运行模拟" : "确认付费生成") : executeHere ? "确认并应用" : "批准 Agent 执行";
    $("providerReviewTitle").textContent = proposal.action === "provider_submit" ? "确认生成任务" : "确认项目修改";
    const details = $("providerReviewDetails"), payload = proposal.payload;
    details.replaceChildren(el("p", `项目：${payload.project} · 修订版本 ${payload.revision}`));
    if (proposal.action === "provider_submit") {
      details.append(el("p", `${providerName(payload.provider)} · ${uiMessage(payload.model)} · ${payload.seconds} 秒 · ${uiMessage(payload.resolution)}`), el("pre", payload.prompt), el("p", money(proposal.estimate_cents)));
    } else if (proposal.action === "ops") {
      for (const op of payload.ops) {
        details.append(el("b", operationName(op.op)));
        for (const [key, value] of Object.entries(op)) if (key !== "op") {
          if (key === "fields") {
            for (const [field, entry] of Object.entries(value)) {
              const label = fieldName(field);
              const shown = typeof entry === "object" ? `${entry.type}, ${entry.frames} 帧` : entry;
              details.append(el("p", `${label}：${shown}${field === "seconds" ? " 秒" : ""}`));
            }
          } else details.append(el("p", `${fieldName(key)}：${value}`));
        }
      }
    } else {
      for (const [key, value] of Object.entries(payload)) if (!["project", "revision"].includes(key)) details.append(el("p", `${fieldName(key)}：${value}`));
    }
    $("providerReviewNotice").textContent = uiMessage(notice);
    error("", "providerReviewError");
    $("providerReview").showModal();
  }
  $("providersOpen").addEventListener("click", run(async () => {
    panel.hidden = false;
    $("providersOpen").setAttribute("aria-expanded", "true");
    $("providersClose").focus();
    providers = (await api("/providers")).providers;
    $("providerSelect").replaceChildren(...Object.entries(providers).map(([name, cap]) => {
      const option = el("option", providerName(name) + (cap.simulated ? "（模拟）" : "（真实 API 未验证）"));
      option.value = name;
      return option;
    }));
    updateCapabilities();
    await refresh();
    clearInterval(refreshTimer);
    refreshTimer = setInterval(() => { if (!busy && !$("providerReview").open) refresh().catch((caught) => error(caught.message)); }, 10000);
  }));
  function close() {
    panel.hidden = true;
    clearInterval(refreshTimer);
    discardRecording();
    $("providersOpen").setAttribute("aria-expanded", "false");
    $("providersOpen").focus();
  }
  $("providersClose").addEventListener("click", close);
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } });
  $("providerSelect").addEventListener("change", updateCapabilities);
  $("providerResolution").addEventListener("change", updateDuration);
  $("providerModel").addEventListener("change", updateQuote);
  $("providerSeconds").addEventListener("change", updateQuote);
  $("providerRefresh").addEventListener("click", run(refresh));
  $("providerPreview").addEventListener("click", run(async () => {
    await flush();
    const proposal = await postJson("/provider-jobs/preview", { provider: $("providerSelect").value, model: $("providerModel").value,
      resolution: $("providerResolution").value, seconds: Number($("providerSeconds").value), prompt: $("providerPrompt").value,
      project: state.folder, clip: state.clip, revision: state.project.revision });
    openReview(proposal, proposal.cost_notice, true);
  }));
  $("providerReviewClose").addEventListener("click", () => $("providerReview").close());
  $("providerReviewAck").addEventListener("change", () => { $("providerConfirm").disabled = !$("providerReviewAck").checked; });
  $("providerReject").addEventListener("click", run(async () => {
    await postJson("/approvals/reject", { approval: review.proposal.id });
    $("providerReview").close();
    await refresh();
  }));
  $("providerConfirm").addEventListener("click", async () => {
    if (busy || !$("providerReviewAck").checked || !review) return;
    busy = true;
    $("providerConfirm").disabled = true;
    try {
      const { proposal, executeHere } = review;
      await postJson("/approvals/approve", { approval: proposal.id });
      if (executeHere) {
        const result = proposal.action === "provider_submit" ? await postJson("/provider-jobs", { approval: proposal.id, cost_acknowledged: true }) :
          await bridge("execute_" + proposal.action, { ...proposal.payload, approval: proposal.id });
        if (result.project && typeof result.project === "object" && state.folder === proposal.payload.project) adopt(result.project);
        else if (proposal.action !== "provider_submit") adopt(await api("/project/" + encodeURIComponent(state.folder)));
        toast(proposal.action === "provider_submit" ? "任务已记录。请先查询状态，再进行其他操作。" : "项目修改已应用。");
      } else toast("请求已批准。Agent 可在有效期内执行一次。");
      $("providerReview").close();
      await refresh();
    } catch (caught) {
      error(caught.message + "重试前请先核对任务列表或项目历史。", "providerReviewError");
    } finally { busy = false; }
  });
  $("voicePreview").addEventListener("click", run(async () => {
    await flush();
    const text = $("voiceTranscript").value.trim();
    if (!text) throw new Error("请先输入或粘贴镜头文字。自动转写尚不可用。");
    const proposal = await bridge("propose_ops", { project: state.folder, revision: state.project.revision,
      ops: [{ op: "add_clip", fields: { action: text, seconds: 4 } }] });
    openReview(proposal, "此操作仅新增一个未渲染的 4 秒镜头，不渲染、不发送音频，也不产生供应商费用。", true);
  }));
  function stopTracks() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    clearTimeout(recordingTimer);
    $("voiceStop").disabled = true;
    $("voiceRecord").disabled = false;
  }
  function discardRecording() {
    recordingEpoch++;
    if (recorder?.state === "recording") recorder.stop();
    recorder = null;
    stopTracks();
    if (recordingURL) URL.revokeObjectURL(recordingURL);
    recordingURL = null;
    $("voicePlayback").pause();
    $("voicePlayback").removeAttribute("src");
    $("voicePlayback").hidden = true;
    $("voiceDiscard").disabled = true;
    $("voiceStatus").textContent = "麦克风已关闭，录音已丢弃。";
  }
  $("voiceDiscard").addEventListener("click", discardRecording);
  $("voiceStop").addEventListener("click", () => { if (recorder?.state === "recording") recorder.stop(); stopTracks(); });
  $("voiceRecord").addEventListener("click", run(async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error("此浏览器不支持本地录音。");
    discardRecording();
    const epoch = recordingEpoch;
    $("voiceRecord").disabled = true;
    try {
      const granted = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (panel.hidden || epoch !== recordingEpoch) { granted.getTracks().forEach((track) => track.stop()); return; }
      stream = granted;
      recorder = new MediaRecorder(stream);
      const chunks = [];
      let bytes = 0;
      recorder.addEventListener("dataavailable", (event) => {
        bytes += event.data.size;
        if (bytes <= 8 * 1024 * 1024) chunks.push(event.data);
        else if (recorder?.state === "recording") recorder.stop();
      });
      recorder.addEventListener("error", () => { discardRecording(); error("录音失败。请重试，或直接输入镜头文字。"); });
      recorder.addEventListener("stop", () => {
        if (epoch !== recordingEpoch) return;
        stopTracks();
        if (bytes > 8 * 1024 * 1024) { discardRecording(); error("录音超过本地大小上限，已丢弃。"); return; }
        const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
        recordingURL = URL.createObjectURL(blob);
        $("voicePlayback").src = recordingURL;
        $("voicePlayback").hidden = false;
        $("voiceDiscard").disabled = false;
        $("voiceStatus").textContent = "麦克风已关闭。录音仅保留在当前标签页。";
      });
      recorder.start(250);
      $("voiceStop").disabled = false;
      $("voiceStatus").textContent = "正在本地录音，30 秒后自动停止。";
      recordingTimer = setTimeout(() => { if (recorder?.state === "recording") recorder.stop(); stopTracks(); }, 30000);
    } catch (_) {
      stopTracks();
      throw new Error("麦克风权限被拒绝或设备不可用。可以直接输入镜头文字。");
    }
  }));
  window.addEventListener("pagehide", discardRecording);
}
