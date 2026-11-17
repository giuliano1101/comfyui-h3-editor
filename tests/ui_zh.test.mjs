import assert from 'node:assert/strict';
import test from 'node:test';
import { uiMessage, providerName, operationName, fieldName } from '../editor/ui-zh.js';

test('known server messages and states have Chinese presentation labels', () => {
  assert.equal(uiMessage('queued'), '排队中');
  assert.equal(uiMessage('succeeded'), '已完成');
  assert.equal(providerName('minimax'), 'MiniMax 官方');
  assert.equal(operationName('add_clip'), '新建镜头');
  assert.equal(fieldName('action'), '画面描述');
});

test('unknown content, user prompts, model IDs and paths pass through unchanged', () => {
  for (const value of ['A paper boat. 湖面纸船。', 'MiniMax-Hailuo-2.3', 'h3take_projects/中文_剪辑/clips/a.mp4', 'queued is part of my prompt', 'new-server-state']) {
    assert.equal(uiMessage(value), value);
  }
});
