#!/usr/bin/env python3
"""Run the explicit 20-question HCX one-stage/two-stage release gate.

The report deliberately contains neither questions nor generated plans. It stores
only validation/match counts and token totals, so it is safe to retain as release
evidence after the operator has injected the real credential.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.models import QueryPlan  # noqa: E402
from app.planner.hcx import HCXError, HCXStructuredPlanner  # noqa: E402
from app.planner.schema import (  # noqa: E402
    HCX_QUERY_PLAN_SCHEMA,
    HCX_SEMANTIC_PLAN_SCHEMA,
    QUERY_PLANNER_SYSTEM_PROMPT,
    SEMANTIC_PLANNER_SYSTEM_PROMPT,
)
from app.semantics.grounder import ground_semantic  # noqa: E402

OFFICIAL_BASE_URL = "https://clovastudio.stream.ntruss.com"
APPROVED_MODEL_ID = "HCX-007"
QUESTION_COUNT = 20
EXPECTED_PROVIDER_CALLS = QUESTION_COUNT * 2

# Fixed, non-private release questions. They cover each planner family without
# embedding any submitted evaluation question or user information in the report.
LIVE_GATE_QUESTIONS = (
    "국내 ETF 중 1년 수익률이 높은 3개를 찾아줘.",
    "해외 ETF 중 보수가 낮은 5개를 보여줘.",
    "국내 채권 중 발행사가 한국전력공사인 상품을 찾아줘.",
    "공모펀드 중 투자지역이 국내인 상품 5개를 찾아줘.",
    "국내 ETF 중 운용사가 삼성자산운용인 상품을 보여줘.",
    "해외 ETF 중 벤치마크에 S&P가 포함된 상품을 찾아줘.",
    "공모펀드 중 벤치마크에 KOSPI가 포함된 상품을 찾아줘.",
    "국내 ETF와 해외 ETF에서 1년 수익률이 높은 상품을 비교해줘.",
    "채권과 공모펀드의 상품 수를 상품군별로 집계해줘.",
    "국내 ETF의 평균 1년 수익률을 계산해줘.",
    "해외 ETF의 최대 순자산을 계산해줘.",
    "공모펀드의 최소 보수를 계산해줘.",
    "국내 채권의 발행금액 합계를 계산해줘.",
    "국내 ETF와 해외 ETF의 평균 보수를 상품군별로 비교해줘.",
    "A069500 상품을 조회해줘.",
    "KR7000010002 상품의 표면금리를 알려줘.",
    "TIGER 미국S&P500 상품을 설명해줘.",
    "quality factor 전략의 해외 ETF를 찾아줘.",
    "ETF 중 국내인지 해외인지 지정하지 않은 상태에서 수익률을 알려줘.",
    "이 데이터에 없는 가상화폐 선물 상품을 추천해줘.",
)
QUESTION_SUITE_SHA256 = hashlib.sha256(
    "\n".join(LIVE_GATE_QUESTIONS).encode("utf-8")
).hexdigest()


def _canonical_plan(plan: QueryPlan) -> dict[str, Any]:
    """Return an order-normalized, non-secret signature for A/B comparison."""

    payload = plan.model_dump(mode="json", exclude={"question"})
    for key in ("scopes", "metrics", "group_by"):
        payload[key] = sorted(payload.get(key) or [])
    payload["entities"] = sorted(
        payload.get("entities") or [], key=lambda item: json.dumps(item, sort_keys=True)
    )
    payload["sort"] = sorted(
        payload.get("sort") or [], key=lambda item: json.dumps(item, sort_keys=True)
    )
    payload["aggregations"] = sorted(
        payload.get("aggregations") or [], key=lambda item: json.dumps(item, sort_keys=True)
    )
    return payload


def _usage_total(target: Counter[str], usage: dict[str, int] | Any) -> None:
    for key in ("promptTokens", "completionTokens", "totalTokens"):
        value = usage.get(key) if hasattr(usage, "get") else None
        if isinstance(value, int) and not isinstance(value, bool):
            target[key] += value


async def _run(report_path: Path) -> dict[str, object]:
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
    stage_one_usage: Counter[str] = Counter()
    stage_two_usage: Counter[str] = Counter()
    cases: list[dict[str, object]] = []
    try:
        for index, question in enumerate(LIVE_GATE_QUESTIONS, start=1):
            one = await planner.create_plan_result(
                question=question,
                schema=HCX_QUERY_PLAN_SCHEMA,
                validator=QueryPlan,
                system_prompt=QUERY_PLANNER_SYSTEM_PROMPT,
                max_completion_tokens=1_024,
            )
            two = await planner.create_plan_result(
                question=question,
                schema=HCX_SEMANTIC_PLAN_SCHEMA,
                validator=lambda payload, q=question: ground_semantic(payload, question=q),
                system_prompt=SEMANTIC_PLANNER_SYSTEM_PROMPT,
                max_completion_tokens=1_024,
            )
            _usage_total(stage_one_usage, one.usage)
            _usage_total(stage_two_usage, two.usage)
            cases.append(
                {
                    "case_id": f"LIVE-{index:02d}",
                    "stage_one_valid": True,
                    "stage_two_valid": True,
                    "canonical_match": _canonical_plan(one.plan) == _canonical_plan(two.plan),
                }
            )
    finally:
        await planner.aclose()

    matches = sum(bool(case["canonical_match"]) for case in cases)
    report: dict[str, object] = {
        "status": "PASS" if matches == QUESTION_COUNT else "FAIL",
        "gate": "HCX_20_QUESTION_ONE_VS_TWO_STAGE",
        "model_id": model_id,
        "approved_planner_stage": "two",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "question_suite_sha256": QUESTION_SUITE_SHA256,
        "case_count": QUESTION_COUNT,
        "provider_call_count": EXPECTED_PROVIDER_CALLS,
        "both_stage_valid_count": len(cases),
        "both_stage_match_count": matches,
        "cases": cases,
        "usage": {
            "stage_one": dict(stage_one_usage),
            "stage_two": dict(stage_two_usage),
        },
        "secret_values_recorded": False,
        "questions_recorded": False,
        "plans_recorded": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make exactly 40 live HCX plan-only calls for the 20-question A/B gate."
    )
    parser.add_argument(
        "--confirm-live-calls",
        type=int,
        metavar="COUNT",
        help=f"must equal {EXPECTED_PROVIDER_CALLS}; requests can consume paid quota",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "live_hcx_gate_report.json",
    )
    args = parser.parse_args()
    if args.confirm_live_calls != EXPECTED_PROVIDER_CALLS:
        raise SystemExit(
            f"refusing live HCX gate without --confirm-live-calls {EXPECTED_PROVIDER_CALLS}"
        )
    try:
        result = asyncio.run(_run(args.output.resolve()))
    except HCXError as exc:
        raise SystemExit(f"live HCX gate failed safely ({type(exc).__name__})") from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
