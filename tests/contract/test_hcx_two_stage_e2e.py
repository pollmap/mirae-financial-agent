"""Two-stage planner (PLANNER_STAGE=two) contract test against a mock HCX.

Mirrors ``test_hcx_app_e2e.py`` but the mock server returns a Stage-1
*semantic* plan (concepts only, no physical field/metric names) and asserts
the request actually used the smaller semantic schema/prompt, then that the
server-side grounder (``app/semantics/grounder.py``) turned it into a correct
physical result through the unmodified single-scope and cross-scope
executors. This is the concrete "gold full A/B" gate from the W2 plan: both
planner_stage values must reach the same answer for the same question.
"""

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
from app.planner.schema import HCX_SEMANTIC_PLAN_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"


@contextmanager
def _mock_hcx(plan: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(size))
            seen.update({"path": self.path, "body": body})
            payload = {
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(plan, ensure_ascii=False),
                    },
                    "finishReason": "stop",
                    "usage": {"promptTokens": 40, "completionTokens": 30, "totalTokens": 70},
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


async def _ask(app, question_id: str, question: str) -> httpx.Response:
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(
                "/answer",
                params={"question_id": question_id, "question": question},
            )
    finally:
        await app.state.service.aclose()


def test_two_stage_semantic_plan_grounds_to_single_scope_lookup() -> None:
    semantic_plan = {
        "intent": "lookup",
        "scope_concepts": ["bond"],
        "metric_concepts": [],
        "aggregations": [],
        "group_by_concepts": [],
        "filters": [],
        "sort_direction": "none",
        "top_n": 5,
        "entities": ["KR101501DA16"],
        "needs_clarification": False,
        "clarification_question": "",
    }
    with _mock_hcx(semantic_plan) as (base_url, seen):
        settings = Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            planner_stage="two",
            hcx_base_url=base_url,
            clova_studio_api_key="mock-service-key",
            hcx_timeout_seconds=3,
            hcx_max_retries=1,
        )
        app = create_app(settings)
        response = asyncio.run(
            _ask(
                app,
                "HCX-STAGE2-LOOKUP",
                # 이 상품 그 자체(this product) so no exact-identifier text
                # reaches the pre-router; the code only appears in the
                # mock's canned Stage-1 `entities` field, same pattern as
                # test_hcx_app_e2e.py.
                "이 채권 상품의 위험등급과 발행기관을 알려줘.",
            )
        )

    assert response.status_code == 200
    payload = response.json()
    context = json.loads(payload["retrieved_context"])
    assert context["answerability"] == "FULL"
    assert context["items"][0]["product_uid"] == "BOND:PRBD01N001:KR101501DA16"
    assert "planner=HCX-007" in payload["think_trace"]

    # Stage-1 request used the smaller concept-only schema, not the physical
    # 59-metric HCX_QUERY_PLAN_SCHEMA.
    sent_schema = seen["body"]["responseFormat"]["schema"]
    assert sent_schema == HCX_SEMANTIC_PLAN_SCHEMA
    assert "scope_concepts" in sent_schema["properties"]
    assert "metrics" not in sent_schema["properties"]


def test_two_stage_cross_scope_unified_rank_matches_stage_one_result() -> None:
    """Both planner stages answer the flagship cross-scope query identically."""

    from app.execution.engine import DuckDBEngine
    from app.planner.deterministic import DeterministicPlanner
    from app.service import AgentService

    question = "국내 ETF와 공모펀드를 합쳐 1년 수익률 높은 3개 알려줘."

    async def stage_one_answer() -> dict[str, Any]:
        service = AgentService(
            Settings(environment="test", database_path=DATABASE, planner_mode="deterministic"),
            DeterministicPlanner(),
            DuckDBEngine(DATABASE),
        )
        response = await service.answer(question_id="STAGE1", question=question)
        await service.aclose()
        return json.loads(response.retrieved_context)

    stage_one_context = asyncio.run(stage_one_answer())
    assert stage_one_context["answerability"] == "PARTIAL_WITH_COVERAGE"
    assert stage_one_context["reason_code"] == "CROSS_SCOPE_SOURCE_LITERAL"
    stage_one_uids = [item["product_uid"] for item in stage_one_context["items"]]

    semantic_plan = {
        "intent": "rank",
        "scope_concepts": ["domestic_etp", "fund"],
        "metric_concepts": ["return_1y"],
        "aggregations": [],
        "group_by_concepts": [],
        "filters": [],
        "sort_direction": "desc",
        "top_n": 3,
        "entities": [],
        "needs_clarification": False,
        "clarification_question": "",
    }
    with _mock_hcx(semantic_plan) as (base_url, _seen):
        settings = Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            planner_stage="two",
            hcx_base_url=base_url,
            clova_studio_api_key="mock-service-key",
            hcx_timeout_seconds=3,
            hcx_max_retries=1,
        )
        app = create_app(settings)
        response = asyncio.run(_ask(app, "HCX-STAGE2-CROSS", question))

    assert response.status_code == 200
    stage_two_context = json.loads(response.json()["retrieved_context"])
    assert stage_two_context["answerability"] == "PARTIAL_WITH_COVERAGE"
    assert stage_two_context["reason_code"] == "CROSS_SCOPE_SOURCE_LITERAL"
    stage_two_uids = [item["product_uid"] for item in stage_two_context["items"]]

    assert stage_two_uids == stage_one_uids
    assert "[교차 상품군 응답: 통합 순위" in response.json()["answer"]


def test_two_stage_aggregate_count_reaches_physical_executor() -> None:
    semantic_plan = {
        "intent": "aggregate",
        "scope_concepts": ["domestic_etp"],
        "metric_concepts": [],
        "aggregations": [{"function": "count", "metric_concept": "", "distinct": True}],
        "group_by_concepts": [],
        "filters": [],
        "sort_direction": "none",
        "top_n": 10,
        "entities": [],
        "needs_clarification": False,
        "clarification_question": "",
    }
    with _mock_hcx(semantic_plan) as (base_url, _seen):
        settings = Settings(
            environment="test",
            database_path=DATABASE,
            planner_mode="hcx",
            planner_stage="two",
            hcx_base_url=base_url,
            clova_studio_api_key="mock-service-key",
            hcx_timeout_seconds=3,
            hcx_max_retries=1,
        )
        app = create_app(settings)
        response = asyncio.run(
            _ask(app, "HCX-STAGE2-AGG", "국내 ETF와 ETN의 전체 상품 수를 집계해줘.")
        )

    assert response.status_code == 200
    context = json.loads(response.json()["retrieved_context"])
    assert context["answerability"] == "FULL"
    assert len(context["aggregates"]) == 1
    aggregate = context["aggregates"][0]
    assert aggregate["aggregate_id"] == "product_count"
    assert int(aggregate["value"]) == 1733
    assert aggregate["source_row_count"] == 1733
