"""FastAPI application for the local/LAN human QA gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from qa_chat.config import QASettings
from qa_chat.crypto import CipherBox, TokenHasher
from qa_chat.engine_client import EngineClient
from qa_chat.models import (
    CreateSessionRequest,
    FeedbackRequest,
    MessageRequest,
    RedeemInviteRequest,
)
from qa_chat.repository import (
    Conflict,
    Forbidden,
    InvalidInvite,
    NotFound,
    QARepository,
    RateLimited,
)
from qa_chat.service import PilotDisabled, QAGatewayService, ReleaseGatePending

LOGGER = logging.getLogger("mirae.qa")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _session_public(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session["id"],
        "mode": session["mode"],
        "scenario_id": session["scenario_id"],
        "title": session["title"],
        "session_version": session["version"],
        "created_at": _iso(session["created_at"]),
        "updated_at": _iso(session["updated_at"]),
        "expires_at": _iso(session["expires_at"]),
    }


def _markdown_export(payload: dict[str, Any]) -> str:
    session = payload["session"]
    lines = [
        f"# {session['title']}",
        "",
        "> 팀 내부 인간 검증 기록입니다. 투자자문이 아닙니다.",
        "",
    ]
    feedback_by_message = {item["message_id"]: item for item in payload["feedback"]}
    for message in payload["messages"]:
        label = "테스터" if message["role"] == "user" else "금융상품 Agent"
        lines.extend([f"## {label} · Turn {message['turn_id']}", "", message["content"], ""])
        if message["role"] == "assistant":
            assistant = message.get("assistant") or {}
            lines.append(
                f"상태: {assistant.get('status', 'UNKNOWN')} / "
                f"근거 판정: {assistant.get('answerability', 'UNKNOWN')}"
            )
            feedback = feedback_by_message.get(message["id"])
            if feedback:
                lines.append(
                    f"피드백: {feedback['verdict']}"
                    + (f" ({', '.join(feedback['tags'])})" if feedback["tags"] else "")
                )
            lines.append("")
    lines.extend(
        [
            "---",
            "Raw engine response, clarification token, private trace는 포함하지 않았습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def create_app(
    settings: QASettings | None = None,
    *,
    engine_client: EngineClient | None = None,
    repository: QARepository | None = None,
) -> FastAPI:
    resolved = settings or QASettings.from_env()
    resolved.validate()
    cipher = CipherBox(resolved.transcript_key)
    hasher = TokenHasher(resolved.auth_secret)
    repo = repository or QARepository(
        resolved.database_path,
        cipher,
        hasher,
        retention_days=resolved.retention_days,
        max_sessions_per_tester=resolved.max_sessions_per_tester,
        max_messages_per_session=resolved.max_messages_per_session,
        max_ciphertext_bytes_per_tester=resolved.max_ciphertext_bytes_per_tester,
        max_ciphertext_bytes_total=resolved.max_ciphertext_bytes_total,
    )
    engine = engine_client or EngineClient(
        resolved.engine_base_url, timeout_seconds=resolved.engine_timeout_seconds
    )
    service = QAGatewayService(resolved, repo, engine)

    purge_task: asyncio.Task[None] | None = None

    async def purge_loop() -> None:
        while True:
            await asyncio.sleep(86_400)
            await asyncio.to_thread(repo.purge_expired)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal purge_task
        await asyncio.to_thread(repo.purge_expired)
        purge_task = asyncio.create_task(purge_loop())
        try:
            yield
        finally:
            if purge_task:
                purge_task.cancel()
            await engine.close()
            repo.close()

    app = FastAPI(
        title="Mirae Financial Agent Human QA",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.repository = repo
    app.state.engine_client = engine
    app.state.gateway_service = service

    @app.middleware("http")
    async def secure_and_measure(request: Request, call_next):
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            LOGGER.info(
                json.dumps(
                    {
                        "event": "qa_http",
                        "request_id": request_id,
                        "method": request.method,
                        "route": getattr(request.scope.get("route"), "path", "unmatched"),
                        "status": status,
                        "content_length": int(request.headers.get("content-length", "0") or 0),
                        "latency_ms": round((time.perf_counter() - started) * 1_000, 2),
                    },
                    separators=(",", ":"),
                )
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST"})

    @app.exception_handler(StarletteHTTPException)
    async def framework_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return JSONResponse(status_code=404, content={"error": "NOT_FOUND"})
        return JSONResponse(status_code=exc.status_code, content={"error": "HTTP_ERROR"})

    @app.exception_handler(InvalidInvite)
    async def invalid_invite(_: Request, __: InvalidInvite) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "INVALID_INVITE"})

    @app.exception_handler(Forbidden)
    async def forbidden(_: Request, __: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"error": "FORBIDDEN"})

    @app.exception_handler(NotFound)
    async def not_found(_: Request, __: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "NOT_FOUND"})

    @app.exception_handler(Conflict)
    async def conflict(_: Request, __: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": "CONFLICT"})

    @app.exception_handler(RateLimited)
    async def limited(_: Request, exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"error": "RATE_LIMITED", "retry_after": exc.retry_after},
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(PilotDisabled)
    async def disabled(_: Request, __: PilotDisabled) -> JSONResponse:
        return JSONResponse(status_code=423, content={"error": "PILOT_DISABLED"})

    @app.exception_handler(ReleaseGatePending)
    async def gate_pending(_: Request, __: ReleaseGatePending) -> JSONResponse:
        return JSONResponse(status_code=423, content={"error": "RELEASE_GATE_PENDING"})

    @app.exception_handler(ValueError)
    async def invalid_value(_: Request, __: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST"})

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        LOGGER.warning(
            json.dumps(
                {"event": "qa_unhandled", "exception_type": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR"})

    def verify_origin(request: Request) -> None:
        if not resolved.require_origin:
            return
        origin = request.headers.get("origin", "").rstrip("/")
        if not origin or origin not in resolved.allowed_origins:
            raise Forbidden("origin is not allowed")

    def authenticate(request: Request, *, csrf: bool = False) -> dict[str, str]:
        session_token = request.cookies.get(resolved.cookie_name, "")
        if not csrf:
            return repo.authenticate(session_token)
        verify_origin(request)
        header_token = request.headers.get("x-csrf-token", "")
        cookie_token = request.cookies.get(resolved.csrf_cookie_name, "")
        if not header_token or not cookie_token or not secrets.compare_digest(
            header_token, cookie_token
        ):
            raise Forbidden("CSRF double-submit validation failed")
        return repo.authenticate(session_token, header_token)

    prefix = "/qa/api/v1"

    @app.post(f"{prefix}/invites/redeem")
    async def redeem(request: Request, body: RedeemInviteRequest) -> JSONResponse:
        verify_origin(request)
        auth = repo.redeem_invite(
            body.code, body.consent_version, auth_ttl_seconds=resolved.auth_ttl_seconds
        )
        response = JSONResponse(
            content={
                "tester": {"id": auth["tester_id"], "alias": auth["alias"]},
                "csrf_token": auth["csrf_token"],
                "retention_days": resolved.retention_days,
            }
        )
        response.set_cookie(
            resolved.cookie_name,
            auth["session_token"],
            max_age=resolved.auth_ttl_seconds,
            httponly=True,
            secure=resolved.cookie_secure,
            samesite="strict",
            path="/",
        )
        response.set_cookie(
            resolved.csrf_cookie_name,
            auth["csrf_token"],
            max_age=resolved.auth_ttl_seconds,
            httponly=False,
            secure=resolved.cookie_secure,
            samesite="strict",
            path="/",
        )
        return response

    @app.post(f"{prefix}/logout")
    async def logout(request: Request) -> JSONResponse:
        authenticate(request, csrf=True)
        repo.logout(request.cookies.get(resolved.cookie_name, ""))
        response = JSONResponse(content={"status": "logged_out"})
        response.delete_cookie(resolved.cookie_name, path="/")
        response.delete_cookie(resolved.csrf_cookie_name, path="/")
        return response

    @app.get(f"{prefix}/me")
    async def me(request: Request) -> dict[str, Any]:
        tester = authenticate(request)
        return {
            "tester": {"id": tester["id"], "alias": tester["alias"]},
            "consent_version": tester["consent_version"],
            "retention_days": resolved.retention_days,
        }

    @app.post(f"{prefix}/sessions")
    async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
        tester = authenticate(request, csrf=True)
        session = repo.create_session(tester["id"], body.mode, body.scenario_id)
        return {"session": _session_public(session)}

    @app.get(f"{prefix}/sessions")
    async def list_sessions(request: Request) -> dict[str, Any]:
        tester = authenticate(request)
        return {"sessions": [_session_public(item) for item in repo.list_sessions(tester["id"])]}

    @app.get(f"{prefix}/sessions/{{session_id}}")
    async def get_session(
        request: Request,
        session_id: str,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        tester = authenticate(request)
        session = repo.get_session(session_id, tester["id"])
        page = repo.list_messages(session_id, tester["id"], cursor=cursor, limit=limit)
        for message in page["messages"]:
            message["created_at"] = _iso(message["created_at"])
        return {"session": _session_public(session), **page}

    @app.post(f"{prefix}/sessions/{{session_id}}/messages")
    async def post_message(
        request: Request, session_id: str, body: MessageRequest
    ) -> dict[str, Any]:
        tester = authenticate(request, csrf=True)
        return await service.submit_message(tester["id"], session_id, body)

    @app.put(f"{prefix}/messages/{{message_id}}/feedback")
    async def put_feedback(
        request: Request, message_id: str, body: FeedbackRequest
    ) -> dict[str, Any]:
        tester = authenticate(request, csrf=True)
        return {"feedback": service.save_feedback(tester["id"], message_id, body)}

    @app.get(f"{prefix}/sessions/{{session_id}}/export")
    async def export_session(
        request: Request,
        session_id: str,
        format: str = Query(pattern=r"^(json|markdown)$"),
    ) -> Response:
        tester = authenticate(request)
        payload = repo.export_session(session_id, tester["id"])
        payload["session"] = _session_public(payload["session"])
        for message in payload["messages"]:
            message["created_at"] = _iso(message["created_at"])
        if format == "json":
            return JSONResponse(
                content=payload,
                headers={"Content-Disposition": f'attachment; filename="qa-{session_id}.json"'},
            )
        return PlainTextResponse(
            _markdown_export(payload),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="qa-{session_id}.md"'},
        )

    @app.delete(f"{prefix}/sessions/{{session_id}}", status_code=204)
    async def delete_session(request: Request, session_id: str) -> Response:
        tester = authenticate(request, csrf=True)
        repo.delete_session(session_id, tester["id"])
        return Response(status_code=204)

    @app.get(f"{prefix}/status")
    async def status() -> dict[str, Any]:
        engine_state = await service.runtime_readiness()
        release_gate_error = resolved.release_gate_error
        release_ready = release_gate_error is None
        if not resolved.pilot_chat_enabled:
            public_status = "DISABLED"
        elif (
            engine_state.get("status") != "ready"
            or not engine_state.get("identity_verified")
            or not release_ready
        ):
            public_status = "DEGRADED"
        else:
            public_status = "READY"
        environment = {
            **service.environment,
            "data_snapshot_date": str(engine_state.get("data_snapshot_date") or "unknown"),
        }
        return {
            "status": public_status,
            "ready": public_status == "READY",
            "pilot_chat_enabled": resolved.pilot_chat_enabled,
            "retention_days": resolved.retention_days,
            "release_gate": "PASS" if release_ready else "PENDING_EXTERNAL",
            "release_gate_reason": release_gate_error,
            "environment": environment,
            "engine": engine_state,
        }

    frontend = Path(__file__).resolve().parents[1] / "qa_web" / "dist"
    if frontend.is_dir():
        index = frontend / "index.html"

        @app.get("/", include_in_schema=False)
        async def frontend_index() -> FileResponse:
            return FileResponse(index, media_type="text/html; charset=utf-8")

        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="qa-assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend_spa(path: str) -> FileResponse:
            if path.startswith("qa/api/") or path in {
                "answer",
                "demo",
                "docs",
                "openapi.json",
                "redoc",
            }:
                raise NotFound("API route not found")
            return FileResponse(index, media_type="text/html; charset=utf-8")
    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={"status": "frontend_not_built", "api_status": f"{prefix}/status"},
            )

    return app
