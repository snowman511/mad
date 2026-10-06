"""Cross-campaign memory: carry negative results between discussion sessions.

A campaign's most valuable output is often what NOT to do again. This module
collects a board's negative memory (DEAD_END / COUNTEREXAMPLE), open questions
and latest measured results, merges them into one JSON file, and re-seeds a
fresh board with them at round 0 — so the next campaign starts on the
shoulders of the last one instead of from amnesia.

Seeded messages are stamped round_no=0: they are context, never candidates
(see Orchestrator._candidate_context) and never UNCONTESTED targets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mad.blackboard import Blackboard
from mad.models import Message, Tag

#: tags carried across campaigns
MEMORY_TAGS = (Tag.DEAD_END, Tag.COUNTEREXAMPLE, Tag.QUESTION)

#: how many measured results to carry (the most recent ones)
MAX_MEASURED_RESULTS = 3

#: hard cap on the merged memory file
DEFAULT_MAX_MESSAGES = 500


def collect_memory(board: Blackboard) -> list[Message]:
    """The set of messages worth remembering after a campaign."""
    mems = list(board.failed_approaches()) + board.messages(tag=Tag.QUESTION)
    measured = [
        m
        for m in board.messages()
        if m.tag in (Tag.EXPERIMENT_RESULT, Tag.BASELINE_RESULT)
        and (m.metadata.get("measured") or m.metadata.get("metrics"))
    ]
    mems += measured[-MAX_MEASURED_RESULTS:]
    return mems


def _load_records(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [rec for rec in data if isinstance(rec, dict)]


def _body_key(rec: dict[str, Any]) -> tuple[str, str]:
    body = " ".join(str(rec.get("body", "")).lower().split())[:160]
    return (str(rec.get("tag")), body)


def save_memory(path: str | Path, board: Blackboard, *, max_messages: int = DEFAULT_MAX_MESSAGES) -> int:
    """Merge this board's memory into the file (dedupe by id and by body), and
    stamp every record round 0. Returns the merged record count."""
    records = _load_records(path) + [m.to_dict() for m in collect_memory(board)]
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_bodies: set[tuple[str, str]] = set()
    for rec in records:
        rid = rec.get("id")
        key = _body_key(rec)
        if rid and rid in seen_ids:
            continue
        if key in seen_bodies:
            continue
        if rid:
            seen_ids.add(rid)
        seen_bodies.add(key)
        rec = dict(rec)
        rec["round_no"] = 0
        merged.append(rec)
    merged = merged[-max_messages:]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return len(merged)


def seed_from_memory(path: str | Path, board: Blackboard) -> int:
    """Post the memory file onto a fresh board at round 0. Returns loaded count."""
    loaded = 0
    for rec in _load_records(path):
        rec = dict(rec)
        rec["round_no"] = 0
        try:
            board.post(Message.from_dict(rec))
            loaded += 1
        except Exception:
            continue
    return loaded


def announce(board: Blackboard, count: int) -> None:
    """One SYSTEM line so every agent knows the negative memory is inherited."""
    if count <= 0:
        return
    board.post(
        Message(
            tag=Tag.SYSTEM,
            body=f"Cross-campaign memory loaded: {count} items from previous campaigns.",
            author="orchestrator",
            round_no=0,
        )
    )
