"""End-to-end mock demo of the multi-agent discussion framework.

Run:  python examples/run_demo.py
Shows: shared blackboard, forced adversarial challenge, immutable dead-ends,
verifier gate as merge decision (not voting), and termination conditions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mad.agents import RoleAgent, experimenter, explorer, modeler, skeptic
from mad.blackboard import Blackboard
from mad.models import Message, SessionConfig, Tag
from mad.orchestrator import Orchestrator
from mad.runtime import MockRuntime
from mad.verifier import ThresholdVerifier

# A scripted conversation: each agent reads the real blackboard but replies from
# a role-specific canned timeline. This keeps the demo deterministic while still
# exercising post / challenge / dead-end / gate code paths.
SCRIPTED = {
    "Explorer": [
        "[CLAIM] 用 2 秒滑窗 LightGBM（IMU 频带能量 + 咀嚼周期性）输出进食帧，再以最短 30 秒约束拼接成段。",
        "[PROPOSAL] 增加餐后 PPG 确认通道（onset 后 10-20 分钟心率上升），压制说话造成的误报。",
        "[CLAIM] 两段式模型（帧分类器 + 半马尔可夫平滑）在标签有噪声时优于纯端到端 TCN。",
    ],
    "Skeptic": [
        "[COUNTEREXAMPLE] 嚼口香糖和说话都产生 1-2 Hz 下颌能量；仅凭 IMU 该主张无法把它们与进食分开，见场景 gum_talk_03。",
        "[COUNTEREXAMPLE] 硬性 30 秒最短时长会把相隔 20 秒的两次加餐并成一段、推高时长 MAE；没有间隙容忍模型就不要用硬闸门。",
        "[CONFIRMED] 在受试者不重叠切分的前提下，两段式主张成立；纯 TCN 基线过拟合设备类型。",
    ],
    "Modeler": [
        "[FIX] 用半马尔可夫先验替换硬性 30 秒最短时长：仅当加餐间隔 < 12 秒且同手 IMU 连续时才允许合并。",
        "[DEAD_END] 尝试在候选边界上用成对堆式优先级 —— 4 次尝试仍无法给出合并代价上界，放弃。",
        "[PLAN] 基线 A：LightGBM 帧 + HMM 平滑；基线 B：1D-TCN；基线 C：规则式日记膨胀。按 leave-one-subject-out 评估 event-F1@±30/60s 与 FP/day。",
    ],
    "Experimenter": [
        "[BASELINE_RESULT] 规则基线，20 名受试者 × 3 天：event-F1@30s = 0.61，FP/day = 4.2。Seed 42。",
        "[EXPERIMENT_RESULT] LightGBM+HMM（已应用半马尔可夫修复）：event-F1@30s = 0.78，onset MAE = 22 秒，FP/day = 1.9。受试者不重叠。Seed 42。",
        "[EVIDENCE] 消融：去掉咀嚼周期性，F1 降到 0.69；去掉 PPG 确认，FP/day 升到 3.1。",
    ],
}


class ScriptedRuntime(MockRuntime):
    def __init__(self, role_name: str):
        self.role_name = role_name
        self._replies = SCRIPTED.get(role_name, ["[NOTE] nothing to add."])
        self._i = 0
        self.calls = []

    def complete(self, *, system: str, user: str, temperature: float = 0.7, max_tokens: int = 1024):
        self.calls.append({"system": system, "user": user})
        text = self._replies[min(self._i, len(self._replies) - 1)]
        self._i += 1
        from mad.runtime import LLMReply

        return LLMReply(text=text)


def enrich_metrics(board: Blackboard) -> None:
    """Attach machine-readable metrics so the threshold verifier can score."""
    for m in board.messages(tag=Tag.BASELINE_RESULT):
        board.append_metadata(m.id, metrics={"event_f1": 0.61, "fp_per_day": 4.2})
    for m in board.messages(tag=Tag.EXPERIMENT_RESULT):
        if "0.78" in m.body or "event-F1" in m.body:
            board.append_metadata(m.id, metrics={"event_f1": 0.78, "onset_mae_s": 22.0, "fp_per_day": 1.9})


class ExperimenterWithMetrics(RoleAgent):
    def step(self, board, config):
        msg = super().step(board, config)
        if msg and msg.tag in (Tag.EXPERIMENT_RESULT, Tag.BASELINE_RESULT):
            if "0.78" in msg.body:
                board.append_metadata(msg.id, metrics={"event_f1": 0.78, "fp_per_day": 1.9})
            elif "0.61" in msg.body:
                board.append_metadata(msg.id, metrics={"event_f1": 0.61, "fp_per_day": 4.2})
        return msg


def main() -> int:
    board = Blackboard()
    agents = [
        RoleAgent(explorer(), ScriptedRuntime("Explorer"), name="Explorer"),
        RoleAgent(skeptic(), ScriptedRuntime("Skeptic"), name="Skeptic"),
        RoleAgent(modeler(), ScriptedRuntime("Modeler"), name="Modeler"),
        ExperimenterWithMetrics(experimenter(), ScriptedRuntime("Experimenter"), name="Experimenter"),
    ]
    config = SessionConfig(
        task=(
            "从可穿戴 IMU+PPG 识别进食时段 [onset, offset]。"
            "优化 event 级 F1（±30s），压低 FP/day。不做营养学声明。"
        ),
        max_rounds=5,
        challenge_grace_rounds=1,
        stall_rounds=2,
        max_messages=80,
        require_verifier_gate=True,
    )
    verifier = ThresholdVerifier("event_f1", 0.70)
    report = Orchestrator(board, agents, config, verifier).run()

    print("=" * 72)
    print("MULTI-AGENT DISCUSSION — MOCK DEMO")
    print("=" * 72)
    print(f"stop_reason : {report.stop_reason}")
    print(f"posts       : {len(board)}")
    print(f"dead ends   : {len(report.dead_ends)} (immutable shared memory)")
    print(f"open qs     : {len(report.open_questions)}")
    if report.verdict:
        print(f"verdict     : passed={report.verdict.passed} score={report.verdict.score}")
        print(f"              {report.verdict.summary}")
    print()
    print("--- board transcript ---")
    for m in board.messages():
        parent = f" ↳{m.parent_id}" if m.parent_id else ""
        print(f"r{m.round_no} [{m.tag.value:<20}] ({m.author}){parent} {m.brief(100)}")
    print()
    print("--- best evidence-backed results (uncontested claims excluded) ---")
    for m in report.best_messages:
        print(f"  [{m.tag.value}] {m.brief(120)}")
    print()
    print("--- permanent negative memory ---")
    for m in report.dead_ends:
        print(f"  [{m.tag.value}] {m.brief(120)}")

    out = Path(__file__).with_name("demo_report.json")
    payload = report.to_dict()
    payload["board"] = board.dump()
    payload["task"] = config.task
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nfull report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
