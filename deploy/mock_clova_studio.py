#!/usr/bin/env python3
"""Local mock of the CLOVA Studio v3 Chat Completions Structured Outputs API.

Development/test tool only — never part of the contest submission runtime.
It reproduces the official request/response contract used by
``app.planner.hcx.HCXStructuredPlanner`` (endpoint path, auth header, status
code envelope, ``result.message.content`` JSON string, ``finishReason``,
``usage``) so the application can run with ``PLANNER_MODE=hcx`` end to end
before the real ``CLOVA_STUDIO_API_KEY`` arrives.

Instead of returning a canned plan, this mock interprets the incoming user
question with the repository's deterministic planner, so the full HCX-mode
pipeline (HTTP adapter, retry, schema validation, plan guards, DuckDB
execution, evidence, clarification) is exercised with realistic plans.

Run:
    .venv/Scripts/python.exe -m uvicorn deploy.mock_clova_studio:app \
        --host 127.0.0.1 --port 8099 --no-access-log
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app.planner.deterministic import DeterministicPlanner  # noqa: E402

APPROVED_MODEL_ID = "HCX-007"

app = FastAPI(title="Mock CLOVA Studio (dev only)", docs_url=None, redoc_url=None)
_planner = DeterministicPlanner()


def _clova_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": {"code": code, "message": message}},
    )


@app.post("/v3/chat-completions/{model_id}")
async def chat_completions(model_id: str, request: Request) -> JSONResponse:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) <= len("Bearer "):
        return _clova_error(401, "40100", "Unauthorized")
    if model_id != APPROVED_MODEL_ID:
        return _clova_error(404, "40400", f"Unknown model: {model_id}")

    body: dict[str, Any] = await request.json()
    response_format = body.get("responseFormat") or {}
    if response_format.get("type") != "json" or "schema" not in response_format:
        return _clova_error(400, "40000", "responseFormat json+schema is required")

    question = ""
    for message in body.get("messages", []):
        if message.get("role") == "user":
            question = str(message.get("content", ""))
    if not question.strip():
        return _clova_error(400, "40000", "user message is required")

    # Optional fault injection for retry-path testing.
    fail_status = os.getenv("MOCK_HCX_FORCE_STATUS", "").strip()
    if fail_status:
        return _clova_error(int(fail_status), fail_status + "00", "forced failure")

    plan = await _planner.plan(question)
    content = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    prompt_tokens = max(1, len(question) // 2)
    completion_tokens = max(1, len(content) // 2)
    return JSONResponse(
        {
            "status": {"code": "20000", "message": "OK"},
            "result": {
                "message": {"role": "assistant", "content": content},
                "finishReason": "stop",
                "usage": {
                    "promptTokens": prompt_tokens,
                    "completionTokens": completion_tokens,
                    "totalTokens": prompt_tokens + completion_tokens,
                },
            },
        }
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mock": "clova-studio", "model": APPROVED_MODEL_ID}
