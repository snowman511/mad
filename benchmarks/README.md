# benchmarks — 申明–复跑闭环模板（Route A）

这一层解决的问题：**agent 不许自己报实验数字**。agent 只能*申明*一个待测配置，
harness 真跑、真测、真判定；实测分数写回黑板，虚报自动变成不可删的 COUNTEREXAMPLE。

```
agent 发帖 [EXPERIMENT_RESULT] + ```json config
        │
        ▼
orchestrator 把 candidate(config) 喂给 ScriptVerifier
        │
        ▼
harness 跑基准 ──► 实测结果 {"passed", "score", "measured", "mismatch"}
        │                │
        │                ├─ measured ──► 写回该消息 metadata（下一轮以此为准）
        │                └─ mismatch ──► 黑板自动追加不可删的 COUNTEREXAMPLE
        ▼
improvement tracking / stagnation / target_score 照常调度
```

## 契约（stdin → stdout）

**stdin**（orchestrator 传入的 candidate 上下文）：

```json
{ "candidate": { "id": "abc123", "config": {"impl": "dijkstra_heap"}, "body": "..." } }
```

**stdout**（harness 必须输出的一份 JSON）：

| 字段 | 含义 |
|------|------|
| `passed` | 该次申明是否经受住复跑（config 可跑 + 结果正确 + 没有虚报） |
| `score` | 实测分数（本模板里 = 相对基线的加速比，>1 表示更快） |
| `measured` | 实测明细，会被 orchestrator 写回 candidate 消息的 metadata |
| `mismatch` | 虚报记录（`{"key", "claimed", "measured", "tolerance"}`），无则 null |
| `reason` | 无可测内容时填 `"no_score"` |

## agent 端怎么申明

发一条带围栏 JSON 的帖子即可（`benchmark_proposer` 角色的 system prompt 已内置该协议）：

```
[EXPERIMENT_RESULT] 提前终止应该在随机图上省一半扫描；预期保守 1.4x
```json
{"impl": "dijkstra_heap_early", "expected": {"speedup": 1.4}}
```
```

`expected` 是**可证伪的预测**：虚高超过容差（30%）→ mismatch → 黑板永久记一笔。
少报不罚。

## 本模板内置的规则（换题目时请保留）

1. **负载由 harness 固定**（图族、规模、seed、重复次数都是常量 + 环境变量覆盖），
   config 只能选算法和算法参数。否则优化器会靠缩小负载刷分。
2. **正确性一票否决（battery）**：计时前先在 3 张随机图（不同规模 / 稀疏度 / seed）× 3 个
   点对（含 `s==t` 退化对）上与基线对拍，负载图上再对拍一次。任何一处不一致直接 FAIL，
   速度再快也不算——**"基准图上对" ≠ "算法对"**。
3. **config 只许带算法语义键**（本模板：`impl` / `expected` / `note`），塞负载参数直接拒收。
4. **score 语义固定**：`baseline_time / candidate_time`，针对的是固定负载。

## 跨战役记忆

session config 里加一个键，负结果就跨场累积：

```json
{ "memory": "benchmarks/sssp_memory.json" }
```

开场自动把上一场的 `DEAD_END` / `COUNTEREXAMPLE` / 未决问题 / 最新实测结果以 round 0
播种进黑板（是上下文，不是候选，不会触发 UNCONTESTED）；散场自动合并回写（按 id 和
正文去重，封顶 500 条）。`mad run` 也有等价的 `--memory` 参数。

## 跑起来

```bash
# 自检（所有实现对拍答案）
python benchmarks/sssp_bench.py --selftest

# 整场战役（真实 CLI agent 申明，harness 复跑；在仓库根目录执行）
python -m mad.cli run examples/session_sp_bench.json --board board.sqlite -o bench_report.json
```

## 换成你自己的题目（3 步）

1. 把 `gen_graph` + 三个实现换成你的负载与算法族（ML 任务：数据加载 + 几个模型/超参）。
2. 保留 `measure()` 的骨架：**固定负载 → 基线计时 → 候选计时 → 正确性对拍 → speedup**。
3. 保留 `compare_expected` 与 stdout 契约不动 —— orchestrator 侧零改动。

环境变量（测试/换机器用）：`MAD_SSSP_N` `MAD_SSSP_DEGREE` `MAD_SSSP_MAX_W` `MAD_SSSP_SEED` `MAD_SSSP_REPEATS`。
