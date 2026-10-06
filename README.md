# mad — Multi-Agent Discussion Framework

**A Python framework for running LLM-agent research debates where every claim must survive adversarial review *and* an objective verification gate.**

> Core belief: *however confident an agent sounds, it doesn't count — only the objective gate does.*
> （核心信念：agent 说得再自信也不算数，客观闸门才算数。）

[中文文档](README.zh-CN.md)

---

## Why

LLM agents are convincing liars. They report success that isn't there, overclaim
improvements, and quietly forget negative results — and a group of them amplifies
these failure modes instead of fixing them.

**mad** takes the opposite bet: agents are placed in a structured debate where

- every claim is posted to a **shared blackboard** (SQLite — append-only, nothing is ever deleted),
- every claim is **challenged by a dedicated adversary** within a grace window, or it lapses,
- every quantitative claim is **re-measured by a deterministic verifier** (a locked
  evaluation harness the agents cannot edit), and
- **negative results are first-class citizens** — `DEAD_END` / `COUNTEREXAMPLE` entries
  are permanent, non-deletable shared memory that stops the team from re-walking dead roads.

The result is a research loop where the *process* leaves an auditable trail: 344 rounds,
~1500 messages, and every number has a provenance.

## Architecture

```
                 ┌────────────────────────────────────────────┐
                 │              shared blackboard              │
                 │   (SQLite; claims, challenges, verdicts;    │
                 │    immutable DEAD_END / COUNTEREXAMPLE)     │
                 └──────────────┬─────────────────────────────┘
                                │ observe / post
   ┌─────────┐    ┌─────────┐ │ ┌─────────┐    ┌──────────┐
   │ proposer│    │ skeptic │ │ │ modeler │ …  │ builder  │   roles = pluggable
   └────┬────┘    └────┬────┘ │ └────┬────┘    └────┬─────┘   (system prompts +
        │  challenge   │      │  fix │              │ code    │   tool policies)
        └──────────────┴──────┴──────┴──────────────┘
                                │
                 ┌──────────────▼──────────────┐
                 │        objective gate       │   verifier = YOUR locked harness;
                 │  (deterministic re-measure) │   claims die if numbers don't match
                 └─────────────────────────────┘
```

- **Roles** are pluggable specs: system prompt + tool policy (`none` / web-search /
  code-sandbox) + runtime. Ships with `proposer`, `skeptic`, `modeler`, `experimenter`,
  `reviewer`, `literature_scout`, `builder` (a Claude-Code-backed coding agent in a sandbox).
- **Runtimes** are pluggable backends: a deterministic `Mock` (offline tests), OpenAI-compatible
  APIs, and local agent CLIs (Claude Code, Kimi) driven in one-shot mode with tool allowlists.
- **The verifier is yours**: point it at any deterministic evaluation — a benchmark harness,
  a unit-test suite, a simulator. The framework never trusts a number an agent typed.
- **Web board**: a zero-dependency dashboard to watch the debate live (`python serve.py`).

## Quickstart (60 seconds)

```bash
pip install -e ".[dev]"      # Python ≥ 3.10, zero third-party deps at core
python examples/run_demo.py  # full mock debate: blackboard, verdicts, negative results
python -m pytest -q          # 52 tests
```

The demo runs an entirely offline debate (mock runtimes) on a shortest-path task and
prints the blackboard, the verification verdicts, and the permanent negative results.

## Using real LLMs

```bash
pip install -e ".[llm]"
python -m mad.cli --help
```

Point roles at local agent CLIs (Claude Code / Kimi) or OpenAI-compatible endpoints.
Tool policies are enforced at the CLI's own permission layer where available
(e.g. `--allowedTools` for Claude Code), with a sandbox working directory for coding agents.

## Battle-tested

The framework drove a **six-day, 344-round multi-agent campaign** on a wearable-sensor
eating-detection task (biomedical engineering competition): **event-F1 0.6066 → 0.6483**
on a held-out validation split, with every gain traced to a new information dimension
(time-frequency features) rather than parameter re-tuning — and every dead end
(14+ research directions) permanently recorded in the blackboard.

The case study will be published here after the competition concludes.
Mechanisms proven in that campaign and shipped in this repo: quota pins for mandatory
allocations, CV-screening admission gates before any validation spend, fused-cache
alignment gates, anti-cherry-pick postproc grids, and an honesty ratchet against
metric-mismatch overclaims.

## Design principles

1. **Objective gates over self-report.** If a number matters, a deterministic harness
   re-measures it. Agents cannot edit the harness.
2. **Adversary is a role, not a mood.** The skeptic is a full participant with its own
   budget, and it attacks the operator's claims with the same force as the agents'.
3. **Negative results are load-bearing.** A closed direction is written once and never
   re-walked; the blackboard makes them impossible to lose.
4. **Pre-registration over post-hoc selection.** Decision rules are declared before the
   measurement; the verifier holds everyone to them.
5. **Everything is a claim.** Even the operator's own measurements are treated as
   unverified evidence until the gate rules on them.

## Repository layout

```
src/mad/            framework (blackboard, orchestrator, roles, runtimes, web)
examples/           60-second offline demo + session configs
benchmarks/         SSSP benchmark used by the original debates
tests/              52 tests
web/                dashboard frontend (vanilla JS, no build step)
serve.py            web board launcher
```

## License

[MIT](LICENSE)
