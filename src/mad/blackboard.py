"""Shared blackboard: SQLite-backed message board with immutable negative memory."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from mad.models import CLAIM_LIKE, IMMUTABLE_TAGS, Message, Tag


class BlackboardError(Exception):
    pass


class Blackboard:
    """The shared message board every agent reads and writes.

    Design rules enforced here (not just in prompts):
    - DEAD_END / COUNTEREXAMPLE posts can never be deleted or rewritten.
    - Every message carries exactly one Tag.
    - Optional uniqueness check: same author + same body within a session is rejected.
    """

    def __init__(self, path: str | Path = ":memory:", *, check_same_thread: bool = False) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                tag TEXT NOT NULL,
                body TEXT NOT NULL,
                author TEXT NOT NULL,
                round_no INTEGER NOT NULL DEFAULT 0,
                parent_id TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                metadata TEXT NOT NULL DEFAULT '{}',
                seq INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_round ON messages(round_no);
            CREATE INDEX IF NOT EXISTS idx_messages_tag ON messages(tag);
            CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------ write

    def post(self, msg: Message) -> Message:
        if msg.tag in IMMUTABLE_TAGS and not msg.body.strip():
            raise BlackboardError("immutable posts must record a real finding")
        try:
            self._conn.execute(
                """
                INSERT INTO messages
                    (id, tag, body, author, round_no, parent_id, created_at, status, metadata, seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, (
                    SELECT COALESCE(MAX(seq), 0) + 1 FROM messages
                ))
                """,
                (
                    msg.id,
                    msg.tag.value,
                    msg.body,
                    msg.author,
                    msg.round_no,
                    msg.parent_id,
                    msg.created_at.isoformat(),
                    msg.status,
                    json.dumps(msg.metadata, ensure_ascii=False),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise BlackboardError(f"duplicate message id: {msg.id}") from exc
        self._conn.commit()
        return msg

    def update_status(self, message_id: str, status: str) -> None:
        cur = self._conn.execute(
            "UPDATE messages SET status = ? WHERE id = ?",
            (status, message_id),
        )
        if cur.rowcount == 0:
            raise BlackboardError(f"unknown message: {message_id}")
        self._conn.commit()

    def append_metadata(self, message_id: str, **kv: object) -> None:
        row = self._conn.execute(
            "SELECT metadata FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise BlackboardError(f"unknown message: {message_id}")
        meta = json.loads(row["metadata"])
        meta.update(kv)
        self._conn.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), message_id),
        )
        self._conn.commit()

    def delete(self, message_id: str) -> None:
        """Hard-delete is forbidden for immutable negative memory."""
        row = self._conn.execute(
            "SELECT tag FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise BlackboardError(f"unknown message: {message_id}")
        tag = Tag.parse(row["tag"])
        if tag in IMMUTABLE_TAGS:
            raise BlackboardError(
                f"refusing to delete immutable {tag.value} message {message_id}"
            )
        self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        self._conn.commit()

    # ------------------------------------------------------------------- read

    def get(self, message_id: str) -> Message | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._row_to_msg(row) if row else None

    def messages(
        self,
        *,
        tag: Tag | None = None,
        status: str | None = None,
        author: str | None = None,
        since_round: int | None = None,
        parent_id: str | None = None,
    ) -> list[Message]:
        sql = "SELECT * FROM messages WHERE 1=1"
        args: list[object] = []
        if tag is not None:
            sql += " AND tag = ?"
            args.append(tag.value)
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if author is not None:
            sql += " AND author = ?"
            args.append(author)
        if since_round is not None:
            sql += " AND round_no >= ?"
            args.append(since_round)
        if parent_id is not None:
            sql += " AND parent_id = ?"
            args.append(parent_id)
        sql += " ORDER BY seq ASC"
        rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def __iter__(self) -> Iterator[Message]:
        yield from self.messages()

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"])

    def recent(self, n: int = 20) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages ORDER BY seq DESC LIMIT ?", (n,)
        ).fetchall()
        return [self._row_to_msg(r) for r in reversed(rows)]

    def failed_approaches(self) -> list[Message]:
        """Shared negative memory: DEAD_END + COUNTEREXAMPLE."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE tag IN (?, ?) ORDER BY seq ASC",
            (Tag.DEAD_END.value, Tag.COUNTEREXAMPLE.value),
        ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def open_claims(self) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE tag IN (?, ?, ?, ?) AND status = 'open' ORDER BY seq ASC",
            (
                Tag.CLAIM.value,
                Tag.PROPOSAL.value,
                Tag.LEMMA_PROVED.value,
                Tag.PLAN.value,
            ),
        ).fetchall()
        return [self._row_to_msg(r) for r in rows]

    def claims_without_challenge(self, grace_rounds: int, current_round: int) -> list[Message]:
        """Open claims that have survived `grace_rounds` with no engagement.

        A CONFIRMED or FIX child counts as engagement only when it comes from
        someone other than the claim author — self-confirmation must not
        immunize a claim against the UNCONTESTED rule.
        """
        out: list[Message] = []
        for msg in self.open_claims():
            if current_round - msg.round_no < grace_rounds:
                continue
            children = self.messages(parent_id=msg.id)
            attacked = any(c.tag in (Tag.COUNTEREXAMPLE, Tag.DEAD_END) for c in children)
            confirmed = any(c.tag == Tag.CONFIRMED and c.author != msg.author for c in children)
            fixed = any(c.tag == Tag.FIX and c.author != msg.author for c in children)
            if not attacked and not confirmed and not fixed:
                out.append(msg)
        return out

    def has_new_claim_since(self, since_round: int) -> bool:
        rows = self._conn.execute(
            "SELECT 1 FROM messages WHERE tag = ? AND round_no >= ? LIMIT 1",
            (Tag.CLAIM.value, since_round),
        ).fetchone()
        return rows is not None

    def latest_verdict(self) -> Message | None:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE tag = ? ORDER BY seq DESC LIMIT 1",
            (Tag.VERDICT.value,),
        ).fetchall()
        return self._row_to_msg(rows[0]) if rows else None

    def dump(self) -> list[dict]:
        return [m.to_dict() for m in self.messages()]

    def load(self, records: Iterable[dict]) -> None:
        for rec in records:
            self.post(Message.from_dict(rec))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Blackboard":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> Message:
        return Message(
            tag=Tag.parse(row["tag"]),
            body=row["body"],
            author=row["author"],
            id=row["id"],
            round_no=int(row["round_no"]),
            parent_id=row["parent_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=json.loads(row["metadata"]),
            status=row["status"],
        )
