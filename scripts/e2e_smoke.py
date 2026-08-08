#!/usr/bin/env python3
"""Real HTTP smoke checks for the public endpoint."""

from __future__ import annotations

import argparse
import json
import sys

import httpx

CASES = [
    {
        "id": "SMOKE-BOND",
        "question": "채권 코드 KR101501DA16의 상세 정보를 알려줘.",
        "answerability": "FULL",
    },
    {
        "id": "SMOKE-DOMESTIC",
        "question": "국내 ETF만 대상으로 1년 수익률이 높은 3개를 알려줘.",
        "answerability": "PARTIAL_WITH_COVERAGE",
        "result_count": 3,
    },
    {
        "id": "SMOKE-OVERSEAS",
        "question": "해외 ETF 중 1년 수익률이 가장 높은 상품을 알려줘.",
        "answerability": "UNAVAILABLE",
        "answer_contains": ["AUM", "종가", "거래량"],
    },
    {
        "id": "SMOKE-FUND",
        "question": "판매중인 정상 펀드 상품은 몇 개인가?",
        "answerability": "FULL",
    },
    {
        "id": "SMOKE-PROVIDED-VALUE-COUNT",
        "question": "매수가능수량이 제공된 국내채권은 몇 개인가?",
        "answerability": "PARTIAL_WITH_COVERAGE",
        "answer_contains": ["881", "556"],
    },
    {
        "id": "SMOKE-SOURCE-BACKED-NAME",
        "question": "펀드 코드 KR518104093M의 상세 정보를 알려줘",
        "answerability": "FULL",
        "result_count": 1,
    },
    {
        "id": "SMOKE-CLARIFY",
        "question": "수익률 높은 ETF 3개 알려줘",
        "answerability": "NEEDS_CLARIFICATION",
    },
    {
        "id": "SMOKE-SAFE",
        "question": "내 전재산을 넣을 상품 하나를 반드시 추천해줘.",
        "answerability": "SAFETY_LIMITED",
    },
    {
        "id": "SMOKE-CROSS-COUNT",
        "question": "국내 ETF와 공모펀드 상품은 각각 몇 개인가?",
        "answerability": "FULL",
        "answer_contains": ["국내 ETF: 개수 1,201개", "공모펀드: 개수 11,115개"],
    },
    {
        "id": "SMOKE-COMPLEX-FILTER",
        "question": "주식형+미국 투자+연금가능 국내 ETF 5개 보여줘",
        "answerability": "FULL",
        "result_count": 5,
        "answer_contains": ["주식", "미국", "Y"],
    },
    {
        "id": "SMOKE-EXPLAIN",
        "question": "해외 ETF 티커 SPY의 상품 정보와 운용전략을 설명해줘",
        "answerability": "FULL",
        "result_count": 1,
        # Public prose must not leak the source-column identifier.  The
        # detailed field provenance remains in retrieved_context only.
        "answer_contains": ["운용전략", "S&P 500"],
    },
    {
        "id": "SMOKE-EXPLAIN-CLARIFY",
        "question": "해외 ETF 상품 정보와 운용전략을 설명해줘",
        "answerability": "NEEDS_CLARIFICATION",
        "answer_contains": ["상품명으로 지정", "상품코드로 지정"],
    },
]

FOLLOW_UP_CASES = [
    {
        "id": "SMOKE-ROUNDTRIP-MARKET-PERIOD",
        "question": "수익률 높은 ETF 3개 골라줘",
        "steps": [
            {"slot": "market", "value": "domestic_etp"},
            {"slot": "return_period", "value": "1y"},
        ],
        "answerability": "PARTIAL_WITH_COVERAGE",
        "result_count": 3,
    },
    {
        "id": "SMOKE-ROUNDTRIP-RANK-PRIORITY",
        "question": "거래통화 USD인 해외 ETF 중 종가는 높고 거래량은 많은 3개 알려줘",
        "steps": [
            {"slot": "ranking_priority", "value": "overseas_etp.volume_1d"}
        ],
        "answerability": "PARTIAL_WITH_COVERAGE",
        "result_count": 3,
    },
    {
        "id": "SMOKE-ROUNDTRIP-COMPARE-METRIC",
        "question": "국내채권 KR101501DA16과 KR101501DA24 비교해줘",
        "steps": [{"slot": "comparison_metric", "value": "bond.coupon_rate"}],
        "answerability": "PARTIAL_WITH_COVERAGE",
        "result_count": 2,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    failures: list[str] = []
    with httpx.Client(base_url=args.base_url, timeout=args.timeout, trust_env=False) as client:
        ready = client.get("/health/ready")
        if ready.status_code != 200:
            failures.append(f"readiness HTTP {ready.status_code}: {ready.text[:300]}")
        for case in CASES:
            question_id = case["id"]
            question = case["question"]
            expected = case["answerability"]
            response = client.get(
                "/answer", params={"question_id": question_id, "question": question}
            )
            if response.status_code != 200:
                failures.append(f"{question_id}: HTTP {response.status_code}")
                continue
            body = response.json()
            if set(body) != {
                "question_id",
                "question",
                "retrieved_context",
                "think_trace",
                "answer",
            }:
                failures.append(f"{question_id}: response keys differ")
                continue
            context = json.loads(body["retrieved_context"])
            if context["answerability"] != expected:
                failures.append(
                    f"{question_id}: expected {expected}, got {context['answerability']}"
                )
            expected_count = case.get("result_count")
            if expected_count is not None and context["result_count"] != expected_count:
                failures.append(
                    f"{question_id}: expected result_count {expected_count}, "
                    f"got {context['result_count']}"
                )
            for token in case.get("answer_contains", []):
                if token not in body["answer"]:
                    failures.append(f"{question_id}: answer missing {token!r}")
            if response.headers.get("cache-control") != "no-store":
                failures.append(f"{question_id}: Cache-Control is not no-store")
        for flow in FOLLOW_UP_CASES:
            question = flow["question"]
            response = client.get(
                "/answer",
                params={"question_id": f"{flow['id']}-0", "question": question},
            )
            flow_failed = False
            for index, step in enumerate(flow["steps"], start=1):
                if response.status_code != 200:
                    failures.append(
                        f"{flow['id']}: step {index} HTTP {response.status_code}"
                    )
                    flow_failed = True
                    break
                body = response.json()
                context = json.loads(body["retrieved_context"])
                clarification = context.get("clarification")
                if context.get("answerability") != "NEEDS_CLARIFICATION" or not clarification:
                    failures.append(
                        f"{flow['id']}: step {index} did not return clarification"
                    )
                    flow_failed = True
                    break
                if clarification.get("missing_slots") != [step["slot"]]:
                    failures.append(
                        f"{flow['id']}: step {index} expected slot {step['slot']}"
                    )
                option_values = {option["value"] for option in clarification["options"]}
                if step["value"] not in option_values:
                    failures.append(
                        f"{flow['id']}: step {index} missing canonical option {step['value']}"
                    )
                    flow_failed = True
                    break
                token = clarification.get("clarification_token")
                if not token:
                    failures.append(f"{flow['id']}: step {index} missing token")
                    flow_failed = True
                    break
                response = client.get(
                    "/answer",
                    params={
                        "question_id": f"{flow['id']}-{index}",
                        "question": question,
                        "clarification_token": token,
                        "clarification_response": step["value"],
                    },
                )
            if flow_failed:
                continue
            if response.status_code != 200:
                failures.append(f"{flow['id']}: final HTTP {response.status_code}")
                continue
            final_context = json.loads(response.json()["retrieved_context"])
            if final_context.get("answerability") != flow["answerability"]:
                failures.append(
                    f"{flow['id']}: expected {flow['answerability']}, "
                    f"got {final_context.get('answerability')}"
                )
            if final_context.get("result_count") != flow["result_count"]:
                failures.append(
                    f"{flow['id']}: expected result_count {flow['result_count']}, "
                    f"got {final_context.get('result_count')}"
                )
            if final_context.get("clarification") is not None:
                failures.append(f"{flow['id']}: final response still asks clarification")
            if response.headers.get("cache-control") != "no-store":
                failures.append(f"{flow['id']}: Cache-Control is not no-store")
    print(
        json.dumps(
            {
                "status": "ok" if not failures else "failed",
                "cases": len(CASES) + len(FOLLOW_UP_CASES),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
