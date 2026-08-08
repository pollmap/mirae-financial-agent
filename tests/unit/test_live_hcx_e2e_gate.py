from __future__ import annotations

from collections import Counter

from deploy.live_hcx_e2e_gate import CASE_TARGETS, QUESTION_COUNT, _select_cases, _suite_hash
from eval.templates import generate


def test_live_hcx_e2e_gate_is_fixed_100_case_stratified_and_digestible() -> None:
    selected = _select_cases(generate())

    assert len(selected) == QUESTION_COUNT == 100
    assert Counter(str(case["kind"]) for case in selected) == CASE_TARGETS
    assert _suite_hash(selected) == _suite_hash(_select_cases(generate()))
    assert all("question" in case and "spec" in case for case in selected)
