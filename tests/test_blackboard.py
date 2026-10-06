import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mad.blackboard import Blackboard
from mad.models import Message, Tag


def test_post_and_read():
    b = Blackboard()
    m = b.post(Message(tag=Tag.CLAIM, body="algo A is faster", author="explorer", round_no=1))
    assert b.get(m.id) is not None
    assert len(b) == 1
    assert b.open_claims()[0].id == m.id


def test_dead_end_and_counterexample_are_immutable():
    b = Blackboard()
    de = b.post(Message(tag=Tag.DEAD_END, body="pairing heap variant failed", author="modeler"))
    ce = b.post(Message(tag=Tag.COUNTEREXAMPLE, body="gum vs chewing", author="skeptic"))
    try:
        b.delete(de.id)
        raise AssertionError("delete of DEAD_END must fail")
    except Exception as exc:
        assert "immutable" in str(exc).lower() or "refusing" in str(exc).lower()
    try:
        b.delete(ce.id)
        raise AssertionError("delete of COUNTEREXAMPLE must fail")
    except Exception as exc:
        assert "immutable" in str(exc).lower() or "refusing" in str(exc).lower()
    # normal posts can be deleted
    n = b.post(Message(tag=Tag.NOTE, body="scratch note about fonts", author="a"))
    b.delete(n.id)
    assert b.get(n.id) is None


def test_open_claims_without_challenge():
    b = Blackboard()
    c = b.post(Message(tag=Tag.CLAIM, body="claim X", author="explorer", round_no=1))
    # immediately: not yet stale
    assert b.claims_without_challenge(grace_rounds=2, current_round=1) == []
    # after grace with no attack
    stale = b.claims_without_challenge(grace_rounds=2, current_round=4)
    assert [m.id for m in stale] == [c.id]
    # attack makes it not stale
    b.post(
        Message(
            tag=Tag.COUNTEREXAMPLE,
            body="counterexample body",
            author="skeptic",
            round_no=2,
            parent_id=c.id,
        )
    )
    assert b.claims_without_challenge(grace_rounds=2, current_round=4) == []


def test_confirmed_by_other_counts_as_engagement():
    b = Blackboard()
    c = b.post(Message(tag=Tag.CLAIM, body="claim Y", author="explorer", round_no=1))
    b.post(
        Message(tag=Tag.CONFIRMED, body="verified, holds", author="skeptic", round_no=2, parent_id=c.id)
    )
    assert b.claims_without_challenge(grace_rounds=1, current_round=3) == []


def test_self_confirm_and_self_fix_do_not_count():
    b = Blackboard()
    c1 = b.post(Message(tag=Tag.CLAIM, body="claim S1", author="explorer", round_no=1))
    b.post(Message(tag=Tag.CONFIRMED, body="self confirm", author="explorer", round_no=2, parent_id=c1.id))
    c2 = b.post(Message(tag=Tag.CLAIM, body="claim S2", author="explorer", round_no=2))
    b.post(Message(tag=Tag.FIX, body="self fix", author="explorer", round_no=2, parent_id=c2.id))
    stale = b.claims_without_challenge(grace_rounds=1, current_round=3)
    assert {m.id for m in stale} == {c1.id, c2.id}


def test_failed_approaches_memory():
    b = Blackboard()
    b.post(Message(tag=Tag.DEAD_END, body="dead 1", author="m"))
    b.post(Message(tag=Tag.COUNTEREXAMPLE, body="ce 1", author="s"))
    b.post(Message(tag=Tag.CLAIM, body="live claim", author="e"))
    failed = b.failed_approaches()
    assert {m.tag for m in failed} == {Tag.DEAD_END, Tag.COUNTEREXAMPLE}
    assert len(failed) == 2


def test_empty_body_rejected():
    try:
        Message(tag=Tag.CLAIM, body="   ", author="x")
        raise AssertionError("empty body must raise")
    except ValueError:
        pass
