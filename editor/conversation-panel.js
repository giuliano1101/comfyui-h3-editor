import { uiMessage } from './ui-zh.js';

export function installConversation({ state, api, postJson, flush, review, recording, generation }) {
  const $ = id => document.getElementById(id);
  let previous = null, approval = null, controller = null, epoch = 0, requestId = null, segments = [], uploaded = null;
  function message(who, text) {
    const row = document.createElement('p'); row.textContent = `${who}：${text}`; $('chatMessages').append(row);
    while ($('chatMessages').children.length > 20) $('chatMessages').firstChild.remove();
    row.scrollIntoView({ block: 'nearest' });
  }
  function speak(text) {
    if (!$('chatSpeak').checked || !globalThis.speechSynthesis) return;
    const voice = speechSynthesis.getVoices().find(v => v.localService && v.lang.toLowerCase().startsWith('zh'));
    if (!voice) { message('系统', '未找到本机离线中文语音，未向任何在线朗读服务发送文字。'); return; }
    const utterance = new SpeechSynthesisUtterance(text); utterance.voice = voice; speechSynthesis.cancel(); speechSynthesis.speak(utterance);
  }
  const bridge = (name, args) => postJson('/bridge/call', { name, arguments: args });
  function error(e) { message('系统', uiMessage(e.message)); }
  $('providersOpen').addEventListener('click', async () => {
    try {
      const [speech, chat] = await Promise.all([api('/speech'), api('/conversation')]);
      $('chatBackend').textContent = `本机转写：${speech.configured ? '已配置' : '未配置'}；本机意图模型：${chat.configured ? '已配置' : '未配置'}。未配置时仍可直接预览为新镜头。`;
    } catch (e) { error(e); }
  });
  $('voiceFile').addEventListener('change', () => { uploaded = $('voiceFile').files[0] || null; segments = []; $('voiceCaption').disabled = true; });
  $('voiceDiscard').addEventListener('click', () => { uploaded = null; $('voiceFile').value = ''; segments = []; $('voiceCaption').disabled = true; });
  $('voiceTranscribe').addEventListener('click', async () => {
    if (controller) return;
    try {
      const audio = uploaded || recording();
      if (!audio) throw new Error('请先录音或选择音频。');
      if (audio.size > 8 * 1024 * 1024) throw new Error('音频超过 8 MiB 上限。');
      if (!$('voiceConsent').checked) throw new Error('请先同意仅向本机转写器发送本次音频。');
      controller = new AbortController(); requestId = crypto.randomUUID(); const mine = ++epoch;
      $('voiceStatus').textContent = '本机离线转写中，可取消。';
      const result = await api('/speech/transcribe', { method: 'POST', body: audio, signal: controller.signal,
        headers: { 'Content-Type': audio.type || 'application/octet-stream', 'X-H3-Local-Audio-Consent': '1', 'X-H3-ASR-Request': requestId } });
      if (mine !== epoch) return;
      $('voiceTranscript').value = result.text; segments = result.segments || [];
      $('voiceCaption').disabled = !segments.length; $('voiceStatus').textContent = '本地转写完成，请先纠正文字，再预览操作。音频临时文件已删除。';
    } catch (e) { if (e.name !== 'AbortError') error(e); }
    finally { controller = null; requestId = null; }
  });
  $('voiceCaption').addEventListener('click', async () => {
    try {
      await flush();
      if (!segments.length) throw new Error('请先完成本地转写。');
      const offset = Math.round(state.playhead || 0);
      const cues = segments.map(segment => ({ id: 'sub' + crypto.randomUUID().replaceAll('-', '').slice(0, 12), start: offset + Math.round(segment.start * 24), end: offset + Math.round(segment.end * 24), text: segment.text }));
      if (cues.length === 1) cues[0].text = $('voiceTranscript').value.trim();
      review(await bridge('propose_captions_import', { project: state.folder, revision: state.project.revision, replace: false, cues }), '字幕从当前播放头开始。多段转写请在字幕列表逐条纠错，再导出。', true);
    } catch (e) { error(e); }
  });
  $('chatSend').addEventListener('click', async () => {
    if (controller) return;
    try {
      await flush(); const utterance = $('voiceTranscript').value.trim();
      if (!utterance || !state.project) throw new Error('请先打开项目并输入剪辑指令。');
      message('你', utterance); controller = new AbortController(); const mine = ++epoch;
      const result = await api('/conversation/preview', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        project: state.folder, revision: state.project.revision, utterance, selected_clip: state.clip, selected_item: state.item, selected_sound: state.sound,
        previous, previous_approval: approval, generation: generation()
      }) });
      if (mine !== epoch) return;
      previous = result.draft; approval = result.proposal?.id || null;
      message('剪辑助手', result.message); speak(result.message);
      if (result.proposal) review(result.proposal, '这是操作预览。核对对象、修改内容和费用后才会执行。需要纠错可关闭此框，在对话中继续说明。', true);
    } catch (e) { if (e.name !== 'AbortError') error(e); }
    finally { controller = null; }
  });
  $('chatCancel').addEventListener('click', async () => {
    epoch++; controller?.abort(); globalThis.speechSynthesis?.cancel();
    try {
      if (requestId) await postJson('/speech/cancel', { request: requestId });
      if (approval) await postJson('/approvals/reject', { approval });
      previous = null; approval = null; $('providerReview').close();
      message('系统', '已取消当前处理或未执行草稿。已经执行的修改请预览撤销；已提交的生成请到任务列表核对。');
    } catch (e) { error(e); }
  });
}
