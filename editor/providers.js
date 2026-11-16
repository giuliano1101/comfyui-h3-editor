// All dynamic content uses textContent. Provider keys and signed URLs stay server-side.
export function installProviderPanel({ state, api, postJson, toast, adopt, flush }) {
  const $ = (id) => document.getElementById(id);
  const panel = $("providersPanel");
  let providers = {}, review = null, busy = false, refreshTimer = 0, selectedClip = null;
  let recorder = null, stream = null, recordingURL = null, recordingTimer = 0, recordingEpoch = 0;
  const money = (cents) => cents == null ? "Price unknown; paid generation disabled." : cents === 0 ? "Simulation: no video and no charge." : `Server quote: $${(cents / 100).toFixed(2)} USD per job. Actual billing can differ.`;
  const chosen = () => providers[$("providerSelect").value];
  const actionNames = { ops: "Edit clips", restore: "Restore history", import: "Import a new take", export: "Export the cut", cancel: "Cancel a local render" };
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
    $(target).textContent = message || "";
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
      const option = el("option", value);
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
    $("providerStatus").textContent = provider.simulated ? "Simulation available. This produces no media." : `${provider.service}: ${provider.status}. Credential ${provider.configured ? "configured" : "not configured"}; ${provider.enabled ? "enabled by operator" : "disabled by operator"}. Cancellation ${provider.cancellation ? "supported" : "unsupported"}.`;
    $("providerCost").textContent = money(estimate());
    setClip();
  }
  function setClip() {
    const clip = state.project?.clips.find((row) => row.id === state.clip);
    $("providerClip").textContent = clip ? `Selected clip: ${clip.name || clip.id}` : "Select a clip in the editor to prepare a job.";
    if (selectedClip !== clip?.id) {
      selectedClip = clip?.id;
      $("providerPrompt").value = clip?.action || "";
    }
    $("providerPreview").disabled = !chosen()?.available || estimate() == null || !clip || Boolean(clip.link);
    if (clip?.link) $("providerClip").textContent += " Linked clips use the existing local H3 path.";
  }
  const bridge = (name, args) => postJson("/bridge/call", { name, arguments: args });
  async function refresh() {
    if (!state.folder) return;
    const folder = state.folder;
    const [jobs, approvals] = await Promise.all([api("/provider-jobs?project=" + encodeURIComponent(folder)), api("/approvals")]);
    if (folder !== state.folder || panel.hidden) return;
    setClip();
    const usage = jobs.usage;
    $("providerLimits").textContent = `${usage.active}/${usage.max_active} active jobs · ${usage.connection_today}/${usage.connection_daily_limit} submitted today. Reservation budget remaining: $${(usage.remaining_cents / 100).toFixed(2)} USD. This is a local limit; actual provider charges can differ.`;
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
    if (!jobs.length) list.append(el("p", "No provider jobs yet. Review a simulation to test this workflow without generating video.", "hint"));
    for (const job of jobs) {
      const row = el("article", null, "provider-job");
      const heading = el("div", null, "providers-head");
      heading.append(el("b", `${job.provider} · ${job.model}`), el("span", job.state.replaceAll("_", " "), "provider-state"));
      row.append(heading, el("p", `${job.seconds} s · ${job.resolution} · ${job.simulated ? "simulated" : "live API unverified"} · ${job.id.slice(0, 8)}`, "hint"));
      if (job.notice) row.append(el("p", job.notice, "hint"));
      const buttons = el("div", null, "provider-actions");
      const call = async (verb) => { await postJson(`/provider-jobs/${encodeURIComponent(job.id)}/${verb}`, {}); await refresh(); };
      if (!["succeeded", "failed", "cancelled", "unknown", "submitting"].includes(job.state)) {
        buttons.append(action("Check status", () => call("poll")));
        if (providers[job.provider]?.cancellation) buttons.append(action("Request cancellation", () => call("cancel")));
      }
      if (job.state === "succeeded" && !job.has_output) row.append(el("p", "Simulation finished. No media was created or added to the project.", "hint"));
      if (job.has_output && job.state === "succeeded") {
        if (!job.staged) buttons.append(action("Download to staging", () => call("stage")));
        else buttons.append(action("Review as a new take", async () => {
          await flush();
          const proposal = await bridge("propose_import", { project: state.folder, clip: job.clip, job: job.id, revision: state.project.revision });
          openReview(proposal, "Import as a new take. Previous takes stay available; a changed project will refuse this import.", true);
        }));
      }
      if (job.state === "unknown") {
        const details = el("details");
        details.append(el("summary", "Reconcile a provider task"), el("p", "Check the provider account first. Paste its existing task ID; this never submits another generation.", "hint"));
        const input = el("input");
        input.maxLength = 128;
        input.setAttribute("aria-label", "Verified provider task ID");
        details.append(input, action("Attach verified task ID", async () => {
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
    if (!proposals.length) list.append(el("p", "No requests waiting for review.", "hint"));
    for (const proposal of proposals) {
      const row = el("div", null, "provider-job");
      row.append(el("b", `${actionNames[proposal.action] || proposal.action} · revision ${proposal.payload.revision}`), action(proposal.approved ? "Review approved request" : "Review request", async () => openReview(proposal, "An agent will execute the exact approved request once. Restore cannot undo external charges or disclosure.", false)));
      list.append(row);
    }
  }
  function openReview(proposal, notice, executeHere) {
    review = { proposal, executeHere };
    $("providerReviewAck").checked = false;
    $("providerConfirm").disabled = true;
    $("providerConfirm").textContent = proposal.action === "provider_submit" ? (proposal.simulated ? "Run simulation" : "Confirm paid generation") : executeHere ? "Confirm and apply" : "Approve for agent";
    $("providerReviewTitle").textContent = proposal.action === "provider_submit" ? "Review generation" : "Review project change";
    const details = $("providerReviewDetails"), payload = proposal.payload;
    details.replaceChildren(el("p", `Project: ${payload.project} · revision ${payload.revision}`));
    if (proposal.action === "provider_submit") {
      details.append(el("p", `${payload.provider} · ${payload.model} · ${payload.seconds} s · ${payload.resolution}`), el("pre", payload.prompt), el("p", money(proposal.estimate_cents)));
    } else if (proposal.action === "ops") {
      for (const op of payload.ops) {
        details.append(el("b", op.op.replaceAll("_", " ")));
        for (const [key, value] of Object.entries(op)) if (key !== "op") {
          if (key === "fields") {
            for (const [field, entry] of Object.entries(value)) {
              const label = field === "action" ? "What happens" : field.replaceAll("_", " ");
              const shown = typeof entry === "object" ? `${entry.type}, ${entry.frames} frames` : entry;
              details.append(el("p", `${label}: ${shown}${field === "seconds" ? " s" : ""}`));
            }
          } else details.append(el("p", `${key.replaceAll("_", " ")}: ${value}`));
        }
      }
    } else {
      for (const [key, value] of Object.entries(payload)) if (!["project", "revision"].includes(key)) details.append(el("p", `${key.replaceAll("_", " ")}: ${value}`));
    }
    $("providerReviewNotice").textContent = notice;
    error("", "providerReviewError");
    $("providerReview").showModal();
  }
  $("providersOpen").addEventListener("click", run(async () => {
    panel.hidden = false;
    $("providersOpen").setAttribute("aria-expanded", "true");
    $("providersClose").focus();
    providers = (await api("/providers")).providers;
    $("providerSelect").replaceChildren(...Object.entries(providers).map(([name, cap]) => {
      const option = el("option", name + (cap.simulated ? " (simulation)" : " (live API unverified)"));
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
        toast(proposal.action === "provider_submit" ? "Job recorded. Check status before taking another action." : "Project change applied.");
      } else toast("Request approved. The agent can execute it once before it expires.");
      $("providerReview").close();
      await refresh();
    } catch (caught) {
      error(caught.message + " Check the job list or project history before retrying.", "providerReviewError");
    } finally { busy = false; }
  });
  $("voicePreview").addEventListener("click", run(async () => {
    await flush();
    const text = $("voiceTranscript").value.trim();
    if (!text) throw new Error("Type or paste the shot text first. Automatic transcription is unavailable.");
    const proposal = await bridge("propose_ops", { project: state.folder, revision: state.project.revision,
      ops: [{ op: "add_clip", fields: { action: text, seconds: 4 } }] });
    openReview(proposal, "This adds one unrendered 4-second clip. It does not render, send audio or incur a provider charge.", true);
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
    $("voiceStatus").textContent = "Microphone off. Recording discarded.";
  }
  $("voiceDiscard").addEventListener("click", discardRecording);
  $("voiceStop").addEventListener("click", () => { if (recorder?.state === "recording") recorder.stop(); stopTracks(); });
  $("voiceRecord").addEventListener("click", run(async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error("Local recording is unavailable in this browser.");
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
      recorder.addEventListener("error", () => { discardRecording(); error("Microphone recording failed. Try again or type the shot text."); });
      recorder.addEventListener("stop", () => {
        if (epoch !== recordingEpoch) return;
        stopTracks();
        if (bytes > 8 * 1024 * 1024) { discardRecording(); error("Recording exceeded the local size limit and was discarded."); return; }
        const blob = new Blob(chunks, { type: chunks[0]?.type || "audio/webm" });
        recordingURL = URL.createObjectURL(blob);
        $("voicePlayback").src = recordingURL;
        $("voicePlayback").hidden = false;
        $("voiceDiscard").disabled = false;
        $("voiceStatus").textContent = "Microphone off. Recording held only in this tab.";
      });
      recorder.start(250);
      $("voiceStop").disabled = false;
      $("voiceStatus").textContent = "Recording locally. Stops after 30 seconds.";
      recordingTimer = setTimeout(() => { if (recorder?.state === "recording") recorder.stop(); stopTracks(); }, 30000);
    } catch (_) {
      stopTracks();
      throw new Error("Microphone permission denied or device unavailable. You can type the shot text instead.");
    }
  }));
  window.addEventListener("pagehide", discardRecording);
}
