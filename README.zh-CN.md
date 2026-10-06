# mad — 多智能体辩论框架

**一个让 LLM agent 研究辩论中的每个主张都必须经受对抗审查与客观验证闸门的 Python 框架。**

> 核心信念：agent 说得再自信也不算数，客观闸门才算数。

[English](README.md)

---

通用多 agent 研究讨论框架。借鉴 C-HD（Vals 用 10 个 agent 在共享黑板上产出最短路算法 + Lean 证明）的**协作机制**，而不是照搬其形式化验证栈。

**核心信念：agent 说得再自信也不算数，客观闸门才算数。**

```
共享黑板（含不可删负结果） + 强制对抗挑战 + 机器验证闸门
```

## 安装（60 秒跑通见下）

```bash
# 开发模式（本仓库）
pip install -e ".[dev]"

# 可选：接真实 LLM
pip install -e ".[llm]"
```

Python ≥ 3.10，核心零第三方依赖（SQLite 用标准库）。

## 60 秒跑通

**用自然语言任务直接开一场辩论**——描述要讨论什么，选好项目文件夹和阵容，`mad init` 生成其余一切：

```bash
mad init   --task "讨论记忆化是否能加速稀疏图查询"   --dir ./my-debate --preset minimal --runtime mock
mad run ./my-debate/session.json --board ./my-debate/board.sqlite   --memory ./my-debate/memory.json -o ./my-debate/report.json
```

`--preset` 选阵容：`minimal`（3 agents：proposer / skeptic / modeler）、`standard`（5：+ experimenter / reviewer）、`full`（7：+ literature scout / builder）。也可用 `--roles` 自行点名。

```bash
python examples/run_demo.py   # 纯离线 mock 辩论
python -m pytest -q           # 58 个测试
```

### 三种验证模式——请诚实选择

对抗循环（skeptic + 挑战窗口 + 不可删黑板）在所有模式下都提供*结构性*严谨；区别在于主张是否还面对*确定性*闸门：

| 模式 | `--verifier` | 含义 | 适用 |
|---|---|---|---|
| **有闸** | `script:<命令>` | 每个主张被送进你的脚本，必须输出 `{"passed": bool}` | 任务有可检验输出（代码/数学/数据） |
| **无闸** | `null`（默认） | 仅对抗审查——是结构化辩论，**不是真理机器** | 开放式讨论、头脑风暴、批判性审查 |
| ~~LLM 裁判~~ | — | 刻意**不内置**：裁判可以被说服，闸门不会 | — |

写一个闸门就是一个小脚本（见 `ScriptVerifier`）——这正是框架的核心信念：agent 说得再自信也不算数，客观闸门才算数。

## Web 看板

```bash
# 起服务（默认 127.0.0.1:8765，自动打开浏览器）
python serve.py --port 8765 --open

# 或指定一份历史报告 / SQLite 黑板
python serve.py --report examples/demo_report.json --port 8765 --open
python serve.py --board board.sqlite --port 8765 --open
```

浏览器访问 **http://127.0.0.1:8765/** 。

页面能力：标签筛选、claim 攻击线程、永久负结果栏、Verifier 判决横幅、UNCONTESTED 标记、统计卡片。

### 运行时选择（类似 Open Design）

右上角胶囊按钮可切换：

| 模式 | 代理 | 说明 |
|------|------|------|
| **本地 CLI** | Claude Code / Kimi CLI | 调用本机 CLI 的 print 模式（`claude -p` / `kimi -p`），复用你的登录态，角色可带工具跑实验 |
| **自带 Key** | OpenAI 兼容 / Mock | 走 `/v1/chat/completions`，适合批量、轻量角色、管道自测 |

「开始讨论」填任务、勾角色、配 Verifier，一键开跑；黑板 2 秒轮询刷新，可随时「停止会话」。

### API

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/runtimes` | 本机探测到的 CLI / API 代理 |
| GET | `/api/sessions` | 会话列表（多会话注册表） |
| GET | `/api/session[?id=]` | 会话状态 + 实时黑板（默认最新一场） |
| POST | `/api/session/start` | 开跑 `{task, roles, max_rounds, runtime, role_runtimes, verifier, board, resume, memory}` |
| POST | `/api/session/stop` | 停止（body 可带 `id`，默认最新一场） |
| GET | `/api/report` `/api/board` `/api/meta` `/api/health` | 只读数据 |

## 概念

| 组件 | 职责 |
|------|------|
| **Blackboard** | SQLite 共享消息板。`DEAD_END` / `COUNTEREXAMPLE` **禁止删除**，构成共享失败记忆。 |
| **Tag 协议** | 每条消息必须带唯一标签：`CLAIM` `EVIDENCE` `COUNTEREXAMPLE` `DEAD_END` `FIX` `QUESTION` `CONFIRMED` `UNCONTESTED` `PROPOSAL` `PLAN` `BASELINE_RESULT` `EXPERIMENT_RESULT` `LEAKAGE_SUSPECTED` `LITERATURE` `LEMMA_PROVED` `VERDICT` … |
| **Roles** | 默认五个：Explorer / Skeptic / Modeler / Experimenter / Reviewer。可任意组合，3 个也够用（探索+怀疑+实验）。另有 `benchmark_proposer`（申明-复跑）和 `literature_scout`（带检索的查证角色）。 |
| **工具策略** | 角色级 `tool_policy`：默认纯文本 one-shot；`"search"` 允许 web 检索（claude 由 `--allowedTools WebSearch WebFetch` 在 CLI 权限层强制，kimi 仅 prompt 级约束）。 |
| **Orchestrator** | 调度循环 + 纪律执行：过期未被攻击的 claim 标 `UNCONTESTED` 并**禁止进入最终结论**；空洞复读发帖直接拒绝；终止条件写死在代码里。 |
| **Verifier** | 合并闸门。`ScriptVerifier`（外部脚本/测试）、`ThresholdVerifier`（指标阈值）、`CompositeVerifier`、`CallableVerifier`。**不是投票**。 |
| **Runtime** | `MockRuntime`（管道自测）/ `OpenAICompatibleRuntime`（OpenAI·DeepSeek·Moonshot·vLLM·Ollama…） |

## 一轮讨论在做什么

1. 各角色读黑板：负结果 + 未决 claim + 近期消息。
2. 每人只发**一条**带标签的新信息（复读/空洞会被拒）。
3. 有挑战者角色（Skeptic/Reviewer）对 open claim 出 `COUNTEREXAMPLE`；经真实尝试无法反驳后由**他人**出 `CONFIRMED`，claim 随即关闭、离开待挑战队列。
4. 超过 `challenge_grace_rounds` 无人攻击/确认/修复 → 自动 `UNCONTESTED`，不得进结论。自己确认、自己修复不算防守。
5. 出现实验结果/引理后跑 Verifier；**通过即终止**并输出判决。
6. 预算/轮次/停滞（连续 N 轮无新 CLAIM）触发正常收束，输出：最佳证据结果 + open questions + 全部 dead ends。

## 配置示例

```json
{
  "task": "用 IMU+PPG 识别进食时间段 [onset, offset]，优化 event-F1@±30s，压制 FP/day。",
  "roles": ["explorer", "skeptic", "modeler", "experimenter", "reviewer"],
  "max_rounds": 8,
  "challenge_grace_rounds": 2,
  "stall_rounds": 3,
  "require_verifier_gate": true,
  "runtime": { "kind": "openai", "model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1" },
  "verifier": { "kind": "threshold", "metric": "event_f1", "threshold": 0.7 }
}
```

```bash
export OPENAI_API_KEY=sk-...
python -m mad.cli run examples/session_eating.json --board board.sqlite -o report.json
```

环境变量：`MAD_API_KEY` 或 `OPENAI_API_KEY`。

## 自定义 Verifier（推荐做法）

把你的「可复现实验」做成脚本，stdin 收 JSON context，stdout 吐 JSON：

```python
# my_gate.py
import json, sys
ctx = json.load(sys.stdin)
# 跑你的 pytest / benchmark / lean build
json.dump({"passed": True, "score": 0.81, "summary": "event-F1=0.81 pass"}, sys.stdout)
```

```json
{ "verifier": { "kind": "script", "command": ["python", "my_gate.py"] } }
```

C-HD 用 Lean kernel 当闸门；你的进食识别项目对应物是：**受控餐锚点 + property tests + leave-one-subject-out**。换研究题目只换 verifier 和角色指令，框架不动。

## 申明–复跑闭环（Route A）

实验数字**不许 agent 自报**。agent 发帖**申明**一个待测配置（正文里一个 ```json 围栏），ScriptVerifier 真跑基准，实测分数写回黑板；预期值虚高超过容差 → 黑板自动追加**不可删**的 COUNTEREXAMPLE。

```
[EXPERIMENT_RESULT] 提前终止应该更快；预期保守 1.4x
```json
{"impl": "dijkstra_heap_early", "expected": {"speedup": 1.4}}
```
```

- 角色 `benchmark_proposer`：每轮读黑板上的实测结果，申明下一个待测配置，永不自报数字。
- 模板：`benchmarks/sssp_bench.py` —— 负载由 harness 固定（config 只能选算法）、正确性 battery 一票否决、score = 相对基线加速比。换题目照抄骨架。
- **正确性 battery**：每次测量前先在随机图族（含退化点对）上与基线对拍——"基准图上对" ≠ "算法对"。
- **跨战役记忆**：config 加 `"memory": "memory.json"`，DEAD_END / COUNTEREXAMPLE / 未决问题 / 最新实测自动合并落盘，下一场 round 0 播种；`mad run` 另有 `--memory` 参数。
- **文献输入**：角色 `literature_scout` 带 `search` 工具策略，真实检索文献、引用带 URL 的来源、拿已知结果攻击 overclaim（已实测：能查到并引用 bidirectional search 最坏情形不占优的文献）。
- 跑一场：`python -m mad.cli run examples/session_sp_bench.json --board board.sqlite -o report.json`

详见 `benchmarks/README.md`。

## 工程能力（第二阶段）

- **跨模型对抗**：`role_runtimes` 给每个角色独立 runtime——Explorer 用 claude、Skeptic 用 kimi 或任意 OpenAI 兼容端点。同权重 agent 有相关盲区，跨模型是对它的解药。
  ```json
  { "runtime": { "kind": "local_cli", "agent": "claude" },
    "role_runtimes": { "skeptic": { "kind": "local_cli", "agent": "kimi" } } }
  ```
- **持久化与续跑**：`mad run --board board.sqlite --resume`——轮号、复读去重、最优分追踪跨场延续；Web 会话传 `"board": "path.sqlite", "resume": true` 同效。不带 resume 打开非空板会被明确拒绝。
- **多会话并发**：Web 端可同时跑多场（默认上限 3，环境变量 `MAD_MAX_SESSIONS` 可调）；看板左下角下拉可切换查看对象，停止可指定会话。
- **溯源**：每条消息 metadata 记录 `provenance`（runtime / model / 耗时 / token 用量）——"这句话是哪个模型说的"从此可答。

## 设计取舍（有意为之）

- **不是多 agent 公司编排**（那是 Paperclip 的活）：没有 org chart、预算、heartbeat。这里只做「互相挑错找结论」。
- **不做多数投票**：结论由 verifier 决定。
- **负结果优先**：没有强制记录失败路径的多 agent 系统，会把同一条死路走十遍。
- **3–5 个角色足够**：10 个会重复刷屏、浪费 context；有效点从来不是数量。

## 全程 Web 化（v0.3）

打开 `python serve.py`，全程不再需要命令行：

- **辩论历史侧栏**——每场辩论按状态与最近活动排列，点击切换，`＋ 新辩论` 一键开新场；
- **实时新建表单**——辩题、阵容（3/5/7 预设）、轮次、验证模式，全部浏览器内完成；
- **辩论中插入自己的想法**——人类以平等参与者身份入场：你的发言立即上黑板，下一个 agent 回合即可见，并同样接受对抗审查；
- **暂停与恢复**——随时停止，之后从同一块黑板无缝续开。

## 目录

```
src/mad/
  models.py        Tag / Message / RoleSpec / SessionConfig
  blackboard.py    SQLite 黑板，负结果不可删
  agents.py        Agent、默认角色、prompt 协议
  runtime.py       Mock / OpenAI-compatible
  verifier.py      客观闸门
  orchestrator.py  轮次、纪律、终止、报告
  memory.py        跨战役记忆：负结果收集 / 合并 / round-0 播种
  cli.py           mad run / mad demo
benchmarks/
  sssp_bench.py    申明–复跑基准模板（SSSP，--selftest 对拍自检）
examples/
  run_demo.py      端到端 mock 演示
  session_eating.json
  session_sp.json
  session_sp_bench.json
tests/
```

## 下一步（可选）

- Web 看板：把 `Blackboard.dump()` 渲染成时间线 + claim 依赖图。
- 会话持久化 / 断点续跑。
- 与 MiMo Desktop / Claude Code / 外部 agent 联动（把 `LLMRuntime` 换成调用外部 CLI 的 adapter）。
- 研究模板包：进食识别的 property tests、泄漏检查清单、基线实验表。
