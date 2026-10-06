"""Local CLI agent runtimes (Claude Code / Kimi CLI) and runtime registry.

Mirrors the Open Design model:
  mode = 本地 CLI  -> LocalCLIRuntime (claude / kimi)
  mode = 自带 Key  -> OpenAICompatibleRuntime

CLIs are invoked in non-interactive print mode:
  claude -p "prompt" --output-format text [--system-prompt ...] [--model ...]
  kimi   -p "prompt" --output-format text [-m model]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Sequence

from mad.runtime import LLMReply, LLMRuntime, MockRuntime, OpenAICompatibleRuntime, RuntimeStopped

__all__ = [
    "LLMReply",
    "LLMRuntime",
    "MockRuntime",
    "OpenAICompatibleRuntime",
    "LocalCLIRuntime",
    "RuntimeCatalog",
    "detect_cli_agents",
    "build_runtime",
]


def detect_cli_agents() -> list[dict[str, Any]]:
    """Probe PATH for known local agent CLIs."""
    probes = [
        {
            "id": "claude",
            "label": "Claude Code",
            "commands": ["claude.cmd", "claude", "claude.exe", "claude.ps1"],
        },
        {
            "id": "kimi",
            "label": "Kimi CLI",
            "commands": ["kimi.cmd", "kimi", "kimi.exe"],
        },
    ]
    out: list[dict[str, Any]] = []
    for p in probes:
        found = None
        for name in p["commands"]:
            found = shutil.which(name)
            if found:
                break
        out.append(
            {
                "id": p["id"],
                "label": p["label"],
                "available": bool(found),
                "path": found,
                "mode": "local_cli",
            }
        )
    return out


@dataclass
class CLIProfile:
    id: str
    label: str
    binary_names: tuple[str, ...]
    model_flag: tuple[str, ...]  # e.g. ("--model",) or ("-m",)
    supports_system_prompt: bool = True
    #: prompt travels via stdin instead of argv — preserves Unicode and newlines
    stdin_prompt: bool = False
    #: CLI flag that whitelists tools; empty = the CLI has no allowlist mechanism
    allowed_tools_flag: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()


CLI_PROFILES: dict[str, CLIProfile] = {
    "claude": CLIProfile(
        id="claude",
        label="Claude Code",
        binary_names=("claude.cmd", "claude", "claude.exe"),
        model_flag=("--model",),
        supports_system_prompt=True,
        stdin_prompt=True,  # `claude -p` reads the prompt from stdin when piped
        allowed_tools_flag=("--allowedTools",),  # enforced by the CLI's own permission system
        extra_args=("--output-format", "text"),
    ),
    "kimi": CLIProfile(
        id="kimi",
        label="Kimi CLI",
        # prefer the real exe over .cmd — cmd.exe re-encodes argv via the ANSI codepage
        binary_names=("kimi.exe", "kimi", "kimi.cmd"),
        model_flag=("-m",),
        supports_system_prompt=False,
        extra_args=("--output-format", "text"),
    ),
}


NO_TOOLS_PREAMBLE = (
    "IMPORTANT: ONE-SHOT TEXT-ONLY mode. Do NOT use any tools. "
    "Do NOT read/write/edit files. Do NOT run shell commands. "
    "Reply with your board post text ONLY. "
)

SEARCH_MODE_PREAMBLE = (
    "TOOL POLICY: you may use web search and page fetching to ground your post in real "
    "sources. Cite what you actually retrieved (title + URL). "
    "Do NOT read/write/edit files and do NOT run shell commands. "
    "Reply with your board post text ONLY. "
)

CODE_MODE_PREAMBLE = (
    "TOOL POLICY: you are the Builder. You may Read, Write, Edit files and run shell "
    "commands **only inside your assigned working directory** (your sandbox). "
    "You must NOT touch anything outside it. "
    "Only these shell commands are pre-approved: python and pip, plus basic inspection "
    "(ls, dir, Get-ChildItem, cat, type, Get-Content, head, cd, Set-Location, mkdir, "
    "New-Item, echo, Test-Path, Get-Command). Anything else is DENIED by the CLI's "
    "permission layer -- do not waste turns probing it. "
    "On Windows the Bash tool may be unavailable (no Git Bash); use the PowerShell tool "
    "the same way. "
    "Your task: implement the assigned spec as working code in the sandbox, "
    "verify it runs (python <script> --selftest or a smoke run), then reply with your "
    "board post text ONLY (a summary of what you built + how to run it). "
    "End the post with the exact line [POST-END] on a line of its own. "
    "Do NOT git commit. Do NOT install packages. Do NOT modify files outside the sandbox."
)
POST_END_SENTINEL = "[POST-END]"


def _argv_safe(text: str, *, allow_unicode: bool = False) -> str:
    """Make a prompt safe for Windows argv: one line, no codepage-hostile chars.

    .exe targets receive Unicode argv via CreateProcessW and keep non-ASCII
    as-is; .cmd shims route through cmd.exe, which may re-encode argv via the
    ANSI codepage (GBK/cp936), so those get non-ASCII replaced, not corrupted.
    """
    flat = " ".join(text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").split())
    if allow_unicode:
        return flat
    out = []
    for ch in flat:
        o = ord(ch)
        if o < 128:
            out.append(ch)
        elif ch in "，。、；：？！“”‘’（）《》—…·【】":
            out.append(" ")
        else:
            out.append("?")
    return " ".join("".join(out).split())


class LocalCLIRuntime(LLMRuntime):
    """Drive a locally installed agent CLI in one-shot print mode.

    By default runs inside a scratch temp dir so the CLI does not wander the
    user's project with its tools. Pass cwd= if a role really needs a workspace.
    """

    def __init__(
        self,
        agent: str = "kimi",
        *,
        model: str | None = None,
        timeout: float = 180.0,
        cwd: str | None = None,
        binary: str | None = None,
    ) -> None:
        if agent not in CLI_PROFILES:
            raise ValueError(f"unknown local CLI agent: {agent}. known: {sorted(CLI_PROFILES)}")
        self.profile = CLI_PROFILES[agent]
        self.agent = agent
        self.model = model
        self.timeout = timeout
        self.cwd = cwd or tempfile.mkdtemp(prefix="mad_cli_")
        self.binary = binary or self._resolve_binary()

    def _resolve_binary(self) -> str:
        for name in self.profile.binary_names:
            path = shutil.which(name)
            if path:
                return path
        raise RuntimeError(
            f"{self.profile.label} not found on PATH "
            f"(tried: {', '.join(self.profile.binary_names)})"
        )

    def _preamble(self, tool_policy: str | None) -> str:
        if tool_policy == "search":
            return SEARCH_MODE_PREAMBLE
        if tool_policy == "code":
            return CODE_MODE_PREAMBLE
        if tool_policy == "full":
            return ""
        return NO_TOOLS_PREAMBLE

    def _build_invocation(
        self, system: str, user: str, tool_policy: str | None = None
    ) -> tuple[list[str], str | None]:
        """Build (argv, stdin payload). A None payload means the prompt rides in argv."""
        system = self._preamble(tool_policy) + (system or "")
        # .exe targets receive Unicode argv via CreateProcessW; .cmd shims go
        # through cmd.exe and may re-encode via the ANSI codepage (GBK on zh-CN).
        allow_unicode = self.binary.lower().endswith(".exe")
        # whitelist the search tools at the CLI's own permission layer — only
        # possible when the CLI exposes an allowlist flag (claude does, kimi not)
        tool_args: list[str] = []
        if tool_policy == "search" and self.profile.allowed_tools_flag:
            tool_args = [*self.profile.allowed_tools_flag, "WebSearch", "WebFetch"]
        elif tool_policy == "code" and self.profile.allowed_tools_flag:
            # Two shell tools exist on Windows hosts: the Git-Bash-backed "Bash"
            # and the native "PowerShell".  Claude Code logs "Git Bash not found;
            # BashTool will be unavailable" when bash is not on its PATH and the
            # model then reaches for PowerShell -- so the allowlist must cover
            # BOTH, or every command dies at the permission layer (the exact bug
            # that made the Builder report "everything is gated behind approval").
            tool_args = [*self.profile.allowed_tools_flag,
                         "Read", "Write", "Edit", "Glob", "Grep",
                         "Bash(python *)", "Bash(ls *)", "Bash(pip *)", "Bash(mkdir *)",
                         "Bash(cat *)", "Bash(head *)", "Bash(wc *)", "Bash(cd *)",
                         "PowerShell(python *)", "PowerShell(pip *)",
                         "PowerShell(ls *)", "PowerShell(dir *)", "PowerShell(Get-ChildItem *)",
                         "PowerShell(cat *)", "PowerShell(type *)", "PowerShell(Get-Content *)",
                         "PowerShell(head *)", "PowerShell(wc *)", "PowerShell(mkdir *)",
                         "PowerShell(New-Item *)", "PowerShell(cd *)", "PowerShell(Set-Location *)",
                         "PowerShell(echo *)", "PowerShell(Test-Path *)", "PowerShell(Get-Command *)"]
        if self.profile.stdin_prompt:
            cmd = [self.binary, "-p", *self.profile.extra_args, *tool_args]
            if self.model:
                cmd += [*self.profile.model_flag, self.model]
            # fold the system prompt into stdin so Unicode and newlines survive
            payload = (
                f"{system.strip()}\n\n---\n\n{user.strip()}\n" if system.strip() else f"{user.strip()}\n"
            )
            return cmd, payload
        if self.profile.supports_system_prompt:
            cmd = [self.binary, "-p", _argv_safe(user, allow_unicode=allow_unicode), *self.profile.extra_args]
            if system.strip():
                cmd += ["--system-prompt", _argv_safe(system, allow_unicode=allow_unicode)]
        else:
            merged = _argv_safe(f"{system.strip()} --- {user.strip()}", allow_unicode=allow_unicode)
            cmd = [self.binary, "-p", merged, *self.profile.extra_args]
        cmd += tool_args
        if self.model:
            cmd += [*self.profile.model_flag, self.model]
        return cmd, None

    def _wait_with_stop(self, proc: subprocess.Popen, stdin_text: str | None) -> tuple[str, str]:
        """communicate() in a poll loop so a stop request kills the CLI mid-call.

        subprocess documents that retrying communicate() after TimeoutExpired
        never loses partial output. Killing on Windows may not take down the
        whole process tree behind a .cmd shim, but it unblocks the session,
        which is the point.
        """
        deadline = time.monotonic() + self.timeout
        out: str | None = None
        err = ""
        while out is None:
            if self.stop_event is not None and self.stop_event.is_set():
                proc.kill()
                out, err = proc.communicate()
                raise RuntimeStopped(f"{self.profile.label} call interrupted by stop request")
            try:
                out, err = proc.communicate(input=stdin_text, timeout=0.25)
            except subprocess.TimeoutExpired:
                # input was handed to the writer thread; retrying must not pass it again
                stdin_text = None
                if time.monotonic() > deadline:
                    proc.kill()
                    out, err = proc.communicate()
                    raise RuntimeError(
                        f"{self.profile.label} timed out after {self.timeout}s"
                    ) from None
        return out, err

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_policy: str | None = None,
    ) -> LLMReply:
        cmd, stdin_text = self._build_invocation(system, user, tool_policy)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.cwd,
            shell=False,
            env=env,
        )
        t0 = time.monotonic()
        stdout, stderr = self._wait_with_stop(proc, stdin_text)
        latency_ms = round((time.monotonic() - t0) * 1000, 2)

        stdout = (stdout or "").strip()
        stderr = (stderr or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(
                f"{self.profile.label} exit={proc.returncode}: {stderr[-400:] or stdout[-400:]}"
            )
        # strip CLI status trailers like "To resume this session: ..."
        text = stdout if stdout else stderr
        lines = []
        for line in text.splitlines():
            if line.strip().startswith("To resume this session"):
                break
            lines.append(line)
        text = "\n".join(lines).strip()
        provenance = {
            "runtime": f"cli:{self.agent}",
            "model": self.model or "default",
            "latency_ms": latency_ms,
        }
        if tool_policy == "code":
            # The upstream backend occasionally cuts long generations mid-word
            # (exit 0, no error).  Builder posts carry a sentinel line so the
            # loss is detectable instead of silently truncating board records.
            if POST_END_SENTINEL in text:
                text = text.replace(POST_END_SENTINEL, "").rstrip()
            else:
                provenance["post_maybe_truncated"] = True
        return LLMReply(
            text=text,
            raw={"returncode": proc.returncode, "stderr": stderr[-500:]},
            provenance=provenance,
        )


class RuntimeCatalog:
    """What the UI dropdown shows."""

    @staticmethod
    def list() -> dict[str, Any]:
        clis = detect_cli_agents()
        return {
            "modes": [
                {
                    "id": "local_cli",
                    "label": "本地 CLI",
                    "agents": clis,
                },
                {
                    "id": "api_key",
                    "label": "自带 Key",
                    "agents": [
                        {
                            "id": "openai_compatible",
                            "label": "OpenAI 兼容",
                            "available": True,
                            "mode": "api_key",
                        },
                        {
                            "id": "mock",
                            "label": "Mock（管道自测）",
                            "available": True,
                            "mode": "api_key",
                        },
                    ],
                },
            ],
            "default_mode": "local_cli" if any(c["available"] for c in clis) else "api_key",
            "default_agent": next((c["id"] for c in clis if c["available"]), "mock"),
        }


def build_runtime(cfg: dict[str, Any]) -> LLMRuntime:
    """Build a runtime from UI/API settings dict.

    Expected keys:
      mode: "local_cli" | "api_key"
      agent: "claude" | "kimi" | "openai_compatible" | "mock"
      model: optional str
      base_url / api_key: for api_key mode
    """
    mode = cfg.get("mode") or "local_cli"
    agent = cfg.get("agent") or "mock"
    model = cfg.get("model") or None

    if mode == "local_cli" or agent in CLI_PROFILES:
        return LocalCLIRuntime(
            agent=agent if agent in CLI_PROFILES else "kimi",
            model=model,
            timeout=float(cfg.get("timeout") or 180.0),
            cwd=cfg.get("cwd") or None,
        )

    if agent == "mock" or mode == "mock":
        replies = cfg.get("replies")
        return MockRuntime(replies)

    if agent in ("openai_compatible", "openai", "api") or mode == "api_key":
        return OpenAICompatibleRuntime(
            model=model or cfg.get("model") or "gpt-4o-mini",
            api_key=cfg.get("api_key"),
            base_url=cfg.get("base_url") or "https://api.openai.com/v1",
        )

    raise ValueError(f"cannot build runtime from {cfg!r}")
