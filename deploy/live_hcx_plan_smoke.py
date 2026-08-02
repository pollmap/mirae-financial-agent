#!/usr/bin/env python3
"""Perform one explicit live HCX structured-plan call and print only safe metadata."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.models import QueryPlan  # noqa: E402
from app.planner.hcx import HCXError, HCXStructuredPlanner  # noqa: E402
from app.planner.schema import HCX_QUERY_PLAN_SCHEMA, QUERY_PLANNER_SYSTEM_PROMPT  # noqa: E402

OFFICIAL_BASE_URL = "https://clovastudio.stream.ntruss.com"
APPROVED_MODEL_ID = "HCX-007"
SAFE_TEST_QUESTION = "국내 ETF 중 1년 수익률이 높은 3개를 찾기 위한 조회 계획을 만들어 줘."


async def _run() -> dict[str, object]:
    key = os.getenv("CLOVA_STUDIO_API_KEY", "").strip()
    model_id = os.getenv("HCX_MODEL_ID", APPROVED_MODEL_ID).strip()
    base_url = os.getenv("HCX_BASE_URL", OFFICIAL_BASE_URL).rstrip("/")
    if len(key.encode("utf-8")) < 20:
        raise SystemExit("CLOVA_STUDIO_API_KEY is missing or too short (value redacted)")
    if model_id != APPROVED_MODEL_ID:
        raise SystemExit(f"HCX_MODEL_ID must be {APPROVED_MODEL_ID}")
    if base_url != OFFICIAL_BASE_URL:
        raise SystemExit("HCX_BASE_URL must be the approved official HTTPS endpoint")

    planner = HCXStructuredPlanner(
        api_key=key,
        model_id=model_id,
        base_url=base_url,
        timeout=float(os.getenv("HCX_TIMEOUT_SECONDS", "12")),
        max_retries=int(os.getenv("HCX_MAX_RETRIES", "2")),
    )
    try:
        result = await planner.create_plan_result(
            question=SAFE_TEST_QUESTION,
            schema=HCX_QUERY_PLAN_SCHEMA,
            validator=QueryPlan,
            system_prompt=QUERY_PLANNER_SYSTEM_PROMPT,
            max_completion_tokens=1_024,
        )
    finally:
        await planner.aclose()
    plan = result.plan.model_dump(mode="json")
    return {
        "status": "PASS",
        "model_id": result.model_id,
        "intent": plan["intent"],
        "scopes": plan["scopes"],
        "metrics": plan["metrics"],
        "needs_clarification": plan["needs_clarification"],
        "usage": dict(result.usage),
        "secret_values_recorded": False,
        "question_recorded": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make exactly one live HCX plan-only call; this can consume paid quota."
    )
    parser.add_argument(
        "--confirm-live-call",
        action="store_true",
        help="required acknowledgement that one real HCX request will be sent",
    )
    args = parser.parse_args()
    if not args.confirm_live_call:
        raise SystemExit("refusing live HCX call without --confirm-live-call")
    try:
        result = asyncio.run(_run())
    except HCXError as exc:
        raise SystemExit(f"live HCX plan smoke failed safely ({type(exc).__name__})") from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
