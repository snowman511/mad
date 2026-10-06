"""Core data models for the multi-agent discussion protocol."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Tag(str, Enum):
    """Message tags. Every board post must carry exactly one primary tag.

    DEAD_END and COUNTEREXAMPLE are immutable — they are the shared negative
    memory that stops agents from re-walking the same dead roads.
    """

    # Research discourse
    CLAIM = "CLAIM"
    EVIDENCE = "EVIDENCE"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    DEAD_END = "DEAD_END"
    FIX = "FIX"
    QUESTION = "QUESTION"
    CONFIRMED = "CONFIRMED"
    UNCONTESTED = "UNCONTESTED"
    PROPOSAL = "PROPOSAL"
    PLAN = "PLAN"
    # Experiment / literature
    BASELINE_RESULT = "BASELINE_RESULT"
    EXPERIMENT_RESULT = "EXPERIMENT_RESULT"
    LEAKAGE_SUSPECTED = "LEAKAGE_SUSPECTED"
    LITERATURE = "LITERATURE"
    # Formal / verification
    LEMMA_PROVED = "LEMMA_PROVED"
    VERDICT = "VERDICT"
    # Meta
    NOTE = "NOTE"
    SYSTEM = "SYSTEM"

    @classmethod
    def parse(cls, raw: str) -> "Tag":
        key = raw.strip().upper().lstrip("[")
        key = key.rstrip("]")
        # allow aliases used in the research notes
        aliases = {
            "COUNTER": cls.COUNTEREXAMPLE,
            "REBUTTAL": cls.COUNTEREXAMPLE,
            "FAILED": cls.DEAD_END,
            "FAILURE": cls.DEAD_END,
            "RESULT": cls.EXPERIMENT_RESULT,
            "OPEN": cls.QUESTION,
            "OK": cls.CONFIRMED,
            "PASS": cls.CONFIRMED,
            "PASSED": cls.CONFIRMED,
            "LEAK": cls.LEAKAGE_SUSPECTED,
        }
        if key in aliases:
            return aliases[key]
        try:
            return cls(key)
        except ValueError as exc:
            raise ValueError(f"unknown tag: {raw!r}") from exc


#: Tags that record negative results and must never be deleted.
IMMUTABLE_TAGS = frozenset({Tag.DEAD_END, Tag.COUNTEREXAMPLE})

#: Tags that represent a truth-claim requiring challenge before acceptance.
CLAIM_LIKE = frozenset({Tag.CLAIM, Tag.PROPOSAL, Tag.LEMMA_PROVED, Tag.PLAN})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Message:
    """One post on the shared blackboard."""

    tag: Tag
    body: str
    author: str
    id: str = field(default_factory=_new_id)
    round_no: int = 0
    parent_id: str | None = None
    created_at: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    #: lifecycle status: open -> challenged | uncontested | confirmed | dead
    status: str = "open"

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("message body must be non-empty")
        if not self.author or not self.author.strip():
            raise ValueError("message author must be non-empty")

    @property
    def is_immutable(self) -> bool:
        return self.tag in IMMUTABLE_TAGS

    @property
    def needs_challenge(self) -> bool:
        return self.tag in CLAIM_LIKE and self.status == "open"

    def brief(self, limit: int = 120) -> str:
        text = " ".join(self.body.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tag": self.tag.value,
            "body": self.body,
            "author": self.author,
            "round_no": self.round_no,
            "parent_id": self.parent_id,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            tag=Tag.parse(data["tag"]),
            body=data["body"],
            author=data["author"],
            id=data["id"],
            round_no=int(data.get("round_no", 0)),
            parent_id=data.get("parent_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
            metadata=dict(data.get("metadata") or {}),
            status=data.get("status", "open"),
        )


@dataclass
class RoleSpec:
    """A research role: system prompt + what it is allowed / required to do."""

    name: str
    mission: str
    system_prompt: str
    #: tags this role is primarily responsible for producing
    primary_tags: tuple[Tag, ...] = ()
    #: if True, this role must challenge open claims within the grace window
    is_challenger: bool = False
    #: None = text-only one-shot (default) | "search" = web search / fetch
    #: allowed | "full" = no framework-imposed restriction (the CLI's own
    #: permission system still applies)
    tool_policy: str | None = None

    def render_prompt(self, task: str) -> str:
        return (
            f"{self.system_prompt.strip()}\n\n"
            f"## Current research task\n{task.strip()}\n\n"
            f"## Your mission\n{self.mission.strip()}"
        )


@dataclass
class SessionConfig:
    """Knobs for one discussion session."""

    task: str
    max_rounds: int = 20
    #: a claim with no challenge within this many rounds becomes UNCONTESTED
    challenge_grace_rounds: int = 2
    #: stop after this many consecutive rounds with no new CLAIM
    stall_rounds: int = 3
    #: hard budget on total messages
    max_messages: int = 200
    #: refuse posts with no new information (heuristic)
    reject_empty_posts: bool = True
    require_verifier_gate: bool = True
    #: roles see only messages newer than this many rounds when priming context
    context_window_rounds: int = 8
    #: optimization mode: stop after this many rounds with no new_best (0 = off)
    no_improvement_rounds: int = 0
    #: minimum score delta to count as improvement (e.g. 0.005 for F1)
    improvement_delta: float = 0.0
    #: optional absolute target; reaching it stops the session
    target_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "max_rounds": self.max_rounds,
            "challenge_grace_rounds": self.challenge_grace_rounds,
            "stall_rounds": self.stall_rounds,
            "max_messages": self.max_messages,
            "reject_empty_posts": self.reject_empty_posts,
            "require_verifier_gate": self.require_verifier_gate,
            "context_window_rounds": self.context_window_rounds,
            "no_improvement_rounds": self.no_improvement_rounds,
            "improvement_delta": self.improvement_delta,
            "target_score": self.target_score,
        }


_TAG_IN_BODY = re.compile(r"^\s*\[?(?P<tag>[A-Z_]+)\]?\s*[:\-]?\s*", re.MULTILINE)


def parse_tagged_body(raw: str, default: Tag = Tag.NOTE) -> tuple[Tag, str]:
    """Split a free-form agent reply into (tag, body).

    Accepts ``[CLAIM] foo``, ``• [CLAIM] foo``, ``* [CLAIM] foo``, ``CLAIM: foo``.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty body")

    # strip bullet/ornament prefixes some CLIs emit
    text = re.sub(r"^[\s•·*\-–—>]+", "", text).strip()

    # [TAG] rest  /  [TAG]: rest  /  [TAG] - rest
    m = re.match(r"^\[(?P<tag>[A-Z_]+)\]\s*(?:[:\-]\s*)?(?P<rest>.+)$", text, re.DOTALL)
    if m:
        try:
            rest = m.group("rest").strip()
            if rest:
                return Tag.parse(m.group("tag")), rest
        except ValueError:
            pass

    # tag buried after a short lead-in, e.g. "Post: [CLAIM] ..." or "## Board [COUNTEREXAMPLE] ..."
    m = re.search(r"\[(?P<tag>[A-Z_]{3,})\]\s*(?P<rest>.+)$", text[:240], re.DOTALL)
    if m:
        try:
            rest = m.group("rest").strip()
            if rest:
                return Tag.parse(m.group("tag")), rest
        except ValueError:
            pass

    # TAG: rest  /  TAG - rest
    m = re.match(r"^(?P<tag>[A-Z_]{2,})\s*[:\-]\s*(?P<rest>.+)$", text, re.DOTALL)
    if m:
        try:
            rest = m.group("rest").strip()
            if rest:
                return Tag.parse(m.group("tag")), rest
        except ValueError:
            pass

    return default, text
