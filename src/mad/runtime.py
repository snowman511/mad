"""LLM runtimes: pluggable backends that drive role agents.

Keep this layer thin. The framework must run end-to-end with MockRuntime
before any real model is wired in.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class LLMReply:
    text: str
    raw: dict[str, Any] | None = None
    #: provenance: which runtime/model produced this, cost and latency hints
    provenance: dict[str, Any] | None = None


class RuntimeStopped(Exception):
    """Raised inside a runtime call when a session stop was requested.

    The orchestrator turns this into a normal `stopped_by_user` termination
    instead of logging the agent as crashed.
    """


class LLMRuntime(ABC):
    """One chat completion."""

    #: set by the session manager; checked around blocking calls so a
    #: user-initiated stop can interrupt a long completion.
    stop_event: threading.Event | None = None

    def _check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeStopped("session stop requested")

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_policy: str | None = None,
    ) -> LLMReply:
        """One chat completion.

        tool_policy: role-declared tool policy ("search" | "full" | None).
        Runtimes that cannot honor it should accept and ignore it.
        """
        raise NotImplementedError


class MockRuntime(LLMRuntime):
    """Deterministic canned replies for pipeline tests and demos.

    Sequence of texts is consumed in order; the last one repeats.
    """

    def __init__(self, replies: Sequence[str] | None = None) -> None:
        self._replies = list(replies or [
            "[CLAIM] Mock 主张：方案 A 在稀疏场景下优于基线。",
            "[COUNTEREXAMPLE] Mock 反例：退化输入会破坏堆不变量。",
            "[DEAD_END] Mock 死路：成对堆变体给不出干净的下界，放弃。",
            "[EVIDENCE] Mock 证据：20 次随机试验，平均加速 1.2 倍，seed=42。",
            "[CONFIRMED] Mock 确认：修复后的变体通过全部性质测试。",
        ])
        self._i = 0
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_policy: str | None = None,
    ) -> LLMReply:
        self._check_stop()
        t0 = time.monotonic()
        self.calls.append({"system": system, "user": user, "tool_policy": tool_policy})
        text = self._replies[min(self._i, len(self._replies) - 1)]
        self._i += 1
        return LLMReply(
            text=text,
            provenance={
                "runtime": "mock",
                "model": "mock",
                "latency_ms": round((time.monotonic() - t0) * 1000, 2),
            },
        )


class OpenAICompatibleRuntime(LLMRuntime):
    """Minimal chat-completions client (OpenAI-compatible /v1/chat/completions).

    Works with OpenAI, DeepSeek, Moonshot, vLLM, Ollama's OpenAI shim, etc.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("MAD_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tool_policy: str | None = None,  # no tool use over plain chat completions
    ) -> LLMReply:
        self._check_stop()
        if not self.api_key:
            raise RuntimeError(
                "no API key: set MAD_API_KEY / OPENAI_API_KEY or pass api_key="
            )
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
        text = data["choices"][0]["message"]["content"]
        self._check_stop()
        return LLMReply(
            text=text or "",
            raw=data,
            provenance={
                "runtime": "openai_compatible",
                "model": data.get("model") or self.model,
                "latency_ms": round((time.monotonic() - t0) * 1000, 2),
                "usage": data.get("usage"),
            },
        )
