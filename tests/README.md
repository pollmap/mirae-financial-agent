# Executable fixture and test contract

전체 test suite:

```bash
.venv/bin/python -m pytest -q
```

현재 local 기준선은 fast 153/153(14.90초), 전체 source rebuild 포함 full
158/158(104.57초)입니다. fixture의 50개 질문은
`tests/integration/test_gold_execution.py`가 40개 plan subset과 103개 선언 assertion을 모두
실행하며 50/50 통과합니다. runtime scan은 28 files/0 findings, real HTTP E2E는 15/15,
load smoke는 100/100·concurrency 10·failure 0·p95 131.75ms입니다.

이 수치는 2026-08-03 **DRAFT local evidence**입니다. 실제 HCX credential E2E, Docker fresh
build/run/restart·image digest, public TLS/domain, Git SHA, 8월 6일 주최 측 최종
contract/model 확인 전에는 FINAL release 증빙으로 표시하지 않습니다.

`gold_queries_v0.jsonl`은 4개 상품군 각 10개, 총 40개입니다. 각 줄은 독립 JSON이며
다음 두 층을 검증합니다.

- `expected_plan_subset`: HCX QueryPlan에서 반드시 일치해야 할 핵심 필드
- `expected_assertions`: deterministic executor·answerability·evidence가 만족해야 할 조건

`exact_count`, `exact_product_uid`, `ordered_product_uids`, `coverage`, `policy_reason`은
기계 검증합니다. `must_cite`는 Evidence Bundle의 source field 존재를 검사합니다.

이 fixture는 2026-07-11 원본의 prebrief 기준입니다. 설명회 참고질의·단위·0 의미·등급
정렬표가 오면 기존 기대값을 조용히 덮지 않고 briefing diff와 함께 version을 올립니다.

source verify는 내부 XLSX 8/8입니다. 기준 DB는 raw 145,393, logical 60,913, serving
60,903, quarantine 10, fund attributes 95,618, metric evidence 1,156,332이며 SHA-256은
`4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`입니다. 국내 ETF 1Y는
source-present 986·quality-valid 951·공통 최신일 `2026-06-15` rankable 940입니다. 펀드는
full 11,138과 API 기본 공모 11,115를 구분하며, 공모 기본 위험등급 보유 8,564·결측 2,551,
판매중 8,445·판매완료 2,670을 검증합니다.

`policy_queries_v0.jsonl`은 교차상품·금융안전·추정금지 통제입니다.

Answerability와 사유 코드는 분리합니다. 원본 필드가 없으면 `UNAVAILABLE`, 필드는
있지만 metric 정책이나 품질 때문에 실행을 막으면 `DATA_QUALITY_BLOCKED`이며,
`PENDING_ZERO_POLICY`·`UNUSABLE_CONSTANT` 등은 `policy_reason`으로 검증합니다.

contract 회귀에는 schema-valid broad lookup의 실행 전 cardinality clarification, 빈
clarification items, answer/context/token/question 길이 상한, source-backed 상품명 `유망`의
정책 오탐 방지, metric source-present 공모 기본 universe 유지가 포함됩니다.

기계검증 경로는 다음으로 고정합니다.

- `policy_reason` → Evidence Bundle `reason_code`
- `coverage` → `coverage.numerator`, `coverage.denominator`, `coverage.basis`
- `exact_count`·`exact_group_counts` → `aggregates[].group_key/value`
- `ordered_product_uids`·`ordered_values` → `items[]`의 rank와 field evidence
- `must_cite` → `items[].fields[]` 또는 aggregate의 `source_table_ids[]`,
  `source_fields[]`, `query_hash`
