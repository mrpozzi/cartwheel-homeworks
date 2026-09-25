"""File-backed review server for Homework 4.

A standard-library HTTP server that serves the review UI and a small JSON API
over the plain files in ``analysis/state/``. The design keeps the reference
server's contract (path to file table, atomic writes, the app posts on every
change) and adds what the session-grouped review needs: a session endpoint,
component-anchored annotations with tags, per-trace and per-session notes,
append-only labels that are also written to Langfuse as scores, and the
requirement index from ``SPEC.md``.

Run from the repository root::

    uv run python -m analysis.review_app.server                # live Langfuse, cached
    uv run python -m analysis.review_app.server --source export
    uv run python -m analysis.review_app.server --refresh       # re-fetch from Langfuse

API::

    GET  /                       the review app
    GET  /api/source             which trace source is in use and why
    GET  /api/sessions           session summaries (sidebar, labels view)
    GET  /api/session?id=...     one full session with turns and steps
    GET  /api/spec               requirement ids and text from SPEC.md
    GET  /api/tags               tags in use, with counts
    GET  /api/manifest           sample_manifest.json (batches), read only
    GET/POST /api/annotations    open codes, anchored to trace components
    GET/POST /api/trace_notes    tags and comments on traces and sessions
    GET/POST /api/patterns       the taxonomy
    GET/POST /api/suggestions    AI suggestions with accept/reject decisions
    GET  /api/labels             live label per trace and mode
    POST /api/labels             one judgment {trace_id, mode, label, note}
    POST /api/labels/sync        retry Langfuse writes for unsynced labels
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from analysis.helpers import _state
from analysis.review_app import loader

HERE = Path(__file__).resolve().parent
UI_DIR = HERE / "ui"

API_FILES: dict[str, str] = {
    "/api/annotations": "annotations.json",
    "/api/trace_notes": "trace_notes.json",
    "/api/patterns": "patterns.json",
    "/api/suggestions": "suggestions.json",
    "/api/manifest": "sample_manifest.json",
}
API_DEFAULTS: dict[str, Any] = {
    "/api/annotations": {"annotations": []},
    "/api/trace_notes": {"traces": {}, "sessions": {}},
    "/api/patterns": {"modes": []},
    "/api/suggestions": [],
    "/api/manifest": {"batches": []},
}
READ_ONLY = {"/api/manifest"}

# Filled once at startup.
SESSIONS: list[dict[str, Any]] = []
SESSION_BY_ID: dict[str, dict[str, Any]] = {}
TRACE_TO_SESSION: dict[str, str] = {}
SOURCE_INFO: dict[str, Any] = {}
SPEC: list[dict[str, str]] = []
_SCORE_CONFIGS: set[str] = set()


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _state_file(name: str) -> Path:
    return _state.state_path(name)


def _read(path_key: str) -> Any:
    path = _state_file(API_FILES[path_key])
    try:
        return _state.read_json(path, default=API_DEFAULTS[path_key])
    except (json.JSONDecodeError, OSError):
        return API_DEFAULTS[path_key]


# ---------------------------------------------------------------------------
# labels: append-only files plus Langfuse scores
# ---------------------------------------------------------------------------


def _labels_path(mode: str) -> Path:
    return _state.state_path("labels", f"{mode}.jsonl")


def _known_modes() -> set[str]:
    patterns = _read("/api/patterns")
    return {m.get("name") for m in patterns.get("modes", []) if isinstance(m, dict) and m.get("name")}


def _live_labels() -> dict[str, dict[str, dict[str, Any]]]:
    """``{mode: {trace_id: record}}`` for the current label of every reviewed trace."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for mode in sorted(_known_modes()):
        rows = _state.read_jsonl(_labels_path(mode))
        live: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("superseded_by"):
                continue
            live[row["trace_id"]] = row
        out[mode] = live
    return out


def _langfuse_write(trace_id: str, mode: str, label: int, note: str | None) -> dict[str, Any]:
    """Write one score to Langfuse; return ``{"synced": bool, "error": str|None}``."""
    try:
        from analysis.helpers import langfuse_io

        if not langfuse_io.is_configured():
            return {"synced": False, "error": "Langfuse not configured"}
        client = langfuse_io._client()
        if mode not in _SCORE_CONFIGS:
            try:
                langfuse_io.ensure_score_config(mode, client=client)
            except Exception as exc:  # a missing config does not block the score
                print(f"[labels] score config for {mode} not ensured: {type(exc).__name__}", file=sys.stderr)
            _SCORE_CONFIGS.add(mode)
        langfuse_io.write_label_score(trace_id=trace_id, mode=mode, label=label, comment=note, client=client)
        return {"synced": True, "error": None, "at": _utcnow()}
    except Exception as exc:
        return {"synced": False, "error": f"{type(exc).__name__}: {exc}"}


def write_label(trace_id: str, mode: str, label: int, note: str | None, sync: bool = True) -> dict[str, Any]:
    """Append a judgment, supersede the prior live record, and mirror it to Langfuse."""
    if mode not in _known_modes():
        raise ValueError(f"unknown mode '{mode}'; add it to patterns.json first")
    if trace_id not in TRACE_TO_SESSION:
        raise ValueError(f"unknown trace id '{trace_id}'")
    if label not in (0, 1):
        raise ValueError("label must be 0 (absent) or 1 (present)")
    path = _labels_path(mode)
    rows = _state.read_jsonl(path)
    label_id = f"{trace_id}#{sum(1 for r in rows if r.get('trace_id') == trace_id)}"
    for row in rows:
        if row.get("trace_id") == trace_id and not row.get("superseded_by"):
            row["superseded_by"] = label_id
    record = {
        "trace_id": trace_id,
        "mode": mode,
        "label": label,
        "source": "human",
        "note": note or None,
        "ts": _utcnow(),
        "label_id": label_id,
        "session_id": TRACE_TO_SESSION.get(trace_id),
        "langfuse": _langfuse_write(trace_id, mode, label, note) if sync else {"synced": False, "error": "sync skipped"},
    }
    _state.write_jsonl(path, rows + [record])
    return record


def sync_unsynced() -> dict[str, Any]:
    """Retry the Langfuse write for every live label that is not synced."""
    retried = 0
    synced = 0
    for mode, live in _live_labels().items():
        path = _labels_path(mode)
        rows = _state.read_jsonl(path)
        changed = False
        for row in rows:
            if row.get("superseded_by") or (row.get("langfuse") or {}).get("synced"):
                continue
            retried += 1
            row["langfuse"] = _langfuse_write(row["trace_id"], mode, int(row["label"]), row.get("note"))
            synced += int(bool(row["langfuse"].get("synced")))
            changed = True
        if changed:
            _state.write_jsonl(path, rows)
    return {"retried": retried, "synced": synced}


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


def _tag_counts() -> dict[str, int]:
    counts: dict[str, int] = {}

    def add(tags: Any) -> None:
        for tag in tags or []:
            if isinstance(tag, str) and tag.strip():
                counts[tag.strip()] = counts.get(tag.strip(), 0) + 1

    data = _read("/api/annotations")
    for ann in data.get("annotations", []) if isinstance(data, dict) else data:
        if isinstance(ann, dict):
            add(ann.get("tags"))
    notes = _read("/api/trace_notes")
    for bucket in ("traces", "sessions"):
        for entry in (notes.get(bucket) or {}).values():
            if isinstance(entry, dict):
                add(entry.get("tags"))
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": f"not found: {path.name}"}, status=404)
            return
        body = path.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json",
            ".svg": "image/svg+xml",
        }.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_file(UI_DIR / "index.html")
            return
        if path.startswith("/ui/"):
            asset = (UI_DIR / path[len("/ui/"):]).resolve()
            if UI_DIR.resolve() in asset.parents:
                self._send_file(asset)
                return
            self._send_json({"error": "forbidden"}, status=403)
            return
        if path == "/api/source":
            self._send_json(SOURCE_INFO)
            return
        if path == "/api/sessions":
            self._send_json([loader.summarize(s) for s in SESSIONS])
            return
        if path == "/api/session":
            sid = (query.get("id") or [""])[0]
            session = SESSION_BY_ID.get(sid) or SESSION_BY_ID.get(TRACE_TO_SESSION.get(sid, ""))
            if session is None:
                self._send_json({"error": f"unknown session or trace id: {sid}"}, status=404)
                return
            self._send_json(session)
            return
        if path == "/api/spec":
            self._send_json(SPEC)
            return
        if path == "/api/tags":
            self._send_json(_tag_counts())
            return
        if path == "/api/labels":
            self._send_json(_live_labels())
            return
        if path in API_FILES:
            self._send_json(_read(path))
            return
        self._send_json({"error": f"unknown path: {path}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        data = self._read_body()
        if path == "/api/labels":
            if not isinstance(data, dict):
                self._send_json({"error": "expected a JSON object"}, status=400)
                return
            try:
                record = write_label(
                    str(data.get("trace_id")),
                    str(data.get("mode")),
                    int(data.get("label")),
                    (data.get("note") or None),
                    sync=bool(data.get("sync", True)),
                )
            except (ValueError, TypeError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"ok": True, "record": record})
            return
        if path == "/api/labels/sync":
            self._send_json({"ok": True, **sync_unsynced()})
            return
        if path not in API_FILES or path in READ_ONLY:
            self._send_json({"error": f"cannot POST to {path}"}, status=404)
            return
        if data is None:
            self._send_json({"error": "expected a JSON body"}, status=400)
            return
        _state.write_json(_state_file(API_FILES[path]), data)
        count = len(data.get("annotations", data)) if isinstance(data, dict) and path == "/api/annotations" else len(data)
        self._send_json({"ok": True, "count": count})


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------


def boot(source: str, refresh: bool) -> None:
    """Load traces, scenarios, and the requirement index into memory."""
    global SESSIONS, SESSION_BY_ID, TRACE_TO_SESSION, SOURCE_INFO, SPEC
    SESSIONS, SOURCE_INFO = loader.load_all(source=source, refresh=refresh)
    SESSION_BY_ID = {s["session_id"]: s for s in SESSIONS}
    TRACE_TO_SESSION = {t["trace_id"]: s["session_id"] for s in SESSIONS for t in s["turns"]}
    SPEC = loader.load_spec()
    SOURCE_INFO["state_dir"] = str(_state.state_root())
    SOURCE_INFO["requirements"] = len(SPEC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--source", choices=["auto", "langfuse", "export"], default="auto")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-fetch from Langfuse")
    args = parser.parse_args()

    print(f"loading traces (source={args.source}{', refresh' if args.refresh else ''})...")
    boot(args.source, args.refresh)
    info = SOURCE_INFO
    print(f"source: {info.get('used')} ({info.get('reason')})")
    print(f"sessions: {info['session_count']}  traces: {info['trace_count']}  requirements: {info['requirements']}")
    print(f"state dir: {info['state_dir']}")
    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"review interface on http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
