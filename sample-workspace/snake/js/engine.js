/*!
 * 贪吃蛇核心逻辑（纯 JavaScript，无 DOM 依赖）
 * - 浏览器：window.SnakeEngine
 * - Node  ：require('./engine.js')
 *
 * 该模块只负责「状态推进」，不关心渲染与输入设备，因此可以单独做单元测试。
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.SnakeEngine = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  /** 四个方向对应的坐标增量 */
  const DIRECTIONS = Object.freeze({
    up: Object.freeze({ x: 0, y: -1 }),
    down: Object.freeze({ x: 0, y: 1 }),
    left: Object.freeze({ x: -1, y: 0 }),
    right: Object.freeze({ x: 1, y: 0 }),
  });

  /** 反向映射，用于禁止 180° 掉头 */
  const OPPOSITE = Object.freeze({ up: 'down', down: 'up', left: 'right', right: 'left' });

  const STATUS = Object.freeze({
    ready: 'ready',     // 等待玩家按方向键开始
    running: 'running',
    paused: 'paused',
    over: 'over',       // 撞墙或咬到自己
    won: 'won',         // 棋盘被填满
  });

  /** 键盘按键 -> 方向名 */
  const KEY_DIRECTIONS = Object.freeze({
    ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
    w: 'up', a: 'left', s: 'down', d: 'right',
    W: 'up', A: 'left', S: 'down', D: 'right',
  });

  const POINTS_PER_LEVEL = 5;

  /** 得分 -> 等级（每 5 分升 1 级） */
  function levelFor(score) {
    return 1 + Math.floor(Math.max(0, score) / POINTS_PER_LEVEL);
  }

  /** 得分 -> 每步间隔毫秒数（越小越快），分数越高速度越快并逐步收敛 */
  function tickInterval(score, { base = 150, min = 60, perFood = 4 } = {}) {
    return Math.max(min, base - Math.max(0, score) * perFood);
  }

  class SnakeGame {
    constructor({ cols = 20, rows = 20, wrap = false, rng = Math.random, initialLength = 3 } = {}) {
      if (!Number.isInteger(cols) || cols < 2) throw new RangeError('cols 必须是 >= 2 的整数');
      if (!Number.isInteger(rows) || rows < 1) throw new RangeError('rows 必须是 >= 1 的整数');
      if (typeof rng !== 'function') throw new TypeError('rng 必须是函数');

      this.cols = cols;
      this.rows = rows;
      this.wrap = Boolean(wrap);
      this.rng = rng;
      this.initialLength = Math.max(1, Math.floor(initialLength));
      this.reset();
    }

    /** 重置为一局新游戏 */
    reset() {
      const len = Math.max(1, Math.min(this.initialLength, this.cols * this.rows));
      const startY = Math.floor(this.rows / 2);
      const startX = Math.max(len - 1, Math.floor(this.cols / 2));

      /** 蛇身：索引 0 为蛇头 */
      this.snake = [];
      for (let i = 0; i < len; i += 1) this.snake.push({ x: startX - i, y: startY });

      this.direction = 'right';
      this.queue = [];          // 待处理的转向输入（最多缓存 2 个，保证快速连按不丢输入）
      this.score = 0;
      this.eaten = 0;
      this.stepCount = 0;
      this.food = null;
      this.status = STATUS.ready;

      this.spawnFood();
      if (!this.food) this.status = STATUS.won; // 棋盘一开始就被填满（极端小棋盘）
      return this;
    }

    get head() { return this.snake[0]; }
    get length() { return this.snake.length; }
    get level() { return levelFor(this.score); }
    get isOver() { return this.status === STATUS.over || this.status === STATUS.won; }

    start() {
      if (this.status !== STATUS.ready) return false;
      this.status = STATUS.running;
      return true;
    }

    pause() {
      if (this.status !== STATUS.running) return false;
      this.status = STATUS.paused;
      return true;
    }

    resume() {
      if (this.status !== STATUS.paused) return false;
      this.status = STATUS.running;
      return true;
    }

    togglePause() {
      if (this.status === STATUS.running) return this.pause();
      if (this.status === STATUS.paused) return this.resume();
      return false;
    }

    /**
     * 记录一次转向。返回是否接受该输入。
     * - 忽略非法方向、与「上一个待执行方向」相同或相反的方向
     * - 队列最多缓存 2 个输入
     */
    setDirection(name) {
      if (!Object.prototype.hasOwnProperty.call(DIRECTIONS, name)) return false;
      if (this.isOver) return false;

      const last = this.queue.length ? this.queue[this.queue.length - 1] : this.direction;
      if (name === last || name === OPPOSITE[last]) return false;
      if (this.queue.length >= 2) return false;

      this.queue.push(name);
      if (this.status === STATUS.ready) this.status = STATUS.running;
      return true;
    }

    /** 在空格子中随机放一个食物；无处可放时返回 null（表示通关） */
    spawnFood() {
      const occupied = new Set(this.snake.map((s) => s.y * this.cols + s.x));
      const free = [];
      for (let y = 0; y < this.rows; y += 1) {
        for (let x = 0; x < this.cols; x += 1) {
          if (!occupied.has(y * this.cols + x)) free.push({ x, y });
        }
      }
      if (free.length === 0) {
        this.food = null;
        return null;
      }
      const idx = Math.min(free.length - 1, Math.floor(this.rng() * free.length));
      this.food = free[idx];
      return this.food;
    }

    /**
     * 推进一步。
     * @returns {{moved:boolean, ate:boolean, dead:boolean, won:boolean, scored:number}}
     */
    step() {
      if (this.status === STATUS.over) return { moved: false, ate: false, dead: true, won: false, scored: 0 };
      if (this.status === STATUS.won) return { moved: false, ate: false, dead: false, won: true, scored: 0 };
      if (this.status === STATUS.paused) return { moved: false, ate: false, dead: false, won: false, scored: 0 };
      if (this.status === STATUS.ready) this.status = STATUS.running;

      if (this.queue.length) this.direction = this.queue.shift();

      const dir = DIRECTIONS[this.direction];
      const head = this.snake[0];
      let nx = head.x + dir.x;
      let ny = head.y + dir.y;

      if (this.wrap) {
        nx = (nx + this.cols) % this.cols;
        ny = (ny + this.rows) % this.rows;
      } else if (nx < 0 || ny < 0 || nx >= this.cols || ny >= this.rows) {
        this.status = STATUS.over;
        return { moved: false, ate: false, dead: true, won: false, scored: 0 };
      }

      const ate = Boolean(this.food && this.food.x === nx && this.food.y === ny);
      // 不生长时蛇尾会让出位置，因此尾巴当前所在的格子是允许进入的
      const body = ate ? this.snake : this.snake.slice(0, -1);
      if (body.some((s) => s.x === nx && s.y === ny)) {
        this.status = STATUS.over;
        return { moved: false, ate: false, dead: true, won: false, scored: 0 };
      }

      this.snake.unshift({ x: nx, y: ny });
      this.stepCount += 1;

      let scored = 0;
      if (ate) {
        this.score += 1;
        this.eaten += 1;
        scored = 1;
        this.spawnFood();
        if (!this.food) this.status = STATUS.won;
      } else {
        this.snake.pop();
      }

      return { moved: true, ate, dead: false, won: this.status === STATUS.won, scored };
    }
  }

  return { DIRECTIONS, OPPOSITE, STATUS, KEY_DIRECTIONS, SnakeGame, tickInterval, levelFor, POINTS_PER_LEVEL };
});
