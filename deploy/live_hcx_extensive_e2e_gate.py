#!/usr/bin/env python3
"""Run the credential-gated, extensive HyperCLOVA X release gate.

The 100-case gate is a fast operational smoke test.  This gate is deliberately
larger: it makes 1,200 semantically distinct direct requests which must each
use the actual HCX two-stage planner, then exercises 100 two-turn, 100
three-turn, and 100 four-turn clarification conversations through ``/answer``
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
from eval.release_corpus import (  # noqa: E402
    DIRECT_CASE_COUNT,
    DIRECT_CATEGORY_COUNTS,
    build_live_direct_cases,
)
from eval.run_eval import fill_runtime_slots, score_response  # noqa: E402

DATABASE = ROOT / "data" / "serving" / "mirae_agent.duckdb"
OFFICIAL_BASE_URL = "https://clovastudio.stream.ntruss.com"
APPROVED_MODEL_ID = "HCX-007"
TWO_TURN_FLOW_COUNT = 100
THREE_TURN_FLOW_COUNT = 100
FOUR_TURN_FLOW_COUNT = 100
FLOW_COUNT = TWO_TURN_FLOW_COUNT + THREE_TURN_FLOW_COUNT + FOUR_TURN_FLOW_COUNT
FLOW_API_REQUEST_COUNT = (
    TWO_TURN_FLOW_COUNT * 2 + THREE_TURN_FLOW_COUNT * 3 + FOUR_TURN_FLOW_COUNT * 4
)
LIVE_API_REQUEST_COUNT = DIRECT_CASE_COUNT + FLOW_API_REQUEST_COUNT
MINIMUM_ACCURACY = 0.98
RESPONSE_KEYS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def build_direct_cases(_questions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return 1,200 independent semantic specifications from official data."""

    return build_live_direct_cases(DATABASE)


def _flow_codes() -> dict[str, list[str]]:
    with Oracle(DATABASE) as oracle:
        metric_by_scope = {
            "bond": "bond.coupon_rate",
            "domestic_etp": "domestic_etp.return_1y",
            "overseas_etp": "overseas_etp.aum_last",
            "fund": "fund.return_1y",
        }
        codes: dict[str, list[str]] = {}
        for scope, metric_id in metric_by_scope.items():
            currency_clause = ""
            if scope == "overseas_etp":
                currency_clause = (
                    "AND c.trading_currency = ("
                    "SELECT trading_currency FROM product_catalog "
                    "WHERE scope = 'overseas_etp' AND trading_currency IS NOT NULL "
                    "GROUP BY trading_currency ORDER BY COUNT(*) DESC, trading_currency LIMIT 1) "
                )
            rows = oracle.connection.execute(
                "SELECT CAST(c.product_id AS VARCHAR) "
                "FROM product_catalog c JOIN product_metrics m USING(product_uid) "
                "WHERE c.scope = ? AND m.metric_id = ? AND m.value_num IS NOT NULL "
                "AND m.quality_status IN ('PARTIAL','SUSPECT_OUTLIER','VALID','ZERO_VALID') "
                + currency_clause
                + "GROUP BY c.product_uid, c.product_id ORDER BY c.product_uid LIMIT 50",
                [scope, metric_id],
            ).fetchall()
            values = [str(row[0]) for row in rows if row[0] not in (None, "")]
            if len(values) < 40:
                raise RuntimeError(f"insufficient metric-valid flow codes for {scope}: {len(values)}")
            codes[scope] = values
        return codes


def build_clarification_flows() -> list[dict[str, Any]]:
    """Return 300 flows: 60 per scope family and 100 per turn length."""

    codes = _flow_codes()
    flows: list[dict[str, Any]] = []

    def pair(scope: str, index: int) -> str:
        values = codes[scope]
        return f"{values[(2 * index) % len(values)]}와 {values[(2 * index + 1) % len(values)]}"

    def add(
        family: str,
        turns: int,
        index: int,
        question: str,
        steps: list[dict[str, Any]],
    ) -> None:
        flows.append(
            {
                "id": f"FLOW-{family.upper()}-{turns}-{index:02d}",
                "type": f"{turns}_turn",
                "scope_family": family,
                "question": question,
                "steps": steps,
            }
        )

    for index in range(20):
        limit = index + 1
        bond_pair = pair("bond", index)
        overseas_pair = pair("overseas_etp", index)
        fund_pair = pair("fund", index)

        add(
            "bond",
            2,
            index,
            f"국내채권 {bond_pair}를 비교해줘.",
            [{"slot": "comparison_metric", "value": "bond.coupon_rate"}],
        )
        add(
            "bond",
            3,
            index,
            "국내채권 상품을 두 개 비교해줘.",
            [
                {"slot": "comparison_targets", "value": bond_pair, "free_text": True},
                {"slot": "comparison_metric", "value": "bond.coupon_rate"},
            ],
        )
        add(
            "bond",
            4,
            index,
            "금융상품 두 개를 비교해줘.",
            [
                {"slot": "scope", "value": "bond"},
                {"slot": "comparison_targets", "value": bond_pair, "free_text": True},
                {"slot": "comparison_metric", "value": "bond.coupon_rate"},
            ],
        )

        add(
            "domestic_etp",
            2,
            index,
            f"국내 ETF 수익률이 높은 {limit}개 보여줘.",
            [{"slot": "return_period", "value": "1y"}],
        )
        add(
            "domestic_etp",
            3,
            index,
            f"수익률이 높은 ETF {limit}개 보여줘.",
            [
                {"slot": "market", "value": "domestic_etp"},
                {"slot": "return_period", "value": "1y"},
            ],
        )
        add(
            "domestic_etp",
            4,
            index,
            f"수익률이 높고 보수가 낮은 ETF {limit}개 보여줘.",
            [
                {"slot": "market", "value": "domestic_etp"},
                {"slot": "return_period", "value": "1y"},
                {"slot": "ranking_priority", "value": "domestic_etp.return_1y"},
            ],
        )

        add(
            "overseas_etp",
            2,
            index,
            f"좋은 해외 ETF {limit}개 보여줘.",
            [{"slot": "selection_criteria", "value": "보수 낮은 순"}],
        )
        add(
            "overseas_etp",
            3,
            index,
            "해외 ETF 상품을 두 개 비교해줘.",
            [
                {"slot": "comparison_targets", "value": overseas_pair, "free_text": True},
                {"slot": "comparison_metric", "value": "overseas_etp.aum_last"},
            ],
        )
        add(
            "overseas_etp",
            4,
            index,
            "ETF 상품을 두 개 비교해줘.",
            [
                {"slot": "market", "value": "overseas_etp"},
                {"slot": "comparison_targets", "value": overseas_pair, "free_text": True},
                {"slot": "comparison_metric", "value": "overseas_etp.aum_last"},
            ],
        )

        add(
            "fund",
            2,
            index,
            f"공모펀드 수익률이 높은 {limit}개 보여줘.",
            [{"slot": "return_period", "value": "1y"}],
        )
        add(
            "fund",
            3,
            index,
            "공모펀드 상품을 두 개 비교해줘.",
            [
                {"slot": "comparison_targets", "value": fund_pair, "free_text": True},
                {"slot": "comparison_metric", "value": "fund.return_1y"},
            ],
        )
        add(
            "fund",
            4,
            index,
            "금융상품 두 개를 비교해줘.",
            [
                {"slot": "scope", "value": "fund"},
                {"slot": "comparison_targets", "value": fund_pair, "free_text": True},
                {"slot": "comparison_metric", "value": "fund.return_1y"},
            ],
        )

        add(
            "cross_scope",
            2,
            index,
            f"국내 ETF와 공모펀드에서 수익률이 높은 {limit}개 보여줘.",
            [{"slot": "return_period", "value": "1y"}],
        )
        add(
            "cross_scope",
            3,
            index,
            f"국내 ETF와 공모펀드에서 수익률이 높고 순자산이 큰 {limit}개 보여줘.",
            [
                {"slot": "return_period", "value": "1y"},
                {"slot": "ranking_priority", "value_contains": "return_1y"},
            ],
        )
        add(
            "cross_scope",
            4,
            index,
            f"ETF와 공모펀드에서 수익률이 높고 순자산이 큰 {limit}개 보여줘.",
            [
                {"slot": "market", "value": "domestic_etp"},
                {"slot": "return_period", "value": "1y"},
                {"slot": "ranking_priority", "value_contains": "return_1y"},
            ],
        )

    type_counts = Counter(str(flow["type"]) for flow in flows)
    family_counts = Counter(str(flow["scope_family"]) for flow in flows)
    if type_counts != {"2_turn": 100, "3_turn": 100, "4_turn": 100}:
        raise AssertionError("unexpected clarification turn count")
    if family_counts != {
        "bond": 60,
        "domestic_etp": 60,
        "overseas_etp": 60,
        "fund": 60,
        "cross_scope": 60,
    }:
        raise AssertionError("unexpected clarification scope-family count")
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


def _score_direct_case(
    oracle: Oracle,
    case: dict[str, Any],
    response: httpx.Response,
    context: dict[str, Any],
) -> tuple[bool, bool]:
    """Score one direct case and return (passed, evidence-or-policy-linked)."""

    spec = dict(case["spec"])
    kind = str(case["kind"])
    if spec.get("expect_kind") == "semantic_retrieval":
        channels = {
            str(item.get("channel"))
            for item in context.get("retrieval_trace") or []
            if isinstance(item, dict) and item.get("status") in {"used", "validated"}
        }
        ledger = context.get("condition_ledger") or []
        grounded = bool(context.get("items")) and _items_have_evidence(context)
        passed = (
            context.get("answerability") in {"FULL", "PARTIAL_WITH_COVERAGE"}
            and "lexical" in channels
            and grounded
            and any(
                isinstance(item, dict)
                and item.get("kind") == "strategy"
                and item.get("status") == "grounded"
                for item in ledger
            )
        )
        return passed, grounded

    expected = oracle.expected(spec)
    scored = score_response(
        spec,
        expected,
        response.json()["answer"] + " " + response.json()["retrieved_context"],
        context,
    )
    passed = bool(scored["passed"])
    if spec.get("required_channel") == "graph":
        passed = passed and any(
            isinstance(item, dict)
            and item.get("channel") == "graph"
            and item.get("status") in {"validated", "fallback"}
            for item in context.get("retrieval_trace") or []
        )
    policy_linked = (
        context.get("answerability")
        in {
            "NEEDS_CLARIFICATION",
            "UNAVAILABLE",
            "INCOMPARABLE",
            "SAFETY_LIMITED",
            "DATA_QUALITY_BLOCKED",
        }
        and bool(context.get("reason_code"))
    )
    if kind in {"ambiguity", "safety"} or policy_linked:
        linked = bool(context.get("reason_code") or context.get("clarification"))
    else:
        linked = bool(context.get("items") or context.get("aggregates")) and _items_have_evidence(
            context
        )
    return passed, linked


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
            selected_value = step.get("value")
            contains = step.get("value_contains")
            if contains:
                selected_value = next(
                    (
                        value
                        for value in option_values
                        if isinstance(value, str) and str(contains) in value
                    ),
                    None,
                )
            free_text = bool(step.get("free_text"))
            if (
                not isinstance(selected_value, str)
                or not selected_value
                or (not free_text and selected_value not in option_values)
                or not isinstance(token, str)
                or not token
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
            response = await client.get(
                "/answer",
                params={
                    "question_id": f"{question_id_prefix}-{index}",
                    "question": question,
                    "clarification_token": token,
                    "clarification_response": selected_value,
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
    flows = build_clarification_flows()
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
                direct_cases = fill_runtime_slots(
                    build_direct_cases(), oracle.sample_codes(per_scope=64)
                )
                suite_hash = _suite_hash(direct_cases, flows)
                for case in direct_cases:
                    kind = str(case["kind"])
                    direct_by_kind[kind]["total"] += 1
                    response = await client.get(
                        "/answer",
                        params={"question_id": str(case["id"]), "question": str(case["question"])},
                    )
                    context, contract_valid = _response_context(response)
                    direct_contract_valid += int(contract_valid)
                    passed = False
                    if context is not None:
                        passed, linked = _score_direct_case(oracle, case, response, context)
                        direct_evidence_linked += int(linked)
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
        and flow_passed["2_turn"] == TWO_TURN_FLOW_COUNT
        and flow_passed["3_turn"] == THREE_TURN_FLOW_COUNT
        and flow_passed["4_turn"] == FOUR_TURN_FLOW_COUNT
        and flow_evidence_final["2_turn"] == TWO_TURN_FLOW_COUNT
        and flow_evidence_final["3_turn"] == THREE_TURN_FLOW_COUNT
        and flow_evidence_final["4_turn"] == FOUR_TURN_FLOW_COUNT
        and flow_contract_valid == FLOW_API_REQUEST_COUNT
        and flow_api_requests == FLOW_API_REQUEST_COUNT
    ) else "FAIL"
    return {
        "status": status,
        "gate": "LOCAL_EXTENSIVE_1200_DISTINCT_PLUS_300_MULTITURN",
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
        "two_turn_flow_count": TWO_TURN_FLOW_COUNT,
        "three_turn_flow_count": THREE_TURN_FLOW_COUNT,
        "four_turn_flow_count": FOUR_TURN_FLOW_COUNT,
        "two_turn_flow_passed_count": flow_passed["2_turn"],
        "three_turn_flow_passed_count": flow_passed["3_turn"],
        "four_turn_flow_passed_count": flow_passed["4_turn"],
        "two_turn_final_evidence_count": flow_evidence_final["2_turn"],
        "three_turn_final_evidence_count": flow_evidence_final["3_turn"],
        "four_turn_final_evidence_count": flow_evidence_final["4_turn"],
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

    flows = build_clarification_flows()
    with Oracle(DATABASE) as corpus_oracle:
        direct_cases = fill_runtime_slots(
            build_direct_cases(), corpus_oracle.sample_codes(per_scope=64)
        )
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
                    for case in direct_cases:
                        kind = str(case["kind"])
                        direct_by_kind[kind]["total"] += 1
                        try:
                            response = await live_client.get(
                                "/answer",
                                params={"question_id": str(case["id"]), "question": str(case["question"])},
                            )
                            context, contract_valid = _response_context(response)
                            direct_contract_valid += int(contract_valid)
                            if context is None:
                                passed = False
                            else:
                                passed, linked = _score_direct_case(
                                    oracle, case, response, context
                                )
                                hcx_used = "planner=HCX-007" in response.json()["think_trace"]
                                direct_hcx_planned += int(hcx_used)
                                direct_evidence_linked += int(linked)
                                if (
                                    kind == "cross_scope"
                                    and str(context.get("answerability")) == "INCOMPARABLE"
                                ):
                                    cross_scope_refusals += 1
                                passed = passed and hcx_used
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
        flow_passed["2_turn"] == TWO_TURN_FLOW_COUNT
        and flow_passed["3_turn"] == THREE_TURN_FLOW_COUNT
        and flow_passed["4_turn"] == FOUR_TURN_FLOW_COUNT
        and flow_hcx_final["2_turn"] == TWO_TURN_FLOW_COUNT
        and flow_hcx_final["3_turn"] == THREE_TURN_FLOW_COUNT
        and flow_hcx_final["4_turn"] == FOUR_TURN_FLOW_COUNT
        and flow_evidence_final["2_turn"] == TWO_TURN_FLOW_COUNT
        and flow_evidence_final["3_turn"] == THREE_TURN_FLOW_COUNT
        and flow_evidence_final["4_turn"] == FOUR_TURN_FLOW_COUNT
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
        "gate": "HCX_EXTENSIVE_1200_DISTINCT_PLUS_300_MULTITURN_E2E",
        "model_id": model_id,
        "approved_planner_stage": "two",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "question_suite_sha256": suite_hash,
        "direct_case_count": DIRECT_CASE_COUNT,
        "direct_semantic_spec_count": DIRECT_CASE_COUNT,
        "direct_surface_count_per_spec": 1,
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
        "two_turn_flow_count": TWO_TURN_FLOW_COUNT,
        "three_turn_flow_count": THREE_TURN_FLOW_COUNT,
        "four_turn_flow_count": FOUR_TURN_FLOW_COUNT,
        "baseline_flow_passed_count": baseline_flow_passed,
        "two_turn_flow_passed_count": flow_passed["2_turn"],
        "three_turn_flow_passed_count": flow_passed["3_turn"],
        "four_turn_flow_passed_count": flow_passed["4_turn"],
        "two_turn_final_hcx_count": flow_hcx_final["2_turn"],
        "three_turn_final_hcx_count": flow_hcx_final["3_turn"],
        "four_turn_final_hcx_count": flow_hcx_final["4_turn"],
        "two_turn_final_evidence_count": flow_evidence_final["2_turn"],
        "three_turn_final_evidence_count": flow_evidence_final["3_turn"],
        "four_turn_final_evidence_count": flow_evidence_final["4_turn"],
        "two_turn_signature_match_count": flow_signature_match["2_turn"],
        "three_turn_signature_match_count": flow_signature_match["3_turn"],
        "four_turn_signature_match_count": flow_signature_match["4_turn"],
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
            "Make 1,200 distinct direct HCX requests plus 300 multi-turn API flows; "
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
        help="run all 1,200 direct and 300 multi-turn cases with the deterministic planner; no key/HCX",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "live_hcx_extensive_e2e_gate_report.json",
    )
    args = parser.parse_args()
    if args.dry_run and args.local_verify:
        raise SystemExit("choose only one of --dry-run or --local-verify")
    with Oracle(DATABASE) as oracle:
        direct_cases = fill_runtime_slots(
            build_direct_cases(), oracle.sample_codes(per_scope=64)
        )
    flows = build_clarification_flows()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "gate": "HCX_EXTENSIVE_1200_DISTINCT_PLUS_300_MULTITURN_E2E",
                    "direct_case_count": len(direct_cases),
                    "direct_semantic_spec_count": DIRECT_CASE_COUNT,
                    "direct_category_counts": DIRECT_CATEGORY_COUNTS,
                    "two_turn_flow_count": TWO_TURN_FLOW_COUNT,
                    "three_turn_flow_count": THREE_TURN_FLOW_COUNT,
                    "four_turn_flow_count": FOUR_TURN_FLOW_COUNT,
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
