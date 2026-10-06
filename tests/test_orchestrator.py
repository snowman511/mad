import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mad.agents import Agent, RoleAgent, explorer, skeptic
from mad.blackboard import Blackboard
from mad.models import Message, SessionConfig, Tag
from mad.orchestrator import Orchestrator
from mad.runtime import MockRuntime, RuntimeStopped
from mad.verifier import NullVerifier, ThresholdVerifier, VerificationResult, CallableVerifier


class CycleRuntime(MockRuntime):
    """Yield one fixed reply per call from a list, always last item after exhaust."""

    def __init__(self, replies):
        super().__init__(replies)


def test_orchestrator_boot_and_rounds():
    board = Blackboard()
    agents = [
        RoleAgent(explorer(), CycleRuntime([
            "[CLAIM] approach alpha improves sparse SSSP",
        ]), name="Explorer"),
        RoleAgent(skeptic(), CycleRuntime([
            "[COUNTEREXAMPLE] alpha breaks on zero-weight cycles",
        ]), name="Skeptic"),
    ]
    cfg = SessionConfig(
        task="improve SSSP",
        max_rounds=3,
        challenge_grace_rounds=1,
        stall_rounds=2,
        require_verifier_gate=False,
    )
    report = Orchestrator(board, agents, cfg, NullVerifier()).run()
    assert report.stop_reason in ("stall_no_new_claim", "max_rounds")
    tags = {m.tag for m in board.messages()}
    assert Tag.SYSTEM in tags
    assert Tag.CLAIM in tags
    assert Tag.COUNTEREXAMPLE in tags


def test_uncontested_claims_are_barred():
    board = Blackboard()
    # Explorer keeps claiming; Skeptic posts NOTE only (never attacks)
    agents = [
        RoleAgent(explorer(), CycleRuntime(["[CLAIM] untested bold claim about complexity"]), name="Explorer"),
        RoleAgent(skeptic(), CycleRuntime(["[NOTE] still thinking about edge cases"]), name="Skeptic"),
    ]
    cfg = SessionConfig(
        task="task",
        max_rounds=4,
        challenge_grace_rounds=1,
        stall_rounds=5,
        require_verifier_gate=False,
    )
    report = Orchestrator(board, agents, cfg, NullVerifier()).run()
    uncontested = board.messages(tag=Tag.UNCONTESTED)
    assert uncontested, "stale claims must be marked UNCONTESTED"
    # best set must not include those claims
    best_ids = {m.id for m in report.best_messages}
    for m in uncontested:
        if m.parent_id:
            assert m.parent_id not in best_ids


def test_verifier_gate_stops_session():
    board = Blackboard()

    class ExpRuntime(MockRuntime):
        def complete(self, *, system, user, temperature=0.7, max_tokens=1024):
            self.calls.append({"system": system, "user": user})
            from mad.runtime import LLMReply

            return LLMReply(text="[EXPERIMENT_RESULT] event-F1 = 0.80 on held-out subjects")

    class MetricAgent(RoleAgent):
        def step(self, board, config):
            msg = super().step(board, config)
            if msg and msg.tag == Tag.EXPERIMENT_RESULT:
                board.append_metadata(msg.id, metrics={"event_f1": 0.80})
            return msg

    agents = [MetricAgent(explorer(), ExpRuntime(), name="Experimenter")]
    cfg = SessionConfig(
        task="detect eating windows",
        max_rounds=6,
        challenge_grace_rounds=3,
        stall_rounds=5,
        require_verifier_gate=True,
    )
    verifier = ThresholdVerifier("event_f1", 0.70)
    report = Orchestrator(board, agents, cfg, verifier).run()
    assert report.stop_reason == "verifier_gate_passed"
    assert report.verdict is not None and report.verdict.passed is True


def test_callable_verifier():
    v = CallableVerifier(lambda ctx: VerificationResult(passed=True, score=1.0, summary="ok"))
    r = v.verify({})
    assert r.passed and r.score == 1.0


# ---------------------------------------------------------------- regressions


def test_confirmed_claim_survives_and_enters_best():
    """A claim CONFIRMED by someone else must close as 'confirmed', never be
    marked UNCONTESTED, and stay eligible for the best set."""
    board = Blackboard()
    agents = [
        RoleAgent(explorer(), CycleRuntime(["[CLAIM] method X is sound"]), name="Explorer"),
        RoleAgent(skeptic(), CycleRuntime(["[CONFIRMED] genuine attempt, holds up"]), name="Skeptic"),
    ]
    cfg = SessionConfig(
        task="t", max_rounds=3, challenge_grace_rounds=1, stall_rounds=5,
        require_verifier_gate=False,
    )
    report = Orchestrator(board, agents, cfg, NullVerifier()).run()
    claims = board.messages(tag=Tag.CLAIM)
    assert claims, "explorer must have claimed"
    assert claims[0].status == "confirmed"
    assert not board.messages(tag=Tag.UNCONTESTED)
    assert claims[0].id in {m.id for m in report.best_messages}


def test_self_confirmation_does_not_protect():
    """Self-CONFIRMED / self-FIX must not immunize a claim against UNCONTESTED."""
    board = Blackboard()
    agents = [
        RoleAgent(
            explorer(),
            CycleRuntime(["[CLAIM] bold claim", "[CONFIRMED] i believe myself"]),
            name="Lone",
        ),
    ]
    cfg = SessionConfig(
        task="t", max_rounds=3, challenge_grace_rounds=1, stall_rounds=5,
        require_verifier_gate=False,
    )
    Orchestrator(board, agents, cfg, NullVerifier()).run()
    claims = board.messages(tag=Tag.CLAIM)
    assert claims and claims[0].status == "uncontested"


def test_infer_parent_prefers_explicit_reference_over_recency():
    board = Blackboard()
    a = board.post(Message(tag=Tag.CLAIM, body="claim A", author="Explorer", round_no=1))
    b = board.post(Message(tag=Tag.CLAIM, body="claim B", author="Explorer", round_no=2))
    # explicit id reference wins even though B is newer
    assert Agent._infer_parent(board, Tag.COUNTEREXAMPLE, f"breaks (id={a.id})") == a.id
    # no reference: fall back to the OLDEST open claim, not the newest
    assert Agent._infer_parent(board, Tag.COUNTEREXAMPLE, "no reference here") == a.id
    # non-attack tags carry no parent
    assert Agent._infer_parent(board, Tag.NOTE, "whatever (id=abc123def456)") is None


def test_optimization_mode_detects_improvement():
    """Rising scores must keep resetting the stagnation timer. The old code
    trusted details['new_best'], which ThresholdVerifier never sets."""
    board = Blackboard()

    class ScoreAgent(RoleAgent):
        def __init__(self, role, runtime, name, scores):
            super().__init__(role, runtime, name=name)
            self._scores = list(scores)
            self._i = 0

        def step(self, board, config):
            msg = super().step(board, config)
            if msg and msg.tag == Tag.EXPERIMENT_RESULT:
                score = self._scores[min(self._i, len(self._scores) - 1)]
                self._i += 1
                board.append_metadata(msg.id, metrics={"event_f1": score})
            return msg

    replies = [f"[EXPERIMENT_RESULT] eval run {i}" for i in range(1, 5)]
    agents = [ScoreAgent(explorer(), CycleRuntime(replies), "Experimenter", [0.60, 0.70, 0.80, 0.90])]
    cfg = SessionConfig(
        task="t", max_rounds=4, challenge_grace_rounds=5, stall_rounds=5,
        require_verifier_gate=True, no_improvement_rounds=2,
    )
    report = Orchestrator(board, agents, cfg, ThresholdVerifier("event_f1", 0.5)).run()
    assert report.stop_reason == "max_rounds", (
        f"rising scores must not trigger stagnation, got: {report.stop_reason}"
    )


def test_optimization_mode_stops_on_real_stagnation():
    board = Blackboard()

    class ScoreAgent(RoleAgent):
        def step(self, board, config):
            msg = super().step(board, config)
            if msg and msg.tag == Tag.EXPERIMENT_RESULT:
                board.append_metadata(msg.id, metrics={"event_f1": 0.60})
            return msg

    replies = [f"[EXPERIMENT_RESULT] eval run {i}" for i in range(1, 7)]
    agents = [ScoreAgent(explorer(), CycleRuntime(replies), name="Experimenter")]
    cfg = SessionConfig(
        task="t", max_rounds=6, challenge_grace_rounds=5, stall_rounds=5,
        require_verifier_gate=True, no_improvement_rounds=2,
    )
    report = Orchestrator(board, agents, cfg, ThresholdVerifier("event_f1", 0.5)).run()
    assert report.stop_reason == "stagnation_no_improvement_2_rounds"


def test_stop_interrupts_runtime_call_and_reports_stopped():
    board = Blackboard()

    class StopAwareRuntime(MockRuntime):
        def complete(self, *, system, user, temperature=0.7, max_tokens=1024):
            if self.stop_event is not None and self.stop_event.is_set():
                raise RuntimeStopped("stop requested")
            return super().complete(system=system, user=user, temperature=temperature)

    rt = StopAwareRuntime(["[CLAIM] something new"])
    rt.stop_event = threading.Event()
    rt.stop_event.set()
    agents = [RoleAgent(explorer(), rt, name="Explorer")]
    cfg = SessionConfig(
        task="t", max_rounds=3, challenge_grace_rounds=2, stall_rounds=5,
        require_verifier_gate=False,
    )
    report = Orchestrator(board, agents, cfg, NullVerifier()).run()
    assert report.stop_reason == "stopped_by_user"
    assert not board.messages(tag=Tag.NOTE), "a stop must not be logged as an agent crash"


def test_mock_runtime_raises_when_stopped():
    rt = MockRuntime()
    rt.stop_event = threading.Event()
    rt.stop_event.set()
    with pytest.raises(RuntimeStopped):
        rt.complete(system="s", user="u")
