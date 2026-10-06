"""Per-role tool policy and the literature_scout role."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mad.agents import RoleAgent, literature_scout, reviewer
from mad.blackboard import Blackboard
from mad.cli_runtime import LocalCLIRuntime
from mad.models import SessionConfig, Tag
from mad.orchestrator import Orchestrator
from mad.runtime import MockRuntime


def _rt(agent: str, binary: str) -> LocalCLIRuntime:
    return LocalCLIRuntime(agent=agent, binary=binary, cwd=".")


def test_scout_declares_search_policy_default_roles_do_not():
    assert literature_scout().tool_policy == "search"
    assert reviewer().tool_policy is None  # default roles stay text-only


def test_claude_search_policy_uses_cli_allowlist_not_prompt_hope():
    rt = _rt("claude", r"C:\fake\claude.cmd")
    cmd, stdin_text = rt._build_invocation("SYS", "USER body", tool_policy="search")
    joined = " ".join(cmd)
    assert "--allowedTools" in joined and "WebSearch" in cmd and "WebFetch" in cmd
    assert "ONE-SHOT TEXT-ONLY" not in (stdin_text or ""), "text-only preamble must be replaced"
    assert "web search" in (stdin_text or "")
    # default stays text-only with no allowlist
    cmd2, stdin2 = rt._build_invocation("SYS", "USER body")
    assert "--allowedTools" not in " ".join(cmd2)
    assert "ONE-SHOT TEXT-ONLY" in (stdin2 or "")


def test_full_policy_drops_the_preamble():
    rt = _rt("claude", r"C:\fake\claude.cmd")
    cmd, stdin_text = rt._build_invocation("SYS", "USER", tool_policy="full")
    assert "ONE-SHOT TEXT-ONLY" not in (stdin_text or "")


def test_kimi_search_is_prompt_level_only():
    rt = _rt("kimi", r"C:\fake\kimi.exe")
    cmd, stdin_text = rt._build_invocation("SYS", "中文 body", tool_policy="search")
    joined = " ".join(cmd)
    assert "--allowedTools" not in joined, "kimi has no allowlist flag; must not invent one"
    assert "web search" in joined, "search policy falls back to prompt-level for kimi"


def test_agent_passes_policy_to_runtime():
    rt = MockRuntime(["[LITERATURE] some paper (https://example.com)"])
    board = Blackboard()
    agents = [RoleAgent(literature_scout(), rt, name="Scout")]
    cfg = SessionConfig(task="t", max_rounds=1, challenge_grace_rounds=5,
                        stall_rounds=9, require_verifier_gate=False)
    Orchestrator(board, agents, cfg).run()
    assert rt.calls and rt.calls[0]["tool_policy"] == "search"
    assert board.messages(tag=Tag.LITERATURE), "scout's LITERATURE post must land on the board"


def test_mock_runtime_accepts_tool_policy_kwarg():
    rt = MockRuntime(["[NOTE] x"])
    reply = rt.complete(system="s", user="u", tool_policy="search")
    assert reply.text.startswith("[NOTE]")
