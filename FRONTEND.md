# mad 看板 · 前端说明（美化交接文档）

> 目标读者：负责视觉美化 / 前端重构的设计师或开发。  
> 当前状态：功能可用的深色控制台风格单页，未做精细视觉设计。  
> 工作目录：项目根目录（也可在本项目根目录直接预览）

---

## 1. 文件结构

| 文件 | 职责 | 美化时是否要改 |
|------|------|----------------|
| `index.html` | 页面骨架、全部 DOM 结构 | 结构可微调，class/id **必须保留**（JS 靠它们取元素） |
| `web/styles.css` | 全部样式（含 CSS 变量设计令牌） | **主要改动文件** |
| `web/app.js` | 数据获取、筛选、渲染、弹窗、轮询 | 只有在改 DOM 结构时同步改；逻辑不必动 |
| `web/demo-data.js` | 离线演示数据（`window.MAD_DEMO_DATA`） | 不用改 |
| `serve.py` / `src/mad/web.py` | 后端静态服务 + API | 不用改（美化不碰后端） |

预览方式：

```bash
python serve.py --port 8765 --open
# 打开 http://127.0.0.1:8765/
```

无后端时会自动落到 `demo-data.js`，页面仍可完整渲染（适合纯静态调样式）。

---

## 2. 整体布局（从上到下）

```
┌─────────────────────────────────────────────────────────────┐
│  Topbar 顶栏：品牌 Logo | 标题        | 运行时胶囊 | 主按钮组 │
├─────────────────────────────────────────────────────────────┤
│  Task Bar 任务条：TASK 标签 + 研究任务全文                    │
├─────────────────────────────────────────────────────────────┤
│  Stats 统计卡 ×6：消息/轮次/永久负结果/未被挑战/OpenQ/验证闸门 │
├─────────────────────────────────────────────────────────────┤
│  Verdict Banner 判决横幅（PASS 绿 / FAIL 红 / 运行中）        │
├──────────────┬──────────────────────────────┬────────────────┤
│  Sidebar     │  Board 主区                   │  Right 侧栏    │
│  ·标签筛选   │  ·标题 + 显示开关             │  ·永久负结果   │
│  ·图例       │  ·消息时间线 Timeline         │  ·最佳证据结果 │
│  ·停止原因   │    （含父子攻击线程）         │  ·Open Qs      │
│  ·数据源     │                               │  ·停止会话按钮 │
│  ·停止会话   │                               │                │
├──────────────┴──────────────────────────────┴────────────────┤
│  Footer 底栏：三条协议提示（负结果不可删等）                   │
└─────────────────────────────────────────────────────────────┘
```

- 三栏栅格：`220px | 1fr | 300px`，间距 12px，最大宽 1440px 居中。
- 断点：`≤1100px` 改单栏（侧栏内容折到上下）；`≤640px` 统计卡 2 列。

---

## 3. 模块清单（按 DOM 区域）

### 3.1 Topbar 顶栏 `.topbar`

| 元素 | id / class | 说明 |
|------|------------|------|
| 品牌 | `.brand` `.logo` `.brand-text` | 左侧：圆角渐变 Logo「mad」+ 主标题「多 Agent 讨论看板」+ 副标题 |
| 运行时胶囊 | `#runtime-chip` `.runtime-chip` | 仿 Open Design：圆角胶囊，内含小圆标 `#rc-logo`、模式 `#rc-mode`、代理 `#rc-agent`、下拉箭头 |
| 运行时浮层 | `#runtime-panel` `.runtime-panel` | 点胶囊弹出，300px 宽，卡片阴影 |
| 开始讨论 | `#btn-new-session` `.btn.primary` | 打开「新建会话」弹窗 |
| 刷新 | `#btn-reload` `.btn.ghost` | 重新拉 `/api/report` |
| 演示数据 | `#btn-demo` `.btn.ghost` | 强制载入 `demo-data.js` |

**运行时浮层 `#runtime-panel` 内部：**

1. **模式** `#mode-seg`：分段控件两枚按钮  
   - `data-mode="local_cli"` 文案「本地 CLI」  
   - `data-mode="api_key"` 文案「自带 Key」
2. **代理** `#agent-row`（标题 `#rp-agent-title`）：动态按钮列表  
   - 示例：`Claude Code`、`Kimi CLI`；或 `OpenAI 兼容`、`Mock`  
   - 每颗按钮左侧状态点 `.dot-av`（绿=可用 / 灰=未安装）  
   - 选中态 `.agent-btn.active`
3. **模型** `#model-select`：下拉，「Default (CLI config)」或具体模型别名
4. **API 区** `#api-key-fields`（仅「自带 Key」显示）  
   - `#inp-base-url` Base URL 输入  
   - `#inp-api-key` API Key（`type=password`）
5. **底部** `#btn-open-settings`「⚙ 打开执行设置」

### 3.2 Task Bar 任务条 `.task-bar`

- 左：`.task-label` 胶囊「TASK」（等宽、浅蓝底）
- 右：`#task-text` 任务长文，可换行

### 3.3 Stats 统计卡 `.stats`（6 个 `.stat`）

| id | 含义 | 视觉建议 |
|----|------|----------|
| `#st-posts` | 消息总数 | 大数字 |
| `#st-rounds` | 轮次 | 大数字 |
| `#st-dead` | 永久负结果数 | **强调红/紫**（负结果是系统灵魂） |
| `#st-uncontested` | 未被挑战条数 | **强调琥珀色** |
| `#st-questions` | Open Question 数 | 大数字 |
| `#st-verdict` / wrap `#st-verdict-wrap` | 验证闸门 | PASS 时整卡绿边绿字（`.pass`），FAIL 红（`.fail`） |

### 3.4 Verdict Banner `#verdict-banner`

- 通过：`.verdict-banner.pass` 绿底绿字，文案「Verifier 闸门 · 通过 — …」
- 未通过：`.verdict-banner.fail` 红底红字
- 会话进行中：中性样式 + 脉冲圆点 `.session-live .pulse`，文案「讨论进行中 — round N」

### 3.5 Sidebar 左侧栏 `.sidebar`

| 面板 | 内容 | id |
|------|------|-----|
| 按标签筛选 | 标签芯片 `.chip`（可多选，再点取消；有「清除筛选」） | `#tag-filters` |
| 图例 | 6 条色彩说明 `.legend` `.dot.t-XXX` | 静态 |
| 停止原因 | 等宽文本 | `#stop-reason` |
| 数据源 | 等宽小字，如 `live session · round 2` 或 `GET /api/report` | `#data-source` |
| 停止会话 | ghost 按钮（JS 动态插入） | 无固定 id |

**标签色系统（核心视觉语言）** — class 模式 `tag-{TAG}` / `t-{TAG}` / `.msg[data-tag=…]`：

| 标签 | 语义 | 当前色相 |
|------|------|----------|
| `CLAIM` / `PROPOSAL` | 待审主张 | 蓝 `#58A6ff` |
| `COUNTEREXAMPLE` | 攻击/反例 | 红 `#F85149` |
| `DEAD_END` | 死胡同（不可删） | 紫 `#A371F7` |
| `UNCONTESTED` | 逾期未攻 | 琥珀 `#D29922` |
| `VERDICT` | 机器判决 | 绿 `#3FB950` |
| `EXPERIMENT_RESULT` / `BASELINE_RESULT` / `EVIDENCE` / `LEMMA_PROVED` | 证据 | 青 `#39C5CF` |
| `FIX` / `PLAN` | 修复/计划 | 橙 `#FF7B72` |
| `QUESTION` | 疑问 | 粉 `#DB61A2` |
| `CONFIRMED` | 确认 | 绿 |
| `LEAKAGE_SUSPECTED` | 泄漏嫌疑 | 琥珀 |
| `LITERATURE` / `NOTE` / `SYSTEM` | 灰 | `#8B949E` |

美化时**请保留这套「颜色=语义」的映射**，可以换色值/饱和度/玻璃拟态，但红=攻击、紫=死路、琥珀=未挑战、绿=通过 这套语义不要反过来。

### 3.6 Board 主区 `.board`（最重要）

**头** `.board-head`：标题「黑板时间线」+ 两个开关  

- `#show-system` 是否显示 SYSTEM  
- `#only-claims` 仅 claim 线程（CLAIM/PROPOSAL/… 及其子消息）

**时间线** `#timeline`：

- 左侧竖线 `.timeline::before`（轨道）
- 每条消息一张卡 `.msg`：
  - 轨道圆点 `.msg::before`
  - 头行 `.msg-head`：标签胶囊 `.tag-pill`、作者 `.author`、状态 `.status-pill`、meta（轮次 + id）、若为回复则 `→ 父作者 / 父标签`
  - 正文 `.msg-body`（默认 4 行截断 `.clamped`，按钮 `.msg-more`「展开全文/收起」）
  - 若有指标：`.meta` 显示 `metrics: {…}`
- **父子攻击线程**：有 `parent_id` 的消息加 `.child`，左缩进 18px + 折线连接 `.msg::after`
- 重点卡左边框色条（红=反例、紫=死路、蓝=claim、绿=判决、琥珀=uncontested）

### 3.7 Right 侧栏 `.right`（可 sticky）

| 面板 | id | 内容 |
|------|-----|------|
| 永久负结果 | `#dead-ends` | DEAD_END + COUNTEREXAMPLE 卡片 `.card` |
| 最佳证据结果 | `#best-results` | 有证据背书的结果（自动排除 UNCONTESTED） |
| Open Questions | `#open-questions` | 未决问题 |
| （空态文案） | `.empty` | 例如「还没有失败记录…」 |

### 3.8 Footer `.footer`

三条协议碎语：`负结果不可删`、`结论由 Verifier 决定，不是投票` 等，弱化灰字。

### 3.9 弹窗（Modal）`.modal` / `.modal-card`

**A. 新建会话 `#session-modal`**

| 字段 | id | 控件 |
|------|-----|------|
| 研究任务 | `#session-task` | 多行文本，带占位示例 |
| 角色 | `#role-checks` | 5 个复选：Explorer / Skeptic / Modeler / Experimenter / Reviewer |
| 最大轮次 | `#session-rounds` | number，默认 6 |
| 挑战宽限（轮） | `#session-grace` | number，默认 2 |
| 启用 Verifier 闸门 | `#session-require-gate` | checkbox |
| 验证器 JSON | `#session-verifier` | 等宽 textarea，默认 `{ "kind": "null" }` |
| 当前运行时摘要 | `#runtime-summary` | 只读 mono 小字 |
| 按钮 | `#session-cancel` `#session-start` | 「取消」/「开跑」 |

**B. 执行设置 `#settings-modal`**

| 字段 | id |
|------|-----|
| 默认模式 | `#set-mode` |
| 默认代理 | `#set-agent` |
| CLI 工作目录 | `#set-cwd` |
| 单次调用超时（秒） | `#set-timeout` |
| 保存 | `#settings-save` |

弹窗遮罩半透明深色 `.modal`，卡片圆角 14px，可滚动（`max-height: 90vh`）。

### 3.10 Toast `#toast`

右下角临时提示条（刷新成功、启动失败等）。

---

## 4. 交互行为（改样式时别破坏）

1. **点运行时胶囊** → 浮层开/关；点空白处关闭。
2. **切换模式** → 代理按钮列表重绘；自动选中第一个可用代理；「自带 Key」时才显示 API 输入。
3. **点「开始讨论」** → 打开会话弹窗，摘要行刷新为当前 runtime。
4. **开跑成功** → 关弹窗、toast、进入 **2 秒轮询** `/api/session`：时间线、统计、侧栏、判决横幅实时更新；结束时 toast 并停轮询。
5. **标签芯片** → 多选过滤时间线；「清除筛选」恢复。
6. **展开全文** → 单卡 4 行截断切换。
7. **「演示数据」** → 不请求后端，直接吃 `window.MAD_DEMO_DATA`（设计走查用）。
8. **「停止会话」** → `POST /api/session/stop`。

---

## 5. 设计令牌（`web/styles.css` 的 `:root`）

```css
--bg: #0d1117          /* 页面底 */
--panel: #161b22       /* 卡片/面板 */
--border: #30363d      /* 描边 */
--text: #e6edf3        /* 主文字 */
--muted: #8b949e       /* 次级文字 */
--accent: #58a6ff      /* 主色/链接 */
--danger: #f85149      /* 攻击/失败 */
--warn: #d29922        /* 未挑战/警告 */
--ok: #3fb950          /* 通过/确认 */
--purple: #a371f7      /* 死胡同 */
--radius: 10px
--mono / --sans        /* 等宽 / 系统 UI + PingFang SC + 微软雅黑 */
```

版式：正文 14px / 行高 1.55；统计数字 22px 加粗；标签胶囊 10.5px 等宽。

---

## 6. 美化建议（可自行发挥的方向）

**可以大胆改：**
- 换一套更精致的深色/浅色主题（浅色也可，但请保留语义色）
- 玻璃拟态、噪点、渐变光晕、微动效（悬停、卡片进入、脉冲）
- 信息密度：时间线更紧凑或更「聊天流」
- 字号层级、几何图形 Logo、插画空态
- 右侧栏改成可折叠抽屉 / 底部 Sheet（移动端）

**不要动（功能契约）：**
- 下列 id 必须存在且对应同义控件：  
  `runtime-chip, runtime-panel, mode-seg, agent-row, model-select, api-key-fields,`  
  `btn-new-session, btn-reload, btn-demo, session-modal, session-start, session-task,`  
  `role-checks, session-rounds, session-grace, session-require-gate, session-verifier,`  
  `runtime-summary, settings-modal, set-mode, set-agent, set-cwd, set-timeout, settings-save,`  
  `task-text, st-posts, st-rounds, st-dead, st-uncontested, st-questions, st-verdict, st-verdict-wrap,`  
  `verdict-banner, tag-filters, stop-reason, data-source, timeline, dead-ends, best-results, open-questions, toast, session-picker`
- 标签 → 颜色语义映射（见 3.5）
- `.msg` / `.tag-pill` / `.chip` / `.card` / `.status-pill` 等类名可增不可随意删除（JS 会拼接 `tag-XXX`）
- `data-tag` 属性用于左侧色条

**JS 拼接规则备忘：**  
`tag-pill tag-${tag}`、`status-pill ${status}`、`dot t-${tag}`、`chip active`。

---

## 7. 推荐的美化验收清单

- [ ] 三栏在 1440 / 1280 / 1100 / 768 / 375 宽度下不溢出
- [ ] 深色下对比度：正文 ≥ 4.5:1，大数字 ≥ 3:1
- [ ] 判决 PASS/FAIL / 运行中 三种横幅一眼可辨
- [ ] 父子消息线程可读（攻击指向清晰）
- [ ] 弹窗键盘 Esc 可关、焦点不丢
- [ ] 空态、加载态、错误 toast 不丑
- [ ] 「演示数据」按钮下所有模块都能看到内容（含负结果、判决）

---

## 8. 样例数据从哪来

- 离线：`web/demo-data.js`（`window.MAD_DEMO_DATA`，含 11 条完整黑板消息、反例、死胡同、PASS 判决）
- 在线：`GET /api/report` / `GET /api/session`
- 自己跑一场：页面「开始讨论」用 Mock 运行时

静态调样式时建议直接开 `index.html` 旁的 server + 点「演示数据」，无需起讨论。
