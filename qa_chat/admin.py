"""Host-only administration for invites and retention operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qa_chat.config import QASettings
from qa_chat.crypto import CipherBox, TokenHasher
from qa_chat.repository import QARepository


def _repository(settings: QASettings) -> QARepository:
    return QARepository(
        settings.database_path,
        CipherBox(settings.transcript_key),
        TokenHasher(settings.auth_secret),
        retention_days=settings.retention_days,
        max_sessions_per_tester=settings.max_sessions_per_tester,
        max_messages_per_session=settings.max_messages_per_session,
        max_ciphertext_bytes_per_tester=settings.max_ciphertext_bytes_per_tester,
        max_ciphertext_bytes_total=settings.max_ciphertext_bytes_total,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirae human-QA host administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    issue = subparsers.add_parser("issue-invite")
    issue.add_argument("--hours", type=int, default=24)
    revoke = subparsers.add_parser("revoke-invite")
    revoke.add_argument(
        "code",
        nargs="?",
        help="omit to read the raw invite code from stdin and keep it out of shell history",
    )
    subparsers.add_parser("purge")
    backup = subparsers.add_parser("backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()

    settings = QASettings.from_env()
    repository = _repository(settings)
    try:
        if args.command == "issue-invite":
            code = repository.create_invite(
                expires_in_seconds=args.hours * 3_600
            )
            # The raw code is intentionally printed only once to the invoking terminal.
            print(json.dumps({"invite_code": code, "expires_in_hours": args.hours}))
        elif args.command == "revoke-invite":
            code = (args.code or sys.stdin.readline()).strip()
            if not code:
                raise SystemExit("a raw invite code is required on stdin")
            print(json.dumps({"revoked": repository.revoke_invite(code)}))
        elif args.command == "purge":
            print(json.dumps({"purged_sessions": repository.purge_expired()}))
        elif args.command == "backup":
            destination = args.destination.resolve()
            if destination == settings.database_path.resolve():
                raise SystemExit("backup destination cannot be the live QA database")
            if destination.exists():
                raise SystemExit("refusing to overwrite an existing backup")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # SQLite's backup API creates a consistent snapshot while WAL is active.
            import sqlite3

            with sqlite3.connect(destination) as target:
                repository._connection.backup(target)  # noqa: SLF001 - host-only admin boundary
            print(json.dumps({"backup": str(destination)}))
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
