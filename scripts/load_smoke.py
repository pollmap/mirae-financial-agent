#!/usr/bin/env python3
"""Bounded concurrent HTTP load smoke for the public evaluation endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import httpx

QUESTIONS = (
    "채권 코드 KR101501DA16의 상세 정보를 알려줘.",
    "국내 ETF만 대상으로 1년 수익률이 높은 3개를 알려줘.",
    "해외 ETF 중 AUM이 큰 3개를 알려줘.",
    "판매중인 정상 펀드 상품은 몇 개인가?",
    "국내 ETF와 공모펀드 상품은 각각 몇 개인가?",
    "나한테 가장 적합한 국내 ETF 하나 추천해줘.",
)
RESPONSE_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(args.concurrency)
    latencies_ms: list[float] = []
    failures: list[str] = []

    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        limits=limits,
        timeout=timeout,
        trust_env=False,
    ) as client:
        ready = await client.get("/health/ready")
        if ready.status_code != 200:
            return {
                "status": "failed",
                "requests": args.requests,
                "concurrency": args.concurrency,
                "failures": [f"readiness HTTP {ready.status_code}"],
            }

        async def one(index: int) -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.get(
                        "/answer",
                        params={
                            "question_id": f"LOAD-{index + 1:05d}",
                            "question": QUESTIONS[index % len(QUESTIONS)],
                        },
                    )
                    if response.status_code != 200:
                        failures.append(f"request {index + 1}: HTTP {response.status_code}")
                        return
                    body = response.json()
                    if set(body) != RESPONSE_KEYS:
                        failures.append(f"request {index + 1}: response contract mismatch")
                        return
                    context = json.loads(body["retrieved_context"])
                    if not context.get("answerability"):
                        failures.append(f"request {index + 1}: missing answerability")
                except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    failures.append(f"request {index + 1}: {type(exc).__name__}")
                finally:
                    latencies_ms.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(args.requests)))
        elapsed = time.perf_counter() - started

    result = {
        "status": "ok" if not failures else "failed",
        "requests": args.requests,
        "concurrency": args.concurrency,
        "completed": len(latencies_ms),
        "failure_count": len(failures),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(args.requests / elapsed, 2) if elapsed else 0.0,
        "latency_ms": {
            "p50": round(_percentile(latencies_ms, 0.50), 2),
            "p95": round(_percentile(latencies_ms, 0.95), 2),
            "p99": round(_percentile(latencies_ms, 0.99), 2),
            "max": round(max(latencies_ms, default=0.0), 2),
        },
        "failures": failures[:20],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.requests <= 10_000:
        parser.error("--requests must be 1..10000")
    if not 1 <= args.concurrency <= min(args.requests, 200):
        parser.error("--concurrency must be 1..min(requests, 200)")

    result = asyncio.run(_run(args))
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if result["status"] != "ok":
        sys.exit(1)


if __name__ == "__main__":
    main()
