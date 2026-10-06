"""Route-A declare-and-measure loop: config extraction, measured write-back,
mismatch -> immutable COUNTEREXAMPLE, and the sssp harness end to end."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]

# shrink the harness workload so tests stay fast
os.environ["MAD_SSSP_N"] = "200"
os.environ["MAD_SSSP_REPEATS"] = "2"

from mad.agents import RoleAgent, benchmark_proposer, explorer
from mad.blackboard import Blackboard
from mad.models import Message, SessionConfig, Tag
from mad.orchestrator import Orchestrator
from mad.runtime import MockRuntime
from mad.verifier import CallableVerifier, ScriptVerifier, VerificationResult


FENCE = "```json\n{}\n```"


def _proposer(reply: str, rounds: int = 1, **cfg_kwargs):
    board = Blackboard()
    agents = [RoleAgent(benchmark_proposer(), MockRuntime([reply]), name="Proposer")]
    defaults = dict(task="t", max_rounds=rounds, challenge_grace_rounds=5, stall_rounds=9)
    defaults.update(cfg_kwargs)
    cfg = SessionConfig(**defaults)
    return board, Orchestrator(board, agents, cfg)


def test_fenced_config_lands_in_metadata():
    body = (
        "[EXPERIMENT_RESULT] early exit should cut the scan\n"
        + FENCE.format(json.dumps({"impl": "dijkstra_heap_early", "expected": {"speedup": 1.5}}))
    )
    board, orch = _proposer(body)
    orch.run()
    msgs = board.messages(tag=Tag.EXPERIMENT_RESULT)
    assert msgs, "proposer post must survive"
    cfg = msgs[0].metadata.get("config")
    assert cfg and cfg["impl"] == "dijkstra_heap_early" and cfg["expected"] == {"speedup": 1.5}


def test_measured_writeback_and_mismatch_counterexample():
    body = (
        "[EXPERIMENT_RESULT] claiming a huge win\n"
        + FENCE.format(json.dumps({"impl": "dijkstra_heap_early", "expected": {"speedup": 9.9}}))
    )
    verifier = CallableVerifier(
        lambda ctx: VerificationResult(
            passed=False,
            score=1.2,
            summary="overclaimed",
            details={
                "measured": {"speedup": 1.2, "correct": True},
                "mismatch": {"key": "speedup", "claimed": 9.9, "measured": 1.2, "tolerance": 0.3},
            },
        )
    )
    board = Blackboard()
    agents = [RoleAgent(benchmark_proposer(), MockRuntime([body]), name="Proposer")]
    cfg = SessionConfig(task="t", max_rounds=1, challenge_grace_rounds=5, stall_rounds=9,
                        require_verifier_gate=True)
    report = Orchestrator(board, agents, cfg, verifier).run()

    exp = board.messages(tag=Tag.EXPERIMENT_RESULT)[0]
    assert exp.metadata.get("measured") == {"speedup": 1.2, "correct": True}
    ces = board.messages(tag=Tag.COUNTEREXAMPLE)
    assert ces and ces[0].parent_id == exp.id, "mismatch must become a threaded counterexample"
    assert "9.9" in ces[0].body and "1.2" in ces[0].body
    try:
        board.delete(ces[0].id)
        raise AssertionError("mismatch COUNTEREXAMPLE must be immutable")
    except Exception:
        pass


def test_candidate_context_prefers_measured_over_claimed():
    board = Blackboard()
    m = board.post(Message(tag=Tag.EXPERIMENT_RESULT, body="exp", author="a", round_no=1))
    board.append_metadata(
        m.id, metrics={"speedup": 0.5}, measured={"speedup": 1.7}, config={"impl": "x"}
    )
    agents = [RoleAgent(explorer(), MockRuntime(), name="E")]
    orch = Orchestrator(board, agents, SessionConfig(task="t"))
    ctx = orch._candidate_context()
    assert ctx["candidate"]["metrics"] == {"speedup": 1.7}, "measured must win over claimed"
    assert ctx["candidate"]["config"] == {"impl": "x"}


def _harness():
    return ScriptVerifier(
        [sys.executable, str(ROOT / "benchmarks" / "sssp_bench.py")],
        cwd=str(ROOT),
        timeout=120,
    )


def test_sssp_harness_measures_and_passes_honest_claim():
    ctx = {
        "candidate": {
            "id": "x1",
            "body": "early exit",
            "config": {"impl": "dijkstra_heap_early", "expected": {"speedup": 1.0}},
        }
    }
    r = _harness().verify(ctx)
    assert r.details.get("measured", {}).get("correct") is True
    assert isinstance(r.score, float) and r.score > 0
    assert r.details.get("mismatch") is None


def test_sssp_harness_flags_overclaim():
    ctx = {
        "candidate": {
            "id": "x2",
            "body": "claiming the moon",
            "config": {"impl": "dijkstra_heap_early", "expected": {"speedup": 99.0}},
        }
    }
    r = _harness().verify(ctx)
    assert r.passed is False
    mismatch = r.details.get("mismatch")
    assert mismatch and mismatch["claimed"] == 99.0 and mismatch["measured"] < 99.0


def test_correctness_battery_catches_broken_impl():
    """A fast-but-wrong implementation must be caught by the randomized
    battery, not pass just because the timed workload happens to agree."""
    sys.path.insert(0, str(ROOT / "benchmarks"))
    import sssp_bench

    original = sssp_bench.IMPLS["dijkstra_heap_early"]
    # subtle bug: off by one on every non-degenerate pair
    sssp_bench.IMPLS["dijkstra_heap_early"] = (
        lambda adj, radj, s, t, cfg: original(adj, radj, s, t, cfg) + (1 if s != t else 0)
    )
    try:
        r = sssp_bench.measure({"impl": "dijkstra_heap_early"})
    finally:
        sssp_bench.IMPLS["dijkstra_heap_early"] = original
    assert r["correct"] is False
    assert r["speedup"] == 0.0
    err = r["correctness_battery"]
    assert err["got"] != err["expected"] and (err["s"], err["t"]) != (0, 0)


def test_ssp_harness_rejects_workload_gaming_and_wrong_answers():
    h = _harness()
    # shrinking the workload is not a config right
    r1 = h.verify({"candidate": {"id": "a", "body": "", "config": {"impl": "dijkstra_heap", "n": 100}}})
    assert r1.passed is False and "workload" in r1.summary
    # unknown impl
    r2 = h.verify({"candidate": {"id": "b", "body": "", "config": {"impl": "quantum_bellman"}}})
    assert r2.passed is False and "unknown impl" in r2.summary
    # no config at all
    r3 = h.verify({"candidate": {"id": "c", "body": "just talk, no fence"}})
    assert r3.passed is False and r3.details.get("reason") == "no_score"


def test_sssp_harness_selftest():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "sssp_bench.py"), "--selftest"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "selftest OK" in proc.stdout


def test_full_loop_with_real_harness():
    """Scripted Proposer declares two configs; the real harness measures both;
    the board must end up with measured metadata and best-score tracking."""
    reply_a = "[EXPERIMENT_RESULT] try early exit\n" + FENCE.format(
        json.dumps({"impl": "dijkstra_heap_early", "expected": {"speedup": 1.0}})
    )
    reply_b = "[EXPERIMENT_RESULT] try bidirectional search\n" + FENCE.format(
        json.dumps({"impl": "bidirectional", "expected": {"speedup": 1.0}})
    )
    board = Blackboard()
    agents = [RoleAgent(benchmark_proposer(), MockRuntime([reply_a, reply_b]), name="Proposer")]
    cfg = SessionConfig(
        task="beat the baseline", max_rounds=2, challenge_grace_rounds=5, stall_rounds=9,
        require_verifier_gate=True, no_improvement_rounds=3,
    )
    report = Orchestrator(board, agents, cfg, _harness()).run()

    exps = board.messages(tag=Tag.EXPERIMENT_RESULT)
    assert len(exps) == 2
    measured = [m.metadata.get("measured") for m in exps]
    assert all(m and m.get("correct") for m in measured), f"both configs must measure correct: {measured}"
    assert all(m.get("workload", {}).get("n") == 200 for m in measured), "workload must be fixed"
    assert report.verdict is not None and report.verdict.score and report.verdict.score > 0
