"""Background session registry: run discussion threads the web board can poll.

Several sessions can run concurrently (bounded by MAD_MAX_SESSIONS, default 3).
Each session may persist its board to a SQLite path and be resumed later;
round numbering, restatement dedupe and best-score tracking carry over.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mad.agents import (
    RoleAgent,
    _extract_json_config,
    benchmark_proposer,
    builder,
    experimenter,
    explorer,
    literature_scout,
    modeler,
    reviewer,
    skeptic,
)
from mad.blackboard import Blackboard
from mad.cli_runtime import build_runtime
from mad.memory import announce, save_memory, seed_from_memory
from mad.models import Message, SessionConfig, Tag
from mad.orchestrator import Orchestrator, RoundResult, SessionReport
from mad.verifier import (
    CompositeVerifier,
    NullVerifier,
    ScriptVerifier,
    ThresholdVerifier,
    Verifier,
)

_ROLE_FACTORIES: dict[str, Callable[[], Any]] = {
    "explorer": explorer,
    "skeptic": skeptic,
    "modeler": modeler,
    "experimenter": experimenter,
    "reviewer": reviewer,
    "benchmark_proposer": benchmark_proposer,
    "proposer": benchmark_proposer,  # friendly alias (matches cli.py presets)
    "literature_scout": literature_scout,
    "builder": builder,
}


def _build_verifier(spec: dict[str, Any] | None) -> Verifier:
    if not spec:
        return NullVerifier()
    kind = spec.get("kind", "null")
    if kind == "null":
        return NullVerifier()
    if kind == "script":
        return ScriptVerifier(spec["command"], cwd=spec.get("cwd"), timeout=float(spec.get("timeout", 300)))
    if kind == "threshold":
        return ThresholdVerifier(
            spec["metric"],
            float(spec["threshold"]),
            higher_is_better=spec.get("higher_is_better", True),
        )
    if kind == "composite":
        return CompositeVerifier([_build_verifier(s) for s in spec.get("verifiers", [])])
    return NullVerifier()


def _sanitize_config(cfg: Any) -> Any:
    """Deep-copy a config with api_key-style values masked — never leak keys to the UI."""
    if isinstance(cfg, dict):
        out: dict[str, Any] = {}
        for k, v in cfg.items():
            if "api_key" in str(k).lower() and isinstance(v, str) and v:
                out[k] = "***"
            else:
                out[k] = _sanitize_config(v)
        return out
    if isinstance(cfg, list):
        return [_sanitize_config(x) for x in cfg]
    return cfg


@dataclass
class SessionHandle:
    id: str
    task: str
    config: dict[str, Any]
    status: str = "idle"  # idle | running | done | error | stopped
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    report: dict[str, Any] | None = None
    progress: str = ""
    board_path: str | None = None
    resumed: bool = False

    def to_dict(self, board_dump: list[dict] | None = None) -> dict[str, Any]:
        d = {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "progress": self.progress,
            "board_path": self.board_path,
            "resumed": self.resumed,
            # deep-masked: role_runtimes may carry nested api keys
            "config": _sanitize_config(self.config),
            "report": self.report,
        }
        if board_dump is not None:
            d["board"] = board_dump
        return d


@dataclass
class _SessionState:
    handle: SessionHandle
    board: Blackboard
    stop_flag: threading.Event
    thread: threading.Thread | None = None


class SessionManager:
    """Registry of discussion sessions; several can run concurrently."""

    def __init__(self, *, max_concurrent: int | None = None, max_finished: int = 20) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionState] = {}
        self._order: list[str] = []
        self._max_concurrent = max_concurrent or int(os.environ.get("MAD_MAX_SESSIONS", "3"))
        self._max_finished = max_finished

    # ------------------------------------------------------------------ query

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            st = self._get(session_id)
            if st is None:
                return {"status": "idle", "board": [], "session": None}
            dump = st.board.dump()
            return {
                "status": st.handle.status,
                "board": dump,
                "session": st.handle.to_dict(dump),
            }

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for sid in reversed(self._order):
                st = self._sessions[sid]
                out.append(
                    {
                        "id": st.handle.id,
                        "task": st.handle.task,
                        "status": st.handle.status,
                        "created_at": st.handle.created_at,
                        "board_path": st.handle.board_path,
                        "resumed": st.handle.resumed,
                    }
                )
            return out

    def is_running(self, session_id: str | None = None) -> bool:
        with self._lock:
            st = self._get(session_id)
            return bool(st and st.handle.status == "running")

    def _get(self, session_id: str | None) -> _SessionState | None:
        if session_id:
            return self._sessions.get(session_id)
        for sid in reversed(self._order):
            return self._sessions[sid]
        return None

    # ----------------------------------------------------------------- control

    def start(self, config: dict[str, Any]) -> SessionHandle:
        with self._lock:
            running = sum(1 for st in self._sessions.values() if st.handle.status == "running")
            if running >= self._max_concurrent:
                raise RuntimeError(
                    f"{running} sessions already running (max {self._max_concurrent}); stop one first"
                )
            task = (config.get("task") or "").strip()
            if not task:
                raise ValueError("task is required")
            board_path = config.get("board") or config.get("board_path") or None
            resume = bool(config.get("resume"))
            board = Blackboard(board_path) if board_path else Blackboard()
            if board_path:
                existing = len(board)
                if existing > 0 and not resume:
                    board.close()
                    raise ValueError(
                        f"board {board_path} already holds {existing} messages; "
                        f"set resume=true to continue it or pick a new board path"
                    )
            handle = SessionHandle(
                id=uuid.uuid4().hex[:10],
                task=task,
                config=dict(config),
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
                board_path=str(board_path) if board_path else None,
                resumed=resume and len(board) > 0,
            )
            seed = config.get("seed_board") or config.get("seed_messages") or []
            if isinstance(seed, str) and seed:
                seed_path = Path(seed)
                if seed_path.exists():
                    seed = json.loads(seed_path.read_text(encoding="utf-8"))
            for rec in seed or []:
                try:
                    board.post(Message.from_dict(rec))
                except Exception:
                    continue
            state = _SessionState(handle=handle, board=board, stop_flag=threading.Event())
            thread = threading.Thread(
                target=self._run,
                args=(state, config),
                name=f"mad-session-{handle.id}",
                daemon=True,
            )
            state.thread = thread
            self._sessions[handle.id] = state
            self._order.append(handle.id)
            self._trim_finished()
            thread.start()
            return handle

    def stop(self, session_id: str | None = None) -> bool:
        with self._lock:
            st = self._get(session_id)
            if not st or st.handle.status != "running":
                return False
            st.stop_flag.set()
            st.handle.progress = "stopping…"
            return True

    def inject(self, session_id: str | None, body: str, *, tag: str = "NOTE",
               author: str = "human") -> dict[str, Any]:
        """Human-in-the-loop: post a message onto a session's blackboard mid-flight.

        The next agent turn re-observes the board and sees it immediately -- no
        pause required. Works on running and stopped sessions alike (a stopped
        session's board keeps the message for its next resume).
        """
        with self._lock:
            st = self._get(session_id)
            if st is None:
                return {"ok": False, "error": "no such session"}
            text = (body or "").strip()
            if not text:
                return {"ok": False, "error": "empty body"}
            try:
                tag_obj = Tag.parse(tag)
            except Exception:
                tag_obj = Tag.NOTE
            try:
                cur_round = int(str(st.handle.progress).rsplit(" ", 1)[-1])
            except Exception:
                cur_round = 0
            msg = st.board.post(
                Message(tag=tag_obj, body=text, author=author, round_no=cur_round))
            return {"ok": True, "id": msg.id, "round_no": msg.round_no}

    def _trim_finished(self) -> None:
        finished = [sid for sid in self._order if self._sessions[sid].handle.status != "running"]
        while len(finished) > self._max_finished:
            oldest = finished.pop(0)
            st = self._sessions.pop(oldest, None)
            self._order.remove(oldest)
            if st is not None:
                st.board.close()

    # ------------------------------------------------------------------- worker

    def _run(self, state: _SessionState, config: dict[str, Any]) -> None:
        handle = state.handle
        try:
            runtime_cfg = config.get("runtime") or {"mode": "local_cli", "agent": "kimi"}
            role_runtimes = config.get("role_runtimes") or {}
            role_names = config.get("roles") or [
                "explorer", "skeptic", "modeler", "experimenter", "reviewer",
            ]
            agents = []
            for name in role_names:
                factory = _ROLE_FACTORIES.get(str(name).lower())
                if factory is None:
                    raise ValueError(f"unknown role: {name}")
                # per-role runtime overrides enable cross-model adversarial review
                rt = build_runtime(role_runtimes.get(str(name).lower()) or runtime_cfg)
                rt.stop_event = state.stop_flag
                agents.append(RoleAgent(factory(), rt, name=str(name).capitalize()))
            if not agents:
                raise ValueError("need at least one role")

            session_cfg = SessionConfig(
                task=handle.task,
                max_rounds=int(config.get("max_rounds", 8)),
                challenge_grace_rounds=int(config.get("challenge_grace_rounds", 2)),
                stall_rounds=int(config.get("stall_rounds", 3)),
                max_messages=int(config.get("max_messages", 120)),
                require_verifier_gate=bool(config.get("require_verifier_gate", True)),
                context_window_rounds=int(config.get("context_window_rounds", 8)),
                no_improvement_rounds=int(config.get("no_improvement_rounds", 0) or 0),
                improvement_delta=float(config.get("improvement_delta", 0.0) or 0.0),
                target_score=(
                    float(config["target_score"])
                    if config.get("target_score") is not None
                    else None
                ),
            )
            verifier = _build_verifier(config.get("verifier"))
            board = state.board

            # cross-campaign memory: seed negatives from previous campaigns
            memory_path = config.get("memory")
            if memory_path:
                try:
                    announce(board, seed_from_memory(memory_path, board))
                except Exception:
                    pass

            # external experiment results can be dropped here mid-session
            inbox_path = config.get("inbox") or config.get("inbox_path")
            # serial whitelist-command queue (Route B: expensive experiments)
            train_queue_path = config.get("train_queue")
            stop_flag = state.stop_flag

            class ProgressOrchestrator(Orchestrator):
                def _run_round(self, round_no: int):
                    if stop_flag.is_set():
                        rr = RoundResult(round_no=round_no, stop_reason="stopped_by_user")
                        return rr
                    if inbox_path:
                        self._drain_inbox(inbox_path, round_no)
                    if train_queue_path:
                        self._drain_train_queue(train_queue_path, round_no)
                    handle.progress = f"round {round_no}"
                    return super()._run_round(round_no)

                def _drain_inbox(self, path: str, round_no: int) -> None:
                    p = Path(path)
                    if not p.exists():
                        return
                    seen = set(m.id for m in self.board.messages())
                    try:
                        lines = p.read_text(encoding="utf-8").splitlines()
                    except Exception:
                        return
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        mid = rec.get("id")
                        if mid and mid in seen:
                            continue
                        try:
                            rec = dict(rec)
                            rec["round_no"] = round_no
                            msg = Message.from_dict(rec)
                            # 操作者注入的申明必须与 agent 发帖同权：agents.py 的
                            # step() 会在发帖前跑 _extract_json_config，排水路径
                            # 此前不做 → 操作者的申明对 verifier 永不可见
                            if msg.metadata.get("config") is None:
                                cfg = _extract_json_config(msg.body)
                                if cfg is not None:
                                    msg.metadata["config"] = cfg
                            self.board.post(msg)
                        except Exception:
                            continue

                def _drain_train_queue(self, path: str, round_no: int) -> None:
                    """Serial whitelist-command executor (Route B).

                    One queued job at a time. On completion the job's embedded
                    score command runs and the verdict lands in the inbox, so
                    the next round sees the measured result. The executor only
                    runs command lists written by trusted local tooling — the
                    harness validates recipes against its whitelist before
                    enqueueing.
                    """
                    q = Path(path)
                    # 统一状态文件：与 harness 的配额/去重共用 train_state.json，
                    # 否则执行器的完成记录永远进不了配额账本
                    state_p = q.parent / "train_state.json"
                    try:
                        state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
                    except Exception:
                        state = {}
                    running = state.get("running")
                    if running:
                        started = str(running.get("started_at", ""))
                        try:
                            age_h = (time.time() - time.mktime(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S"))) / 3600
                        except Exception:
                            age_h = 0.0
                        if age_h < 6.0:
                            return
                        # 服务中断留下的死锁：记为失败、清锁，下一轮重新派发
                        state.setdefault("failed", []).append({
                            "id": running.get("id"), "tag": running.get("tag"),
                            "status": "failed",
                            "note": "executor died mid-training (stale lock cleared)",
                            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        })
                        state.pop("running", None)
                        try:
                            state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                        except Exception:
                            return
                        return
                    job = None
                    try:
                        done_ids = {j.get("id") for j in state.get("done", [])} | {
                            j.get("id") for j in state.get("failed", [])
                        }
                        for line in q.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            rec = json.loads(line)
                            if rec.get("status") == "pending" and rec.get("id") not in done_ids:
                                job = rec
                                break
                    except Exception:
                        return
                    if job is None or not job.get("command"):
                        return

                    state["running"] = {
                        "id": job["id"], "tag": job.get("tag"),
                        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                    try:
                        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                    except Exception:
                        return
                    threading.Thread(
                        target=self._run_train_job, args=(job, q, state_p, inbox_path),
                        name=f"mad-train-{job.get('tag', 'job')}", daemon=True,
                    ).start()

                def _run_train_job(self, job: dict, q: Path, state_p: Path, inbox_path: str | None) -> None:
                    t0 = time.time()
                    log_path = q.parent / f"train_{job.get('tag', 'job')}.log"
                    rc = -1
                    try:
                        with log_path.open("w", encoding="utf-8", errors="replace") as f:
                            proc = subprocess.Popen(
                                job["command"], stdout=f, stderr=subprocess.STDOUT,
                                text=True, cwd=job.get("cwd") or str(q.parent),
                            )
                            try:
                                proc.wait(timeout=4 * 3600)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                f.write("\n[executor] 超过 4 小时，已强杀\n")
                            rc = proc.returncode if proc.returncode is not None else -1
                    except Exception as exc:
                        try:
                            log_path.write_text(f"[executor] {exc}", encoding="utf-8")
                        except Exception:
                            pass

                    state = {}
                    try:
                        state = json.loads(state_p.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                    state.pop("running", None)
                    bucket = "done" if rc == 0 else "failed"
                    state.setdefault(bucket, []).append({
                        "id": job.get("id"), "recipe": job.get("recipe"),
                        "seed": job.get("seed"), "tag": job.get("tag"), "status": bucket,
                        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "duration_min": round((time.time() - t0) / 60, 1),
                    })
                    try:
                        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                    except Exception:
                        pass

                    if not inbox_path:
                        return
                    try:
                        score_config = json.loads(job.get("score_stdin", "{}"))
                    except Exception:
                        score_config = {}
                    record = None
                    if rc == 0 and job.get("score_command"):
                        try:
                            sproc = subprocess.run(
                                job["score_command"],
                                input=job.get("score_stdin", ""),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=600,
                                cwd=job.get("cwd") or str(q.parent),
                            )
                            verdict = json.loads(sproc.stdout.strip().splitlines()[-1])
                            measured = verdict.get("measured") or {}
                            probs_files = ", ".join(measured.get("probs") or []) or "见 measured"
                            record = {
                                "id": f"{job.get('id')}_score",
                                "tag": "EXPERIMENT_RESULT",
                                "author": "train_executor",
                                # inbox 回灌走 Message.from_dict，created_at 必填
                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "body": (
                                    f"训练 {job.get('recipe')} (seed={job.get('seed')}) 完成，"
                                    f"耗时 {round((time.time() - t0) / 60, 1)} 分钟。产物 {probs_files} "
                                    f"已入白名单可申明融合。自动实测：{verdict.get('summary')}"
                                ),
                                "metadata": {
                                    "measured": verdict.get("measured") or {},
                                    "config": score_config,
                                    "trained": {"recipe": job.get("recipe"), "seed": job.get("seed"),
                                                "tag": job.get("tag")},
                                },
                            }
                        except Exception as exc:
                            record = {
                                "id": f"{job.get('id')}_note",
                                "tag": "NOTE",
                                "author": "train_executor",
                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "body": f"训练 {job.get('recipe')} 完成（exit 0）但自动评分失败: {exc}",
                            }
                    elif rc != 0:
                        tail = ""
                        try:
                            tail = log_path.read_text(encoding="utf-8", errors="replace")[-400:]
                        except Exception:
                            pass
                        record = {
                            "id": f"{job.get('id')}_fail",
                            "tag": "NOTE",
                            "author": "train_executor",
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "body": f"训练 {job.get('recipe')} 失败 (exit {rc})。日志尾部: {tail}",
                        }
                    if record is not None:
                        try:
                            with Path(inbox_path).open("a", encoding="utf-8") as f:
                                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        except Exception:
                            pass

            orch = ProgressOrchestrator(board, agents, session_cfg, verifier)
            report = orch.run(resume=handle.resumed)
            if memory_path:
                try:
                    save_memory(memory_path, board)
                except Exception:
                    pass
            handle.report = report.to_dict()
            handle.stop_reason = report.stop_reason
            handle.status = "stopped" if report.stop_reason == "stopped_by_user" else "done"
        except Exception as exc:
            handle.status = "error"
            try:
                handle.error = f"{exc}\n{traceback.format_exc()[-800:]}"
            except Exception:
                handle.error = "session failed (unprintable error)"
        finally:
            handle.finished_at = datetime.now(timezone.utc).isoformat()
            if not handle.progress:
                handle.progress = handle.status


# process-wide registry used by the web server
MANAGER = SessionManager()
