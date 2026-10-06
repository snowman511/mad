"""Command-line entry: run a discussion session from a YAML/JSON-like config.

Minimal JSON config:
{
  "task": "...",
  "roles": ["explorer", "skeptic", "modeler"],
  "runtime": {"kind": "mock"}  or  {"kind": "openai", "model": "gpt-4o-mini", "base_url": "..."}
}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mad.agents import (
    RoleAgent,
    DEFAULT_ROLES,
    benchmark_proposer,
    builder,
    experimenter,
    explorer,
    literature_scout,
    modeler,
    reviewer,
    skeptic,
)
from mad.blackboard import Blackboard
from mad.cli_runtime import LocalCLIRuntime
from mad.memory import announce, save_memory, seed_from_memory
from mad.models import Message, SessionConfig, Tag
from mad.orchestrator import Orchestrator
from mad.runtime import LLMRuntime, MockRuntime, OpenAICompatibleRuntime
from mad.verifier import (
    CompositeVerifier,
    NullVerifier,
    ScriptVerifier,
    ThresholdVerifier,
    Verifier,
)

_ROLE_FACTORIES = {
    "explorer": explorer,
    "skeptic": skeptic,
    "modeler": modeler,
    "experimenter": experimenter,
    "reviewer": reviewer,
    "benchmark_proposer": benchmark_proposer,
    "proposer": benchmark_proposer,  # friendly alias
    "literature_scout": literature_scout,
    "builder": builder,
}


def _build_runtime(spec: dict) -> LLMRuntime:
    kind = (spec or {}).get("kind", "mock")
    if kind == "mock":
        return MockRuntime(spec.get("replies"))
    if kind == "local_cli":
        return LocalCLIRuntime(
            agent=spec.get("agent", "claude"),
            model=spec.get("model"),
            cwd=spec.get("cwd"),
            timeout=float(spec.get("timeout", 180.0)),
        )
    if kind in ("openai", "openai_compatible", "api"):
        return OpenAICompatibleRuntime(
            model=spec.get("model", "gpt-4o-mini"),
            api_key=spec.get("api_key"),
            base_url=spec.get("base_url", "https://api.openai.com/v1"),
        )
    raise SystemExit(f"unknown runtime kind: {kind}")



#: role presets: how many agents, and which combination works well together
ROLE_PRESETS = {
    "minimal": ["proposer", "skeptic", "modeler"],
    "standard": ["proposer", "skeptic", "modeler", "experimenter", "reviewer"],
    "full": ["proposer", "skeptic", "modeler", "experimenter", "reviewer",
             "literature_scout", "builder"],
}


def _parse_verifier(spec: str) -> dict:
    """'null' -> gate-less debate; 'script:<cmd>' -> run <cmd> with a JSON claim
    on stdin; the command must print {"passed": bool, ...} and exit 0/1."""
    if spec == "null":
        return {"kind": "null"}
    if spec.startswith("script:"):
        return {"kind": "script", "command": spec[len("script:"):]}
    raise SystemExit(
        f"unknown verifier {spec!r}: use 'null' (gate-less debate) or "
        f"'script:<command>' (deterministic gate)"
    )


def _build_verifier(spec: dict | None) -> Verifier:
    if not spec:
        return NullVerifier()
    kind = spec.get("kind", "null")
    if kind == "null":
        return NullVerifier()
    if kind == "script":
        return ScriptVerifier(
            spec["command"], cwd=spec.get("cwd"), timeout=float(spec.get("timeout", 300.0))
        )
    if kind == "threshold":
        return ThresholdVerifier(
            spec["metric"],
            float(spec["threshold"]),
            higher_is_better=spec.get("higher_is_better", True),
        )
    if kind == "composite":
        return CompositeVerifier([_build_verifier(s) for s in spec.get("verifiers", [])])
    raise SystemExit(f"unknown verifier kind: {kind}")


def load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def run_from_config(
    cfg: dict,
    *,
    board_path: str = ":memory:",
    memory_path: str | Path | None = None,
    resume: bool = False,
) -> dict:
    task = cfg["task"]
    session_cfg = SessionConfig(
        task=task,
        max_rounds=int(cfg.get("max_rounds", 12)),
        challenge_grace_rounds=int(cfg.get("challenge_grace_rounds", 2)),
        stall_rounds=int(cfg.get("stall_rounds", 3)),
        max_messages=int(cfg.get("max_messages", 120)),
        require_verifier_gate=bool(cfg.get("require_verifier_gate", True)),
    )
    runtime_cfg = cfg.get("runtime") or {"kind": "mock"}
    role_runtimes = cfg.get("role_runtimes") or {}
    role_names = cfg.get("roles") or ["explorer", "skeptic", "modeler", "experimenter", "reviewer"]
    agents = []
    for name in role_names:
        factory = _ROLE_FACTORIES.get(name)
        if factory is None:
            raise SystemExit(f"unknown role: {name}. choose from {sorted(_ROLE_FACTORIES)}")
        # per-role runtime overrides enable cross-model adversarial review
        agents.append(RoleAgent(factory(), _build_runtime(role_runtimes.get(name, runtime_cfg))))
    verifier = _build_verifier(cfg.get("verifier"))
    board = Blackboard(board_path)
    if board_path != ":memory:" and len(board) > 0 and not resume:
        raise SystemExit(
            f"board {board_path} already holds {len(board)} messages; "
            f"pass --resume to continue it or choose a new --board path"
        )
    memory_path = memory_path or cfg.get("memory")
    if memory_path:
        announce(board, seed_from_memory(memory_path, board))
    orch = Orchestrator(board, agents, session_cfg, verifier)
    report = orch.run(resume=resume)
    if memory_path:
        try:
            save_memory(memory_path, board)
        except Exception:
            pass
    payload = report.to_dict()
    payload["board"] = board.dump()
    payload["memory_items"] = len(board.failed_approaches())
    return payload


def cmd_init(args) -> int:
    """Generate a session config + project folder from a natural-language task."""
    roles = (
        [r.strip() for r in args.roles.split(",") if r.strip()]
        if args.roles else ROLE_PRESETS[args.preset]
    )
    unknown = [r for r in roles if r not in _ROLE_FACTORIES]
    if unknown:
        raise SystemExit(f"unknown role(s): {unknown}. choose from {sorted(_ROLE_FACTORIES)}")

    runtimes = {
        "mock": {"kind": "mock"},
        "claude": {"kind": "local_cli", "agent": "claude"},
        "kimi": {"kind": "local_cli", "agent": "kimi"},
        "openai": {"kind": "openai_compatible", **({"model": args.model} if args.model else {})},
    }
    verifier = _parse_verifier(args.verifier)
    cfg = {
        "task": args.task.strip(),
        "roles": roles,
        "max_rounds": args.rounds,
        "max_messages": args.max_messages,
        "challenge_grace_rounds": 2,
        "stall_rounds": 3,
        # gate-less mode: the adversarial loop is the rigor; no gate to require
        "require_verifier_gate": verifier.get("kind") != "null",
        "runtime": runtimes[args.runtime],
        "verifier": verifier,
    }
    args.dir.mkdir(parents=True, exist_ok=True)
    cfg_path = args.dir / "session.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    mem_path = args.dir / "memory.json"
    if not mem_path.exists():
        mem_path.write_text(json.dumps({"items": []}, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    board = args.dir / "board.sqlite"
    mode_note = (
        "gate-less debate: rigor comes from the adversarial loop (no mechanical verification)"
        if verifier["kind"] == "null" else
        "gated mode: every claim is re-measured by your script"
    )
    print(f"session config -> {cfg_path}")
    print(f"memory seed    -> {mem_path}")
    print(f"mode           : {mode_note}")
    print(f"agents ({len(roles)}): {', '.join(roles)}")
    print("run it with    :")
    print(f"  mad run {cfg_path} --board {board} --memory {mem_path} -o {args.dir / 'report.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mad", description="Multi-Agent Discussion runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run a session from a JSON config")
    p_run.add_argument("config", type=Path, help="path to session config JSON")
    p_run.add_argument("--board", default=":memory:", help="SQLite board path (default in-memory)")
    p_run.add_argument("--memory", type=Path, help="cross-campaign memory JSON: loaded before, merged after")
    p_run.add_argument(
        "--resume", action="store_true",
        help="continue a persisted board: round numbering, dedupe and best-score carry over",
    )
    p_run.add_argument("-o", "--output", type=Path, help="write full report JSON here")

    p_demo = sub.add_parser("demo", help="run the built-in mock demo")
    p_demo.add_argument("--rounds", type=int, default=4)
    p_demo.add_argument("-o", "--output", type=Path)

    p_init = sub.add_parser(
        "init", help="generate a session config in a project folder from a natural-language task"
    )
    p_init.add_argument("--task", required=True,
                        help="natural-language description of what to discuss/solve")
    p_init.add_argument("--dir", default=Path("."), type=Path,
                        help="project folder: session.json, memory.json and board.sqlite live here")
    p_init.add_argument("--preset", choices=sorted(ROLE_PRESETS), default="standard",
                        help="agent lineup preset (how many agents and which roles)")
    p_init.add_argument("--roles", default=None,
                        help="comma list of roles overriding the preset, e.g. proposer,skeptic,modeler")
    p_init.add_argument("--runtime", choices=["mock", "claude", "kimi", "openai"], default="mock",
                        help="agent backend (mock = offline, no API needed)")
    p_init.add_argument("--model", default=None, help="model name for openai-compatible runtimes")
    p_init.add_argument("--rounds", type=int, default=12, help="max discussion rounds")
    p_init.add_argument("--max-messages", type=int, default=120, help="hard budget on messages")
    p_init.add_argument("--verifier", default="null",
                        help="'null' = gate-less debate; 'script:<command>' = deterministic gate "
                             "(command receives the claim JSON on stdin, prints {\"passed\": bool})")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        return cmd_init(args)

    if args.cmd == "demo":
        cfg = {
            "task": "Find a shortest-path algorithm improvement in the sparse regime.",
            "roles": ["explorer", "skeptic", "experimenter"],
            "max_rounds": args.rounds,
            "runtime": {"kind": "mock"},
            "verifier": {"kind": "threshold", "metric": "speedup", "threshold": 1.0},
        }
        # seed a believable experiment metric so the gate has something to score
        payload = run_from_config(cfg)
    else:
        cfg = load_config(args.config)
        payload = run_from_config(
            cfg, board_path=args.board, memory_path=args.memory, resume=args.resume
        )

    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
