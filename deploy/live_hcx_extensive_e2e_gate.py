#!/usr/bin/env python3
"""Run the credential-gated, extensive HyperCLOVA X release gate.

The 100-case gate is a fast operational smoke test.  This gate is deliberately
larger: it makes 1,000 direct semantic requests which must each use the actual
HCX two-stage planner, then exercises 100 two-follow-up and 100
three-follow-up clarification conversations through the public ``/answer``
contract.  The direct cases are judged against the independent SQL oracle;
conversation finals are compared with the deterministic planner plus their
source-row evidence signature.  Its retained report contains only totals and
one digest -- never questions, prompts, plans, answers, tokens, identifiers,
or credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from eval.oracle import Oracle  # noqa: E402
from eval.run_eval import fill_runtime_slots, score_response  # noqa: E402
from eval.templates import generate  # noqa: E402

DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
OFFICIAL_BASE_URL = "https://clovastudio.stream.ntruss.com"
APPROVED_MODEL_ID = "HCX-007"
DIRECT_KINDS = ("rank_single", "filter_search", "count_aggregate", "cross_scope")
DIRECT_SURFACES = ("base", "official_evidence")
DIRECT_CASE_COUNT = 1_000
TWO_FOLLOW_UP_FLOW_COUNT = 100
THREE_FOLLOW_UP_FLOW_COUNT = 100
FLOW_COUNT = TWO_FOLLOW_UP_FLOW_COUNT + THREE_FOLLOW_UP_FLOW_COUNT
FLOW_API_REQUEST_COUNT = TWO_FOLLOW_UP_FLOW_COUNT * 3 + THREE_FOLLOW_UP_FLOW_COUNT * 4
LIVE_API_REQUEST_COUNT = DIRECT_CASE_COUNT + FLOW_API_REQUEST_COUNT
MINIMUM_ACCURACY = 0.98
RESPONSE_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


# These forms preserve the same objective request while varying sentence
# structure, imperative, and evidence instruction.  They intentionally never
# name a market or return period: the point is to make the signed clarification
# state carry those decisions across realistic later turns.
_TWO_FOLLOW_UP_FORMS = (
    "수익률이 높은 ETF {limit}개 알려줘.",
    "수익률 높은 ETF {limit}개 보여줘.",
    "수익률 기준 상위 ETF {limit}개 정리해줘.",
    "수익률이 좋은 ETF {limit}개를 확인해줘.",
    "수익률이 높은 ETF {limit}개 목록을 보여줘.",
    "수익률이 높은 ETF {limit}개를 정리해줘.",
    "수익률이 높은 ETF 상품 {limit}개 알려줘.",
    "수익률 기준으로 ETF {limit}개 보여줘.",
    "ETF 중 수익률이 높은 상품 {limit}개 정리해줘.",
    "수익률이 높은 순서로 ETF {limit}개 알려줘.",
)
_THREE_FOLLOW_UP_FORMS = (
    "수익률이 높고 보수가 낮은 ETF {limit}개 알려줘.",
    "수익률 높고 보수 낮은 ETF {limit}개 보여줘.",
    "수익률이 높은 ETF 중 보수가 낮은 상품 {limit}개 정리해줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개 목록을 알려줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개 결과를 보여줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개를 자세히 알려줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개 결과를 알려줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개를 같이 알려줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개 순위를 정리해줘.",
    "수익률이 높고 보수가 낮은 ETF {limit}개를 근거와 함께 보여줘.",
)


def _evidence_surface(question: str) -> str:
    """Add a source/evidence instruction without changing query semantics."""

    return f"공식 제공 데이터만 기준으로, 근거를 함께 확인해 주세요. {question}"


def build_direct_cases(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand the 500 HCX-eligible semantic specifications to 1,000 inputs."""

    eligible = [case for case in questions if str(case["kind"]) in DIRECT_KINDS]
    if len(eligible) != DIRECT_CASE_COUNT // len(DIRECT_SURFACES):
        raise RuntimeError("extensive gate requires exactly 500 HCX-eligible base cases")

    expanded: list[dict[str, Any]] = []
    for surface in DIRECT_SURFACES:
        for case in eligible:
            question = str(case["question"])
            expanded.append(
                {
                    "id": f"EXT-{case['id']}-{surface}",
                    "question": question if surface == "base" else _evidence_surface(question),
                    "kind": str(case["kind"]),
                    "surface": surface,
                    "spec": dict(case["spec"]),
                }
            )
    if len(expanded) != DIRECT_CASE_COUNT:
        raise AssertionError("unexpected direct extensive gate case count")
    return expanded


def build_clarification_flows() -> list[dict[str, Any]]:
    """Return 100 two-follow-up and 100 three-follow-up stateful scenarios."""

    flows: list[dict[str, Any]] = []
    for index, template in enumerate(_TWO_FOLLOW_UP_FORMS):
        for limit in range(3, 13):
            flows.append(
                {
                    "id": f"FLOW-2-{index:02d}-{limit:02d}",
                    "type": "two_follow_up",
                    "question": template.format(limit=limit),
                    "steps": [
                        {"slot": "market", "value": "domestic_etp"},
                        {"slot": "return_period", "value": "1y"},
                    ],
                }
            )
    for index, template in enumerate(_THREE_FOLLOW_UP_FORMS):
        for limit in range(3, 13):
            flows.append(
                {
                    "id": f"FLOW-3-{index:02d}-{limit:02d}",
                    "type": "three_follow_up",
                    "question": template.format(limit=limit),
                    "steps": [
                        {"slot": "market", "value": "domestic_etp"},
                        {"slot": "return_period", "value": "1y"},
                        {"slot": "ranking_priority", "value": "domestic_etp.return_1y"},
                    ],
                }
            )
    counts = Counter(str(flow["type"]) for flow in flows)
    if counts != {
        "two_follow_up": TWO_FOLLOW_UP_FLOW_COUNT,
        "three_follow_up": THREE_FOLLOW_UP_FLOW_COUNT,
    }:
        raise AssertionError("unexpected clarification flow category count")
    return flows


def _suite_hash(direct_cases: list[dict[str, Any]], flows: list[dict[str, Any]]) -> str:
    """Digest inputs without retaining the reversible test corpus in a report."""

    canonical = {
        "direct": [
            {
                "id": str(case["id"]),
                "question": str(case["question"]),
                "kind": str(case["kind"]),
                "spec": case["spec"],
            }
            for case in direct_cases
        ],
        "flows": [
            {
                "id": str(flow["id"]),
                "question": str(flow["question"]),
                "type": str(flow["type"]),
                "steps": flow["steps"],
            }
            for flow in flows
        ],
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _response_context(response: httpx.Response) -> tuple[dict[str, Any] | None, bool]:
    """Validate the public contract without retaining any response payload."""

    if response.status_code != 200:
        return None, False
    try:
        payload = response.json()
        if set(payload) != RESPONSE_KEYS or not all(isinstance(value, str) for value in payload.values()):
            return None, False
        return json.loads(payload["retrieved_context"]), True
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, False


def _items_have_evidence(context: dict[str, Any]) -> bool:
    items = context.get("items") or []
    return all(
        isinstance(item, dict)
        and isinstance(item.get("fields"), list)
        and any(
            isinstance(field, dict)
            and bool(field.get("source_row_hash"))
            and bool(field.get("source_file"))
            for field in item["fields"]
        )
        for item in items
    )


def _evidence_signature(context: dict[str, Any]) -> str:
    """Compare final answers without retaining names, ids, values, or rows."""

    items = context.get("items") or []
    anchors = []
    for item in items:
        if not isinstance(item, dict):
            anchors.append(["invalid"])
            continue
        anchors.append(
            [
                str(item.get("scope", "")),
                str(item.get("source_file", "")),
                str(item.get("source_sheet", "")),
                str(item.get("source_excel_row", "")),
                str(item.get("source_row_hash", "")),
            ]
        )
    canonical = {
        "answerability": str(context.get("answerability", "")),
        "reason_code": str(context.get("reason_code", "")),
        "result_count": int(context.get("result_count", -1)),
        "anchors": sorted(anchors),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _run_flow(
    client: httpx.AsyncClient,
    flow: dict[str, Any],
    *,
    question_id_prefix: str,
) -> dict[str, object]:
    """Exercise a complete signed follow-up flow and return only safe metrics."""

    question = str(flow["question"])
    steps = list(flow["steps"])
    request_count = 0
    hcx_response_count = 0
    contract_valid_response_count = 0
    final_hcx_used = False
    final_evidence_linked = False
    try:
        response = await client.get(
            "/answer", params={"question_id": f"{question_id_prefix}-0", "question": question}
        )
        request_count += 1
        for index, step in enumerate(steps, start=1):
            context, contract_valid = _response_context(response)
            contract_valid_response_count += int(contract_valid)
            if context is None:
                return {
                    "passed": False,
                    "request_count": request_count,
                    "hcx_response_count": hcx_response_count,
                    "contract_valid_response_count": contract_valid_response_count,
                    "final_hcx_used": False,
                    "final_evidence_linked": False,
                    "final_signature": None,
                }
            trace = response.json()["think_trace"]
            hcx_response_count += int("planner=HCX-007" in trace)
            clarification = context.get("clarification")
            if (
                context.get("answerability") != "NEEDS_CLARIFICATION"
                or not isinstance(clarification, dict)
                or clarification.get("missing_slots") != [step["slot"]]
            ):
                return {
                    "passed": False,
                    "request_count": request_count,
                    "hcx_response_count": hcx_response_count,
                    "contract_valid_response_count": contract_valid_response_count,
                    "final_hcx_used": False,
                    "final_evidence_linked": False,
                    "final_signature": None,
                }
            options = clarification.get("options") or []
            option_values = {
                option.get("value") for option in options if isinstance(option, dict)
            }
            token = clarification.get("clarification_token")
            if step["value"] not in option_values or not isinstance(token, str) or not token:
                return {
                    "passed": False,
                    "request_count": request_count,
                    "hcx_response_count": hcx_response_count,
                    "contract_valid_response_count": contract_valid_response_count,
                    "final_hcx_used": False,
                    "final_evidence_linked": False,
                    "final_signature": None,
                }
            response = await client.get(
                "/answer",
                params={
                    "question_id": f"{question_id_prefix}-{index}",
                    "question": question,
                    "clarification_token": token,
                    "clarification_response": step["value"],
                },
            )
            request_count += 1

        context, contract_valid = _response_context(response)
        contract_valid_response_count += int(contract_valid)
        if context is None:
            return {
                "passed": False,
                "request_count": request_count,
                "hcx_response_count": hcx_response_count,
                "contract_valid_response_count": contract_valid_response_count,
                "final_hcx_used": False,
                "final_evidence_linked": False,
                "final_signature": None,
            }
        trace = response.json()["think_trace"]
        final_hcx_used = "planner=HCX-007" in trace
        hcx_response_count += int(final_hcx_used)
        final_evidence_linked = _items_have_evidence(context)
        passed = (
            context.get("answerability") in {"FULL", "PARTIAL_WITH_COVERAGE", "NO_RESULT"}
            and context.get("clarification") is None
        )
        return {
            "passed": passed,
            "request_count": request_count,
            "hcx_response_count": hcx_response_count,
            "contract_valid_response_count": contract_valid_response_count,
            "final_hcx_used": final_hcx_used,
            "final_evidence_linked": final_evidence_linked,
            "final_signature": _evidence_signature(context) if passed else None,
        }
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "passed": False,
            "request_count": request_count,
            "hcx_response_count": hcx_response_count,
            "contract_valid_response_count": contract_valid_response_count,
            "final_hcx_used": final_hcx_used,
            "final_evidence_linked": final_evidence_linked,
            "final_signature": None,
        }


def _live_settings(key: str, model_id: str, base_url: str) -> Settings:
    return Settings(
        environment="test",
        database_path=DATABASE,
        planner_mode="hcx",
        planner_stage="two",
        clova_studio_api_key=key,
        hcx_model_id=model_id,
        hcx_base_url=base_url,
        hcx_timeout_seconds=float(os.getenv("HCX_TIMEOUT_SECONDS", "12")),
        hcx_total_deadline_seconds=float(os.getenv("HCX_TOTAL_DEADLINE_SECONDS", "25")),
        hcx_max_retries=int(os.getenv("HCX_MAX_RETRIES", "2")),
        hcx_max_concurrency=1,
        hcx_qpm_limit=int(os.getenv("HCX_QPM_LIMIT", "60")),
        hcx_tpm_budget=int(os.getenv("HCX_TPM_BUDGET", "60000")),
        clarification_signing_key="live-hcx-extensive-gate-state-key-rotate-in-production",
    )


def _deterministic_settings() -> Settings:
    return Settings(
        environment="test",
        database_path=DATABASE,
        planner_mode="deterministic",
        planner_stage="two",
        clarification_signing_key="live-hcx-extensive-gate-state-key-rotate-in-production",
    )


async def _run_local_verify() -> dict[str, object]:
    """Re-run the full corpus locally before a key or quota is available."""

    if not DATABASE.is_file():
        raise SystemExit("serving database is missing; rebuild official data before local verification")
    direct_cases = build_direct_cases(generate())
    flows = build_clarification_flows()
    suite_hash = _suite_hash(direct_cases, flows)
    app = create_app(_deterministic_settings())
    direct_passed = 0
    direct_evidence_linked = 0
    direct_contract_valid = 0
    direct_by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    flow_passed = Counter[str]()
    flow_evidence_final = Counter[str]()
    flow_contract_valid = 0
    flow_api_requests = 0
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            with Oracle(DATABASE) as oracle:
                all_cases = fill_runtime_slots(generate(), oracle.sample_codes(per_scope=16))
                cases_by_id = {str(case["id"]): case for case in all_cases}
                for case in direct_cases:
                    base_id = str(case["id"]).removeprefix("EXT-").rsplit("-", 1)[0]
                    source_case = cases_by_id.get(base_id)
                    kind = str(case["kind"])
                    direct_by_kind[kind]["total"] += 1
                    response = await client.get(
                        "/answer",
                        params={"question_id": str(case["id"]), "question": str(case["question"])},
                    )
                    context, contract_valid = _response_context(response)
                    direct_contract_valid += int(contract_valid)
                    passed = False
                    if context is not None and source_case is not None:
                        expected = oracle.expected(dict(source_case["spec"]))
                        scored = score_response(
                            dict(source_case["spec"]),
                            expected,
                            response.json()["answer"] + " " + response.json()["retrieved_context"],
                            context,
                        )
                        passed = bool(scored["passed"])
                        direct_evidence_linked += int(_items_have_evidence(context))
                    if passed:
                        direct_passed += 1
                        direct_by_kind[kind]["passed"] += 1

            for index, flow in enumerate(flows, start=1):
                result = await _run_flow(client, flow, question_id_prefix=f"LOCAL-FLOW-{index:03d}")
                flow_type = str(flow["type"])
                flow_api_requests += int(result["request_count"])
                flow_contract_valid += int(result["contract_valid_response_count"])
                if bool(result["passed"]):
                    flow_passed[flow_type] += 1
                if bool(result["final_evidence_linked"]):
                    flow_evidence_final[flow_type] += 1
    finally:
        await app.state.service.aclose()

    status = "PASS" if (
        direct_passed == DIRECT_CASE_COUNT
        and direct_evidence_linked == DIRECT_CASE_COUNT
        and direct_contract_valid == DIRECT_CASE_COUNT
        and flow_passed["two_follow_up"] == TWO_FOLLOW_UP_FLOW_COUNT
        and flow_passed["three_follow_up"] == THREE_FOLLOW_UP_FLOW_COUNT
        and flow_evidence_final["two_follow_up"] == TWO_FOLLOW_UP_FLOW_COUNT
        and flow_evidence_final["three_follow_up"] == THREE_FOLLOW_UP_FLOW_COUNT
        and flow_contract_valid == FLOW_API_REQUEST_COUNT
        and flow_api_requests == FLOW_API_REQUEST_COUNT
    ) else "FAIL"
    return {
        "status": status,
        "gate": "LOCAL_EXTENSIVE_1000_DIRECT_PLUS_MULTITURN",
        "planner_mode": "deterministic",
        "question_suite_sha256": suite_hash,
        "direct_case_count": DIRECT_CASE_COUNT,
        "direct_passed_count": direct_passed,
        "direct_evidence_linked_case_count": direct_evidence_linked,
        "direct_contract_valid_response_count": direct_contract_valid,
        "direct_by_kind": {
            kind: {"total": values["total"], "passed": values["passed"]}
            for kind, values in sorted(direct_by_kind.items())
        },
        "two_follow_up_flow_count": TWO_FOLLOW_UP_FLOW_COUNT,
        "three_follow_up_flow_count": THREE_FOLLOW_UP_FLOW_COUNT,
        "two_follow_up_flow_passed_count": flow_passed["two_follow_up"],
        "three_follow_up_flow_passed_count": flow_passed["three_follow_up"],
        "two_follow_up_final_evidence_count": flow_evidence_final["two_follow_up"],
        "three_follow_up_final_evidence_count": flow_evidence_final["three_follow_up"],
        "flow_contract_valid_response_count": flow_contract_valid,
        "flow_api_request_count": flow_api_requests,
        "questions_recorded": False,
        "secrets_recorded": False,
    }


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
    if not DATABASE.is_file():
        raise SystemExit("serving database is missing; rebuild official data before live gate")

    direct_cases = build_direct_cases(generate())
    flows = build_clarification_flows()
    suite_hash = _suite_hash(direct_cases, flows)
    live_app = create_app(_live_settings(key, model_id, base_url))
    baseline_app = create_app(_deterministic_settings())

    direct_failures = 0
    direct_evidence_linked = 0
    direct_hcx_planned = 0
    cross_scope_refusals = 0
    direct_contract_valid = 0
    direct_by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    baseline_flow_passed = 0
    flow_passed = Counter[str]()
    flow_hcx_final = Counter[str]()
    flow_evidence_final = Counter[str]()
    flow_signature_match = Counter[str]()
    flow_contract_valid = 0
    flow_api_requests = 0
    flow_hcx_responses = 0

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=baseline_app), base_url="http://baseline"
        ) as baseline_client:
            baseline_results = [
                await _run_flow(
                    baseline_client, flow, question_id_prefix=f"BASE-{index:03d}"
                )
                for index, flow in enumerate(flows, start=1)
            ]
        baseline_flow_passed = sum(bool(result["passed"]) for result in baseline_results)

        # Never spend a live HCX quota on a malformed local test corpus.  The
        # report still records a failed gate with no question data.
        if baseline_flow_passed == FLOW_COUNT:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=live_app), base_url="http://live"
            ) as live_client:
                with Oracle(DATABASE) as oracle:
                    all_cases = fill_runtime_slots(generate(), oracle.sample_codes(per_scope=16))
                    cases_by_id = {str(case["id"]): case for case in all_cases}
                    for case in direct_cases:
                        base_id = str(case["id"]).removeprefix("EXT-").rsplit("-", 1)[0]
                        source_case = cases_by_id.get(base_id)
                        kind = str(case["kind"])
                        direct_by_kind[kind]["total"] += 1
                        try:
                            response = await live_client.get(
                                "/answer",
                                params={"question_id": str(case["id"]), "question": str(case["question"])},
                            )
                            context, contract_valid = _response_context(response)
                            direct_contract_valid += int(contract_valid)
                            if context is None or source_case is None:
                                passed = False
                            else:
                                expected = oracle.expected(dict(source_case["spec"]))
                                scored = score_response(
                                    dict(source_case["spec"]),
                                    expected,
                                    response.json()["answer"]
                                    + " "
                                    + response.json()["retrieved_context"],
                                    context,
                                )
                                hcx_used = "planner=HCX-007" in response.json()["think_trace"]
                                direct_hcx_planned += int(hcx_used)
                                direct_evidence_linked += int(_items_have_evidence(context))
                                if (
                                    kind == "cross_scope"
                                    and str(context.get("answerability")) == "INCOMPARABLE"
                                ):
                                    cross_scope_refusals += 1
                                passed = bool(scored["passed"]) and hcx_used
                        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
                            passed = False
                        if passed:
                            direct_by_kind[kind]["passed"] += 1
                        else:
                            direct_failures += 1

                for index, (flow, baseline_result) in enumerate(
                    zip(flows, baseline_results, strict=True), start=1
                ):
                    flow_type = str(flow["type"])
                    live_result = await _run_flow(
                        live_client, flow, question_id_prefix=f"LIVE-FLOW-{index:03d}"
                    )
                    flow_api_requests += int(live_result["request_count"])
                    flow_hcx_responses += int(live_result["hcx_response_count"])
                    flow_contract_valid += int(live_result["contract_valid_response_count"])
                    signatures_match = (
                        bool(baseline_result["passed"])
                        and bool(live_result["passed"])
                        and baseline_result["final_signature"] == live_result["final_signature"]
                    )
                    if signatures_match:
                        flow_passed[flow_type] += 1
                        flow_signature_match[flow_type] += 1
                    if bool(live_result["final_hcx_used"]):
                        flow_hcx_final[flow_type] += 1
                    if bool(live_result["final_evidence_linked"]):
                        flow_evidence_final[flow_type] += 1
    finally:
        await live_app.state.service.aclose()
        await baseline_app.state.service.aclose()

    direct_passed = DIRECT_CASE_COUNT - direct_failures
    direct_accuracy = direct_passed / DIRECT_CASE_COUNT
    total_live_api_requests = DIRECT_CASE_COUNT + flow_api_requests
    all_flow_types_passed = (
        flow_passed["two_follow_up"] == TWO_FOLLOW_UP_FLOW_COUNT
        and flow_passed["three_follow_up"] == THREE_FOLLOW_UP_FLOW_COUNT
        and flow_hcx_final["two_follow_up"] == TWO_FOLLOW_UP_FLOW_COUNT
        and flow_hcx_final["three_follow_up"] == THREE_FOLLOW_UP_FLOW_COUNT
        and flow_evidence_final["two_follow_up"] == TWO_FOLLOW_UP_FLOW_COUNT
        and flow_evidence_final["three_follow_up"] == THREE_FOLLOW_UP_FLOW_COUNT
    )
    status = "PASS" if (
        direct_accuracy >= MINIMUM_ACCURACY
        and direct_hcx_planned == DIRECT_CASE_COUNT
        and direct_evidence_linked == DIRECT_CASE_COUNT
        and cross_scope_refusals == 0
        and direct_contract_valid == DIRECT_CASE_COUNT
        and baseline_flow_passed == FLOW_COUNT
        and all_flow_types_passed
        and flow_contract_valid == FLOW_API_REQUEST_COUNT
        and total_live_api_requests == LIVE_API_REQUEST_COUNT
    ) else "FAIL"
    report: dict[str, object] = {
        "status": status,
        "gate": "HCX_EXTENSIVE_1000_DIRECT_PLUS_MULTITURN_E2E",
        "model_id": model_id,
        "approved_planner_stage": "two",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "question_suite_sha256": suite_hash,
        "direct_case_count": DIRECT_CASE_COUNT,
        "direct_semantic_spec_count": DIRECT_CASE_COUNT // len(DIRECT_SURFACES),
        "direct_surface_count_per_spec": len(DIRECT_SURFACES),
        "minimum_accuracy": MINIMUM_ACCURACY,
        "direct_passed_count": direct_passed,
        "direct_accuracy": round(direct_accuracy, 4),
        "direct_hcx_planned_case_count": direct_hcx_planned,
        "direct_evidence_linked_case_count": direct_evidence_linked,
        "direct_contract_valid_response_count": direct_contract_valid,
        "cross_scope_refusal_count": cross_scope_refusals,
        "direct_by_kind": {
            kind: {"total": values["total"], "passed": values["passed"]}
            for kind, values in sorted(direct_by_kind.items())
        },
        "two_follow_up_flow_count": TWO_FOLLOW_UP_FLOW_COUNT,
        "three_follow_up_flow_count": THREE_FOLLOW_UP_FLOW_COUNT,
        "baseline_flow_passed_count": baseline_flow_passed,
        "two_follow_up_flow_passed_count": flow_passed["two_follow_up"],
        "three_follow_up_flow_passed_count": flow_passed["three_follow_up"],
        "two_follow_up_final_hcx_count": flow_hcx_final["two_follow_up"],
        "three_follow_up_final_hcx_count": flow_hcx_final["three_follow_up"],
        "two_follow_up_final_evidence_count": flow_evidence_final["two_follow_up"],
        "three_follow_up_final_evidence_count": flow_evidence_final["three_follow_up"],
        "two_follow_up_signature_match_count": flow_signature_match["two_follow_up"],
        "three_follow_up_signature_match_count": flow_signature_match["three_follow_up"],
        "flow_contract_valid_response_count": flow_contract_valid,
        "flow_live_api_request_count": flow_api_requests,
        "flow_hcx_planned_response_count": flow_hcx_responses,
        "live_api_request_count": total_live_api_requests,
        "secret_values_recorded": False,
        "questions_recorded": False,
        "prompts_recorded": False,
        "plans_recorded": False,
        "answers_recorded": False,
        "clarification_tokens_recorded": False,
        "product_identifiers_recorded": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Make 1,000 confirmed direct live HCX requests plus 200 multi-turn API flows; "
            "this can consume quota."
        )
    )
    parser.add_argument(
        "--confirm-direct-hcx-calls",
        type=int,
        metavar="COUNT",
        help=f"must equal {DIRECT_CASE_COUNT}; each direct case must use HCX",
    )
    parser.add_argument(
        "--confirm-api-requests",
        type=int,
        metavar="COUNT",
        help=f"must equal {LIVE_API_REQUEST_COUNT}; includes all clarification turns",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print only safe suite counts and digest; never reads a credential or calls HCX",
    )
    parser.add_argument(
        "--local-verify",
        action="store_true",
        help="run all 1,000 direct and 200 multi-turn cases with the deterministic planner; no key/HCX",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "live_hcx_extensive_e2e_gate_report.json",
    )
    args = parser.parse_args()
    if args.dry_run and args.local_verify:
        raise SystemExit("choose only one of --dry-run or --local-verify")
    direct_cases = build_direct_cases(generate())
    flows = build_clarification_flows()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "gate": "HCX_EXTENSIVE_1000_DIRECT_PLUS_MULTITURN_E2E",
                    "direct_case_count": len(direct_cases),
                    "direct_semantic_spec_count": DIRECT_CASE_COUNT // len(DIRECT_SURFACES),
                    "two_follow_up_flow_count": TWO_FOLLOW_UP_FLOW_COUNT,
                    "three_follow_up_flow_count": THREE_FOLLOW_UP_FLOW_COUNT,
                    "live_api_request_count": LIVE_API_REQUEST_COUNT,
                    "question_suite_sha256": _suite_hash(direct_cases, flows),
                    "questions_recorded": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if args.local_verify:
        result = asyncio.run(_run_local_verify())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result["status"] != "PASS":
            raise SystemExit(1)
        return
    if args.confirm_direct_hcx_calls != DIRECT_CASE_COUNT:
        raise SystemExit(
            "refusing extensive live gate without "
            f"--confirm-direct-hcx-calls {DIRECT_CASE_COUNT}"
        )
    if args.confirm_api_requests != LIVE_API_REQUEST_COUNT:
        raise SystemExit(
            "refusing extensive live gate without "
            f"--confirm-api-requests {LIVE_API_REQUEST_COUNT}"
        )
    result = asyncio.run(_run(args.output.resolve()))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
