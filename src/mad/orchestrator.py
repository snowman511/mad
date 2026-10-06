"""Orchestrator: rounds, discipline enforcement, gates, termination."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from mad.agents import Agent
from mad.blackboard import Blackboard
from mad.models import CLAIM_LIKE, Message, SessionConfig, Tag
from mad.runtime import RuntimeStopped
from mad.verifier import NullVerifier, VerificationResult, Verifier


@dataclass
class RoundResult:
    round_no: int
    posts: list[Message] = field(default_factory=list)
    uncontested: list[str] = field(default_factory=list)
    verified: VerificationResult | None = None
    stop_reason: str | None = None


@dataclass
class SessionReport:
    rounds: list[RoundResult] = field(default_factory=list)
    stop_reason: str = ""
    verdict: VerificationResult | None = None
    best_messages: list[Message] = field(default_factory=list)
    open_questions: list[Message] = field(default_factory=list)
    dead_ends: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "best_messages": [m.to_dict() for m in self.best_messages],
            "open_questions": [m.to_dict() for m in self.open_questions],
            "dead_ends": [m.to_dict() for m in self.dead_ends],
            "rounds": [
                {
                    "round_no": r.round_no,
                    "posts": [m.to_dict() for m in r.posts],
                    "uncontested": r.uncontested,
                    "verified": r.verified.to_dict() if r.verified else None,
                    "stop_reason": r.stop_reason,
                }
                for r in self.rounds
            ],
        }


class Orchestrator:
    """Runs the discussion loop.

    Discipline (enforced in code, not only in prompts):
    - every agent step yields at most one tagged post
    - open claims past `challenge_grace_rounds` without attack become UNCONTESTED
      and cannot enter the final best set
    - EMPTY / restating posts are rejected when reject_empty_posts is on
    - merge gate is the Verifier, never a vote
    - termination: gate passed | stall | max_rounds | max_messages
    """

    def __init__(
        self,
        board: Blackboard,
        agents: Sequence[Agent],
        config: SessionConfig,
        verifier: Verifier | None = None,
    ) -> None:
        if not agents:
            raise ValueError("need at least one agent")
        self.board = board
        self.agents = list(agents)
        self.config = config
        self.verifier = verifier or NullVerifier()
        self._recent_bodies: dict[str, None] = {}
        self._last_claim_round = -1
        self._best_score: float | None = None
        self._last_improvement_round = 0

    # ------------------------------------------------------------------ public

    def run(self, *, resume: bool = False) -> SessionReport:
        report = SessionReport()
        start_round = 1
        if resume:
            start_round = self._resume_state()
        self._boot(resume=resume, start_round=start_round)
        for round_no in range(start_round, start_round + self.config.max_rounds):
            rr = self._run_round(round_no)
            report.rounds.append(rr)
            if rr.stop_reason:
                report.stop_reason = rr.stop_reason
                break
        else:
            report.stop_reason = "max_rounds"

        self._finalize(report)
        return report

    def _resume_state(self) -> int:
        """Rebuild trackers from a persisted board and return the next round no.

        Restored: dedupe keys (restatement rejection works across campaigns),
        last claim round (stall continues counting — honest, if strict), and
        best-so-far tracking. Route-A harnesses stamp the external anchor into
        every verdict as details.measured.best_before — the board's own scores
        may all be below the anchor, so the anchor is the authoritative best.
        The stagnation timer counts from the last verdict that flagged
        measured.new_best, or (if none) from the first measured round.
        """
        msgs = self.board.messages()
        if not msgs:
            return 1
        any_new_best = False
        first_measured_round = None
        verdicts = [m for m in msgs if m.tag == Tag.VERDICT]
        for m in msgs:
            key = " ".join(m.body.lower().split())[:240]
            self._recent_bodies[key] = None
            if m.tag == Tag.CLAIM and m.round_no > self._last_claim_round:
                self._last_claim_round = m.round_no
        # pass 1: the external anchor carried by Route-A harnesses is the
        # authoritative best — the board's own scores may all sit below it
        for m in verdicts:
            measured = (m.metadata.get("details") or {}).get("measured") or {}
            if m.round_no >= 1 and first_measured_round is None:
                first_measured_round = m.round_no
            anchor = measured.get("best_before")
            if isinstance(anchor, (int, float)) and (
                self._best_score is None or anchor > self._best_score
            ):
                self._best_score = float(anchor)
            if measured.get("new_best"):
                self._last_improvement_round = m.round_no
                any_new_best = True
        # pass 2: board scores above the seeded anchor are genuine improvements
        for m in verdicts:
            score = m.metadata.get("score")
            if isinstance(score, (int, float)) and (
                self._best_score is None or score > self._best_score
            ):
                self._best_score = float(score)
                self._last_improvement_round = m.round_no
                any_new_best = True
        if not any_new_best and first_measured_round is not None:
            self._last_improvement_round = first_measured_round
        return max(m.round_no for m in msgs) + 1

    def _boot(self, *, resume: bool = False, start_round: int = 1) -> None:
        if resume and start_round > 1:
            self.board.post(
                Message(
                    tag=Tag.SYSTEM,
                    body=(
                        f"会话续跑：从第 {start_round} 轮继续，"
                        f"黑板上有 {len(self.board)} 条历史消息。"
                    ),
                    author="orchestrator",
                    round_no=0,
                    status="open",
                )
            )
            return
        self.board.post(
            Message(
                tag=Tag.SYSTEM,
                body=(
                    f"会话开始。\n任务: {self.config.task}\n"
                    f"角色: {', '.join(a.name for a in self.agents)}\n"
                    f"挑战宽限: {self.config.challenge_grace_rounds} 轮。"
                    f"DEAD_END / COUNTEREXAMPLE（死路 / 反例）不可删除。"
                ),
                author="orchestrator",
                round_no=0,
                status="open",
            )
        )

    def _run_round(self, round_no: int) -> RoundResult:
        rr = RoundResult(round_no=round_no)
        for agent in self.agents:
            if len(self.board) >= self.config.max_messages:
                rr.stop_reason = "max_messages"
                return rr
            try:
                msg = agent.step(self.board, self.config)
            except RuntimeStopped:
                rr.stop_reason = "stopped_by_user"
                return rr
            except Exception as exc:  # keep the session alive; record the failure
                msg = self.board.post(
                    Message(
                        tag=Tag.NOTE,
                        body=f"agent {agent.name} crashed: {exc}",
                        author="orchestrator",
                        round_no=round_no,
                    )
                )
            if msg is None:
                continue
            # enforce round stamp (agents may have guessed 0)
            if msg.round_no != round_no:
                self.board._conn.execute(
                    "UPDATE messages SET round_no = ? WHERE id = ?",
                    (round_no, msg.id),
                )
                self.board._conn.commit()
                msg.round_no = round_no

            if self.config.reject_empty_posts and self._is_restatement(msg):
                try:
                    self.board.delete(msg.id)
                except Exception:
                    self.board.update_status(msg.id, "rejected")
                continue

            rr.posts.append(msg)
            self._apply_claim_status(msg)
            if msg.tag == Tag.CLAIM:
                self._last_claim_round = round_no

        # discipline: uncontested claims
        stale = self.board.claims_without_challenge(
            self.config.challenge_grace_rounds, round_no
        )
        for m in stale:
            self.board.update_status(m.id, "uncontested")
            notice = self.board.post(
                Message(
                    tag=Tag.UNCONTESTED,
                    body=(
                        f"Claim {m.id} received no challenge/FIX within "
                        f"{self.config.challenge_grace_rounds} rounds and is barred "
                        f"from the final best set.\nOriginal: {m.brief(200)}"
                    ),
                    author="orchestrator",
                    round_no=round_no,
                    parent_id=m.id,
                )
            )
            rr.uncontested.append(m.id)
            rr.posts.append(notice)

        # termination checks
        if self.config.require_verifier_gate and self._maybe_verify(round_no, rr):
            return rr
        if (
            self._last_claim_round >= 0
            and round_no - self._last_claim_round >= self.config.stall_rounds
        ):
            rr.stop_reason = "stall_no_new_claim"
        # optimization stagnation: many rounds without a new_best
        if (
            self.config.no_improvement_rounds > 0
            and round_no - self._last_improvement_round >= self.config.no_improvement_rounds
        ):
            rr.stop_reason = (
                f"stagnation_no_improvement_{self.config.no_improvement_rounds}_rounds"
            )
        return rr

    def _is_restatement(self, msg: Message) -> bool:
        key = " ".join(msg.body.lower().split())[:240]
        if key in self._recent_bodies:
            return True
        self._recent_bodies[key] = None
        if len(self._recent_bodies) > 400:
            # dict preserves insertion order, so this deterministically drops the oldest
            for k in list(self._recent_bodies)[:200]:
                del self._recent_bodies[k]
        return False

    def _apply_claim_status(self, msg: Message) -> None:
        """Close a claim as 'confirmed' when someone else posts CONFIRMED on it.

        Self-confirmation does not count. Challenged claims stay open; the
        COUNTEREXAMPLE child itself already keeps them out of the UNCONTESTED
        sweep via claims_without_challenge().
        """
        if not msg.parent_id or msg.tag is not Tag.CONFIRMED:
            return
        parent = self.board.get(msg.parent_id)
        if parent is None or parent.tag not in CLAIM_LIKE:
            return
        if msg.author == parent.author or parent.status != "open":
            return
        self.board.update_status(parent.id, "confirmed")

    def _maybe_verify(self, round_no: int, rr: RoundResult) -> bool:
        """Run the gate when a candidate is ready.

        Optimization mode: a valid score updates the best-so-far tracker and
        continues; the session only stops on target hit or stagnation.
        Classic mode: first `passed=True` stops the session.
        """
        candidate = self._candidate_context()
        if candidate is None and not self._has_candidate_signal():
            return False
        try:
            result = self.verifier.verify(candidate or {})
        except Exception as exc:
            result = VerificationResult(
                passed=False,
                summary=f"verifier crashed: {exc}",
                details={"error": str(exc)},
            )
        rr.verified = result
        verdict = self.board.post(
            Message(
                tag=Tag.VERDICT,
                body=result.summary or ("通过" if result.passed else "未通过"),
                author="verifier",
                round_no=round_no,
                metadata=result.to_dict(),
            )
        )
        rr.posts.append(verdict)

        details = result.details or {}
        score = result.score if result.score is not None else details.get("best")
        new_best = bool(details.get("new_best"))
        if isinstance(score, (int, float)):
            prev_best = self._best_score
            if prev_best is None or score > prev_best:
                self._best_score = float(score)
            # improvement is judged here, not trusted from the verifier: a
            # plain ThresholdVerifier never emits details["new_best"]
            if new_best or prev_best is None or score > prev_best + self.config.improvement_delta:
                new_best = True
                self._last_improvement_round = round_no

        # Route A: the harness measured reality — write it back onto the
        # candidate so later rounds score against measured, not claimed, numbers
        cand = candidate.get("candidate") if isinstance(candidate, dict) else None
        cand_id = cand.get("id") if isinstance(cand, dict) else None
        if isinstance(cand_id, str):
            measured = details.get("measured")
            if isinstance(measured, dict):
                try:
                    self.board.append_metadata(
                        cand_id,
                        measured=measured,
                        measured_score=score if isinstance(score, (int, float)) else None,
                    )
                except Exception:
                    pass
            # overclaiming leaves permanent negative memory
            if details.get("mismatch"):
                try:
                    self.board.post(
                        Message(
                            tag=Tag.COUNTEREXAMPLE,
                            body=(
                                f"Verifier mismatch on candidate {cand_id}: "
                                f"{json.dumps(details['mismatch'], ensure_ascii=False)}. "
                                f"Claimed numbers must match measured reality."
                            ),
                            author="verifier",
                            round_no=round_no,
                            parent_id=cand_id,
                        )
                    )
                except Exception:
                    pass

        # target hit → stop
        if self.config.target_score is not None and self._best_score is not None:
            if self._best_score >= self.config.target_score:
                rr.stop_reason = "target_score_reached"
                return True

        # classic gate: hard pass ends the session (not used in open-ended F1 mode)
        if result.passed and self.config.no_improvement_rounds <= 0:
            if new_best or details.get("reason") != "no_score":
                rr.stop_reason = "verifier_gate_passed"
                return True
        return False

    def _has_candidate_signal(self) -> bool:
        tags = (Tag.EXPERIMENT_RESULT, Tag.BASELINE_RESULT, Tag.LEMMA_PROVED, Tag.CLAIM)
        for t in tags:
            if self.board.messages(tag=t):
                return True
        return False

    def _candidate_context(self) -> dict[str, Any] | None:
        # Only this-session measurements are candidates. Seed-board baselines
        # (round 0) are context, not new results.
        experiments = [m for m in self.board.messages(tag=Tag.EXPERIMENT_RESULT) if m.round_no >= 1]
        baselines = [m for m in self.board.messages(tag=Tag.BASELINE_RESULT) if m.round_no >= 1]
        lemmas = [m for m in self.board.messages(tag=Tag.LEMMA_PROVED) if m.round_no >= 1]
        claims = [m for m in self.board.open_claims() if m.status == "open"]
        if not (experiments or baselines or lemmas):
            return None
        # score the most recent measurement, not the first: later experiments
        # must be able to improve on earlier ones for stagnation tracking to work
        measurements = [
            m for m in self.board.messages()
            if m.round_no >= 1 and m.tag in (Tag.EXPERIMENT_RESULT, Tag.BASELINE_RESULT)
        ]
        best = None
        for m in measurements:
            best = {
                "id": m.id,
                "author": m.author,
                "metrics": m.metadata.get("measured") or m.metadata.get("metrics") or {},
                "config": m.metadata.get("config") or m.body,
                "body": m.body,
                "seed": m.metadata.get("seed", m.metadata.get("seeds")),
                "eval_protocol": m.metadata.get("eval_protocol", "perfile"),
            }
        return {
            "task": self.config.task,
            "candidate": best or {
                "id": lemmas[-1].id if lemmas else None,
                "body": (lemmas[-1].body if lemmas else (claims[-1].body if claims else "")),
                "metrics": {},
            },
            "experiments": [m.to_dict() for m in experiments[-10:]],
            "baselines": [m.to_dict() for m in baselines[-10:]],
            "open_claims": [m.to_dict() for m in claims[-10:]],
            "dead_ends": [m.to_dict() for m in self.board.failed_approaches()[-20:]],
        }

    def _finalize(self, report: SessionReport) -> None:
        # best = confirmed / experiment-backed claims; never uncontested
        best: list[Message] = []
        for m in self.board.messages():
            if m.status == "uncontested":
                continue
            if m.tag in (Tag.EXPERIMENT_RESULT, Tag.BASELINE_RESULT, Tag.LEMMA_PROVED, Tag.CONFIRMED):
                best.append(m)
            elif m.tag == Tag.CLAIM and m.status in ("confirmed", "open"):
                # only keep claims that have an evidence/confirm child
                kids = self.board.messages(parent_id=m.id)
                if any(k.tag in (Tag.EVIDENCE, Tag.CONFIRMED, Tag.FIX) for k in kids):
                    best.append(m)
        report.best_messages = best
        report.open_questions = self.board.messages(tag=Tag.QUESTION)
        report.dead_ends = self.board.failed_approaches()
        if report.verdict is None:
            last_v = self.board.latest_verdict()
            if last_v is not None and isinstance(last_v.metadata, dict) and "passed" in last_v.metadata:
                report.verdict = VerificationResult(
                    passed=bool(last_v.metadata.get("passed")),
                    score=last_v.metadata.get("score"),
                    summary=str(last_v.metadata.get("summary", "")),
                    details=last_v.metadata,
                )
