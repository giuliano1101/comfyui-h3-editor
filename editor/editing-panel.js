import { uiMessage } from './ui-zh.js';

export function installEditingPanel({ state, postJson, flush, review, viewing }) {
  const $ = id => document.getElementById(id);
  const node = (tag, text) => { const n = document.createElement(tag); if (text != null) n.textContent = text; return n; };
  const bridge = (name, args) => postJson('/bridge/call', { name, arguments: args });
  let busy = false;
  const run = fn => async () => {
    if (busy) return; busy = true; $('editingError').hidden = true;
    try { await flush(); if (!state.project) throw new Error('请先打开项目。'); await fn(); }
    catch (e) { $('editingError').textContent = uiMessage(e.message); $('editingError').hidden = false; }
    finally { busy = false; }
  };
  const base = () => ({ project: state.folder, revision: state.project.revision });
  async function propose(ops, revision = state.project.revision) {
    review(await bridge('propose_ops', { ...base(), revision, ops }), '仅应用预览中的修改；可从项目历史恢复。', true);
  }
  function refresh() {
    const root = $('subtitleRows'); root.replaceChildren();
    for (const cue of state.project.subtitles || []) {
      const renderedRevision = state.project.revision;
      const row = node('div'); row.className = 'provider-job';
      const start = node('input'), end = node('input'), text = node('textarea');
      const times = node('div'); times.className = 'subtitle-times';
      for (const [input, title, value] of [[start, '开始帧', cue.start], [end, '结束帧', cue.end]]) {
        input.type = 'number'; input.min = 0; input.max = 1000000; input.value = value; input.setAttribute('aria-label', `${cue.id} ${title}`);
        const label = node('label', `${title}（24 帧/秒）`); label.className = 'provider-field'; label.append(input); times.append(label);
      }
      text.value = cue.text; text.maxLength = 1000; text.rows = 2; text.setAttribute('aria-label', `${cue.id} 字幕文字`);
      const save = node('button', '预览修改'), remove = node('button', '预览删除');
      save.addEventListener('click', run(() => propose([{ op: 'update_subtitle', subtitle: cue.id, fields: { start: Number(start.value), end: Number(end.value), text: text.value } }], renderedRevision)));
      remove.addEventListener('click', run(() => propose([{ op: 'remove_subtitle', subtitle: cue.id }], renderedRevision)));
      const actions = node('div'); actions.className = 'provider-actions'; actions.append(save, remove);
      row.append(node('b', `${(cue.start / 24).toFixed(2)} → ${(cue.end / 24).toFixed(2)} 秒`), times, text, actions); root.append(row);
    }
    if (!root.children.length) root.append(node('p', '当前项目暂无字幕。'));
    for (const [id, rows] of [['audioSyncItem', state.project.timeline || []], ['audioSyncSound', state.project.sounds || []]]) {
      const old = $(id).value;
      $(id).replaceChildren(...rows.map(row => { const option = node('option', row.name || row.id); option.value = row.id; return option; }));
      if (rows.some(row => row.id === old)) $(id).value = old;
    }
  }
  $('editingOpen').addEventListener('click', run(async () => {
    $('providersClose').click(); $('editingPanel').hidden = false; $('editingOpen').setAttribute('aria-expanded', 'true'); refresh(); $('editingClose').focus();
  }));
  function close() { $('editingPanel').hidden = true; $('editingOpen').setAttribute('aria-expanded', 'false'); $('editingOpen').focus(); }
  $('editingClose').addEventListener('click', close);
  $('editingPanel').addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } });
  $('subtitleFile').addEventListener('change', run(async () => {
    const file = $('subtitleFile').files[0]; if (!file) return;
    if (file.size > 500000) throw new Error('字幕文件超过 500 KB 上限。');
    $('subtitleText').value = await file.text(); $('subtitleFormat').value = file.name.toLowerCase().endsWith('.vtt') ? 'vtt' : 'srt';
  }));
  $('subtitleImport').addEventListener('click', run(async () => review(await bridge('propose_subtitle_file', {
    ...base(), text: $('subtitleText').value, format: $('subtitleFormat').value, replace: $('subtitleReplace').checked
  }), '核对字幕文字、时间和替换范围后再应用。原始字幕文件保持不变。', true)));
  $('subtitleRefresh').addEventListener('click', run(refresh));
  $('audioTidy').addEventListener('click', run(async () => {
    const proposal = await bridge('propose_tidy_audio', base());
    if (proposal.unchanged) { $('projectHealthResult').textContent = proposal.notice; return; }
    review(proposal, '仅调整音轨编号，不移动音频时间、不改变裁切或音量。', true);
  }));
  $('audioSync').addEventListener('click', run(async () => review(await bridge('propose_audio_sync', {
    ...base(), item: $('audioSyncItem').value, sound: $('audioSyncSound').value
  }), '按共同波形估计起点。请在应用后试听并核对；这不是口型识别。', true)));
  $('audioMute').addEventListener('click', run(() => propose([{ op: 'update_sound', sound: $('audioSyncSound').value, fields: { gain: 0 } }])));
  $('projectHealth').addEventListener('click', run(async () => {
    const result = await bridge('project_health', { project: state.folder }), root = $('projectHealthResult');
    root.replaceChildren(node('p', result.missing.length ? `发现 ${result.missing.length} 项缺失或未生成素材：` : '未发现缺失的镜头、音频或特效文件。'));
    const kinds = { clips: '镜头', effects: '特效', sounds: '音频' };
    for (const row of result.missing) root.append(node('p', `${kinds[row.kind]} · ${row.name}：${row.reason}`));
    root.append(node('p', result.notice));
  }));
  const overlay = node('div'); overlay.className = 'subtitle-overlay'; overlay.setAttribute('aria-hidden', 'true'); $('screen').append(overlay);
  let shown = '';
  function paint() {
    const text = viewing() ? '' : (state.project?.subtitles || []).filter(c => c.start <= state.playhead && c.end > state.playhead).map(c => c.text).join('\n');
    if (text !== shown) { shown = text; overlay.textContent = text; overlay.hidden = !text; }
    requestAnimationFrame(paint);
  }
  overlay.hidden = true; requestAnimationFrame(paint);
}
