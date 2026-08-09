from __future__ import annotations

from collections import Counter

from deploy.live_hcx_extensive_e2e_gate import (
    DIRECT_CASE_COUNT,
    FLOW_API_REQUEST_COUNT,
    FOUR_TURN_FLOW_COUNT,
    LIVE_API_REQUEST_COUNT,
    THREE_TURN_FLOW_COUNT,
    TWO_TURN_FLOW_COUNT,
    _suite_hash,
    build_clarification_flows,
    build_direct_cases,
)
from eval.release_corpus import DIRECT_CATEGORY_COUNTS


def test_extensive_live_gate_has_1200_distinct_cases_and_300_multiturn_flows() -> None:
    direct_cases = build_direct_cases()
    flows = build_clarification_flows()

    assert len(direct_cases) == DIRECT_CASE_COUNT == 1_200
    assert Counter(str(case["kind"]) for case in direct_cases) == DIRECT_CATEGORY_COUNTS
    assert len({str(case["semantic_key"]) for case in direct_cases}) == 1_200
    assert Counter(str(flow["type"]) for flow in flows) == {
        "2_turn": TWO_TURN_FLOW_COUNT,
        "3_turn": THREE_TURN_FLOW_COUNT,
        "4_turn": FOUR_TURN_FLOW_COUNT,
    }
    assert Counter(str(flow["scope_family"]) for flow in flows) == {
        "bond": 60,
        "domestic_etp": 60,
        "overseas_etp": 60,
        "fund": 60,
        "cross_scope": 60,
    }
    assert all(len(flow["steps"]) == int(str(flow["type"])[0]) - 1 for flow in flows)
    assert FLOW_API_REQUEST_COUNT == 900
    assert LIVE_API_REQUEST_COUNT == 2_100
    assert _suite_hash(direct_cases, flows) == _suite_hash(
        build_direct_cases(), build_clarification_flows()
    )
