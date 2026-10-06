import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mad.cli_runtime import LocalCLIRuntime


def _rt(agent: str, binary: str) -> LocalCLIRuntime:
    return LocalCLIRuntime(agent=agent, binary=binary, cwd=".")


def test_claude_uses_stdin_so_unicode_and_structure_survive():
    rt = _rt("claude", r"C:\fake\claude.cmd")
    cmd, stdin_text = rt._build_invocation("SYS keep tools off", "USER 观察：黑板上有三条\n## 分节内容")
    assert stdin_text is not None
    assert "观察" in stdin_text and "## 分节内容" in stdin_text
    joined = " ".join(cmd)
    assert "-p" in cmd
    assert "SYS" not in joined, "stdin mode must not put the prompt in argv"
    assert "--output-format" in cmd


def test_kimi_exe_keeps_unicode_in_argv():
    rt = _rt("kimi", r"C:\fake\kimi.exe")
    cmd, stdin_text = rt._build_invocation("SYS", "中文任务描述")
    assert stdin_text is None
    assert "中文任务描述" in " ".join(cmd)


def test_kimi_cmd_shim_falls_back_to_ascii():
    rt = _rt("kimi", r"C:\fake\kimi.cmd")
    cmd, stdin_text = rt._build_invocation("SYS", "中文任务描述")
    assert stdin_text is None
    assert "中文" not in " ".join(cmd)


def test_code_policy_allowlist_covers_powershell_as_well_as_bash():
    # Windows hosts without Git Bash expose only the PowerShell tool ("Git Bash
    # not found; BashTool will be unavailable") — a Bash-only allowlist denies
    # every shell command and the Builder burns rounds probing instead of working.
    rt = _rt("claude", r"C:\fake\claude.cmd")
    cmd, _ = rt._build_invocation("SYS", "USER", tool_policy="code")
    joined = " ".join(cmd)
    assert "--allowedTools" in cmd
    assert "Bash(python *)" in joined
    assert "PowerShell(python *)" in joined
    assert "PowerShell(Get-Content *)" in joined
    assert "Read" in cmd and "Write" in cmd and "Edit" in cmd


def test_search_policy_allowlist_has_web_tools():
    rt = _rt("claude", r"C:\fake\claude.cmd")
    cmd, _ = rt._build_invocation("SYS", "USER", tool_policy="search")
    assert "WebSearch" in cmd and "WebFetch" in cmd


def test_kimi_has_no_allowlist_flag_so_code_policy_adds_nothing():
    rt = _rt("kimi", r"C:\fake\kimi.exe")
    cmd, _ = rt._build_invocation("SYS", "USER", tool_policy="code")
    assert "--allowed-tools" not in " ".join(cmd)
