from __future__ import annotations

from collections import Counter

from deploy.live_hcx_extensive_e2e_gate import (
    DIRECT_CASE_COUNT,
    FLOW_API_REQUEST_COUNT,
    LIVE_API_REQUEST_COUNT,
    _suite_hash,
    build_clarification_flows,
    build_direct_cases,
)
from eval.templates import generate


def test_extensive_live_gate_has_1000_hcx_direct_cases_and_200_multiturn_flows() -> None:
    direct_cases = build_direct_cases(generate())
    flows = build_clarification_flows()

    assert len(direct_cases) == DIRECT_CASE_COUNT == 1_000
    assert Counter(str(case["kind"]) for case in direct_cases) == {
        "rank_single": 468,
        "filter_search": 228,
        "count_aggregate": 166,
        "cross_scope": 138,
    }
    assert Counter(str(flow["type"]) for flow in flows) == {
        "two_follow_up": 100,
        "three_follow_up": 100,
    }
    assert all(len(flow["steps"]) in {2, 3} for flow in flows)
    assert FLOW_API_REQUEST_COUNT == 700
    assert LIVE_API_REQUEST_COUNT == 1_700
    assert _suite_hash(direct_cases, flows) == _suite_hash(
        build_direct_cases(generate()), build_clarification_flows()
    )
