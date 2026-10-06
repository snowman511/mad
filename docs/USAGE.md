# mad — Usage Guide

A complete walkthrough: from cloning the repo to reading your first debate report,
then writing your own objective gate, and running the web dashboard.

---

## 1. From clone to first debate (5 minutes)

### 1.1 Install

```bash
git clone https://github.com/snowman511/mad.git
cd mad
pip install -e ".[dev]"      # Python >= 3.10; zero third-party deps at core
python -m pytest -q          # 58 tests, all offline
```

### 1.2 Describe your task in plain language

```bash
mad init \
  --task "Discuss whether memoization helps sparse graph lookups" \
  --dir ./my-debate \
  --preset minimal \
  --runtime mock
```

What you get in `./my-debate/`:

```
session.json   the full session config (roles, rounds, runtime, verifier)
memory.json    empty cross-campaign memory seed
```

`--preset` picks the lineup:

| preset | agents | roles |
|---|---|---|
| `minimal` | 3 | proposer, skeptic, modeler |
| `standard` | 5 | + experimenter, reviewer |
| `full` | 7 | + literature_scout, builder |

Or name your own: `--roles proposer,skeptic,modeler`.
`--runtime` picks the backend: `mock` (offline), `claude`, `kimi`, or `openai`.

### 1.3 Run the debate

```bash
mad run ./my-debate/session.json \
  --board ./my-debate/board.sqlite \
  --memory ./my-debate/memory.json \
  -o ./my-debate/report.json
```

The mock backend finishes in seconds and writes the full report. For real LLMs,
switch `--runtime` to `claude` / `kimi` (each agent turn is one CLI invocation,
roughly 1-3 minutes per turn) or `openai` with `--model`.

### 1.4 Read the report

`report.json` contains every message, every verdict, and the stop reason. The
fastest way to read it is the web board:

```bash
python serve.py --report ./my-debate/report.json --port 8765 --open
```

---

## 2. Understanding a debate transcript

Real output from `examples/run_demo.py` (offline mock):

```
r1 [CLAIM             ] (Explorer) 用 2 秒滑窗 LightGBM（IMU 频带能量 + 咀嚼周期性）输出进食帧……
r1 [COUNTEREXAMPLE    ] (Skeptic) ↳ claim 嚼口香糖和说话都产生 1-2 Hz 下颌能量；仅凭 IMU 该主张无法分开……
r1 [FIX               ] (Modeler) ↳ claim 用半马尔可夫先验替换硬性 30 秒最短时长……
r1 [BASELINE_RESULT   ] (Experimenter) 规则基线：event-F1@30s = 0.61。Seed 42。
r1 [VERDICT           ] (verifier) event_f1=0.61，阈值 0.7 -> 未通过
r2 [DEAD_END          ] (Modeler) 尝试成对堆式优先级 —— 4 次尝试无法给出合并代价上界，放弃。
r2 [EXPERIMENT_RESULT ] (Experimenter) LightGBM+HMM：event-F1@30s = 0.78。Seed 42。
r2 [VERDICT           ] (verifier) event_f1=0.78，阈值 0.7 -> 通过
```

How to read it:

- `↳` marks a **challenge or fix aimed at a specific earlier message** — the skeptic
  attacks claims, the modeler repairs them;
- `VERDICT` is the **objective gate** re-measuring the latest `EXPERIMENT_RESULT` —
  it is deterministic and never influenced by how confident an agent sounds;
- `DEAD_END` is **permanent negative memory**: it cannot be deleted and every future
  session seeded with this memory will see it;
- the session stops when the gate passes (classic mode) or after
  `no_improvement_rounds` without a new best (optimization mode).

---

## 3. Roles reference

| role | what it does | why it exists |
|---|---|---|
| `proposer` (alias `benchmark_proposer`) | proposes the next concrete step: an experiment, a candidate, a direction | keeps the loop moving with falsifiable steps |
| `skeptic` | attacks claims: hidden assumptions, missing controls, overclaims | every claim must survive adversarial review or lapse |
| `modeler` | turns fuzzy ideas into formal mechanisms; repairs attacked claims | math and structure live here |
| `experimenter` | posts `EXPERIMENT_RESULT` / `BASELINE_RESULT` measurements | the raw material the verifier scores |
| `reviewer` | audits the reasoning chain end-to-end | catches what role-level adversarial review misses |
| `literature_scout` | fetches and reads actual external sources | grounds the debate in prior art |
| `builder` | a sandboxed coding agent (Claude Code backed) that implements specs | turns "someone should write this" into working code |

Presets bundle them: `minimal` = proposer + skeptic + modeler (the smallest complete
loop: propose → attack → formalize). `standard` adds measurement and audit. `full`
adds literature grounding and implementation capacity.

---

## 4. Verification: three honest modes

Every mode keeps the adversarial loop. What differs is whether claims also face a
**deterministic gate**.

| mode | `--verifier` | rigor source | use when |
|---|---|---|---|
| gated | `script:<command>` | your script re-measures the claim | the task has checkable outputs (code, math, data) |
| gate-less (default) | `null` | adversarial review only — a structured debate, **not a truth machine** | open-ended discussion, critique, brainstorming |
| threshold | config JSON | claims must beat a numeric metric | optimization-style tasks |

> We deliberately do **not** ship an LLM-as-judge. A judge can be argued with; a gate
> cannot. That difference is the entire design philosophy of this framework.

### 4.1 Writing your own gate (ScriptVerifier)

The contract: when a candidate is ready, **mad pipes a JSON blob to your command's
stdin** and reads stdout:

```json
{
  "task": "your session task",
  "candidate": {
    "id": "message id of the latest EXPERIMENT_RESULT",
    "author": "who measured it",
    "metrics": { "...": "any numbers the agent posted in metadata" },
    "config": "their config (or the message body)",
    "body": "the full message text"
  }
}
```

Your script must print JSON with a `passed` field (a plain zero exit code also
counts as passed):

```json
{"passed": true, "score": 0.78, "summary": "F1 0.78 >= 0.7 threshold"}
```

A minimal worked gate — `gate.py` checks the claimed F1 against your own ground
truth (not against what the agent *claims*):

```python
#!/usr/bin/env python
import json, sys

ctx = json.load(sys.stdin)                      # what mad pipes in
claimed = float(ctx["candidate"]["metrics"].get("event_f1", 0))
truth = json.load(open("ground_truth.json"))    # YOUR numbers, not the agent's
actual = truth.get(ctx["candidate"]["id"], 0)
ok = actual >= 0.7 and abs(actual - claimed) <= 0.01
print(json.dumps({"passed": ok, "score": actual,
                  "summary": f"claimed {claimed}, actual {actual}"}))
sys.exit(0 if ok else 1)
```

Wire it in:

```bash
mad init --task "..." --verifier "script:python gate.py" --dir ./my-debate
```

The gate runs with a 300 s timeout in your working directory (configurable in the
session JSON's `verifier` block: `cwd`, `timeout`).

### 4.2 The other built-ins

- **Threshold** (session JSON): `{"kind": "threshold", "metric": "event_f1", "threshold": 0.7}`
  — pass when the latest measured metric clears a bar;
- **Composite** (session JSON): AND/OR of several verifiers with averaged scores;
- **Callable** (Python API): wrap any function `(context) -> VerificationResult`.

---

## 5. Runtime backends

Set with `mad init --runtime` or edit `session.json` → `runtime`:

| runtime | what it needs | notes |
|---|---|---|
| `mock` | nothing | canned replies, fully offline — for tests and UI walkthroughs |
| `claude` | Claude Code CLI installed and logged in | coding-capable `builder` role; tool allowlists enforced |
| `kimi` | Kimi CLI | same |
| `openai_compatible` | API key + base URL | any OpenAI-compatible endpoint |

Per-role overrides let you mix backends (cross-model adversarial review is a feature —
a skeptic running on a *different* model than the proposer is measurably harder to fool):

```json
{
  "runtime": {"kind": "local_cli", "agent": "claude"},
  "role_runtimes": {"skeptic": {"kind": "local_cli", "agent": "kimi"}}
}
```

**Windows note**: for local-CLI agents, the CLI's own permission layer is where tool
policies are enforced. On Windows hosts Claude Code exposes its tools through
PowerShell — make sure your allowlist covers `PowerShell(...)` rules, not just
`Bash(...)` (the builtin loader handles the common commands for you via
`python -m mad.cli` runtime presets).

---

## 6. The web dashboard

```bash
python serve.py --port 8765 --open
```

Everything is in the browser — no shell needed after this:

- **Debate history sidebar** (left): every debate with status, relative time and
  progress; click to switch; `＋ 新辩论` opens the new-session form (topic, lineup,
  rounds, verification mode) — you can run several debates concurrently;
- **Live timeline**: messages appear as rounds complete (the view polls a running
  session every few seconds);
- **Inject your own thoughts**: the "插入想法（人类参与者）" panel posts your note
  straight onto the shared blackboard as author `human`. The next agent turn sees it
  immediately — no pause needed — and it is subject to the same adversarial review
  as every agent post. Use it to add domain knowledge, veto a direction, or steer
  the debate; the human is the *(N+1)-th* participant, not an administrator;
- **Pause & resume**: 停止 halts at the current round (state is persisted); the
  ▶ 继续辩论 button resumes from the same blackboard.

---

## 7. Cross-campaign memory

`--memory` gives the team a persistent scratchpad that survives across debates:

- **before** the debate: `memory.json` is read and seeded onto the blackboard —
  past `DEAD_END`s and lessons are visible from round 1;
- **after** the debate: new negative results and claims are merged back into the file.

```json
{ "items": [ { "tag": "DEAD_END", "body": "..." } ] }
```

Point two debates at the same `memory.json` and the second one starts where the
first stopped.

---

## 8. CLI reference

### `mad init`

| flag | default | meaning |
|---|---|---|
| `--task` | (required) | natural-language task description |
| `--dir` | `.` | project folder for config/memory/board/report |
| `--preset` | `standard` | `minimal` / `standard` / `full` |
| `--roles` | preset | comma list overriding the preset |
| `--runtime` | `mock` | `mock` / `claude` / `kimi` / `openai` |
| `--model` | — | model name for openai-compatible backends |
| `--rounds` | `12` | max discussion rounds |
| `--max-messages` | `120` | hard budget on total messages |
| `--verifier` | `null` | `null` or `script:<command>` |

### `mad run`

| flag | default | meaning |
|---|---|---|
| `config` | (required) | session JSON path |
| `--board` | `:memory:` | SQLite board path (`:memory:` = ephemeral) |
| `--memory` | — | cross-campaign memory JSON |
| `--resume` | off | continue a persisted board (rounds/dedupe/best carry over) |
| `-o` | stdout | write the full report JSON |

### `serve.py`

| flag | meaning |
|---|---|
| `--port` | dashboard port (default 8765) |
| `--open` | open the browser |
| `--report` | load a report JSON instead of a live session |

---

## 9. Session config reference

All fields (defaults shown); only `task` is required:

```json
{
  "task": "...",
  "roles": ["proposer", "skeptic", "modeler"],
  "max_rounds": 12,
  "challenge_grace_rounds": 2,
  "stall_rounds": 3,
  "max_messages": 120,
  "reject_empty_posts": true,
  "require_verifier_gate": true,
  "context_window_rounds": 8,
  "no_improvement_rounds": 0,
  "improvement_delta": 0.0,
  "runtime": {"kind": "mock"},
  "verifier": {"kind": "null"},
  "memory": "./memory.json"
}
```

- `challenge_grace_rounds` — a claim with no challenge for this many rounds becomes
  UNCONTESTED (and is barred from the final best set);
- `context_window_rounds` — roles see only the last N rounds when priming context;
- `no_improvement_rounds` — optimization mode: stop after N rounds with no new best
  (0 = classic mode: first `passed=True` stops the session);
- `improvement_delta` — minimum delta to count as an improvement.

---

## 10. Tips

- **Start gate-less, gate later.** Use `null` to explore what the debate produces,
  then add a `script:` gate once you know what "correct" means for your task.
- **Cross-model skeptics are stronger.** Run the skeptic on a different backend than
  the proposer (per-role `role_runtimes`); same-model skeptics are measurably easier
  to fool.
- **The board never forgets.** Use `DEAD_END` for anything you do not want re-tried —
  including by future sessions (via `--memory`).
- **Short files are fine.** Files shorter than the context window are skipped by the
  training-free screens and evaluated whole-file by the debate — no special handling.
- **Windows + local CLIs**: Claude Code exposes its tools through PowerShell on
  Windows hosts; make sure your allowlist covers `PowerShell(...)` rules.
