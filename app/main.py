"""FastAPI public compatibility endpoint for organizer evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.clarification import ClarificationTokenError
from app.config import Settings
from app.execution.engine import DuckDBEngine
from app.planner.service import build_planner
from app.retrieval.vector_retriever import ClovaQueryEmbedder
from app.service import AgentService, PlannerUnavailable

REQUEST_LOGGER = logging.getLogger("mirae.request")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    resolved.validate()
    planner = build_planner(resolved)
    query_embedder = (
        ClovaQueryEmbedder(resolved.clova_studio_api_key)
        if resolved.vector_enabled and resolved.clova_studio_api_key
        else None
    )
    engine = DuckDBEngine(
        resolved.database_path,
        vector_enabled=resolved.vector_enabled,
        query_embedder=query_embedder,
    )
    service = AgentService(resolved, planner, engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await service.aclose()

    app = FastAPI(
        title="Mirae Asset Financial Product Agent",
        version="1.3.0",
        docs_url=None if resolved.environment == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.service = service
    readiness_data_hash: str | None = None
    readiness_hash_lock = asyncio.Lock()
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.middleware("http")
    async def privacy_safe_request_metrics(request: Request, call_next):
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            if request.url.path == "/answer":
                question = request.query_params.get("question", "")
                question_id = request.query_params.get("question_id", "")
                REQUEST_LOGGER.info(
                    json.dumps(
                        {
                            "event": "answer_request",
                            # Never log query contents or deterministic hashes:
                            # a small evaluation-question dictionary can be
                            # reversed by offline guessing. Lengths are enough
                            # for basic abuse/latency diagnostics.
                            "question_chars": len(question),
                            "question_id_chars": len(question_id),
                            "status_code": status_code,
                            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        },
                        separators=(",", ":"),
                    )
                )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": "INVALID_REQUEST",
                "detail": "필수 parameter와 길이를 확인해 주세요.",
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        """Return a redacted contract-shaped failure without leaking internals.

        Starlette's ServerErrorMiddleware re-raises after this handler runs,
        so uvicorn's own error logger still records the full exception
        (traceback, message, file paths) to container stderr -- that's
        intentional upstream behavior for ops visibility, and this handler
        doesn't fight it. What it adds is a second, guaranteed-safe line via
        this app's own logger: exception *type* only, never str(exc) or
        request content, so a triage signal survives even if some future
        raise site is ever changed to interpolate user-controlled text
        (question, entity label) into its message.
        """

        REQUEST_LOGGER.warning(
            json.dumps(
                {"event": "unhandled_exception", "exception_type": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        question_id = request.query_params.get("question_id", "unknown")[:200] or "unknown"
        question = request.query_params.get("question", "처리 중 오류가 발생했습니다.")[:2000]
        payload = {
            "question_id": question_id,
            "question": question,
            "retrieved_context": json.dumps(
                {
                    "answerability": "UNAVAILABLE",
                    "reason_code": "INTERNAL_EXECUTION_ERROR",
                },
                ensure_ascii=False,
            ),
            "think_trace": "status=controlled_internal_error; details=redacted",
            "answer": "요청을 안전하게 처리하지 못했습니다. 동일 요청을 다시 시도해 주세요.",
        }
        return JSONResponse(status_code=500, content=payload)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        nonlocal readiness_data_hash
        readiness_error = engine.readiness_error()
        if readiness_error:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": readiness_error},
            )
        if readiness_data_hash is None:
            async with readiness_hash_lock:
                if readiness_data_hash is None:
                    readiness_data_hash = await asyncio.to_thread(
                        _sha256_file, resolved.database_path
                    )
        return JSONResponse(
            content={
                "status": "ready",
                "data_snapshot_date": "2026-07-11",
                "engine_git_sha": resolved.engine_git_sha,
                "engine_image_digest": resolved.engine_image_digest,
                "data_hash": readiness_data_hash,
                "model_id": resolved.hcx_model_id,
                "hcx_base_url": resolved.hcx_base_url,
                "planner_stage": resolved.planner_stage,
            }
        )

    @app.get("/answer")
    async def answer(
        question_id: str = Query(min_length=1, max_length=200, pattern=r".*\S.*"),
        question: str = Query(
            min_length=1,
            max_length=resolved.question_max_chars,
            pattern=r".*\S.*",
        ),
        clarification_token: str | None = Query(default=None, max_length=10000),
        clarification_response: str | None = Query(default=None, max_length=500),
    ) -> JSONResponse:
        if not question.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "INVALID_QUESTION", "detail": "question은 공백일 수 없습니다."},
            )
        try:
            result = await service.answer(
                question_id=question_id,
                question=question,
                clarification_token=clarification_token,
                clarification_response=clarification_response,
            )
        except PlannerUnavailable:
            payload = {
                "question_id": question_id,
                "question": question,
                "retrieved_context": json.dumps(
                    {
                        "answerability": "UNAVAILABLE",
                        "reason_code": "HCX_TEMPORARILY_UNAVAILABLE",
                    },
                    ensure_ascii=False,
                ),
                "think_trace": (
                    f"planner={service.planner.name}; "
                    "status=controlled_unavailable; fallback_llm=none"
                ),
                "answer": "현재 HyperCLOVA X 질의 해석을 일시적으로 사용할 수 없습니다. 다른 언어모델로 대체하지 않았습니다.",
            }
            return JSONResponse(status_code=503, content=payload)
        except ClarificationTokenError:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "INVALID_CLARIFICATION",
                    "detail": "역질문 후속 parameter 또는 토큰을 확인해 주세요.",
                },
            )
        return JSONResponse(content=result.model_dump(mode="json"))

    # The page is a minimal public client for the same five-field API.  It
    # never receives a key and deliberately hides trace/context internals; the
    # evaluator can continue to call GET /answer directly.
    web_page = Path(__file__).resolve().parents[1] / "web" / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/demo", include_in_schema=False)
    async def web_client() -> FileResponse:
        return FileResponse(web_page, media_type="text/html; charset=utf-8")

    return app


app = create_app()
