'use strict';

/**
 * 界面层冒烟测试（无头运行，不需要浏览器）：
 * 用最小 DOM 打桩加载 engine.js + main.js，驱动主循环与键盘输入，
 * 验证「渲染 -> 步进 -> 吃到食物/撞墙 -> 浮层与 HUD」整条链路不抛异常且状态正确。
 *
 * 运行：node --test snake/test/dom.smoke.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');

/* ------------------------- 最小 DOM 打桩 ------------------------- */
const gradient = { addColorStop() {} };
const ctxStub = new Proxy({}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === 'createLinearGradient' || prop === 'createRadialGradient') return () => gradient;
    return () => undefined; // 其余绘图 API 一律 no-op
  },
  set(target, prop, value) {
    target[prop] = value;
    return true;
  },
});

function makeEl(id) {
  const listeners = {};
  return {
    id,
    textContent: '',
    disabled: false,
    checked: false,
    style: {},
    dataset: {},
    clientWidth: 640,
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatch(type, event) { (listeners[type] || []).forEach((fn) => fn(event)); },
    blur() {},
    closest() { return null; },
    focus() {},
  };
}

const elements = {};
for (const id of ['score', 'best', 'level', 'overlay', 'overlay-title', 'overlay-text',
  'overlay-btn', 'pause-btn', 'restart-btn', 'wrap-toggle', 'sound-toggle']) {
  elements[id] = makeEl(id);
}
const canvas = makeEl('board');
canvas.getContext = () => ctxStub;
elements.board = canvas;

const stage = makeEl('stage');
const dpad = makeEl('dpad');

const localStore = {};
const rafQueue = [];
const windowListeners = {};

const windowStub = {
  innerWidth: 800,
  innerHeight: 900,
  devicePixelRatio: 1,
  localStorage: {
    getItem: (k) => (k in localStore ? localStore[k] : null),
    setItem: (k, v) => { localStore[k] = String(v); },
  },
  addEventListener(type, fn) { (windowListeners[type] = windowListeners[type] || []).push(fn); },
  dispatch(type, event) { (windowListeners[type] || []).forEach((fn) => fn(event)); },
  requestAnimationFrame(fn) { rafQueue.push(fn); return rafQueue.length; },
  SnakeEngine: require('../js/engine.js'),
};

global.window = windowStub;
global.document = {
  getElementById: (id) => elements[id] || null,
  querySelector: (sel) => (sel === '.stage' ? stage : sel === '.dpad' ? dpad : null),
};

/* ------------------------- 加载界面层 ------------------------- */
let clock = 0;
function driveFrames(count, stepMs = 200) {
  for (let i = 0; i < count; i += 1) {
    clock += stepMs;
    const cb = rafQueue.shift();
    assert.ok(cb, '主循环应持续通过 requestAnimationFrame 排队');
    cb(clock);
  }
}

test('界面层：加载后完成首帧渲染并更新 HUD', () => {
  require('../js/main.js');
  assert.ok(rafQueue.length > 0, '应已排入首帧');
  clock += 16;
  rafQueue.shift()(clock);
  assert.equal(elements.score.textContent, '0');
  assert.equal(elements.level.textContent, '1');
  assert.equal(elements['overlay-title'].textContent, '准备开始');
});

test('界面层：方向键开始游戏并推进，撞墙后弹出结束浮层', () => {
  windowStub.dispatch('keydown', { key: 'ArrowUp', code: 'ArrowUp', preventDefault() {} });
  assert.ok(elements.overlay.classList.contains('hidden'), '开始后浮层应隐藏');

  // 10 行的棋盘从中间向上走，必然撞墙结束
  driveFrames(12);

  assert.ok(!elements.overlay.classList.contains('hidden'), '结束后应显示浮层');
  assert.equal(elements['overlay-title'].textContent, '游戏结束');
  assert.equal(elements['overlay-btn'].textContent, '再来一局');
  assert.equal(elements['pause-btn'].disabled, true);
});

test('界面层：R 键重开、空格暂停/继续', () => {
  windowStub.dispatch('keydown', { key: 'r', code: 'KeyR', preventDefault() {} });
  assert.equal(elements['overlay-title'].textContent, '准备开始');
  assert.equal(elements.score.textContent, '0');

  windowStub.dispatch('keydown', { key: ' ', code: 'Space', preventDefault() {} });
  assert.ok(elements.overlay.classList.contains('hidden'), '空格应开始游戏');

  windowStub.dispatch('keydown', { key: 'p', code: 'KeyP', preventDefault() {} });
  assert.equal(elements['overlay-title'].textContent, '已暂停');
  assert.equal(elements['pause-btn'].textContent, '继续');

  windowStub.dispatch('keydown', { key: 'p', code: 'KeyP', preventDefault() {} });
  assert.ok(elements.overlay.classList.contains('hidden'), '再次按 P 应继续游戏');
});

test('界面层：暂停期间主循环不推进', () => {
  windowStub.dispatch('keydown', { key: 'p', code: 'KeyP', preventDefault() {} }); // 暂停
  const before = elements.score.textContent;
  driveFrames(30); // 长时间空转
  assert.equal(elements.score.textContent, before, '暂停时不应计分/移动');
  windowStub.dispatch('keydown', { key: 'p', code: 'KeyP', preventDefault() {} }); // 继续
});

test('界面层：穿墙开关会重开一局并写入本地存储', () => {
  elements['wrap-toggle'].checked = true;
  elements['wrap-toggle'].dispatch('change', {});
  assert.equal(localStore['snake.wrap.v1'], '1');
  assert.equal(elements['overlay-title'].textContent, '准备开始');

  // 穿墙模式下向同一方向连续前进不会撞墙结束
  windowStub.dispatch('keydown', { key: 'ArrowLeft', code: 'ArrowLeft', preventDefault() {} });
  driveFrames(40);
  assert.ok(elements.overlay.classList.contains('hidden'), '穿墙模式不应撞墙结束');
});

test('界面层：虚拟方向键按钮可用', () => {
  windowStub.dispatch('keydown', { key: 'r', code: 'KeyR', preventDefault() {} });
  dpad.closest = null;
  dpad.dispatch('click', {
    target: { closest: () => ({ dataset: { dir: 'up' }, blur() {} }) },
  });
  assert.ok(elements.overlay.classList.contains('hidden'), '点击方向键应开始游戏');
});
