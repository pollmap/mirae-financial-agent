"""Async evaluation driver: score the live service against the independent oracle.

Usage (repo root, after the serving DB exists)::

    ./.venv/Scripts/python.exe -m eval.run_eval --limit 50
    ./.venv/Scripts/python.exe -m eval.run_eval            # full run (~640 questions)

The driver builds the same in-process service the integration tests use
(deterministic planner, DuckDB engine), fills runtime code slots by sampling
the first product ids per scope ordered by ``product_uid`` (deterministic),
asks every question through ``AgentService.answer`` and scores the parsed
``retrieved_context`` against ``eval.oracle.Oracle.expected``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from eval.oracle import Oracle
from eval.templates import generate

DEFAULT_DATABASE = Path("data") / "serving" / "mirae_agent.duckdb"
DEFAULT_OUT = Path("artifacts") / "eval_report.json"
DISCLOSURE_TOKEN = "[교차 상품군 응답"
OK_ANSWERABILITY = {"FULL", "PARTIAL_WITH_COVERAGE"}
REFUSAL_ANSWERABILITY = {"INCOMPARABLE"}
SCOPE_LABELS_KO = {
    "bond": "국내채권",
    "domestic_etp": "국내 ETF·ETN",
    "overseas_etp": "해외 ETF·ETN",
    "fund": "공모펀드",
}
MAX_FAILURES = 50


def fill_runtime_slots(
    questions: list[dict[str, Any]], codes_by_scope: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Substitute ``{code}``/``{code_a}``/``{code_b}`` deterministically."""

    filled: list[dict[str, Any]] = []
    for entry in questions:
        spec = dict(entry["spec"])
        question = str(entry["question"])
        scope = spec.get("scope")
        codes = codes_by_scope.get(str(scope), []) if scope else []
        if spec.get("slot") == "code":
            if not codes:
                raise RuntimeError(f"no runtime codes available for scope {scope!r}")
            code = codes[int(spec["slot_index"]) % len(codes)]
            question = question.replace("{code}", code)
            spec["code"] = code
        elif spec.get("slots") == ["code_a", "code_b"]:
            if len(codes) < 2:
                raise RuntimeError(f"need at least two runtime codes for scope {scope!r}")
            pair = int(spec["pair_index"])
            code_a = codes[(2 * pair) % len(codes)]
            code_b = codes[(2 * pair + 1) % len(codes)]
            if code_a == code_b:
                code_b = codes[(2 * pair + 2) % len(codes)]
            question = question.replace("{code_a}", code_a).replace("{code_b}", code_b)
            spec["code_a"] = code_a
            spec["code_b"] = code_b
        filled.append({**entry, "question": question, "spec": spec})
    return filled


def _to_int(value: object) -> int | None:
    try:
        return int(round(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return None


def _parse_context(retrieved_context: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(retrieved_context)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _position_match(expected: list[str], got: list[str]) -> float:
    if not expected:
        return 1.0 if not got else 0.0
    hits = sum(1 for exp, actual in zip(expected, got, strict=False) if exp == actual)
    return hits / len(expected)


def _set_overlap(expected: list[str], got: list[str]) -> float:
    if not expected:
        return 1.0 if not got else 0.0
    return len(set(expected) & set(got)) / len(expected)


def score_response(
    spec: dict[str, Any],
    expected: dict[str, Any],
    answer_text: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return ``{passed, detail, got}`` for a single question."""

    if context is None:
        return {
            "passed": False,
            "detail": {"error": "retrieved_context is not valid JSON"},
            "got": {"answer_head": answer_text[:120]},
        }
    answerability = str(context.get("answerability"))
    items = context.get("items") or []
    got_uids = [str(item.get("product_uid")) for item in items if isinstance(item, dict)]
    aggregates = context.get("aggregates") or []
    result_count = context.get("result_count") or 0
    disclosure = DISCLOSURE_TOKEN in answer_text or any(
        DISCLOSURE_TOKEN in str(line) for line in context.get("limitations") or []
    )
    got = {
        "answerability": answerability,
        "reason_code": context.get("reason_code"),
        "result_count": result_count,
        "product_uids": got_uids[:10],
        "aggregate_values": [
            _to_int(agg.get("value")) for agg in aggregates[:8] if isinstance(agg, dict)
        ],
        "disclosure": disclosure,
    }
    kind = str(expected["kind"])
    detail: dict[str, Any] = {}
    passed = False

    if kind == "rank":
        exp_uids = list(expected["product_uids"])
        detail = {
            "exact": got_uids == exp_uids,
            "set_overlap": round(_set_overlap(exp_uids, got_uids), 4),
            "position_match": round(_position_match(exp_uids, got_uids), 4),
        }
        passed = bool(detail["exact"])
    elif kind == "search":
        exp_uids = list(expected["product_uids"])
        total = int(expected["total"])
        if total == 0:
            passed = not got_uids and answerability in {"NO_RESULT", "FULL", "UNAVAILABLE"}
            detail = {"expected_total": 0}
        else:
            detail = {
                "exact": got_uids == exp_uids,
                "set_overlap": round(_set_overlap(exp_uids, got_uids), 4),
            }
            passed = bool(detail["exact"])
    elif kind == "count":
        value = int(expected["value"])
        aggregate_hit = any(
            _to_int(agg.get("value")) == value for agg in aggregates if isinstance(agg, dict)
        )
        text_hit = f"{value:,}" in answer_text or (value < 1000 and str(value) in answer_text)
        detail = {"expected_value": value, "aggregate_hit": aggregate_hit, "text_hit": text_hit}
        passed = aggregate_hit or text_hit
    elif kind == "group_count":
        expected_map = {str(k): int(v) for k, v in dict(expected["value"]).items()}
        actual_map: dict[str, int | None] = {}
        for agg in aggregates:
            if isinstance(agg, dict):
                actual_map[str(agg.get("group_key"))] = _to_int(agg.get("value"))
        matched = {}
        for scope, value in expected_map.items():
            label = SCOPE_LABELS_KO.get(scope, scope)
            matched[scope] = actual_map.get(scope, actual_map.get(label)) == value
        detail = {"expected_value": expected_map, "matched": matched}
        passed = bool(matched) and all(matched.values())
    elif kind == "lookup":
        exp_uids = list(expected["product_uids"])
        if not exp_uids:
            passed = answerability in {"NO_RESULT", "UNAVAILABLE", "NEEDS_CLARIFICATION"}
            detail = {"expected": "no such code in serving catalog"}
        elif len(exp_uids) == 1:
            passed = bool(got_uids) and got_uids[0] == exp_uids[0]
            detail = {"expected_uid": exp_uids[0]}
        else:
            passed = (bool(got_uids) and got_uids[0] in exp_uids) or (
                answerability == "NEEDS_CLARIFICATION"
            )
            detail = {"expected_uids": exp_uids[:5], "ambiguous_code": True}
    elif kind == "compare":
        exp_uids = set(expected["product_uids"])
        detail = {"expected_uids": sorted(exp_uids)[:5]}
        passed = bool(exp_uids) and exp_uids.issubset(set(got_uids)) and (
            answerability in OK_ANSWERABILITY
        )
    elif kind == "behavior":
        allowed = expected.get("answerability")
        answerability_ok = True if not allowed else answerability in set(allowed)
        expected_reason = expected.get("reason_code")
        reason_ok = True if not expected_reason else context.get("reason_code") == expected_reason
        # A genuine follow-up question (e.g. "which metric first?" when a
        # cross-scope rank names two distinct, non-comparable concepts) is
        # not a refusal: it is the same interactive clarification pattern
        # already used for single-scope multi-metric ranking, and it always
        # carries a concrete missing_slots/options payload the caller can
        # act on next. It naturally has no cross-scope disclosure block yet
        # (that only exists once a plan actually executes), so it satisfies
        # both the disclosure and refusal checks for the cross-scope mandate
        # on its own -- it does not license an answerability mismatch,
        # which answerability_ok above still gates independently.
        is_clarification = answerability == "NEEDS_CLARIFICATION" and bool(
            (context.get("clarification") or {}).get("missing_slots")
        )
        disclosure_ok = disclosure or not expected.get("disclosure_required") or is_clarification
        # NOTE: unlike an earlier version of this check, disclosure text
        # alone no longer satisfies refusal_ok -- a bundle can carry the
        # cross-scope disclosure marker even on its total-failure path
        # (0 results), which must not count as "answered". A real answer
        # needs actual results (or a legitimate clarification); the
        # disclosure requirement is verified separately via disclosure_ok.
        refusal_ok = (
            answerability not in REFUSAL_ANSWERABILITY and int(result_count) > 0
        ) or is_clarification
        detail = {
            "answerability_ok": answerability_ok,
            "reason_ok": reason_ok,
            "disclosure_ok": disclosure_ok,
        }
        if expected.get("disclosure_required"):
            detail["refusal_ok"] = refusal_ok
            passed = answerability_ok and reason_ok and disclosure_ok and refusal_ok
        else:
            passed = answerability_ok and reason_ok
    elif kind == "cross_rank":
        # This is the mandate's flagship case (unified cross-scope ranking):
        # the oracle computes an exact expected order via the same fusion
        # rule the executor uses (value, uid) tie-break, so -- unlike
        # `behavior` -- there IS one correct answer here and it must be
        # checked, not just "did it answer at all". An earlier version of
        # this branch computed rank_exact/rank_set_overlap into `detail` but
        # never included them in `passed`, so this case could not fail on a
        # wrong ranking as long as disclosure text was present.
        exp_uids = list(expected["product_uids"])
        refusal_ok = answerability not in REFUSAL_ANSWERABILITY and int(result_count) > 0
        rank_exact = got_uids == exp_uids
        detail = {
            "refusal_ok": refusal_ok,
            "disclosure_ok": disclosure,
            "rank_exact": rank_exact,
            "rank_set_overlap": round(_set_overlap(exp_uids, got_uids), 4),
        }
        passed = refusal_ok and disclosure and rank_exact
    elif kind == "cross_split_rank":
        # SPLIT_PRESENTATION family (e.g. AUM domestic+overseas): there is no
        # single fused ordering to require exact-match against (see
        # Oracle._expected_cross_split_rank) -- each scope's own top-n must
        # simply all be present in the response, order-agnostic.
        exp_uids = set(expected["product_uids"])
        refusal_ok = answerability not in REFUSAL_ANSWERABILITY and int(result_count) > 0
        contains_all = bool(exp_uids) and exp_uids.issubset(set(got_uids))
        safely_truncated = (
            context.get("reason_code") == "RESPONSE_TRUNCATED"
            and answerability == "PARTIAL_WITH_COVERAGE"
            and bool(got_uids)
        )
        detail = {
            "refusal_ok": refusal_ok,
            "disclosure_ok": disclosure,
            "contains_all_expected": contains_all,
            "safely_truncated": safely_truncated,
            "missing_uids": sorted(exp_uids - set(got_uids))[:5],
        }
        passed = refusal_ok and disclosure and (contains_all or safely_truncated)
    else:
        detail = {"error": f"unknown expected kind {kind!r}"}

    return {"passed": passed, "detail": detail, "got": got}


def _expected_summary(expected: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"kind": expected["kind"]}
    if "product_uids" in expected:
        uids = list(expected["product_uids"])
        summary["product_uids"] = uids[:5]
        summary["total_uids"] = len(uids)
    if "value" in expected:
        summary["value"] = expected["value"]
    if "total" in expected:
        summary["total"] = expected["total"]
    if expected.get("answerability"):
        summary["answerability"] = expected["answerability"]
    return summary


async def run(
    database_path: Path | str = DEFAULT_DATABASE,
    *,
    limit: int | None = None,
    out_path: Path | str = DEFAULT_OUT,
) -> dict[str, Any]:
    """Evaluate the service and write a JSON report; returns the report dict."""

    database_path = Path(database_path)
    if not database_path.is_file():
        raise FileNotFoundError(
            f"serving database not found: {database_path} "
            "(build it with scripts/build_data.py first)"
        )

    # Imported here so eval.templates/eval.oracle stay importable without app.
    from app.config import Settings
    from app.execution.engine import DuckDBEngine
    from app.planner.deterministic import DeterministicPlanner
    from app.service import AgentService

    settings = Settings(
        environment="test", database_path=database_path, planner_mode="deterministic"
    )
    service = AgentService(settings, DeterministicPlanner(), DuckDBEngine(database_path))

    by_kind: dict[str, dict[str, int]] = {}
    failures: list[dict[str, Any]] = []
    cross_total = cross_refused = 0
    multi_total = multi_disclosed = 0
    rank_position_sum = 0.0
    rank_scored = 0
    evaluated = 0
    passed_total = 0

    with Oracle(database_path) as oracle:
        questions = fill_runtime_slots(generate(), oracle.sample_codes(per_scope=16))
        if limit is not None:
            questions = questions[: max(limit, 0)]

        for entry in questions:
            kind = str(entry["kind"])
            spec = entry["spec"]
            expected = oracle.expected(spec)
            try:
                response = await service.answer(
                    question_id=str(entry["id"]), question=str(entry["question"])
                )
                answer_text = response.answer + " " + response.retrieved_context
                context = _parse_context(response.retrieved_context)
                result = score_response(spec, expected, answer_text, context)
            except Exception as exc:  # keep scoring the remaining questions
                result = {
                    "passed": False,
                    "detail": {"error": f"{type(exc).__name__}: {exc}"[:300]},
                    "got": {},
                }

            evaluated += 1
            bucket = by_kind.setdefault(kind, {"total": 0, "passed": 0})
            bucket["total"] += 1
            if result["passed"]:
                bucket["passed"] += 1
                passed_total += 1
            else:
                if len(failures) < MAX_FAILURES:
                    failures.append(
                        {
                            "id": entry["id"],
                            "kind": kind,
                            "question": entry["question"],
                            "expected": _expected_summary(expected),
                            "got": {**result["got"], "detail": result["detail"]},
                        }
                    )

            detail = result.get("detail") or {}
            if expected["kind"] == "rank" and "position_match" in detail:
                rank_position_sum += float(detail["position_match"])
                rank_scored += 1
            if kind == "cross_scope":
                cross_total += 1
                if not detail.get("refusal_ok", True):
                    cross_refused += 1
            scopes = spec.get("scopes") or []
            if kind == "cross_scope" and len(scopes) > 1:
                multi_total += 1
                if result.get("got", {}).get("disclosure"):
                    multi_disclosed += 1

    report: dict[str, Any] = {
        "database_path": str(database_path),
        "question_total": evaluated,
        "accuracy": round(passed_total / evaluated, 4) if evaluated else 0.0,
        "by_kind": {
            kind: {
                "total": bucket["total"],
                "passed": bucket["passed"],
                "accuracy": round(bucket["passed"] / bucket["total"], 4),
            }
            for kind, bucket in sorted(by_kind.items())
        },
        "rank_position_match_mean": (
            round(rank_position_sum / rank_scored, 4) if rank_scored else None
        ),
        "cross_scope_refusal_rate": (
            round(cross_refused / cross_total, 4) if cross_total else None
        ),
        "disclosure_rate": round(multi_disclosed / multi_total, 4) if multi_total else None,
        "failure_count": evaluated - passed_total,
        "failures": failures,
    }

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score the agent service against the independent SQL oracle."
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE),
        help="path to the serving DuckDB file (default: data/serving/mirae_agent.duckdb)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N questions"
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="report path (default: artifacts/eval_report.json)",
    )
    args = parser.parse_args()
    report = asyncio.run(run(Path(args.database), limit=args.limit, out_path=Path(args.out)))
    print(
        json.dumps(
            {
                "question_total": report["question_total"],
                "accuracy": report["accuracy"],
                "cross_scope_refusal_rate": report["cross_scope_refusal_rate"],
                "disclosure_rate": report["disclosure_rate"],
                "failure_count": report["failure_count"],
                "report": str(args.out),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
