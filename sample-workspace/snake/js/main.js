/*!
 * 贪吃蛇界面层：画布渲染、输入处理、主循环、最高分持久化。
 * 依赖 engine.js 暴露的 window.SnakeEngine。
 */
(function () {
  'use strict';

  const { SnakeGame, DIRECTIONS, STATUS, KEY_DIRECTIONS, tickInterval, levelFor } = window.SnakeEngine;

  const COLS = 20;
  const ROWS = 20;
  const STORAGE_BEST = 'snake.best.v1';
  const STORAGE_WRAP = 'snake.wrap.v1';
  const STORAGE_SOUND = 'snake.sound.v1';

  const $ = (id) => document.getElementById(id);

  const canvas = $('board');
  const ctx = canvas.getContext('2d');
  const stage = document.querySelector('.stage');
  const scoreEl = $('score');
  const bestEl = $('best');
  const levelEl = $('level');
  const overlay = $('overlay');
  const overlayTitle = $('overlay-title');
  const overlayText = $('overlay-text');
  const overlayBtn = $('overlay-btn');
  const pauseBtn = $('pause-btn');
  const restartBtn = $('restart-btn');
  const wrapToggle = $('wrap-toggle');
  const soundToggle = $('sound-toggle');
  const dpad = document.querySelector('.dpad');

  /* ---------------- 本地存储（无痕模式等场景下静默降级） ---------------- */
  const store = {
    get(key, fallback) {
      try {
        const value = window.localStorage.getItem(key);
        return value === null ? fallback : value;
      } catch (err) {
        return fallback;
      }
    },
    set(key, value) {
      try { window.localStorage.setItem(key, String(value)); } catch (err) { /* 忽略 */ }
    },
  };

  /* ---------------- 状态 ---------------- */
  const game = new SnakeGame({ cols: COLS, rows: ROWS, wrap: store.get(STORAGE_WRAP, '0') === '1' });
  wrapToggle.checked = game.wrap;

  let best = Number(store.get(STORAGE_BEST, '0')) || 0;
  let soundOn = store.get(STORAGE_SOUND, '1') === '1';
  soundToggle.checked = soundOn;

  let cell = 20;
  let boardSize = COLS * 20;
  let lastTime = 0;
  let acc = 0;        // 用于固定步长累加的时间余量
  let eatPulse = 0;   // 吃到食物时的头部缩放动效

  /* ---------------- 音效 ---------------- */
  let audioCtx = null;

  function beep(freq, duration, type) {
    if (!soundOn) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = audioCtx || new Ctx();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      const now = audioCtx.currentTime;
      osc.type = type || 'triangle';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.07, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + duration + 0.02);
    } catch (err) { /* 音效失败不影响游戏 */ }
  }

  /* ---------------- 画布尺寸 ---------------- */
  function resize() {
    const availWidth = Math.max(240, (stage ? stage.clientWidth : window.innerWidth) - 16);
    const availHeight = Math.max(240, window.innerHeight - 330);
    const maxSize = Math.min(availWidth, availHeight, 640);

    cell = Math.max(10, Math.floor(maxSize / COLS));
    boardSize = cell * COLS;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.style.width = boardSize + 'px';
    canvas.style.height = boardSize + 'px';
    canvas.width = Math.round(boardSize * dpr);
    canvas.height = Math.round(boardSize * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /* ---------------- 绘制 ---------------- */
  function roundRect(x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }

  function bodyColor(t) {
    const hue = 172 + t * 30;
    const light = 60 - t * 16;
    return 'hsl(' + hue.toFixed(1) + ', 78%, ' + light.toFixed(1) + '%)';
  }

  function drawBoard() {
    const size = boardSize;

    const bg = ctx.createLinearGradient(0, 0, size, size);
    bg.addColorStop(0, '#0b1424');
    bg.addColorStop(1, '#111d33');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, size, size);

    ctx.strokeStyle = 'rgba(120, 190, 255, 0.07)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 1; i < COLS; i += 1) {
      const p = i * cell;
      ctx.moveTo(p, 0);
      ctx.lineTo(p, size);
      ctx.moveTo(0, p);
      ctx.lineTo(size, p);
    }
    ctx.stroke();
  }

  function drawFood(time) {
    const food = game.food;
    if (!food) return;

    const cx = (food.x + 0.5) * cell;
    const cy = (food.y + 0.5) * cell;
    const pulse = 1 + Math.sin(time / 170) * 0.08;
    const r = cell * 0.31 * pulse;

    ctx.save();
    ctx.shadowColor = 'rgba(255, 99, 132, 0.85)';
    ctx.shadowBlur = cell * 0.9;
    const grad = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
    grad.addColorStop(0, '#ffe4ec');
    grad.addColorStop(0.45, '#ff6b81');
    grad.addColorStop(1, '#e11d48');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    ctx.fillStyle = 'rgba(255, 255, 255, 0.75)';
    ctx.beginPath();
    ctx.arc(cx - r * 0.3, cy - r * 0.35, r * 0.17, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawSnake() {
    const snake = game.snake;
    const count = snake.length;
    const pad = cell * 0.12;
    const body = cell - pad * 2;
    const lastIndex = Math.max(1, count - 1);

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // 相邻节之间连线，让身体更连贯；穿墙跳跃处（跨屏）不连线
    for (let i = count - 1; i >= 1; i -= 1) {
      const a = snake[i];
      const b = snake[i - 1];
      if (Math.abs(a.x - b.x) > 1 || Math.abs(a.y - b.y) > 1) continue;
      ctx.strokeStyle = bodyColor(i / lastIndex);
      ctx.lineWidth = body * 0.94;
      ctx.beginPath();
      ctx.moveTo((a.x + 0.5) * cell, (a.y + 0.5) * cell);
      ctx.lineTo((b.x + 0.5) * cell, (b.y + 0.5) * cell);
      ctx.stroke();
    }

    // 身体分节
    for (let i = count - 1; i >= 0; i -= 1) {
      const seg = snake[i];
      ctx.fillStyle = bodyColor(i / lastIndex);
      roundRect(seg.x * cell + pad, seg.y * cell + pad, body, body, body * 0.36);
      ctx.fill();
    }

    // 头部（略微高亮 + 吃到食物时弹一下）
    const head = snake[0];
    const scale = 1 + eatPulse * 0.1;
    const hx = (head.x + 0.5) * cell;
    const hy = (head.y + 0.5) * cell;
    const half = (body / 2) * scale;

    ctx.save();
    ctx.shadowColor = 'rgba(34, 211, 238, 0.6)';
    ctx.shadowBlur = cell * 0.7;
    ctx.fillStyle = bodyColor(0);
    roundRect(hx - half, hy - half, half * 2, half * 2, half * 0.72);
    ctx.fill();
    ctx.restore();

    // 眼睛（朝向当前方向）
    const dir = DIRECTIONS[game.direction];
    const perpX = -dir.y;
    const perpY = dir.x;
    const eyeOffset = cell * 0.19;
    const forward = cell * 0.13;

    for (const sign of [1, -1]) {
      const ex = hx + perpX * eyeOffset * sign + dir.x * forward;
      const ey = hy + perpY * eyeOffset * sign + dir.y * forward;
      ctx.fillStyle = '#f8fafc';
      ctx.beginPath();
      ctx.arc(ex, ey, cell * 0.1, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#0f172a';
      ctx.beginPath();
      ctx.arc(ex + dir.x * cell * 0.035, ey + dir.y * cell * 0.035, cell * 0.05, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function draw(time) {
    drawBoard();
    drawFood(time);
    drawSnake();
  }

  /* ---------------- HUD 与浮层 ---------------- */
  function updateHud() {
    scoreEl.textContent = String(game.score);
    bestEl.textContent = String(best);
    levelEl.textContent = String(levelFor(game.score));
  }

  function showOverlay(title, text, btnLabel) {
    overlayTitle.textContent = title;
    overlayText.textContent = text;
    overlayBtn.textContent = btnLabel;
    overlay.classList.remove('hidden');
  }

  function syncOverlay() {
    switch (game.status) {
      case STATUS.ready:
        showOverlay('准备开始', '方向键 / WASD 移动，空格暂停', '开始游戏');
        break;
      case STATUS.paused:
        showOverlay('已暂停', '按空格或点击按钮继续', '继续');
        break;
      case STATUS.over:
        showOverlay('游戏结束', '本局得分 ' + game.score + ' · 等级 ' + game.level, '再来一局');
        break;
      case STATUS.won:
        showOverlay('通关啦 🎉', '棋盘已被填满，得分 ' + game.score, '再玩一次');
        break;
      default:
        overlay.classList.add('hidden');
    }

    if (game.status === STATUS.ready) {
      pauseBtn.textContent = '开始';
      pauseBtn.disabled = false;
    } else if (game.isOver) {
      pauseBtn.textContent = '暂停';
      pauseBtn.disabled = true;
    } else {
      pauseBtn.textContent = game.status === STATUS.paused ? '继续' : '暂停';
      pauseBtn.disabled = false;
    }
  }

  /* ---------------- 操作 ---------------- */
  function restart() {
    game.wrap = wrapToggle.checked;
    game.reset();
    best = Math.max(best, game.score);
    store.set(STORAGE_WRAP, game.wrap ? '1' : '0');
    acc = 0;
    lastTime = 0;
    eatPulse = 0;
    updateHud();
    syncOverlay();
  }

  function togglePause() {
    if (game.status === STATUS.ready) {
      game.start();
    } else if (game.status === STATUS.running || game.status === STATUS.paused) {
      game.togglePause();
    } else {
      return; // 已结束，用「重新开始」
    }
    lastTime = 0;
    syncOverlay();
  }

  function handleStep(res) {
    if (res.ate) {
      eatPulse = 1;
      beep(680, 0.08, 'triangle');
      if (game.score > best) {
        best = game.score;
        store.set(STORAGE_BEST, best);
      }
      updateHud();
    }

    if (res.dead) {
      beep(190, 0.3, 'sawtooth');
      syncOverlay();
    } else if (res.won) {
      beep(920, 0.36, 'sine');
      syncOverlay();
    }
  }

  /* ---------------- 主循环 ---------------- */
  function loop(time) {
    if (!lastTime) lastTime = time;
    let dt = time - lastTime;
    lastTime = time;
    if (dt > 250) dt = 250; // 切换标签页回来时避免一次性追帧

    eatPulse = Math.max(0, eatPulse - dt / 240);

    if (game.status === STATUS.running) {
      acc += dt;
      const interval = tickInterval(game.score);
      while (game.status === STATUS.running && acc >= interval) {
        acc -= interval;
        handleStep(game.step());
      }
      if (game.status !== STATUS.running) acc = 0;
    } else {
      acc = 0;
    }

    draw(time);
    window.requestAnimationFrame(loop);
  }

  /* ---------------- 输入：键盘 ---------------- */
  window.addEventListener('keydown', (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    const dir = KEY_DIRECTIONS[event.key];
    if (dir) {
      event.preventDefault();
      if (game.status === STATUS.ready) game.start();
      game.setDirection(dir);
      syncOverlay();
      return;
    }

    if (event.code === 'Space' || event.key === ' ') {
      event.preventDefault();
      togglePause();
      return;
    }

    if (event.key === 'p' || event.key === 'P') {
      togglePause();
      return;
    }

    if (event.key === 'r' || event.key === 'R') {
      restart();
    }
  });

  /* ---------------- 输入：按钮 / 方向键 ---------------- */
  overlayBtn.addEventListener('click', () => {
    if (game.isOver) restart();
    else togglePause();
    overlayBtn.blur();
  });

  pauseBtn.addEventListener('click', () => {
    togglePause();
    pauseBtn.blur();
  });

  restartBtn.addEventListener('click', () => {
    restart();
    restartBtn.blur();
  });

  wrapToggle.addEventListener('change', restart);

  soundToggle.addEventListener('change', () => {
    soundOn = soundToggle.checked;
    store.set(STORAGE_SOUND, soundOn ? '1' : '0');
  });

  if (dpad) {
    dpad.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-dir]');
      if (!btn) return;
      if (game.status === STATUS.ready) game.start();
      game.setDirection(btn.dataset.dir);
      syncOverlay();
      btn.blur();
    });
  }

  /* ---------------- 输入：滑动手势 ---------------- */
  let touchStart = null;

  canvas.addEventListener('touchstart', (event) => {
    const touch = event.changedTouches[0];
    touchStart = { x: touch.clientX, y: touch.clientY };
  }, { passive: true });

  canvas.addEventListener('touchmove', (event) => {
    event.preventDefault(); // 阻止页面滚动
  }, { passive: false });

  canvas.addEventListener('touchend', (event) => {
    if (!touchStart) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - touchStart.x;
    const dy = touch.clientY - touchStart.y;
    touchStart = null;
    if (Math.hypot(dx, dy) < 24) return;

    const name = Math.abs(dx) > Math.abs(dy)
      ? (dx > 0 ? 'right' : 'left')
      : (dy > 0 ? 'down' : 'up');

    if (game.status === STATUS.ready) game.start();
    game.setDirection(name);
    syncOverlay();
  }, { passive: true });

  canvas.addEventListener('touchcancel', () => { touchStart = null; }, { passive: true });

  /* ---------------- 启动 ---------------- */
  window.addEventListener('resize', resize);
  if (window.ResizeObserver && stage) {
    new ResizeObserver(resize).observe(stage);
  }

  resize();
  updateHud();
  syncOverlay();
  window.requestAnimationFrame(loop);
})();
