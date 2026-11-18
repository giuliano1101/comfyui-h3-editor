// Functional UI flows with a small DOM/HTTP fixture; no browser, keys or network.
import assert from 'node:assert/strict';
import test from 'node:test';
import { installProviderPanel } from '../editor/providers.js';

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.value = ''; this.textContent = '';
    this.children = []; this.listeners = new Map(); this.hidden = false;
    this.checked = false; this.disabled = false; this.open = false;
  }
  addEventListener(name, callback) { this.listeners.set(name, [...(this.listeners.get(name) || []), callback]); }
  async emit(name) { for (const callback of this.listeners.get(name) || []) await callback({ key: '', stopPropagation() {} }); }
  replaceChildren(...nodes) { this.children = nodes; if (this.tagName === 'SELECT') this.value = nodes[0]?.value || ''; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute() {}
  removeAttribute() {}
  focus() {}
  pause() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function setup() {
  const selects = new Set(['providerSelect', 'providerModel', 'providerSeconds', 'providerResolution']);
  const nodes = new Map();
  const node = (id) => { if (!nodes.has(id)) nodes.set(id, new Element(selects.has(id) ? 'select' : 'div')); return nodes.get(id); };
  const original = { document: globalThis.document, window: globalThis.window, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  globalThis.document = { getElementById: node, createElement: tag => new Element(tag) };
  globalThis.window = { addEventListener() {} };
  globalThis.setInterval = () => 1;
  globalThis.clearInterval = () => {};
  const state = { folder: 'Test', clip: 'c1', project: { revision: 1, clips: [{ id: 'c1', name: 'Example', action: 'A paper boat' }] } };
  const jobs = [], calls = [], adopted = [];
  const providers = {
    simulation: { available: true, simulated: true, models: ['simulation'], resolutions: ['test'], durations: [1,2,3,4], protocol: 'simulation-v1', estimate_cents: 0 },
    minimax: { available: true, simulated: false, enabled: true, configured: true, service: 'MiniMax official', status: 'live API unverified', cancellation: false,
      models: ['MiniMax-Hailuo-2.3'], resolutions: ['768P','1080P'], durations: [6,10], protocol: 'minimax-video-v1', quotes_cents: { 'MiniMax-Hailuo-2.3:768P:6': 10, 'MiniMax-Hailuo-2.3:1080P:6': 20 } }
  };
  const api = async path => {
    if (path === '/providers') return { providers };
    if (path.startsWith('/provider-jobs?')) return { jobs, usage: { active: jobs.length, max_active: 2, connection_today: jobs.length, connection_daily_limit: 20, remaining_cents: 100 } };
    if (path === '/approvals') return { proposals: [] };
    if (path === '/project/Test') return state.project;
    throw new Error('Unexpected local read: ' + path);
  };
  const postJson = async (path, body) => {
    calls.push({ path, body });
    if (path === '/provider-jobs/preview') return { id: 'approval', action: 'provider_submit', payload: body, simulated: true, estimate_cents: 0, cost_notice: 'No charge' };
    if (path === '/approvals/approve') return { approved: true };
    if (path === '/provider-jobs') {
      const job = { id: 'job1', provider: 'simulation', project: 'Test', model: 'simulation', seconds: 4, resolution: 'test', simulated: true, state: 'queued' };
      jobs.push(job); return job;
    }
    if (path === '/bridge/call' && body.name === 'propose_ops') return { id: 'voice-approval', action: 'ops', payload: body.arguments };
    if (path === '/bridge/call' && body.name === 'execute_ops') { state.project = { revision: 2, clips: [...state.project.clips, { id: 'c2', action: body.arguments.ops[0].fields.action }] }; return { revision: 2, summary: {} }; }
    throw new Error('Unexpected local write: ' + path);
  };
  installProviderPanel({ state, api, postJson, toast() {}, flush: async () => {}, adopt(project) {
    assert.equal(typeof project, 'object', 'job.project is a folder name, not a project document'); adopted.push(project);
  } });
  return { node, state, jobs, calls, adopted, providers, restore() { Object.assign(globalThis, original); } };
}

test('generation preview discloses input and performs no submission before confirmation', async () => {
  const f = setup();
  try {
    await f.node('providersOpen').emit('click');
    assert.equal(f.node('providerPreview').disabled, false);
    assert.match(f.node('providerLimits').textContent, /20 次今日提交/);
    await f.node('providerPreview').emit('click');
    assert.equal(f.node('providerReview').open, true);
    assert.equal(f.node('providerConfirm').disabled, true);
    assert.equal(f.calls.some(c => c.path === '/provider-jobs'), false);
    assert.equal(f.calls[0].body.prompt, 'A paper boat');
  } finally { f.restore(); }
});

test('confirmed simulation handles job folder string without adopting it as a project; duplicate clicks submit once', async () => {
  const f = setup();
  try {
    await f.node('providersOpen').emit('click');
    await f.node('providerPreview').emit('click');
    f.node('providerReviewAck').checked = true;
    await f.node('providerReviewAck').emit('change');
    await Promise.all([f.node('providerConfirm').emit('click'), f.node('providerConfirm').emit('click')]);
    assert.equal(f.calls.filter(c => c.path === '/provider-jobs').length, 1);
    assert.equal(f.jobs.length, 1);
    assert.equal(f.adopted.length, 0);
    assert.equal(f.node('providerReview').open, false);
    assert.equal(f.node('providerReviewError').hidden, true);
  } finally { f.restore(); }
});

test('unknown quotes block generation and 1080P removes ten-second option', async () => {
  const f = setup();
  try {
    await f.node('providersOpen').emit('click');
    f.node('providerSelect').value = 'minimax'; await f.node('providerSelect').emit('change');
    assert.match(f.node('providerCost').textContent, /\$0.10 美元/);
    f.node('providerSeconds').value = '10'; await f.node('providerSeconds').emit('change');
    assert.equal(f.node('providerPreview').disabled, true);
    f.node('providerResolution').value = '1080P'; await f.node('providerResolution').emit('change');
    assert.deepEqual(f.node('providerSeconds').children.map(n => n.value), ['6']);
    assert.match(f.node('providerCost').textContent, /\$0.20 美元/);
    assert.equal(f.node('providerPreview').disabled, false);
  } finally { f.restore(); }
});

test('typed transcript is a local edit preview and cannot trigger generation', async () => {
  const f = setup();
  try {
    await f.node('providersOpen').emit('click');
    f.node('voiceTranscript').value = 'A second paper boat';
    await f.node('voicePreview').emit('click');
    assert.equal(f.state.project.clips.length, 1);
    f.node('providerReviewAck').checked = true;
    await f.node('providerConfirm').emit('click');
    assert.equal(f.state.project.clips.length, 2);
    assert.equal(f.adopted.length, 1);
    assert.equal(f.calls.some(c => c.path === '/provider-jobs'), false);
  } finally { f.restore(); }
});

test('timed-out microphone permission stops a late grant and leaves controls usable', async () => {
  const f = setup();
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const originalTimeout = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  let grant, timer, stopped = 0;
  try {
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: () => new Promise(resolve => { grant = resolve; }) } } });
    window.MediaRecorder = function () {};
    globalThis.setTimeout = (callback, milliseconds) => { timer = { callback, milliseconds }; return 1; };
    globalThis.clearTimeout = () => {};
    await f.node('providersOpen').emit('click');
    const pending = f.node('voiceRecord').emit('click');
    assert.equal(timer.milliseconds, 15000);
    assert.equal(f.node('voiceRecord').disabled, true);
    timer.callback(); await pending;
    assert.equal(f.node('voiceRecord').disabled, false);
    assert.match(f.node('providerError').textContent, /麦克风授权未完成/);
    grant({ getTracks: () => [{ stop() { stopped++; } }] });
    await Promise.resolve(); await Promise.resolve();
    assert.equal(stopped, 1, 'late permission must not leave a live audio track');
    assert.equal(f.calls.length, 0, 'permission timeout must not upload audio or generate');
  } finally {
    if (navigatorDescriptor) Object.defineProperty(globalThis, 'navigator', navigatorDescriptor); else delete globalThis.navigator;
    globalThis.setTimeout = originalTimeout; globalThis.clearTimeout = originalClear; f.restore();
  }
});

test('model capabilities narrow protocol options and CNY quotes do not authorize unpriced image generation', async () => {
  const f = setup();
  try {
    const model = 'doubao-seedance-2-5-flash-261115';
    f.providers.seedance = { available: true, enabled: true, configured: true, simulated: false, protocol: 'ark-video-v3',
      models: [model], resolutions: ['480p','720p','4k'], durations: [4,5,6], quote_currency: 'CNY',
      quotes_cents: { [`${model}:480p:4:text`]: 200 },
      model_capabilities: { [model]: { resolutions: ['480p','720p'], durations: [4], modes: ['text','image','reference'] } } };
    await f.node('providersOpen').emit('click');
    f.node('providerSelect').value = 'seedance'; await f.node('providerSelect').emit('change');
    assert.deepEqual(f.node('providerResolution').children.map(n => n.value), ['480p','720p']);
    assert.deepEqual(f.node('providerSeconds').children.map(n => n.value), ['4']);
    assert.match(f.node('providerCost').textContent, /¥2.00 人民币/);
    f.node('providerMode').value = 'image'; await f.node('providerMode').emit('change');
    assert.equal(f.node('providerPreview').disabled, true);
    assert.equal(f.calls.length, 0);
  } finally { f.restore(); }
});
