# 재기준화 검증 리포트 (W1-W3 실측)

기준: 2026-08-08, branch `briefing-rebaseline-v2`, HEAD는 이 문서 커밋 시점 `git log -1`.
`docs/14_BRIEFING_REBASELINE_PLAN.md`에서 정의한 게이트의 실측 결과다. 측정하지 않은
항목은 "미측정"으로 명시하고 추정치를 대신 적지 않는다.

## 1. 핵심 지표 (eval/run_eval.py, 640문항, 독립 SQL oracle)

```text
question_total            640
accuracy                   100.0%  (목표 ≥95%, 이전 라운드 77.5%→91.1%→94.5%→99.38%→100%)
cross_scope_refusal_rate   0.0%   (목표 0% — 사용자 핵심 지시)
disclosure_rate            98.55%
rank_position_match_mean   1.0
```

카테고리별 (전부 100%): lookup_code 60, rank_single 234, filter_search 114,
count_aggregate 83, cross_scope 69, compare 40, safety_block 20, ambiguous 20.

metamorphic (동의어·어순 불변성, eval/metamorphic.py): 137개 그룹 중 137개 불변
(invariance_rate 1.0, violation 0).

**이 100%가 의미하는 것과 의미하지 않는 것.** 오라클은 엔진의 *실제* 정책(통화
파티션 요구, ETF 한정 AUM, 국내 ETP 수익률 -100 sentinel 승순위 차단, 기준일
일치, 판매상태 코드 변환)을 반영하도록 여러 차례 수정됐다 — 즉 이 100%는
"오라클이 기대하는 대로 앱이 답한다"이지 "오라클이 항상 옳다"가 아니다.
반대로, 이번 라운드에서 실제 **앱 버그 4건**을 발견·수정했다(§3). 오라클
수정과 앱 버그 수정은 각 커밋 메시지에서 구분해 기록했다.

## 2. 교차 상품군 무거절 실측 (사용자 핵심 지시)

W1 이전(prebrief-v1 태그) 대비:

| 질의 | 이전 | 현재 |
|---|---|---|
| 국내 ETF+공모펀드 1년수익률 통합 top N | `INCOMPARABLE` 거절 | `PARTIAL_WITH_COVERAGE`, 통합 순위 + sentinel/0값/기준일 공시 |
| 국내+해외 ETF 총보수 | `INCOMPARABLE` 거절 | 원본값 통합 순위 + 0값 개수 공시 |
| 국내+해외 ETF AUM | `INCOMPARABLE` 거절 | 통화 자동판별(국내 KRW 99.94%·해외 USD 100%) 분리 제시 |
| 채권 표면금리 vs 펀드 수익률 | `INCOMPARABLE` 거절 | 병렬 설명(통합 순위 없음) |
| 채권 신용등급 vs 펀드 위험등급 | `INCOMPARABLE` 거절 | 척도 상이 안내 + 대안 지표 |
| 해외 ETP처럼 원본 필드 자체가 없는 스코프 | (동일 경로로 거절) | 대안 섹션 안내, 나머지 스코프는 정상 답변 |

eval의 cross_scope 69문항 실측 거절률 0.0%, 69/69 정답. 실행 경로는 W1의
`app/execution/cross_scope.py`(단일스코프 서브플랜 분해 후 기존 검증된
단일스코프 엔진 재사용)로, 스코프별 최신 기준일·품질 게이트는 그대로 유지된다.

## 3. 이번 라운드에서 발견·수정한 실제 버그 4건

eval 하네스가 오라클-정합화만이 아니라 **실제 코드 결함**을 노출했다. 전부
`app/planner/deterministic.py`(로컬 개발용 결정론 planner, 제출 runtime은
HCX 경유이지만 동일 하위 QueryPlan 계약을 공유)에서 발견:

1. **엔티티 조각화**: `[A-Z]{1,6}` 보조 코드 탐지가 스팬 겹침을 확인하지 않아
   `"AAAA.K"` 하나가 `AAAA.K`/`AAAA`/`AAA`/`K` 4개 엔티티로 쪼개짐 →
   비교 질의가 항상 `COMPARE_TARGET_NOT_UNIQUE`로 실패. `pre_router.py`가
   이미 쓰던 스팬-클레임 기법을 재사용해 수정.
2. **"작은" 미인식**: 순위 트리거 키워드 목록에 "큰"/"많은"/"적은"은 있는데
   "작은"이 빠져 있어 "가장 작은 N개"가 정렬 없는 `search`로 처리됨(순서
   무작위) → 키워드 추가.
3. **"ETF·ETN" 비대칭 가드**: 결합 문구 가드가 ETF 쪽에만 있고 ETN 쪽엔
   없어서 "국내 ETF·ETN은 몇 개?"가 ETN만 세고 있었음(532 vs 정답 1733) →
   가드 대칭화.
4. **교차스코프 다중지표 크래시**: `metrics`는 스코프 수만큼 늘어날 수 있는데
   `sort`는 같은 스코프 다중지표 경우만 늘어나게 되어 있어, 서로 다른
   스코프의 서로 다른 지표를 "나란히" 요청하면 Pydantic
   `ValidationError`가 서비스까지 전파됨(외부에는 안전한 controlled 500으로만
   노출되지만 내부적으로는 처리 실패) → `metrics` 개수에 맞춰 `sort` 생성.

이 4건은 전부 `git log`상 별도로 식별 가능한 수정이며, 회귀 테스트
(`tests/unit/test_deterministic_planner.py` 18개 + 관련 fixture)가 그대로
green임을 확인했다.

## 4. 2단계 플래너 (Stage-1 개념 → Stage-2 grounding)

`tests/contract/test_hcx_two_stage_e2e.py`로 mock HCX 경유 실측:

```text
Stage-1 실제 전송 스키마         HCX_SEMANTIC_PLAN_SCHEMA (물리 필드명 없음) 확인
단일스코프 lookup                Stage-2 grounding → 정확한 product_uid 일치
교차스코프 통합순위              Stage-1(개념)+grounding 결과 == Stage-1(물리) 결과 (바이트 동일 product_uid 리스트)
```

요청당 TPM 예약량: **13,013B → 5,383B (−58.6%)**. 기본 `HCX_TPM_BUDGET=60000`
기준 처리량 **4건/분 → 11건/분**(2.75배). `PLANNER_STAGE=two`는 설계·구현·
테스트 완료 상태이며, 기본값은 여전히 `one`(운영 승격은 8/6 설명회 이후
실 HCX 대비 A/B로 최종 확인 예정, `deploy/env.production.example` 주석 참고).

## 5. Knowledge Graph / Lexical / Vector 실측

```text
kg_node                    71,671
kg_edge                   206,274   (managedBy 6,782 · issuedBy 42,145 · hasAssetType 60,895
                                      · hasRiskGrade 52,692 · inRegion 18,501 · tracksBenchmark 14,129
                                      · managedByCode 11,130)
kg_alias                  249,857
lex_doc                    80,670   (상품명 전체 스코프 + 해외 전략문 5,638 + 비-sentinel benchmark)
lex_term(posting)       1,288,698
lex_vocab                  43,935
vec_embedding                   0   (CLOVA_STUDIO_API_KEY 미수령 — 캐시 없음, `vector_enabled=false`)
```

무결성: product 노드 수 == serving 상품 수(60,903) 일치 assert, orphan edge 0건
assert. 둘 다 `etl/build.py` 빌드 시 자동 검증되며 실패 시 빌드가 중단된다.

## 6. 실측하지 못한 항목 (정직하게 명시)

- **2단계 플래너의 640문항 전체 A/B**: eval 하네스는 `DeterministicPlanner`를
  직접 호출한다(물리 QueryPlan 생성). Stage-1 의미론적 plan을 640문항 전부에
  대해 생성하려면 별도 mock-semantic-HCX 서버 + 640회 HTTP 왕복이 필요해
  이번 라운드에서는 수행하지 않았다. 대신 flagship 교차질의 1건으로 정확한
  동등성을, TPM으로 효율성을 실측했다(§4). 전체 A/B는 W3+ 후속 과제로 남긴다.
- **Vector 채널 품질**: 임베딩 캐시가 없어 `vector_retriever.py`의 실제
  검색 품질은 미측정이다(단위테스트는 fake embedder로 배관만 검증).
- **Lexical entity fallback의 실사용 적중률**: eval 640문항은 전부 정확한
  코드 또는 exact-alias로 해석 가능해 새 lexical fallback 경로가 실제로
  트리거된 사례가 없다(설계상 세 번째 안전망이므로 당연한 결과). 안전성
  (겹치는 후보는 항상 clarify로 감)만 확인했고 재현율 개선폭은 미측정.
- **HCX 실 API**: 여전히 mock/local-LLM 검증까지만 완료.

## 7. 재현 명령

```bash
.venv/Scripts/python.exe -m pytest -q                      # 238/238
.venv/Scripts/python.exe -m ruff check app deploy etl scripts tests eval
.venv/Scripts/python.exe scripts/scan_runtime_compliance.py # 44 files/0 findings
.venv/Scripts/python.exe -m eval.run_eval                   # 640/640, artifacts/eval_report.json
.venv/Scripts/python.exe -m eval.metamorphic                # 137/137, artifacts/metamorphic_report.json
```
