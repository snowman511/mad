"""Web dashboard server for the mad multi-agent discussion framework.

Serves:
  GET  /                 index.html (and other static assets from the project root)
  GET  /api/health       liveness
  GET  /api/report       full session report JSON
  GET  /api/board        message list only
  GET  /api/meta         compact metadata
  GET  /api/runtimes     available runtimes (local CLI / API key)
  GET  /api/session      live session status + board
  POST /api/session/start   body: {task, roles, max_rounds, runtime, verifier, ...}
  POST /api/session/stop    stop the running session

Usage:
  python -m mad.web --port 8765 --open
  python -m mad.web --report examples/demo_report.json --port 8765
  python -m mad.web --board board.sqlite --port 8765
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mad.blackboard import Blackboard
from mad.cli_runtime import RuntimeCatalog
from mad.models import Tag
from mad.session_manager import MANAGER

# project root: D:\C-HD  (src/mad/web.py -> parents[2])
DEFAULT_ROOT = Path(__file__).resolve().parents[2]


class BoardStore:
    """Static snapshot viewer (demo report / sqlite). Live session wins if present."""

    def __init__(self, report_path: Path | None = None, board_path: Path | None = None) -> None:
        self.report_path = report_path
        self.board_path = board_path

    def report(self) -> dict[str, Any]:
        live = MANAGER.status()
        if live.get("session"):
            sess = live["session"] or {}
            board = live.get("board") or []
            report = sess.get("report") or {}
            if not report:
                report = {
                    "stop_reason": sess.get("status"),
                    "verdict": None,
                    "best_messages": [
                        m
                        for m in board
                        if m.get("tag")
                        in ("EXPERIMENT_RESULT", "BASELINE_RESULT", "LEMMA_PROVED", "CONFIRMED")
                    ],
                    "open_questions": [m for m in board if m.get("tag") == "QUESTION"],
                    "dead_ends": [
                        m for m in board if m.get("tag") in ("DEAD_END", "COUNTEREXAMPLE")
                    ],
                    "rounds": [],
                    "board": board,
                    "task": sess.get("task"),
                }
            else:
                report = dict(report)
                report.setdefault("board", board)
                report.setdefault("task", sess.get("task"))
            report["session"] = sess
            report["live"] = True
            return report

        if self.board_path and self.board_path.exists():
            return self._from_sqlite(self.board_path)
        raw: dict[str, Any] | None = None
        if self.report_path and self.report_path.exists():
            data = json.loads(self.report_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = data
        if raw is None:
            for cand in (
                DEFAULT_ROOT / "examples" / "demo_report.json",
                DEFAULT_ROOT / "web" / "demo-data.json",
            ):
                if cand.exists():
                    raw = json.loads(cand.read_text(encoding="utf-8"))
                    break
        if raw is None:
            return {
                "stop_reason": "no_data",
                "verdict": None,
                "best_messages": [],
                "open_questions": [],
                "dead_ends": [],
                "rounds": [],
                "board": [],
                "task": "no data source — 用右上角「开始讨论」跑一场，或 pass --report/--board",
            }
        if not raw.get("board"):
            board: list[dict[str, Any]] = []
            for r in raw.get("rounds") or []:
                board.extend(r.get("posts") or [])
            raw["board"] = board
        return raw

    @staticmethod
    def _from_sqlite(path: Path) -> dict[str, Any]:
        with Blackboard(path) as board:
            dump = board.dump()
            failed = [m.to_dict() for m in board.failed_approaches()]
            questions = [m.to_dict() for m in board.messages(tag=Tag.QUESTION)]
            verdict = board.latest_verdict()
            verdict_dict = verdict.to_dict() if verdict else None
            best = [
                m.to_dict()
                for m in board.messages()
                if m.tag.value
                in ("EXPERIMENT_RESULT", "BASELINE_RESULT", "LEMMA_PROVED", "CONFIRMED")
            ]
        task = ""
        for m in dump:
            if m.get("tag") == "SYSTEM":
                body = m.get("body", "")
                if "Task:" in body:
                    task = body.split("Task:", 1)[1].split("\n", 1)[0].strip()
                    break
        rounds = sorted({m.get("round_no", 0) for m in dump})
        return {
            "stop_reason": "board_snapshot",
            "verdict": (verdict_dict or {}).get("metadata") if verdict_dict else None,
            "best_messages": best,
            "open_questions": questions,
            "dead_ends": failed,
            "rounds": [{"round_no": r} for r in rounds],
            "board": dump,
            "task": task,
        }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, store: BoardStore, root: Path, **kwargs):
        self.store = store
        super().__init__(*args, directory=str(root), **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        import sys

        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ----------------------------------------------------------------- HTTP

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json({"ok": True, "service": "mad-web"})
        if path == "/api/report":
            return self._json(self.store.report())
        if path == "/api/board":
            return self._json(self.store.report().get("board") or [])
        if path == "/api/meta":
            r = self.store.report()
            return self._json(
                {
                    "task": r.get("task"),
                    "stop_reason": r.get("stop_reason"),
                    "verdict": r.get("verdict"),
                    "session": r.get("session"),
                    "live": r.get("live", False),
                    "counts": {
                        "posts": len(r.get("board") or []),
                        "dead_ends": len(r.get("dead_ends") or []),
                        "questions": len(r.get("open_questions") or []),
                    },
                }
            )
        if path == "/api/runtimes":
            return self._json(RuntimeCatalog.list())
        if path == "/api/session":
            qs = parse_qs(urlparse(self.path).query)
            return self._json(MANAGER.status((qs.get("id") or [None])[0]))
        if path == "/api/sessions":
            return self._json({"sessions": MANAGER.list()})
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        try:
            if path == "/api/session/start":
                handle = MANAGER.start(body or {})
                return self._json({"ok": True, "session": handle.to_dict()})
            if path == "/api/session/stop":
                stopped = MANAGER.stop((body or {}).get("id"))
                return self._json({"ok": True, "stopped": stopped})
            if path == "/api/inject":
                # human-in-the-loop: post the operator's/participant's thought
                # straight onto a session's blackboard; the next agent turn
                # re-observes and sees it.
                return self._json(MANAGER.inject(
                    (body or {}).get("id"),
                    (body or {}).get("body", ""),
                    tag=(body or {}).get("tag", "NOTE"),
                    author=(body or {}).get("author", "human"),
                ))
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        self.send_error(404, "Not Found")

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path: str) -> str:  # type: ignore[override]
        ctype = super().guess_type(path)
        if path.endswith((".html", ".js", ".css")) and "charset" not in ctype:
            return f"{ctype}; charset=utf-8"
        return ctype

    # ----------------------------------------------------------------- helpers

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _json(self, obj: Any, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def find_free_port(preferred: int = 8765) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    root: Path | None = None,
    report_path: Path | None = None,
    board_path: Path | None = None,
    open_browser: bool = False,
) -> None:
    root = (root or DEFAULT_ROOT).resolve()
    store = BoardStore(report_path=report_path, board_path=board_path)
    port = port or find_free_port(8765)
    handler = partial(Handler, store=store, root=root)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"mad web board  root={root}")
    print(f"  serving on {url}")
    print(f"  api: /api/report /api/runtimes /api/session")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mad.web", description="mad discussion web board")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="static root (index.html)")
    parser.add_argument("--report", type=Path, help="session report JSON to display")
    parser.add_argument("--board", type=Path, help="SQLite blackboard to display")
    parser.add_argument("--open", action="store_true", help="open the default browser")
    args = parser.parse_args(argv)
    serve(
        host=args.host,
        port=args.port,
        root=args.root,
        report_path=args.report,
        board_path=args.board,
        open_browser=args.open,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
