"""Cross-campaign memory: collect / merge / seed round-trips."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mad.blackboard import Blackboard
from mad.cli import run_from_config
from mad.memory import collect_memory, save_memory, seed_from_memory
from mad.models import Message, Tag


def test_collect_memory_picks_negatives_questions_and_measured():
    b = Blackboard()
    b.post(Message(tag=Tag.DEAD_END, body="dead", author="a"))
    b.post(Message(tag=Tag.COUNTEREXAMPLE, body="ce", author="a"))
    b.post(Message(tag=Tag.QUESTION, body="q?", author="a"))
    b.post(Message(tag=Tag.NOTE, body="noise", author="a"))
    e = b.post(Message(tag=Tag.EXPERIMENT_RESULT, body="exp", author="a", round_no=1))
    b.append_metadata(e.id, measured={"speedup": 2.0})
    b.post(Message(tag=Tag.EXPERIMENT_RESULT, body="no metrics", author="a", round_no=1))
    mems = collect_memory(b)
    tags = sorted(m.tag.value for m in mems)
    assert tags == ["COUNTEREXAMPLE", "DEAD_END", "EXPERIMENT_RESULT", "QUESTION"]
    assert all(m.metadata.get("measured") for m in mems if m.tag == Tag.EXPERIMENT_RESULT)


def test_save_seed_roundtrip_and_dedupe(tmp_path):
    b = Blackboard()
    b.post(Message(tag=Tag.DEAD_END, body="heap variant failed", author="modeler"))
    b.post(Message(tag=Tag.QUESTION, body="does bucket queue help?", author="e"))
    mem = tmp_path / "memory.json"
    n1 = save_memory(mem, b)
    n2 = save_memory(mem, b)  # re-saving the same board must not grow the file
    assert n1 == n2
    # a case-variant restatement from a later campaign dedupes by body
    b2 = Blackboard()
    b2.post(Message(tag=Tag.DEAD_END, body="Heap Variant  Failed", author="other"))
    save_memory(mem, b2)
    records = json.loads(mem.read_text(encoding="utf-8"))
    assert len([r for r in records if r["tag"] == "DEAD_END"]) == 1
    assert all(r["round_no"] == 0 for r in records)
    # seed a fresh board
    b3 = Blackboard()
    loaded = seed_from_memory(mem, b3)
    assert loaded == len(records)
    assert {m.tag for m in b3.messages()} == {Tag.DEAD_END, Tag.QUESTION}
    assert b3.failed_approaches(), "seeded negatives must appear in shared memory"


def test_memory_trim_keeps_recent(tmp_path):
    mem = tmp_path / "memory.json"
    b = Blackboard()
    for i in range(5):
        b.post(Message(tag=Tag.DEAD_END, body=f"dead end number {i}", author="a"))
    save_memory(mem, b, max_messages=3)
    records = json.loads(mem.read_text(encoding="utf-8"))
    assert len(records) == 3
    assert records[-1]["body"].startswith("dead end number 4"), "trim must keep the most recent"


def test_run_from_config_carries_memory_across_campaigns(tmp_path):
    mem = tmp_path / "memory.json"
    cfg = {
        "task": "t",
        "roles": ["explorer"],
        "max_rounds": 2,
        "challenge_grace_rounds": 5,
        "stall_rounds": 9,
        "require_verifier_gate": False,
        "runtime": {"kind": "mock", "replies": ["[DEAD_END] pairing heap approach failed"]},
    }
    r1 = run_from_config(cfg, memory_path=mem)
    assert mem.exists(), "campaign 1 must leave a memory file"
    # the mock explorer repeats its DEAD_END in round 2; the duplicate is
    # rejected as a restatement but stays on the board (immutable), so the
    # board holds 2 — the memory FILE must hold exactly 1 (body dedupe)
    assert r1["memory_items"] >= 1
    records = json.loads(mem.read_text(encoding="utf-8"))
    assert len([r for r in records if r["tag"] == "DEAD_END"]) == 1
    r2 = run_from_config(cfg, memory_path=mem)
    seeds = [m for m in r2["board"] if m["round_no"] == 0]
    assert any(m["tag"] == "DEAD_END" for m in seeds), "campaign 2 must start with inherited negatives"
    assert any(
        m["tag"] == "SYSTEM" and "memory" in m.get("body", "").lower() for m in r2["board"]
    ), "agents must be told the memory is inherited"
