from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .errors import ServiceError


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def merge(base, patch):
    result = json.loads(canonical(base))
    for key, value in patch.items():
        result[key] = (
            merge(result.get(key, {}), value)
            if isinstance(value, dict) and isinstance(result.get(key, {}), dict)
            else value
        )
    return result


class Store:
    def __init__(self, root: Path, ttl_s: int):
        self.root, self.ttl_s = root, ttl_s
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner_file = (root / "service.lock").open("a+")
        try:
            fcntl.flock(self.owner_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.owner_file.close()
            raise ServiceError(
                "STORE_IN_USE", "Another service owns this data directory", 409
            ) from None
        (root / "media").mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(
            root / "state.sqlite3", check_same_thread=False, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS contexts(
          id TEXT PRIMARY KEY, namespace TEXT NOT NULL, task_id TEXT,
          version INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}',
          state TEXT NOT NULL DEFAULT 'active', busy_request TEXT, updated REAL NOT NULL,
          UNIQUE(namespace, task_id));
        CREATE TABLE IF NOT EXISTS requests(
          namespace TEXT NOT NULL, id TEXT NOT NULL, payload_hash TEXT NOT NULL,
          context_id TEXT, task_mode INTEGER NOT NULL, parent_version INTEGER NOT NULL,
          status TEXT NOT NULL, request_json TEXT NOT NULL, snapshot_json TEXT,
          result_json TEXT, created REAL NOT NULL, ended REAL,
          PRIMARY KEY(namespace,id));
        CREATE TABLE IF NOT EXISTS media(
          id TEXT PRIMARY KEY, namespace TEXT NOT NULL, context_id TEXT NOT NULL,
          hash TEXT NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL,
          width INTEGER NOT NULL, height INTEGER NOT NULL, size INTEGER NOT NULL,
          metadata TEXT NOT NULL, UNIQUE(namespace,context_id,hash));
        CREATE TABLE IF NOT EXISTS records(
          id TEXT PRIMARY KEY, namespace TEXT NOT NULL, context_id TEXT NOT NULL,
          version INTEGER NOT NULL, request_id TEXT NOT NULL, body TEXT NOT NULL,
          UNIQUE(namespace,context_id,version));
        """)
        # A single service process owns the database. A restart must never adopt a late answer.
        with self.tx():
            rows = self.db.execute(
                "SELECT * FROM requests WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                result = self.failure(
                    row["id"],
                    "failed",
                    "SERVICE_RESTARTED",
                    "Inference interrupted by service restart",
                )
                self.db.execute(
                    "UPDATE requests SET status='failed',result_json=?,ended=? WHERE namespace=? AND id=?",
                    (canonical(result), time.time(), row["namespace"], row["id"]),
                )
            self.db.execute("UPDATE contexts SET busy_request=NULL")

    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    @staticmethod
    def failure(request_id, status, code, message):
        return {
            "request_id": request_id,
            "execution_status": status,
            "provenance": {"model_route": None, "model_id": None},
            "usage": {"input_tokens": None, "output_tokens": None},
            "error": {"code": code, "message": message},
        }

    def context_for(self, namespace, req):
        with self.tx():
            existing = self.db.execute(
                "SELECT context_id FROM requests WHERE namespace=? AND id=?",
                (namespace, req.request_id),
            ).fetchone()
            if existing:
                return self._context(namespace, existing["context_id"])
            spec = req.context
            if spec.mode == "stateless":
                context_id = uid("request-media")
                self.db.execute(
                    "INSERT INTO contexts(id,namespace,updated) VALUES(?,?,?)",
                    (context_id, namespace, time.time()),
                )
            elif spec.context_id:
                context = self._context(namespace, spec.context_id)
                if context["task_id"] != spec.task_id:
                    raise ServiceError("CONTEXT_MISMATCH", "Task does not match context", 409)
                return context
            else:
                row = self.db.execute(
                    "SELECT id FROM contexts WHERE namespace=? AND task_id=?",
                    (namespace, spec.task_id),
                ).fetchone()
                if row:
                    raise ServiceError(
                        "CONTEXT_EXISTS",
                        "Task already has a context; supply its context_id",
                        409,
                    )
                context_id = uid("ctx")
                self.db.execute(
                    "INSERT INTO contexts(id,namespace,task_id,updated) VALUES(?,?,?,?)",
                    (context_id, namespace, spec.task_id, time.time()),
                )
            return self._context(namespace, context_id)

    def _context(self, namespace, context_id):
        row = self.db.execute(
            "SELECT * FROM contexts WHERE namespace=? AND id=?", (namespace, context_id)
        ).fetchone()
        if row is None:
            raise ServiceError("CONTEXT_NOT_FOUND", "Context not found", 404)
        if row["state"] == "expired":
            raise ServiceError("CONTEXT_EXPIRED", "Context expired", 410)
        if row["state"] != "active":
            raise ServiceError("CONTEXT_CLOSED", "Context is closed", 409)
        if not row["busy_request"] and time.time() - row["updated"] > self.ttl_s:
            raise ServiceError("CONTEXT_EXPIRED", "Context expired", 410)
        return dict(row)

    def context(self, namespace, context_id):
        with self.lock:
            row = self._context(namespace, context_id)
            return {
                "context_id": row["id"],
                "task_id": row["task_id"],
                "version": row["version"],
                "state": row["state"],
                "data": json.loads(row["data"]),
                "busy": bool(row["busy_request"]),
            }

    def close_context(self, namespace, context_id, expected_version):
        with self.tx():
            row = self._context(namespace, context_id)
            if row["busy_request"]:
                raise ServiceError("CONTEXT_BUSY", "Context has an active inference", 409)
            if row["version"] != expected_version:
                raise ServiceError("CONTEXT_VERSION_CONFLICT", "Context version changed", 409)
            self.db.execute("UPDATE contexts SET state='closed' WHERE id=?", (context_id,))
            return {
                "context_id": context_id,
                "state": "closed",
                "version": row["version"],
            }

    def put_media(self, namespace, context_id, content, mime, width, height, metadata):
        sha = hashlib.sha256(content).hexdigest()
        with self.tx():
            self._context(namespace, context_id)
            row = self.db.execute(
                "SELECT * FROM media WHERE namespace=? AND context_id=? AND hash=?",
                (namespace, context_id, sha),
            ).fetchone()
            if row:
                return dict(row)
            media_id = uid("media")
            # Namespace/context metadata governs access, filenames contain no caller input.
            path = self.root / "media" / media_id
            with path.open("xb") as f:
                f.write(content)
            path.chmod(0o600)
            self.db.execute(
                "INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    media_id,
                    namespace,
                    context_id,
                    sha,
                    str(path),
                    mime,
                    width,
                    height,
                    len(content),
                    canonical(metadata),
                ),
            )
            return dict(self.db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone())

    def media(self, namespace, context_id, media_id):
        with self.lock:
            self._context(namespace, context_id)
            row = self.db.execute(
                "SELECT * FROM media WHERE namespace=? AND context_id=? AND id=?",
                (namespace, context_id, media_id),
            ).fetchone()
            if row is None:
                raise ServiceError("MEDIA_NOT_FOUND", "Media not found in this context", 404)
            path = Path(row["path"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["hash"]:
                raise ServiceError("MEDIA_CORRUPT", "Stored media is missing or changed", 409)
            return dict(row)

    def records(self, namespace, context_id, record_ids, parent_version):
        with self.lock:
            out = []
            for record_id in record_ids:
                row = self.db.execute(
                    "SELECT * FROM records WHERE namespace=? AND context_id=? AND id=? AND version<=?",
                    (namespace, context_id, record_id, parent_version),
                ).fetchone()
                if row is None:
                    raise ServiceError(
                        "RECORD_NOT_FOUND",
                        "Selected record is not in the context version",
                        404,
                    )
                out.append(
                    {
                        "record_id": record_id,
                        "version": row["version"],
                        **json.loads(row["body"]),
                    }
                )
            return out

    def admit(self, namespace, req, context, normalized, payload_hash):
        with self.tx():
            old = self.db.execute(
                "SELECT * FROM requests WHERE namespace=? AND id=?",
                (namespace, req.request_id),
            ).fetchone()
            if old:
                if old["payload_hash"] != payload_hash:
                    raise ServiceError(
                        "REQUEST_CONFLICT",
                        "request_id reused with different payload",
                        409,
                    )
                return dict(old), False
            row = self._context(namespace, context["id"])
            task_mode = req.context.mode == "task"
            if task_mode:
                if row["version"] != req.context.expected_version:
                    raise ServiceError(
                        "CONTEXT_VERSION_CONFLICT",
                        "Expected context version does not match",
                        409,
                    )
                if row["busy_request"]:
                    raise ServiceError("CONTEXT_BUSY", "Another inference holds this context", 409)
            self.db.execute(
                "UPDATE contexts SET busy_request=? WHERE id=?",
                (req.request_id, row["id"]),
            )
            self.db.execute(
                "INSERT INTO requests(namespace,id,payload_hash,context_id,task_mode,parent_version,status,request_json,created) VALUES(?,?,?,?,?,?,'queued',?,?)",
                (
                    namespace,
                    req.request_id,
                    payload_hash,
                    row["id"],
                    int(task_mode),
                    row["version"],
                    canonical(normalized),
                    time.time(),
                ),
            )
            return dict(
                self.db.execute(
                    "SELECT * FROM requests WHERE namespace=? AND id=?",
                    (namespace, req.request_id),
                ).fetchone()
            ), True

    def request(self, namespace, request_id):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM requests WHERE namespace=? AND id=?",
                (namespace, request_id),
            ).fetchone()
            if row is None:
                raise ServiceError("REQUEST_NOT_FOUND", "Request not found", 404)
            value = {
                "request_id": request_id,
                "status": row["status"],
                "created_at": row["created"],
                "ended_at": row["ended"],
            }
            context = self.db.execute(
                "SELECT state FROM contexts WHERE id=?", (row["context_id"],)
            ).fetchone()
            if context and context["state"] == "expired":
                raise ServiceError("CONTEXT_EXPIRED", "Request context and evidence expired", 410)
            if row["result_json"]:
                value["result"] = json.loads(row["result_json"])
            return value

    def running(self, namespace, request_id, snapshot):
        with self.tx():
            return bool(
                self.db.execute(
                    "UPDATE requests SET status='running',snapshot_json=? WHERE namespace=? AND id=? AND status='queued'",
                    (canonical(snapshot), namespace, request_id),
                ).rowcount
            )

    def update_snapshot(self, namespace, request_id, snapshot):
        with self.tx():
            return bool(
                self.db.execute(
                    "UPDATE requests SET snapshot_json=? WHERE namespace=? AND id=? AND status='running'",
                    (canonical(snapshot), namespace, request_id),
                ).rowcount
            )

    def finish(self, namespace, request_id, result, updates=None, record=None):
        with self.tx():
            req = self.db.execute(
                "SELECT * FROM requests WHERE namespace=? AND id=?",
                (namespace, request_id),
            ).fetchone()
            if req is None or req["status"] not in ("queued", "running"):
                return False
            if "metrics" not in result:
                result["metrics"] = {"total_ms": round((time.time() - req["created"]) * 1000, 3)}
            if result.get("provenance", {}).get("model_route") is None:
                result["provenance"] = {
                    **result.get("provenance", {}),
                    "model_route": json.loads(req["request_json"]).get("model_route"),
                }
            if result["execution_status"] == "succeeded" and req["task_mode"]:
                ctx = self._context(namespace, req["context_id"])
                if ctx["busy_request"] != request_id or ctx["version"] != req["parent_version"]:
                    raise ServiceError(
                        "CONTEXT_VERSION_CONFLICT",
                        "Reservation is no longer valid",
                        409,
                    )
                version = ctx["version"] + 1
                record_id = uid("rec")
                self.db.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?)",
                    (
                        record_id,
                        namespace,
                        ctx["id"],
                        version,
                        request_id,
                        canonical(record),
                    ),
                )
                self.db.execute(
                    "UPDATE contexts SET version=?,data=?,updated=? WHERE id=?",
                    (
                        version,
                        canonical(merge(json.loads(ctx["data"]), updates or {})),
                        time.time(),
                        ctx["id"],
                    ),
                )
                result["context"] = {
                    "context_id": ctx["id"],
                    "task_id": ctx["task_id"],
                    "branch_id": "main",
                    "parent_version": req["parent_version"],
                    "version": version,
                }
                result["output"]["record_id"] = record_id
            elif req["task_mode"]:
                result["context"] = {
                    "context_id": req["context_id"],
                    "version": req["parent_version"],
                    "branch_id": "main",
                }
            self.db.execute(
                "UPDATE requests SET status=?,result_json=?,ended=? WHERE namespace=? AND id=?",
                (
                    result["execution_status"],
                    canonical(result),
                    time.time(),
                    namespace,
                    request_id,
                ),
            )
            self.db.execute(
                "UPDATE contexts SET busy_request=NULL WHERE id=? AND busy_request=?",
                (req["context_id"], request_id),
            )
            return True

    def snapshot(self, namespace, request_id):
        with self.lock:
            row = self.db.execute(
                "SELECT snapshot_json FROM requests WHERE namespace=? AND id=?",
                (namespace, request_id),
            ).fetchone()
            if not row:
                raise ServiceError("REQUEST_NOT_FOUND", "Request not found", 404)
            return json.loads(row["snapshot_json"]) if row["snapshot_json"] else None

    def collect_expired(self):
        with self.tx():
            rows = self.db.execute(
                "SELECT * FROM contexts WHERE state!='expired' AND busy_request IS NULL AND updated<?",
                (time.time() - self.ttl_s,),
            ).fetchall()
            deleted = 0
            for row in rows:
                self.db.execute("UPDATE contexts SET state='expired' WHERE id=?", (row["id"],))
                for media in self.db.execute(
                    "SELECT path FROM media WHERE context_id=?", (row["id"],)
                ).fetchall():
                    Path(media["path"]).unlink(missing_ok=True)
                    deleted += 1
                self.db.execute("DELETE FROM media WHERE context_id=?", (row["id"],))
                self.db.execute("DELETE FROM records WHERE context_id=?", (row["id"],))
                self.db.execute("UPDATE contexts SET data='{}' WHERE id=?", (row["id"],))
                self.db.execute(
                    "UPDATE requests SET request_json='{}',snapshot_json=NULL,result_json=NULL WHERE context_id=?",
                    (row["id"],),
                )
            return {"expired_contexts": len(rows), "deleted_media": deleted}

    def close(self):
        self.db.close()
        fcntl.flock(self.owner_file, fcntl.LOCK_UN)
        self.owner_file.close()
