"""Phase 2: provenance, resume, multi-session registry, config sanitization."""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mad.agents import RoleAgent, explorer
from mad.blackboard import Blackboard
from mad.cli import run_from_config
from mad.models import SessionConfig, Tag
from mad.orchestrator import Orchestrator
from mad.runtime import MockRuntime
from mad.session_manager import SessionManager, _sanitize_config
from mad.verifier import NullVerifier


# ---------------------------------------------------------------- provenance

def test_provenance_recorded_on_board():
    rt = MockRuntime(["[CLAIM] something"])
    board = Blackboard()
    agents = [RoleAgent(explorer(), rt, name="Explorer")]
    cfg = SessionConfig(task="t", max_rounds=1, challenge_grace_rounds=5,
                        stall_rounds=9, require_verifier_gate=False)
    Orchestrator(board, agents, cfg, NullVerifier()).run()
    msg = board.messages(tag=Tag.CLAIM)[0]
    prov = msg.metadata["provenance"]
    assert prov["runtime"] == "mock" and prov["model"] == "mock"
    assert isinstance(prov["latency_ms"], (int, float))


def test_openai_provenance_carries_usage(monkeypatch):
    from mad.runtime import OpenAICompatibleRuntime

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({
                "model": "gpt-x",
                "choices": [{"message": {"content": "[CLAIM] hi"}}],
                "usage": {"total_tokens": 42},
            }).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: FakeResp())
    rt = OpenAICompatibleRuntime(model="gpt-x", api_key="k")
    reply = rt.complete(system="s", user="u")
    assert reply.provenance["model"] == "gpt-x"
    assert reply.provenance["usage"] == {"total_tokens": 42}


# -------------------------------------------------------------------- resume

def test_resume_continues_rounds_and_carries_dedupe(tmp_path):
    board_path = tmp_path / "board.sqlite"
    cfg = SessionConfig(task="t", max_rounds=2, challenge_grace_rounds=5,
                        stall_rounds=9, require_verifier_gate=False)
    b1 = Blackboard(board_path)
    Orchestrator(b1, [RoleAgent(explorer(), MockRuntime(["[CLAIM] idea one"]), name="E")],
                 cfg, NullVerifier()).run()
    b1.close()

    b2 = Blackboard(board_path)
    # campaign 2 says something new, then repeats campaign 1's claim verbatim
    agents = [RoleAgent(explorer(), MockRuntime(["[CLAIM] idea two", "[CLAIM] idea one"]), name="E")]
    report = Orchestrator(b2, agents, cfg, NullVerifier()).run(resume=True)
    assert report.rounds[0].round_no == 2, "resumed campaign must continue round numbering"
    assert any("续跑" in m.body for m in b2.messages(tag=Tag.SYSTEM))
    claims = b2.messages(tag=Tag.CLAIM)
    assert [m.body for m in claims] == ["idea one", "idea two"], (
        "the verbatim repeat from campaign 1 must be rejected as a restatement"
    )


def test_fresh_run_on_nonempty_board_is_refused(tmp_path):
    board_path = str(tmp_path / "board.sqlite")
    cfg = {"task": "t", "roles": ["explorer"], "max_rounds": 1,
           "challenge_grace_rounds": 5, "stall_rounds": 9,
           "require_verifier_gate": False, "runtime": {"kind": "mock"}}
    run_from_config(cfg, board_path=board_path)
    with pytest.raises(SystemExit):
        run_from_config(cfg, board_path=board_path)  # no resume flag
    r2 = run_from_config(cfg, board_path=board_path, resume=True)
    assert any(
        m["tag"] == "SYSTEM" and "续跑" in (m.get("body") or "") for m in r2["board"]
    )


# -------------------------------------------------------------- multi-session

class GateRuntime(MockRuntime):
    """Blocks until released — holds sessions in 'running' deterministically."""

    def __init__(self, gate):
        super().__init__(["[CLAIM] gated claim"])
        self.gate = gate

    def complete(self, *, system, user, temperature=0.7, max_tokens=1024, tool_policy=None):
        self.gate.wait(timeout=10)
        return super().complete(system=system, user=user, temperature=temperature,
                                max_tokens=max_tokens, tool_policy=tool_policy)


def _wait_status(mgr, sid, want, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mgr.status(sid)["status"] == want:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def gated_manager(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr("mad.session_manager.build_runtime", lambda cfg: GateRuntime(gate))
    return SessionManager(max_concurrent=2, max_finished=10), gate


def _cfg(task, **over):
    base = {"task": task, "roles": ["explorer"], "max_rounds": 1,
            "challenge_grace_rounds": 5, "stall_rounds": 9, "require_verifier_gate": False}
    base.update(over)
    return base


def test_sessions_run_concurrently_and_are_isolated(gated_manager):
    mgr, gate = gated_manager
    h1 = mgr.start(_cfg("alpha"))
    h2 = mgr.start(_cfg("beta"))
    assert mgr.is_running(h1.id) and mgr.is_running(h2.id)
    assert mgr.status(h1.id)["session"]["task"] == "alpha"
    assert mgr.status(h2.id)["session"]["task"] == "beta"
    gate.set()
    assert _wait_status(mgr, h1.id, "done") and _wait_status(mgr, h2.id, "done")
    assert {s["task"] for s in mgr.list()} >= {"alpha", "beta"}


def test_concurrent_cap_is_enforced(gated_manager):
    mgr, gate = gated_manager
    mgr.start(_cfg("a"))
    mgr.start(_cfg("b"))
    with pytest.raises(RuntimeError):
        mgr.start(_cfg("c"))
    gate.set()


def test_stop_targets_one_session_only(gated_manager):
    mgr, gate = gated_manager
    h1 = mgr.start(_cfg("a", max_rounds=5))
    h2 = mgr.start(_cfg("b", max_rounds=5))
    assert mgr.stop(h1.id) is True
    gate.set()
    assert _wait_status(mgr, h1.id, "stopped")
    assert _wait_status(mgr, h2.id, "done"), "stopping one session must not touch the other"


def test_config_api_keys_are_masked_recursively():
    cfg = {"api_key": "secret", "role_runtimes": {"skeptic": {"api_key": "sk-2", "model": "m"}}}
    clean = _sanitize_config(cfg)
    assert clean["api_key"] == "***"
    assert clean["role_runtimes"]["skeptic"]["api_key"] == "***"
    assert clean["role_runtimes"]["skeptic"]["model"] == "m"
    assert cfg["api_key"] == "secret", "sanitizer must not mutate the input"


def test_session_with_board_path_persists_and_resumes(gated_manager, tmp_path):
    mgr, gate = gated_manager
    bp = str(tmp_path / "web_board.sqlite")
    h1 = mgr.start(_cfg("t", board=bp))
    gate.set()
    assert _wait_status(mgr, h1.id, "done")
    assert Path(bp).exists(), "board must be persisted to disk"
    h2 = mgr.start(_cfg("t", board=bp, resume=True))
    gate.set()
    assert _wait_status(mgr, h2.id, "done")
    assert mgr.status(h2.id)["session"]["resumed"] is True
    with pytest.raises(ValueError):
        mgr.start(_cfg("t", board=bp))  # non-empty board without resume
