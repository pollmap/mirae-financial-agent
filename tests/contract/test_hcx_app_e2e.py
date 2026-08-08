from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"


@contextmanager
def _mock_hcx(plan: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(size))
            seen.update(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "request_id": self.headers.get("X-NCP-CLOVASTUDIO-REQUEST-ID"),
                    "body": body,
                }
            )
            payload = {
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(plan, ensure_ascii=False),
                    },
                    "finishReason": "stop",
                    "usage": {"promptTokens": 100, "completionTokens": 80, "totalTokens": 180},
                },
            }
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_endpoint_runs_through_real_hcx_http_adapter_before_duckdb() -> None:
    plan = {
        "version": "1.1",
        "intent": "lookup",
        "scopes": ["bond"],
        "entities": [{"name": "", "code": "KR101501DA16", "scope": "bond"}],
        "filter_groups": [],
        "groups_join": "AND",
        "metrics": [],
        "aggregations": [],
        "sort": [],
        "group_by": [],
        "limit": 10,
        "assumptions": [],
        "needs_clarification": False,
        "clarification_question": "",
        "missing_slots": [],
        "clarification_options": [],
    }
    with _mock_hcx(plan) as (base_url, seen):
        settings = Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            hcx_base_url=base_url,
            clova_studio_api_key="mock-service-key",
            hcx_timeout_seconds=3,
            hcx_max_retries=1,
        )
        app = create_app(settings)

        async def request() -> httpx.Response:
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    return await client.get(
                        "/answer",
                        params={
                            "question_id": "HCX-MOCK-E2E",
                            # No exact identifier in the question text: the
                            # deterministic pre-router (app/planner/pre_router.py)
                            # would otherwise resolve an ISIN-shaped code
                            # straight to a lookup plan and this request would
                            # never reach the HCX adapter under test.  The mock
                            # server below returns the canned `plan` (entity
                            # code KR101501DA16) regardless of the question
                            # text, so this still exercises the same execution
                            # path end to end.
                            "question": "이 국공채 상품의 발행기관과 위험등급을 알려줘.",
                        },
                    )
            finally:
                await app.state.service.aclose()

        response = asyncio.run(request())

    assert response.status_code == 200
    context = json.loads(response.json()["retrieved_context"])
    assert context["answerability"] == "FULL"
    assert context["result_count"] == 1
    assert context["items"][0]["product_uid"] == "BOND:PRBD01N001:KR101501DA16"
    assert "planner=HCX-007" in response.json()["think_trace"]
    assert seen["path"] == "/v3/chat-completions/HCX-007"
    assert seen["authorization"] == "Bearer mock-service-key"
    assert seen["request_id"]
    assert seen["body"]["thinking"] == {"effort": "none"}
    assert seen["body"]["responseFormat"]["type"] == "json"
