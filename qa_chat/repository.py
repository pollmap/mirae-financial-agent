"""SQLite persistence with encrypted content and transactional versioning."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from qa_chat.crypto import CipherBox, TokenHasher
from qa_chat.privacy import contains_sensitive_input


class RepositoryError(RuntimeError):
    code = "REPOSITORY_ERROR"


class NotFound(RepositoryError):
    code = "NOT_FOUND"


class Forbidden(RepositoryError):
    code = "FORBIDDEN"


class Conflict(RepositoryError):
    code = "CONFLICT"


class RequestPending(Conflict):
    code = "REQUEST_PENDING"

    def __init__(self, lease_expires_at: float) -> None:
        super().__init__("the matching request is still processing")
        self.lease_expires_at = lease_expires_at


class InvalidInvite(RepositoryError):
    code = "INVALID_INVITE"


class RateLimited(RepositoryError):
    code = "RATE_LIMITED"

    def __init__(self, retry_after: int) -> None:
        super().__init__("request budget exceeded")
        self.retry_after = retry_after


def _now() -> float:
    return time.time()


def _id() -> str:
    return uuid.uuid4().hex


class QARepository:
    """Single-process repository; SQLite still enforces cross-transaction invariants."""

    def __init__(
        self,
        path: Path,
        cipher: CipherBox,
        hasher: TokenHasher,
        *,
        retention_days: int = 14,
        max_sessions_per_tester: int = 50,
        max_messages_per_session: int = 1_000,
        max_ciphertext_bytes_per_tester: int = 64 * 1024 * 1024,
        max_ciphertext_bytes_total: int = 512 * 1024 * 1024,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.cipher = cipher
        self.hasher = hasher
        self.retention_days = retention_days
        self.max_sessions_per_tester = max_sessions_per_tester
        self.max_messages_per_session = max_messages_per_session
        self.max_ciphertext_bytes_per_tester = max_ciphertext_bytes_per_tester
        self.max_ciphertext_bytes_total = max_ciphertext_bytes_total
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=15000")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY, applied_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invites (
          token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL,
          max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
          revoked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS testers (
          id TEXT PRIMARY KEY, alias TEXT NOT NULL UNIQUE, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS consents (
          tester_id TEXT PRIMARY KEY REFERENCES testers(id) ON DELETE CASCADE,
          version TEXT NOT NULL, consented_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
          token_hash TEXT PRIMARY KEY,
          tester_id TEXT NOT NULL REFERENCES testers(id) ON DELETE CASCADE,
          csrf_hash TEXT NOT NULL, expires_at REAL NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_sessions (
          id TEXT PRIMARY KEY,
          tester_id TEXT NOT NULL REFERENCES testers(id) ON DELETE CASCADE,
          mode TEXT NOT NULL CHECK(mode IN ('guided','free')),
          scenario_id TEXT,
          title_cipher TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner
          ON chat_sessions(tester_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS message_requests (
          session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
          client_message_id TEXT NOT NULL,
          request_hash TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('pending','completed')),
          result_cipher TEXT,
          created_at REAL NOT NULL, updated_at REAL NOT NULL,
          expected_version INTEGER NOT NULL DEFAULT -1,
          lease_expires_at REAL NOT NULL DEFAULT 0,
          PRIMARY KEY(session_id, client_message_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK(role IN ('user','assistant')),
          status TEXT,
          content_cipher TEXT NOT NULL,
          view_cipher TEXT,
          turn_id INTEGER NOT NULL,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session
          ON messages(session_id, turn_id, created_at);
        CREATE TABLE IF NOT EXISTS engine_exchanges (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
          turn_id INTEGER NOT NULL,
          request_cipher TEXT NOT NULL,
          response_cipher TEXT,
          outcome TEXT NOT NULL,
          latency_ms REAL NOT NULL,
          created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS encrypted_states (
          session_id TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
          state_cipher TEXT NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feedback (
          message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
          tester_id TEXT NOT NULL REFERENCES testers(id) ON DELETE CASCADE,
          verdict TEXT NOT NULL, tags_json TEXT NOT NULL,
          note_cipher TEXT, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tester_id TEXT NOT NULL REFERENCES testers(id) ON DELETE CASCADE,
          attempted_at REAL NOT NULL, outcome TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_ledger(attempted_at);
        CREATE INDEX IF NOT EXISTS idx_usage_tester_time
          ON usage_ledger(tester_id, attempted_at);
        CREATE TABLE IF NOT EXISTS pilot_counters (
          id INTEGER PRIMARY KEY CHECK(id=1),
          total_started INTEGER NOT NULL CHECK(total_started>=0)
        );
        CREATE TABLE IF NOT EXISTS deletion_audit (
          session_fingerprint TEXT PRIMARY KEY,
          deleted_at REAL NOT NULL, deleted_message_count INTEGER NOT NULL,
          reason TEXT NOT NULL
        );
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)
            request_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(message_requests)"
                ).fetchall()
            }
            if "expected_version" not in request_columns:
                self._connection.execute(
                    "ALTER TABLE message_requests "
                    "ADD COLUMN expected_version INTEGER NOT NULL DEFAULT -1"
                )
            if "lease_expires_at" not in request_columns:
                self._connection.execute(
                    "ALTER TABLE message_requests "
                    "ADD COLUMN lease_expires_at REAL NOT NULL DEFAULT 0"
                )
            # Rows created by an older build have no trustworthy lease. Treat
            # those interrupted requests as expired before adding the unique
            # in-flight version guard.
            self._connection.execute(
                "DELETE FROM message_requests "
                "WHERE status='pending' AND lease_expires_at<=?",
                (_now(),),
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_session_version
                ON message_requests(session_id, expected_version)
                WHERE status='pending' AND expected_version>=0
                """
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (_now(),),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, ?)",
                (_now(),),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO pilot_counters(id, total_started) VALUES(1, 0)"
            )

    def create_invite(
        self,
        *,
        code: str | None = None,
        expires_in_seconds: int = 86_400,
        max_uses: int = 1,
    ) -> str:
        if expires_in_seconds < 60 or max_uses != 1:
            raise ValueError("invalid invite limits")
        raw = code or secrets.token_urlsafe(32)
        fingerprint = self.hasher.digest("invite", raw)
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO invites VALUES(?, ?, ?, 0, 0, ?)",
                (fingerprint, now + expires_in_seconds, max_uses, now),
            )
        return raw

    def revoke_invite(self, code: str) -> bool:
        fingerprint = self.hasher.digest("invite", code)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE invites SET revoked=1 WHERE token_hash=?", (fingerprint,)
            )
        return cursor.rowcount == 1

    def redeem_invite(
        self, code: str, consent_version: str, *, auth_ttl_seconds: int
    ) -> dict[str, str]:
        fingerprint = self.hasher.digest("invite", code)
        now = _now()
        tester_id = _id()
        alias = f"tester-{tester_id[:8]}"
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        auth_hash = self.hasher.digest("auth", session_token)
        csrf_hash = self.hasher.digest("csrf", csrf_token)
        with self._lock, self._connection:
            invite = self._connection.execute(
                "SELECT * FROM invites WHERE token_hash=?", (fingerprint,)
            ).fetchone()
            if (
                invite is None
                or invite["revoked"]
                or invite["expires_at"] <= now
                # Enforce one-time redemption even for a database created by
                # an older build that allowed max_uses greater than one.
                or invite["uses"] >= 1
            ):
                raise InvalidInvite("invite is invalid, expired, or exhausted")
            self._connection.execute(
                "UPDATE invites SET uses=uses+1 WHERE token_hash=?", (fingerprint,)
            )
            self._connection.execute(
                "INSERT INTO testers VALUES(?, ?, ?)", (tester_id, alias, now)
            )
            self._connection.execute(
                "INSERT INTO consents VALUES(?, ?, ?)",
                (tester_id, consent_version, now),
            )
            self._connection.execute(
                "INSERT INTO auth_sessions VALUES(?, ?, ?, ?, ?)",
                (auth_hash, tester_id, csrf_hash, now + auth_ttl_seconds, now),
            )
        return {
            "tester_id": tester_id,
            "alias": alias,
            "session_token": session_token,
            "csrf_token": csrf_token,
        }

    def authenticate(self, session_token: str, csrf_token: str | None = None) -> dict[str, str]:
        if not session_token:
            raise Forbidden("missing authentication session")
        auth_hash = self.hasher.digest("auth", session_token)
        now = _now()
        with self._lock:
            row = self._connection.execute(
                """
                SELECT a.*, t.alias, c.version AS consent_version
                FROM auth_sessions a
                JOIN testers t ON t.id=a.tester_id
                JOIN consents c ON c.tester_id=t.id
                WHERE a.token_hash=?
                """,
                (auth_hash,),
            ).fetchone()
        if row is None or row["expires_at"] <= now:
            raise Forbidden("authentication session is invalid or expired")
        if csrf_token is not None and not secrets.compare_digest(
            row["csrf_hash"], self.hasher.digest("csrf", csrf_token)
        ):
            raise Forbidden("CSRF validation failed")
        return {
            "id": row["tester_id"],
            "alias": row["alias"],
            "consent_version": row["consent_version"],
        }

    def logout(self, session_token: str) -> None:
        if not session_token:
            return
        token_hash = self.hasher.digest("auth", session_token)
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))

    def _ciphertext_usage(self, tester_id: str | None = None) -> int:
        """Count encrypted envelope characters without decrypting content."""

        owner_where = " WHERE tester_id=?" if tester_id is not None else ""
        joined_where = " WHERE s.tester_id=?" if tester_id is not None else ""
        params = (tester_id,) if tester_id is not None else ()
        statements = (
            f"SELECT COALESCE(SUM(LENGTH(title_cipher)), 0) FROM chat_sessions{owner_where}",
            "SELECT COALESCE(SUM(LENGTH(m.content_cipher) + "
            "COALESCE(LENGTH(m.view_cipher), 0)), 0) FROM messages m "
            f"JOIN chat_sessions s ON s.id=m.session_id{joined_where}",
            "SELECT COALESCE(SUM(LENGTH(e.request_cipher) + "
            "COALESCE(LENGTH(e.response_cipher), 0)), 0) FROM engine_exchanges e "
            f"JOIN chat_sessions s ON s.id=e.session_id{joined_where}",
            "SELECT COALESCE(SUM(LENGTH(e.state_cipher)), 0) FROM encrypted_states e "
            f"JOIN chat_sessions s ON s.id=e.session_id{joined_where}",
            "SELECT COALESCE(SUM(COALESCE(LENGTH(f.note_cipher), 0)), 0) "
            "FROM feedback f JOIN messages m ON m.id=f.message_id "
            f"JOIN chat_sessions s ON s.id=m.session_id{joined_where}",
            "SELECT COALESCE(SUM(COALESCE(LENGTH(r.result_cipher), 0)), 0) "
            "FROM message_requests r "
            f"JOIN chat_sessions s ON s.id=r.session_id{joined_where}",
        )
        with self._lock:
            return sum(
                int(self._connection.execute(statement, params).fetchone()[0])
                for statement in statements
            )

    def _enforce_ciphertext_quota(self, tester_id: str, projected_delta: int) -> None:
        if projected_delta < 0:
            raise ValueError("projected ciphertext delta cannot be negative")
        if (
            self._ciphertext_usage(tester_id) + projected_delta
            > self.max_ciphertext_bytes_per_tester
            or self._ciphertext_usage() + projected_delta > self.max_ciphertext_bytes_total
        ):
            raise RateLimited(60)

    def create_session(
        self, tester_id: str, mode: str, scenario_id: str | None
    ) -> dict[str, Any]:
        session_id = _id()
        now = _now()
        expires_at = now + self.retention_days * 86_400
        title = "자유 테스트" if mode == "free" else f"가이드 · {scenario_id}"
        title_cipher = self.cipher.seal(title, aad=f"session-title:{session_id}")
        empty_state = {
            "schema_version": 1,
            "pending_clarification": None,
            "last_completed": None,
            "active_conditions": [],
            "turn_count": 0,
        }
        state_cipher = self.cipher.seal(empty_state, aad=f"session-state:{session_id}")
        with self._lock, self._connection:
            active_sessions = self._connection.execute(
                "SELECT COUNT(*) FROM chat_sessions WHERE tester_id=? AND expires_at>?",
                (tester_id, now),
            ).fetchone()[0]
            if active_sessions >= self.max_sessions_per_tester:
                raise RateLimited(60)
            self._enforce_ciphertext_quota(
                tester_id, len(title_cipher) + len(state_cipher)
            )
            self._connection.execute(
                "INSERT INTO chat_sessions VALUES(?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    session_id,
                    tester_id,
                    mode,
                    scenario_id,
                    title_cipher,
                    now,
                    now,
                    expires_at,
                ),
            )
            self._connection.execute(
                "INSERT INTO encrypted_states VALUES(?, ?, ?)",
                (session_id, state_cipher, now),
            )
        return {
            "id": session_id,
            "mode": mode,
            "scenario_id": scenario_id,
            "title": title,
            "version": 0,
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
        }

    def _owned_session(self, session_id: str, tester_id: str) -> sqlite3.Row:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM chat_sessions WHERE id=? AND tester_id=?",
                (session_id, tester_id),
            ).fetchone()
        if row is None:
            raise NotFound("session not found")
        if row["expires_at"] <= _now():
            raise NotFound("session expired")
        return row

    def list_sessions(self, tester_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM chat_sessions
                WHERE tester_id=? AND expires_at>?
                ORDER BY updated_at DESC LIMIT 100
                """,
                (tester_id, _now()),
            ).fetchall()
        return [self._session_view(row) for row in rows]

    def get_session(self, session_id: str, tester_id: str) -> dict[str, Any]:
        return self._session_view(self._owned_session(session_id, tester_id))

    def _session_view(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "mode": row["mode"],
            "scenario_id": row["scenario_id"],
            "title": self.cipher.open_text(
                row["title_cipher"], aad=f"session-title:{row['id']}"
            ),
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
        }

    def load_state(self, session_id: str, tester_id: str) -> dict[str, Any]:
        self._owned_session(session_id, tester_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT state_cipher FROM encrypted_states WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise RepositoryError("session state missing")
        return self.cipher.open_json(row["state_cipher"], aad=f"session-state:{session_id}")

    def request_status(
        self, session_id: str, tester_id: str, client_message_id: str, request_hash: str
    ) -> dict[str, Any] | None:
        self._owned_session(session_id, tester_id)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM message_requests
                WHERE session_id=? AND client_message_id=?
                """,
                (session_id, client_message_id),
            ).fetchone()
        if row is None:
            return None
        if not secrets.compare_digest(row["request_hash"], request_hash):
            raise Conflict("client_message_id was reused with a different request")
        if row["status"] == "pending":
            lease_expires_at = float(row["lease_expires_at"] or 0)
            if lease_expires_at <= _now():
                with self._lock, self._connection:
                    self._connection.execute(
                        """
                        DELETE FROM message_requests
                        WHERE session_id=? AND client_message_id=?
                          AND status='pending' AND lease_expires_at<=?
                        """,
                        (session_id, client_message_id, _now()),
                    )
                return None
            raise RequestPending(lease_expires_at)
        return self.cipher.open_json(
            row["result_cipher"], aad=f"request-result:{session_id}:{client_message_id}"
        )

    def ensure_turn_capacity(self, session_id: str, tester_id: str) -> None:
        self._owned_session(session_id, tester_id)
        with self._lock:
            message_count = self._connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if message_count + 2 > self.max_messages_per_session:
                raise RateLimited(60)
            if (
                self._ciphertext_usage(tester_id) >= self.max_ciphertext_bytes_per_tester
                or self._ciphertext_usage() >= self.max_ciphertext_bytes_total
            ):
                raise RateLimited(60)

    def reserve_request(
        self,
        session_id: str,
        tester_id: str,
        client_message_id: str,
        request_hash: str,
        expected_version: int,
        lease_seconds: float,
    ) -> None:
        session = self._owned_session(session_id, tester_id)
        if session["version"] != expected_version:
            raise Conflict("session version changed; refresh before retrying")
        now = _now()
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "DELETE FROM message_requests "
                    "WHERE session_id=? AND status='pending' AND lease_expires_at<=?",
                    (session_id, now),
                )
                self._connection.execute(
                    """
                    INSERT INTO message_requests(
                      session_id, client_message_id, request_hash, status,
                      result_cipher, created_at, updated_at,
                      expected_version, lease_expires_at
                    ) VALUES(?, ?, ?, 'pending', NULL, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        client_message_id,
                        request_hash,
                        now,
                        now,
                        expected_version,
                        now + lease_seconds,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict("client_message_id is already reserved") from exc

    def abandon_request(self, session_id: str, client_message_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                DELETE FROM message_requests
                WHERE session_id=? AND client_message_id=? AND status='pending'
                """,
                (session_id, client_message_id),
            )

    def commit_turn(
        self,
        *,
        session_id: str,
        tester_id: str,
        client_message_id: str,
        expected_version: int,
        user_message_id: str,
        user_text: str,
        assistant_view: dict[str, Any],
        state: dict[str, Any],
        engine_request: dict[str, Any] | None,
        engine_response: dict[str, Any] | None,
        engine_outcome: str,
        latency_ms: float,
    ) -> dict[str, Any]:
        self._owned_session(session_id, tester_id)
        now = _now()
        turn_id = expected_version + 1
        assistant_id = assistant_view["id"]
        result = {
            "session_id": session_id,
            "session_version": turn_id,
            "turn_id": turn_id,
            "assistant": assistant_view,
        }
        user_cipher = self.cipher.seal(user_text, aad=f"message:{user_message_id}")
        assistant_content_cipher = self.cipher.seal(
            assistant_view["content"], aad=f"message:{assistant_id}"
        )
        assistant_view_cipher = self.cipher.seal(
            assistant_view, aad=f"message-view:{assistant_id}"
        )
        state_cipher = self.cipher.seal(state, aad=f"session-state:{session_id}")
        result_cipher = self.cipher.seal(
            result, aad=f"request-result:{session_id}:{client_message_id}"
        )
        exchange_id = _id() if engine_request is not None else None
        request_cipher = (
            self.cipher.seal(engine_request, aad=f"engine-request:{exchange_id}")
            if engine_request is not None and exchange_id is not None
            else None
        )
        response_cipher = (
            self.cipher.seal(engine_response or {}, aad=f"engine-response:{exchange_id}")
            if engine_response is not None and exchange_id is not None
            else None
        )
        with self._lock, self._connection:
            request_row = self._connection.execute(
                """
                SELECT expected_version FROM message_requests
                WHERE session_id=? AND client_message_id=? AND status='pending'
                """,
                (session_id, client_message_id),
            ).fetchone()
            if request_row is None or request_row["expected_version"] != expected_version:
                raise Conflict("request reservation is missing or changed")
            message_count = self._connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if message_count + 2 > self.max_messages_per_session:
                raise RateLimited(60)
            old_state_size = self._connection.execute(
                "SELECT LENGTH(state_cipher) FROM encrypted_states WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            projected_delta = (
                len(user_cipher)
                + len(assistant_content_cipher)
                + len(assistant_view_cipher)
                + len(state_cipher)
                + len(result_cipher)
                + (len(request_cipher) if request_cipher is not None else 0)
                + (len(response_cipher) if response_cipher is not None else 0)
                - int(old_state_size or 0)
            )
            self._enforce_ciphertext_quota(tester_id, projected_delta)
            updated = self._connection.execute(
                """
                UPDATE chat_sessions SET version=version+1, updated_at=?
                WHERE id=? AND tester_id=? AND version=?
                """,
                (now, session_id, tester_id, expected_version),
            )
            if updated.rowcount != 1:
                raise Conflict("session version changed during request")
            self._connection.execute(
                "INSERT INTO messages VALUES(?, ?, 'user', NULL, ?, NULL, ?, ?)",
                (
                    user_message_id,
                    session_id,
                    user_cipher,
                    turn_id,
                    now,
                ),
            )
            self._connection.execute(
                "INSERT INTO messages VALUES(?, ?, 'assistant', ?, ?, ?, ?, ?)",
                (
                    assistant_id,
                    session_id,
                    assistant_view["status"],
                    assistant_content_cipher,
                    assistant_view_cipher,
                    turn_id,
                    now + 0.000001,
                ),
            )
            self._connection.execute(
                "UPDATE encrypted_states SET state_cipher=?, updated_at=? WHERE session_id=?",
                (
                    state_cipher,
                    now,
                    session_id,
                ),
            )
            if request_cipher is not None and exchange_id is not None:
                self._connection.execute(
                    "INSERT INTO engine_exchanges VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        exchange_id,
                        session_id,
                        turn_id,
                        request_cipher,
                        response_cipher,
                        engine_outcome,
                        latency_ms,
                        now,
                    ),
                )
            completed = self._connection.execute(
                """
                UPDATE message_requests SET status='completed', result_cipher=?, updated_at=?
                WHERE session_id=? AND client_message_id=? AND status='pending'
                """,
                (
                    result_cipher,
                    now,
                    session_id,
                    client_message_id,
                ),
            )
            if completed.rowcount != 1:
                raise Conflict("request reservation disappeared during commit")
        return result

    def list_messages(
        self, session_id: str, tester_id: str, *, cursor: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        self._owned_session(session_id, tester_id)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.*, f.verdict feedback_verdict, f.tags_json feedback_tags,
                       f.note_cipher feedback_note_cipher, f.updated_at feedback_updated_at
                FROM messages m LEFT JOIN feedback f ON f.message_id=m.id
                WHERE m.session_id=?
                ORDER BY turn_id, created_at LIMIT ? OFFSET ?
                """,
                (session_id, limit + 1, cursor),
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        messages = []
        for row in rows:
            content = self.cipher.open_text(row["content_cipher"], aad=f"message:{row['id']}")
            item: dict[str, Any] = {
                "id": row["id"],
                "role": row["role"],
                "content": content,
                "turn_id": row["turn_id"],
                "created_at": row["created_at"],
            }
            if row["role"] == "assistant":
                item["assistant"] = self.cipher.open_json(
                    row["view_cipher"], aad=f"message-view:{row['id']}"
                )
                if row["feedback_verdict"]:
                    item["assistant"]["feedback"] = {
                        "message_id": row["id"],
                        "verdict": row["feedback_verdict"],
                        "tags": json.loads(row["feedback_tags"]),
                        "note": self.cipher.open_text(
                            row["feedback_note_cipher"],
                            aad=f"feedback-note:{row['id']}",
                        )
                        if row["feedback_note_cipher"]
                        else None,
                        "updated_at": row["feedback_updated_at"],
                    }
            messages.append(item)
        return {
            "messages": messages,
            "next_cursor": cursor + limit if has_more else None,
        }

    def upsert_feedback(
        self,
        *,
        message_id: str,
        tester_id: str,
        verdict: str,
        tags: list[str],
        note: str | None,
    ) -> dict[str, Any]:
        if note and contains_sensitive_input(note):
            raise ValueError("feedback notes cannot contain personal identifiers")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT m.id FROM messages m
                JOIN chat_sessions s ON s.id=m.session_id
                WHERE m.id=? AND m.role='assistant' AND s.tester_id=?
                """,
                (message_id, tester_id),
            ).fetchone()
        if row is None:
            raise NotFound("assistant message not found")
        note_cipher = (
            self.cipher.seal(note, aad=f"feedback-note:{message_id}") if note else None
        )
        now = _now()
        with self._lock, self._connection:
            previous = self._connection.execute(
                "SELECT LENGTH(note_cipher) FROM feedback WHERE message_id=?",
                (message_id,),
            ).fetchone()
            previous_size = int(previous[0] or 0) if previous is not None else 0
            self._enforce_ciphertext_quota(
                tester_id, max(0, (len(note_cipher) if note_cipher else 0) - previous_size)
            )
            self._connection.execute(
                """
                INSERT INTO feedback VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                  verdict=excluded.verdict, tags_json=excluded.tags_json,
                  note_cipher=excluded.note_cipher, updated_at=excluded.updated_at
                """,
                (message_id, tester_id, verdict, json.dumps(tags), note_cipher, now),
            )
        return {"message_id": message_id, "verdict": verdict, "tags": tags, "updated_at": now}

    def admit_usage(
        self,
        tester_id: str,
        *,
        per_minute: int,
        per_day: int,
        global_per_minute: int,
        global_per_day: int,
        total: int,
    ) -> None:
        now = _now()
        minute = now - 60
        day = now - 86_400
        with self._lock, self._connection:
            counts = self._connection.execute(
                """
                SELECT
                  SUM(CASE WHEN tester_id=? AND attempted_at>? THEN 1 ELSE 0 END) tm,
                  SUM(CASE WHEN tester_id=? AND attempted_at>? THEN 1 ELSE 0 END) td,
                  SUM(CASE WHEN attempted_at>? THEN 1 ELSE 0 END) gm,
                  SUM(CASE WHEN attempted_at>? THEN 1 ELSE 0 END) gd
                FROM usage_ledger
                """,
                (tester_id, minute, tester_id, day, minute, day),
            ).fetchone()
            pilot_started = self._connection.execute(
                "SELECT total_started FROM pilot_counters WHERE id=1"
            ).fetchone()["total_started"]
            values = [counts[key] or 0 for key in ("tm", "td", "gm", "gd")] + [
                pilot_started
            ]
            limits = [per_minute, per_day, global_per_minute, global_per_day, total]
            if any(value >= limit for value, limit in zip(values, limits, strict=True)):
                raise RateLimited(60)
            self._connection.execute(
                "INSERT INTO usage_ledger(tester_id, attempted_at, outcome) VALUES(?, ?, 'started')",
                (tester_id, now),
            )
            self._connection.execute(
                "UPDATE pilot_counters SET total_started=total_started+1 WHERE id=1"
            )

    def record_usage_outcome(self, tester_id: str, outcome: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT id FROM usage_ledger
                WHERE tester_id=? AND outcome='started'
                ORDER BY id DESC LIMIT 1
                """,
                (tester_id,),
            ).fetchone()
            if row:
                self._connection.execute(
                    "UPDATE usage_ledger SET outcome=? WHERE id=?", (outcome, row["id"])
                )

    def export_session(self, session_id: str, tester_id: str) -> dict[str, Any]:
        session = self.get_session(session_id, tester_id)
        page = self.list_messages(session_id, tester_id, cursor=0, limit=10_000)
        with self._lock:
            feedback_rows = self._connection.execute(
                """
                SELECT f.* FROM feedback f
                JOIN messages m ON m.id=f.message_id
                WHERE m.session_id=? AND f.tester_id=?
                """,
                (session_id, tester_id),
            ).fetchall()
        feedback = []
        for row in feedback_rows:
            feedback.append(
                {
                    "message_id": row["message_id"],
                    "verdict": row["verdict"],
                    "tags": json.loads(row["tags_json"]),
                    "note": self.cipher.open_text(
                        row["note_cipher"], aad=f"feedback-note:{row['message_id']}"
                    )
                    if row["note_cipher"]
                    else None,
                    "updated_at": row["updated_at"],
                }
            )
        return {
            "format_version": "1.0",
            "session": session,
            "messages": page["messages"],
            "feedback": feedback,
            "privacy": {
                "raw_engine_response_included": False,
                "clarification_tokens_included": False,
                "private_trace_included": False,
            },
        }

    def delete_session(self, session_id: str, tester_id: str, *, reason: str = "user") -> int:
        self._owned_session(session_id, tester_id)
        deleted = self._delete_session_record(session_id, reason=reason)
        if deleted is None:
            raise NotFound("session not found")
        return deleted

    def _delete_session_record(self, session_id: str, *, reason: str) -> int | None:
        """Delete one already-authorized session, including an expired session."""

        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM chat_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if exists is None:
                return None
            count = self._connection.execute(
                "SELECT COUNT(*) count FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()["count"]
            self._connection.execute(
                "INSERT OR REPLACE INTO deletion_audit VALUES(?, ?, ?, ?)",
                (self.hasher.digest("deleted-session", session_id), _now(), count, reason),
            )
            self._connection.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        return count

    def purge_expired(self) -> int:
        now = _now()
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, tester_id FROM chat_sessions WHERE expires_at<=?", (now,)
            ).fetchall()
        count = 0
        for row in rows:
            if self._delete_session_record(row["id"], reason="retention") is not None:
                count += 1
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM message_requests "
                "WHERE status='pending' AND lease_expires_at<=?",
                (now,),
            )
            self._connection.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
            self._connection.execute("DELETE FROM usage_ledger WHERE attempted_at<?", (now - 86_400,))
            self._connection.execute(
                """
                DELETE FROM testers
                WHERE id NOT IN (SELECT tester_id FROM auth_sessions)
                  AND id NOT IN (SELECT tester_id FROM chat_sessions)
                """
            )
            self._connection.execute(
                "DELETE FROM invites WHERE expires_at<=? OR revoked=1", (now,)
            )
        return count

    def plaintext_columns(self) -> Iterable[tuple[str, str]]:
        """Expose the intentional plaintext schema for compliance tests."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.name table_name, p.name column_name
                FROM sqlite_master m JOIN pragma_table_info(m.name) p
                WHERE m.type='table' ORDER BY m.name, p.cid
                """
            ).fetchall()
        return [(row["table_name"], row["column_name"]) for row in rows]
