'use strict';

/**
 * 核心逻辑单元测试：node --test snake/test/engine.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  SnakeGame, STATUS, DIRECTIONS, tickInterval, levelFor, KEY_DIRECTIONS,
} = require('../js/engine.js');

/** 固定随机数，便于断言食物位置 */
const firstFree = () => 0;
const lastFree = () => 0.999999;

function newGame(options = {}) {
  return new SnakeGame({ cols: 10, rows: 10, rng: firstFree, ...options });
}

test('初始状态：蛇在中央、长度 3、状态为 ready', () => {
  const game = newGame();
  assert.equal(game.length, 3);
  assert.deepEqual(game.head, { x: 5, y: 5 });
  assert.equal(game.direction, 'right');
  assert.equal(game.status, STATUS.ready);
  assert.equal(game.score, 0);
  assert.equal(game.level, 1);
  assert.ok(game.food);
  assert.ok(!game.snake.some((s) => s.x === game.food.x && s.y === game.food.y), '食物不应落在蛇身上');
});

test('普通移动：前进一格、长度不变、自动开始', () => {
  const game = newGame();
  const res = game.step();
  assert.equal(res.moved, true);
  assert.equal(res.ate, false);
  assert.equal(game.status, STATUS.running);
  assert.deepEqual(game.head, { x: 6, y: 5 });
  assert.equal(game.length, 3);
  assert.equal(game.stepCount, 1);
});

test('吃到食物：长度 +1、得分 +1、食物重新生成', () => {
  const game = newGame();
  const food = { x: game.head.x + 1, y: game.head.y };
  game.food = food;
  const res = game.step();
  assert.equal(res.ate, true);
  assert.equal(res.scored, 1);
  assert.equal(game.score, 1);
  assert.equal(game.eaten, 1);
  assert.equal(game.length, 4);
  assert.ok(game.food && !(game.food.x === food.x && game.food.y === food.y), '旧食物应被替换');
});

test('禁止 180° 掉头与非法方向', () => {
  const game = newGame();
  assert.equal(game.setDirection('left'), false, '向右时不能立即向左');
  assert.equal(game.setDirection('right'), false, '与当前方向相同也不记录');
  assert.equal(game.setDirection('diagonal'), false, '非法方向名应被忽略');
  assert.equal(game.setDirection('up'), true);
});

test('转向队列：一个 tick 内连按两次可连续转弯，超出的输入被丢弃', () => {
  const game = newGame();
  game.setDirection('up');
  game.setDirection('left');
  assert.equal(game.setDirection('down'), false, '队列最多缓存 2 个输入');

  game.step();
  assert.equal(game.direction, 'up');
  assert.deepEqual(game.head, { x: 5, y: 4 });

  game.step();
  assert.equal(game.direction, 'left');
  assert.deepEqual(game.head, { x: 4, y: 4 });
});

test('撞墙：非穿墙模式下一出界即结束', () => {
  const game = newGame({ wrap: false });
  game.snake = [{ x: 0, y: 2 }, { x: 1, y: 2 }, { x: 2, y: 2 }];
  game.direction = 'left';
  const res = game.step();
  assert.equal(res.dead, true);
  assert.equal(game.status, STATUS.over);
  assert.equal(game.isOver, true);
});

test('穿墙模式：从左边出去从右边进来', () => {
  const game = newGame({ wrap: true });
  game.snake = [{ x: 0, y: 2 }, { x: 1, y: 2 }, { x: 2, y: 2 }];
  game.direction = 'left';
  const res = game.step();
  assert.equal(res.dead, false);
  assert.equal(game.status, STATUS.running);
  assert.deepEqual(game.head, { x: game.cols - 1, y: 2 });
  assert.equal(game.length, 3);
});

test('咬到自己：撞上身体则结束', () => {
  const game = newGame();
  game.snake = [{ x: 2, y: 2 }, { x: 2, y: 3 }, { x: 1, y: 3 }, { x: 1, y: 2 }];
  game.direction = 'down'; // 下一格 (2,3) 是自己的身体
  const res = game.step();
  assert.equal(res.dead, true);
  assert.equal(game.status, STATUS.over);
});

test('追尾允许：下一格只是蛇尾时不算撞到自己', () => {
  const game = newGame();
  game.snake = [{ x: 1, y: 1 }, { x: 2, y: 1 }, { x: 2, y: 2 }, { x: 1, y: 2 }];
  game.direction = 'down'; // 下一格 (1,2) 是尾巴，尾巴会让位
  const res = game.step();
  assert.equal(res.dead, false);
  assert.deepEqual(game.head, { x: 1, y: 2 });
  assert.equal(game.length, 4);
});

test('食物总是落在空格子上（含随机数取最大值的边界）', () => {
  const game = newGame({ rng: lastFree });
  for (let i = 0; i < 200; i += 1) {
    game.food = null;
    const food = game.spawnFood();
    assert.ok(food, '棋盘未满时应总能生成食物');
    assert.ok(!game.snake.some((s) => s.x === food.x && s.y === food.y));
    assert.ok(food.x >= 0 && food.x < game.cols && food.y >= 0 && food.y < game.rows);
  }
});

test('棋盘被填满时判定通关（won）', () => {
  const game = new SnakeGame({ cols: 3, rows: 1, initialLength: 3, rng: firstFree });
  assert.equal(game.length, 3);
  assert.equal(game.food, null);
  assert.equal(game.status, STATUS.won);
  assert.equal(game.step().won, true);
});

test('暂停时 step() 不推进任何状态', () => {
  const game = newGame();
  game.start();
  assert.equal(game.pause(), true);
  const before = JSON.stringify(game.snake);
  const res = game.step();
  assert.equal(res.moved, false);
  assert.equal(JSON.stringify(game.snake), before);
  assert.equal(game.status, STATUS.paused);
  assert.equal(game.resume(), true);
  assert.equal(game.step().moved, true);
});

test('结束后不再接受输入与推进', () => {
  const game = newGame({ wrap: false });
  game.snake = [{ x: 0, y: 2 }, { x: 1, y: 2 }, { x: 2, y: 2 }];
  game.direction = 'left';
  game.step();
  assert.equal(game.status, STATUS.over);
  assert.equal(game.setDirection('up'), false);
  assert.equal(game.step().moved, false);
  assert.equal(game.step().dead, true);
});

test('速度与等级：分数越高越快，并有下限', () => {
  assert.equal(tickInterval(0), 150);
  assert.equal(tickInterval(5), 130);
  assert.equal(tickInterval(1000), 60, '间隔不会低于最小值');
  assert.equal(levelFor(0), 1);
  assert.equal(levelFor(4), 1);
  assert.equal(levelFor(5), 2);
  assert.equal(levelFor(12), 3);
});

test('方向表与按键映射完整', () => {
  for (const name of ['up', 'down', 'left', 'right']) {
    assert.ok(DIRECTIONS[name], '缺少方向 ' + name);
  }
  assert.equal(KEY_DIRECTIONS.ArrowUp, 'up');
  assert.equal(KEY_DIRECTIONS.a, 'left');
  assert.equal(KEY_DIRECTIONS.D, 'right');
});

test('非法构造参数会抛错', () => {
  assert.throws(() => new SnakeGame({ cols: 1 }), RangeError);
  assert.throws(() => new SnakeGame({ rows: 0 }), RangeError);
  assert.throws(() => new SnakeGame({ rng: 'not-a-function' }), TypeError);
});

test('reset() 可完整重开一局', () => {
  const game = newGame();
  game.setDirection('down');
  game.step();
  game.score = 7;
  game.reset();
  assert.equal(game.score, 0);
  assert.equal(game.length, 3);
  assert.equal(game.status, STATUS.ready);
  assert.equal(game.direction, 'right');
  assert.deepEqual(game.head, { x: 5, y: 5 });
});
