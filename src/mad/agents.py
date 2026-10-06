"""Role agents that read the blackboard and post tagged messages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from mad.blackboard import Blackboard
from mad.models import CLAIM_LIKE, Message, RoleSpec, SessionConfig, Tag, parse_tagged_body
from mad.runtime import LLMRuntime, MockRuntime

DEFAULT_INSTRUCTIONS = """\
You are participating in a multi-agent research discussion on a SHARED BLACKBOARD.

Hard protocol rules:
1. Every post MUST start with exactly one tag in square brackets.
   Allowed: [CLAIM] [EVIDENCE] [COUNTEREXAMPLE] [DEAD_END] [FIX] [QUESTION]
            [CONFIRMED] [UNCONTESTED] [PROPOSAL] [PLAN] [BASELINE_RESULT]
            [EXPERIMENT_RESULT] [LEAKAGE_SUSPECTED] [LITERATURE]
            [LEMMA_PROVED] [VERDICT] [NOTE]
2. [DEAD_END] and [COUNTEREXAMPLE] are permanent shared negative memory.
   Record failed approaches honestly. Never suggest deleting them.
3. Do not restate what is already on the board. Add new information only:
   a new claim, evidence, counterexample, fix, question, or a refined plan.
4. An unchallenged claim is NOT established. If you disagree, attack it with
   [COUNTEREXAMPLE]. If you cannot attack it, say [CONFIRMED] only after
   evidence exists — silence is not agreement.
5. Prefer one focused post over a long essay. No filler. No praise.
6. Ground claims in evidence when possible (numbers, cases, references).
7. When your post attacks, confirms, or repairs a SPECIFIC earlier claim,
   quote its id in the body, e.g. `breaks (id=abc123def456)`. Claim ids
   appear as id=... in the board listing below.

Reply with ONE post only, in this exact form:
[TAG] body...
"""

#: `id=abc123...` / `id: abc123...` / `Claim abc123...` references in a post body
_ID_REF_RE = re.compile(r"\b(?:id\s*[=:]\s*|Claim\s+)([0-9a-fA-F]{6,16})\b", re.IGNORECASE)

#: fenced ```json {...}``` block in a post body — the machine-readable payload
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_config(body: str) -> dict | None:
    """Last valid fenced JSON object in the body, e.g. a benchmark config."""
    out = None
    for m in _JSON_FENCE_RE.finditer(body):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out = data
    return out


@dataclass
class Agent:
    """A thin wrapper: identity + runtime + optional custom step function."""

    name: str
    runtime: LLMRuntime
    role: RoleSpec | None = None
    temperature: float = 0.7

    def observe(self, board: Blackboard, config: SessionConfig) -> str:
        """Build the observation block: task, negative memory, open claims, recent traffic."""
        since = max(0, self._last_round_seen - config.context_window_rounds) if hasattr(self, "_last_round_seen") else 0
        recent = board.messages(since_round=since)
        failed = board.failed_approaches()
        claims = board.open_claims()

        lines: list[str] = []
        lines.append("## Shared negative memory (NEVER repeat these roads)")
        if failed:
            for m in failed[-15:]:
                lines.append(f"- [{m.tag.value}] ({m.author}) {m.brief(160)}")
        else:
            lines.append("(empty)")

        lines.append("")
        lines.append("## Open claims awaiting challenge")
        if claims:
            shown = claims[:20]  # oldest first: these expire into UNCONTESTED first
            for m in shown:
                lines.append(f"- id={m.id} [{m.tag.value}] ({m.author}) {m.brief(160)}")
            if len(claims) > len(shown):
                lines.append(f"(+{len(claims) - len(shown)} older open claims not shown)")
        else:
            lines.append("(none)")

        lines.append("")
        lines.append("## Recent board traffic")
        if recent:
            for m in recent[-30:]:
                parent = f" -> {m.parent_id}" if m.parent_id else ""
                lines.append(
                    f"- r{m.round_no} id={m.id}{parent} [{m.tag.value}] ({m.author}) {m.brief(160)}"
                )
        else:
            lines.append("(empty)")

        if recent:
            self._last_round_seen = max(m.round_no for m in recent)
        return "\n".join(lines)

    def step(self, board: Blackboard, config: SessionConfig) -> Message | None:
        """Produce and post one message. Returns the message, or None if idle."""
        system = (
            self.role.render_prompt(config.task)
            if self.role
            else f"You are agent '{self.name}'.\n\nTask:\n{config.task}"
        )
        system = system + "\n\n" + DEFAULT_INSTRUCTIONS
        user = (
            f"## TASK (follow this)\n{config.task}\n\n"
            + self.observe(board, config)
        )
        # pass the tool policy only when the role declares one, so custom
        # runtime subclasses with narrow signatures keep working
        policy = self.role.tool_policy if self.role else None
        kwargs: dict[str, Any] = {"tool_policy": policy} if policy else {}
        reply = self.runtime.complete(
            system=system,
            user=user,
            temperature=self.temperature,
            **kwargs,
        )
        tag, body = parse_tagged_body(reply.text, default=Tag.NOTE)
        if config.reject_empty_posts and len(body.strip()) < 8:
            return None
        parent_id = self._infer_parent(board, tag, body)
        existing = board.messages()
        next_round = (existing[-1].round_no + 1) if existing else 0
        msg = Message(
            tag=tag,
            body=body,
            author=self.name,
            round_no=next_round,
            parent_id=parent_id,
        )
        config = _extract_json_config(body)
        if config is not None:
            msg.metadata["config"] = config
        if isinstance(getattr(reply, "provenance", None), dict):
            msg.metadata["provenance"] = reply.provenance
        return board.post(msg)

    @staticmethod
    def _infer_parent(board: Blackboard, tag: Tag, body: str) -> str | None:
        """Reply routing: an explicit claim id in the body wins; otherwise the
        oldest open claim (the one closest to being marked UNCONTESTED)."""
        if tag not in (Tag.COUNTEREXAMPLE, Tag.CONFIRMED, Tag.FIX, Tag.EVIDENCE):
            return None
        for m in _ID_REF_RE.finditer(body):
            target = board.get(m.group(1).lower())
            if target is not None and target.tag in CLAIM_LIKE:
                return target.id
        claims = board.open_claims()
        return claims[0].id if claims else None


class RoleAgent(Agent):
    """Agent bound to a RoleSpec (convenience subclass)."""

    def __init__(self, role: RoleSpec, runtime: LLMRuntime, *, name: str | None = None, temperature: float = 0.7):
        super().__init__(name=name or role.name, runtime=runtime, role=role, temperature=temperature)


# --------------------------------------------------------------------- presets

def explorer() -> RoleSpec:
    return RoleSpec(
        name="Explorer",
        mission="Propose new approaches, reductions, and concrete hypotheses. Stay falsifiable.",
        system_prompt=(
            "You are the Explorer. You generate new technical ideas for the research task. "
            "Each idea must be falsifiable and scoped. Do not sell; propose. "
            "Prefer [CLAIM] or [PROPOSAL]; use [QUESTION] when blocked on missing info."
        ),
        primary_tags=(Tag.CLAIM, Tag.PROPOSAL, Tag.QUESTION),
    )


def skeptic() -> RoleSpec:
    return RoleSpec(
        name="Skeptic",
        mission="Attack open claims. Find counterexamples, edge cases, complexity holes, leakage.",
        system_prompt=(
            "You are the Skeptic. Your ONLY job is to break other people's claims. "
            "If you cannot break a claim after a genuine attempt, reply [CONFIRMED] and say why. "
            "Silence is not agreement. Prefer [COUNTEREXAMPLE]; use [LEAKAGE_SUSPECTED] for evaluation bugs."
        ),
        primary_tags=(Tag.COUNTEREXAMPLE, Tag.LEAKAGE_SUSPECTED, Tag.DEAD_END),
        is_challenger=True,
    )


def modeler() -> RoleSpec:
    return RoleSpec(
        name="Modeler",
        mission="Turn surviving claims into implementable algorithms / models. Compare alternatives.",
        system_prompt=(
            "You are the Modeler. You turn claims into concrete methods: formulas, algorithms, "
            "model choices, complexity arguments. Prefer [PLAN] or [FIX]; cite tradeoffs. "
            "Do not repeat failed approaches listed in negative memory."
        ),
        primary_tags=(Tag.PLAN, Tag.FIX, Tag.CLAIM),
    )


def experimenter() -> RoleSpec:
    return RoleSpec(
        name="Experimenter",
        mission="Design and report runnable experiments. Emit EVIDENCE / BASELINE_RESULT / EXPERIMENT_RESULT.",
        system_prompt=(
            "You are the Experimenter. You specify minimal decisive experiments and report results "
            "with configuration, seeds, and metrics. Prefer [EVIDENCE] [BASELINE_RESULT] [EXPERIMENT_RESULT]. "
            "Flag data leakage explicitly with [LEAKAGE_SUSPECTED]."
        ),
        primary_tags=(Tag.EVIDENCE, Tag.BASELINE_RESULT, Tag.EXPERIMENT_RESULT),
    )


def reviewer() -> RoleSpec:
    return RoleSpec(
        name="Reviewer",
        mission="Check novelty against literature and enforce protocol discipline. Emit LITERATURE / NOTE / VERDICT hints.",
        system_prompt=(
            "You are the Reviewer. You check whether claims are already known, whether the "
            "protocol is being followed, and whether results are overstated. Prefer [LITERATURE] "
            "and [NOTE]. Call out overclaiming bluntly."
        ),
        primary_tags=(Tag.LITERATURE, Tag.NOTE),
        is_challenger=True,
    )


def benchmark_proposer() -> RoleSpec:
    return RoleSpec(
        name="Proposer",
        mission=(
            "Declare runnable experiment configs for the benchmark harness. "
            "The harness measures; you never self-report measured numbers."
        ),
        system_prompt=(
            "You are the Proposer in a declare-and-measure loop. You cannot run code, and the "
            "only measured numbers you know are the ones already on the board (VERDICT posts). "
            "Each round: read the board — measured results, negative memory, prior configs — then "
            "post ONE [EXPERIMENT_RESULT] declaring the NEXT config to measure, exactly like:\n"
            "[EXPERIMENT_RESULT] one-line rationale: what you changed vs prior configs and why\n"
            "```json\n"
            "{\"impl\": \"dijkstra_heap\", \"early_exit\": true, \"expected\": {\"speedup\": 1.4}}\n"
            "```\n"
            "`expected` is your honest falsifiable prediction; the harness compares it against "
            "reality and flags overclaiming as a COUNTEREXAMPLE. Never re-post a config that was "
            "already measured. Never state a measured number yourself. Use [COUNTEREXAMPLE] to "
            "attack configs that measured results already refute. When the harness VERDICT rejects "
            "your config, it states the exact contract (known impls, allowed keys) — obey it in "
            "your next post."
        ),
        primary_tags=(Tag.EXPERIMENT_RESULT, Tag.PROPOSAL),
    )


def literature_scout() -> RoleSpec:
    return RoleSpec(
        name="Scout",
        mission=(
            "Check the board's open claims against the actual literature via web search. "
            "Emit LITERATURE posts with concrete sources; cite known results to refute overclaims."
        ),
        system_prompt=(
            "You are the Scout. Your job is to check the board's open claims against the real "
            "world: is this already known? does a published method already dominate it? Use your "
            "web search / page fetch tools, actually read what you find, then post ONE "
            "[LITERATURE] message with concrete citations: title + URL + one line on relevance "
            "to the claim. If a claim is contradicted or already dominated by known results, "
            "aim the citation at it like an attack (reference the claim id). If you find nothing "
            "relevant, say so honestly as [NOTE] — an empty literature report must never be "
            "dressed up as a confirmation. Never claim you read a source you did not fetch."
        ),
        primary_tags=(Tag.LITERATURE, Tag.NOTE),
        is_challenger=True,
        tool_policy="search",
    )


def builder() -> RoleSpec:
    return RoleSpec(
        name="Builder",
        mission="Implement the assigned spec as working code inside the sandbox. Write, test, verify, report.",
        system_prompt=(
            "You are the Builder. Your job is to turn specifications into working code. "
            "You have Read/Write/Edit/Bash access **only inside your sandbox working directory**. "
            "Workflow: (1) read the spec carefully, (2) check what already exists in the sandbox, "
            "(3) implement the spec as clean, minimal code, (4) verify it runs (smoke test), "
            "(5) post ONE [NOTE] summarizing: what you built, file paths, how to run it, any issues. "
            "If the spec is unclear or impossible, say so in [NOTE] with your reasoning — don't guess. "
            "If you find existing code that already solves the problem, say so instead of rewriting."
        ),
        primary_tags=(Tag.NOTE,),
        tool_policy="code",
    )


DEFAULT_ROLES: tuple[RoleSpec, ...] = (explorer(), skeptic(), modeler(), experimenter(), reviewer())


def build_default_agents(runtime: LLMRuntime | None = None) -> list[Agent]:
    rt = runtime or MockRuntime()
    return [RoleAgent(role, rt) for role in DEFAULT_ROLES]
