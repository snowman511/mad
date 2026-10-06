"""Objective verification gate.

C-HD's lesson: agents' confidence is worthless; the machine checker is the merge
gate. For general research this interface wraps property tests / benchmarks /
reproducible scripts — NOT majority voting.
"""

from __future__ import annotations

import json
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class VerificationResult:
    passed: bool
    score: float | None = None
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "summary": self.summary,
            "details": self.details,
        }


class Verifier(ABC):
    """Merge gate. Return passed=True only when objective criteria hold."""

    @abstractmethod
    def verify(self, context: dict[str, Any]) -> VerificationResult:
        raise NotImplementedError


class NullVerifier(Verifier):
    """Always fails when the gate is required; useful for pure discussion dry-runs."""

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        return VerificationResult(
            passed=False,
            summary="未配置客观闸门（NullVerifier）：请配置 script / threshold 验证器",
            details={"reason": "configure ScriptVerifier or a custom Verifier"},
        )


class CallableVerifier(Verifier):
    """Wrap a Python function as a gate."""

    def __init__(self, fn: Callable[[dict[str, Any]], VerificationResult | bool | dict]) -> None:
        self._fn = fn

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        out = self._fn(context)
        if isinstance(out, VerificationResult):
            return out
        if isinstance(out, bool):
            return VerificationResult(passed=out, summary=f"callable returned {out}")
        if isinstance(out, dict):
            return VerificationResult(
                passed=bool(out.get("passed", False)),
                score=out.get("score"),
                summary=str(out.get("summary", "")),
                details=out,
            )
        raise TypeError(f"unsupported verifier return type: {type(out)}")


class ScriptVerifier(Verifier):
    """Run an external command; exit code 0 means pass.

    The script receives a JSON context on stdin and must print JSON:
    {"passed": true/false, "score": optional float, "summary": str, ...}
    If the output is not JSON, a zero exit code still means passed=True.
    """

    def __init__(self, command: list[str], *, cwd: str | Path | None = None, timeout: float = 300.0) -> None:
        if not command:
            raise ValueError("command must be non-empty")
        self.command = list(command)
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        # Always exchange UTF-8 text; Windows default codepage is not UTF-8.
        payload = json.dumps(context, ensure_ascii=False)
        proc = subprocess.run(
            self.command,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.cwd,
            timeout=self.timeout,
        )
        stdout = (proc.stdout or "").strip()
        parsed: dict[str, Any] | None = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        if parsed is not None:
            return VerificationResult(
                passed=bool(parsed.get("passed", proc.returncode == 0)),
                score=parsed.get("score"),
                summary=str(parsed.get("summary", stdout[:200])),
                details=parsed,
            )
        return VerificationResult(
            passed=proc.returncode == 0,
            summary=(stdout or (proc.stderr or "")[:200]) or f"exit={proc.returncode}",
            details={
                "returncode": proc.returncode,
                "stdout": stdout[-2000:],
                "stderr": (proc.stderr or "")[-2000:],
            },
        )


class ThresholdVerifier(Verifier):
    """Pass when a numeric metric in the candidate meets a threshold.

    context = {"candidate": {"metrics": {"event_f1": 0.83, ...}, ...}, ...}
    """

    def __init__(self, metric: str, threshold: float, *, higher_is_better: bool = True) -> None:
        self.metric = metric
        self.threshold = threshold
        self.higher_is_better = higher_is_better

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        metrics = (context.get("candidate") or {}).get("metrics") or context.get("metrics") or {}
        if self.metric not in metrics:
            return VerificationResult(
                passed=False,
                summary=f"metric {self.metric} missing",
                details={"metrics": metrics},
            )
        value = float(metrics[self.metric])
        ok = value >= self.threshold if self.higher_is_better else value <= self.threshold
        return VerificationResult(
            passed=ok,
            score=value,
            summary=f"{self.metric}={value}，阈值 {self.threshold} -> {'通过' if ok else '未通过'}",
            details={"metric": self.metric, "value": value, "threshold": self.threshold},
        )


class CompositeVerifier(Verifier):
    """All sub-verifiers must pass (AND). Optional weighted score average."""

    def __init__(self, verifiers: list[Verifier], *, require_all: bool = True) -> None:
        self.verifiers = list(verifiers)
        self.require_all = require_all

    def verify(self, context: dict[str, Any]) -> VerificationResult:
        results = [v.verify(context) for v in self.verifiers]
        if self.require_all:
            passed = all(r.passed for r in results)
        else:
            passed = any(r.passed for r in results)
        scores = [r.score for r in results if r.score is not None]
        summary = " | ".join(r.summary for r in results if r.summary)
        return VerificationResult(
            passed=passed,
            score=(sum(scores) / len(scores)) if scores else None,
            summary=summary,
            details={"results": [r.to_dict() for r in results]},
        )
