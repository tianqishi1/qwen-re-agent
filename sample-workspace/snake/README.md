# 贪吃蛇（Snake）

一个零依赖、纯前端的贪吃蛇小游戏。直接用浏览器打开 `index.html` 即可玩，不需要安装依赖或启动服务器。

## 快速开始

- 双击 `index.html`（或右键用浏览器打开）
- 可选：用本地服务器打开，例如在本目录执行 `python -m http.server 8000`，然后访问 <http://localhost:8000>

## 玩法

| 操作 | 说明 |
| --- | --- |
| `↑` `↓` `←` `→` / `W` `A` `S` `D` | 控制方向 |
| `空格` / `P` | 暂停 / 继续（未开始时为「开始游戏」） |
| `R` | 重新开始 |
| 滑动屏幕 / 屏幕上的方向键 | 移动端控制 |

规则：

- 吃掉红色果实得 1 分，蛇身变长 1 节；
- 每 5 分升 1 级，移动速度随之加快（最快每步 60ms）；
- 撞墙或咬到自己即结束；勾选「穿墙模式」后可从一侧穿到对侧；
- 最高分记录在浏览器 `localStorage` 中（键：`snake.best.v1`，另有 `snake.wrap.v1`、`snake.sound.v1`）。

## 目录结构

```
snake/
├── index.html            页面结构与脚本引入
├── style.css             深色霓虹主题样式，含移动端方向键
├── js/
│   ├── engine.js         核心逻辑（纯 JS，无 DOM 依赖，浏览器/Node 通用）
│   └── main.js           画布渲染、输入处理、主循环、最高分持久化
└── test/
    ├── engine.test.cjs   核心逻辑单元测试
    └── dom.smoke.cjs     界面层无头冒烟测试（最小 DOM 打桩）
```

`engine.js` 采用 UMD 包装：浏览器中挂到 `window.SnakeEngine`，Node 中可直接 `require`，
因此游戏规则可以脱离界面单独测试。

## 运行测试

```bash
node --test snake/test/engine.test.cjs snake/test/dom.smoke.cjs
```

覆盖内容：初始状态、移动与生长、禁止 180° 掉头、转向输入队列、撞墙 / 咬到自己 / 追尾判定、
穿墙模式、食物生成（不落在蛇身上）、棋盘填满通关、暂停、速度与等级曲线、非法参数校验，
以及界面层的首帧渲染、按键控制、结束浮层、暂停空转、穿墙开关持久化与虚拟方向键。

## 可调参数

在 `js/engine.js` 中：

- `tickInterval(score, { base, min, perFood })`：`base` 初始步进间隔（默认 150ms）、`min` 最快间隔（60ms）、`perFood` 每吃一个食物加速的毫秒数（4ms）。
- `POINTS_PER_LEVEL`：每升一级所需分数（默认 5）。
- `new SnakeGame({ cols, rows, wrap, rng, initialLength })`：棋盘尺寸（默认 20×20）、是否穿墙、随机数源、初始长度。

在 `js/main.js` 中：

- `COLS` / `ROWS`：棋盘格数（默认 20×20，画布尺寸自适应）。

## 说明

- 画面使用 `requestAnimationFrame` + 固定步长累加器推进，逻辑速度与显示刷新率解耦（暂停或切到后台再回来不会追帧卡顿）。
- 配色、圆角、发光等纯 Canvas 绘制，支持高 DPI 屏（`devicePixelRatio` 最高按 2 倍渲染）。
