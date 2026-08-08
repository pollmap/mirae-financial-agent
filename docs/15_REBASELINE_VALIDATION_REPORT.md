# 재기준화 검증 리포트 (W1-W4 실측)

기준: 2026-08-08, branch `briefing-rebaseline-v2`, HEAD는 이 문서 커밋 시점 `git log -1`.
`docs/14_BRIEFING_REBASELINE_PLAN.md`에서 정의한 게이트의 실측 결과다. 측정하지 않은
항목은 "미측정"으로 명시하고 추정치를 대신 적지 않는다.

**중요 — §0을 먼저 읽을 것.** 이 문서의 최초 버전(W1-W3 시점)이 보고한 "640/640 100%,
cross_scope 69/69 정답"은 사후 적대적 리뷰에서 **채점 로직 자체의 결함**으로 밝혀졌다.
거절률 0% 측정치는 그때도 지금도 유효하지만, "순위·값이 정확하다"는 부분은 당시
검증되지 않은 채로 통과 처리되고 있었다. §0에 전체 경위를 기록한다.

## 0. 사후 적대적 리뷰에서 발견·수정한 것 (2026-08-08, W1-W3 완료 직후)

W1-W4 코드가 전부 green으로 나온 뒤, 별도의 독립 리뷰 라운드를 돌렸다(3개 병렬
에이전트가 각각 (1) cross-scope/semantics 코드, (2) federated retrieval 코드,
(3) 데이터 정책·eval 하네스 자체를 대상으로 적대적 검증; 전부 실제 코드 실행으로
재현 확인, read-only). 핵심 발견:

### 앱 코드의 실제 버그 (전부 수정·재검증 완료)

1. **[크래시]** `cross_scope.py`의 SPLIT/EXPLAIN 융합 결과 캡이 `max()`를 써서
   사실상 무제한이었음 — 4개 스코프 × limit 50 = 200개 항목이 `EvidenceBundle.items`의
   `max_length=50` 제약을 그대로 뚫고 `ValidationError`로 죽는 경로가 실존했다(실제
   `EvidenceBundle` 생성으로 재현 확인). `min()`으로 수정.
2. **[침묵 오답]** 하나의 스코프에만 바인딩된 개념(예: `credit_rating`은 채권에만
   존재)을 여러 스코프에 걸쳐 조회하면, 텍스트값("AAA" 등)이 숫자 파싱에 실패해
   `fused_items=[]`인 채로 `PARTIAL_WITH_COVERAGE`(성공처럼 보이는) 빈 응답이
   나갔다. `capability.py`가 바인딩 스코프 1개 이하면 애초에 UNIFIED_RANK를
   선택하지 않도록 수정 + `cross_scope.py`에 숫자 융합이 전부 실패하면
   SPLIT_PRESENTATION으로 강등하는 방어 로직 추가.
3. **[비대칭 가드]** "ETF·ETN" 결합 문구 인식이 특정 구분자·순서 조합만 나열해
   "ETN/ETF"(반대 순서)는 놓쳤다 — 두 값 모두 AND로 걸려 항상 0건. 개별 토큰
   나열 대신 "ETF와 ETN이 동시에 언급되면 둘 다 필터링 안 함"으로 일반화.
4. **[의도 소실]** "비교" + 순위 키워드가 함께 오면 순위가 항상 이겨서, 코드로 명시된
   2개 비교 대상이 있어도 무시하고 전체 모집단 순위를 대신 반환했다. 명시적 상품
   코드 2개 이상이 감지되면 비교가 이기도록 수정(기존 GOLD-D08처럼 코드 없이
   "비교해줘"가 순위를 뜻하는 케이스는 회귀 없이 유지 확인).
5. 그 외 마이너: `product.scope` 조건이 공시 없이 조용히 드롭되던 것(현재는
   도달 불가 경로지만 방어적으로 수정), `grounder.py`의 `top_n=Infinity/NaN`
   미처리, 죽은 코드 `MetricPolicy.cross_product_allowed` 제거,
   보수 0값 배제 설명이 정책 완화 이후 사라졌던 것을 원본값 보유·제외 개수
   공시로 복원(단, 원본 CSV notes는 사용자 노출용이 아니므로 요약 문구만 사용).

### eval 하네스 자체의 결함 (가장 심각 — 정직하게 기록)

`eval/run_eval.py`의 `cross_rank`/`behavior` 채점이 **disclosure 텍스트 존재 여부만
보고 실제 순위·값·사유코드 일치는 계산만 하고 판정에 반영하지 않았다.** 마침
`execute_cross_scope`는 완전 실패 경로에서도 disclosure 마커를 남기므로, 이 채점은
구조적으로 거의 실패할 수 없었다. 수정: `cross_rank`는 오라클이 계산한 정확한
순서와 실제 일치해야 통과, `behavior`는 오라클이 제공하는 `reason_code`와도
일치해야 통과, `refusal_ok`에서 "disclosure만 있으면 통과" 조건 제거(실제 결과가
있어야 함).

### 오라클 자체의 기존 정책 미반영 (진짜 앱 버그 아님, 오라클 보정)

채점을 정직하게 만들자 정확도가 100%→95.78%로 떨어졌다. 27건 실패를 전수 조사한
결과 **전부 오라클이 기존에 이미 있던(이번 세션 이전부터 존재) 단일스코프 정책을
반영하지 못한 것**이었고, 앱 자체는 정확했다:

- 국내 ETP 수익률 하락순위는 `-100` sentinel 정책 미확정으로 원래 차단되는데,
  오라클의 교차순위 기대값 계산이 이를 몰라서 국내 ETP 쪽 기대값을 잘못 포함시킴
  → 오라클에 `_single_scope_rank_refusal` 사전검사 추가.
- AUM(국내+해외) 쌍은 통화가 달라 `comparability_matrix_v1.csv`(W1에서 이미
  SPLIT_PRESENTATION으로 명시)대로 스코프별 분리 응답이 맞는데, eval 템플릿이
  이 가족을 "단일 통합순위(cross_rank)"로 잘못 분류해 기대값 자체가 틀렸음
  → 템플릿의 `currency_partition` 유무로 `cross_rank`/`cross_split_rank`를
  분기하도록 수정, `cross_split_rank`는 스코프별 top-N이 결과에 전부 포함되는지만
  확인(순서는 스코프별로 독립이므로 강제하지 않음).

이 두 수정 후 재실측: 95.78%→97.19%→**100%**(진짜). cross_scope 카테고리(69문항)만
따로 보면 이 라운드에서 60.87%→73.91%→100%로 움직였다 — 이 변동 폭 자체가
"이전 100%가 채점 결함으로 부풀려져 있었다"는 증거다.

### 검토했지만 낮은 우선순위로 남긴 것

- `etl/kg.py`(KG 빌드 스테이지) 전용 단위테스트 없음 — reconciliation assert만
  안전망. `normalize_party`는 접미사만 다른 두 실제 다른 회사를 이론상 병합할 수
  있음(현재 실 데이터 32건 전수 검사로는 오탐 없음 확인, 하지만 향후 데이터
  갱신 시 재확인 필요). `vector_retriever`의 짧은 벡터 zero-padding이 문서의
  "엄격한 차원불일치 거부" 주장보다 느슨함(vector_enabled=false라 현재 미도달).
  이 세 가지는 federated retrieval의 **현재 프로덕션 경로에 연결되지 않은
  scaffold 코드**(그래프의 party 함수·fusion.py·vector 전부 실제 호출자가
  아직 없음, entity 해석에만 lexical fallback이 연결됨)에 있어 지금 당장의
  정답 정확성에는 영향이 없다고 판단해 이번 라운드에서는 보류했다.
- `bond.credit_rating` 등급 순서, compare의 `COMPARISON_VALUE_UNUSABLE` 분기는
  실제로는 `quality_flags`가 엔진 전체에서 항상 빈 배열로 생성되어 도달
  불가능한 것으로 드러났다(별개의 사전 존재 이슈, 이번 범위 밖).

## 1. 핵심 지표 (eval/run_eval.py, 640문항, 독립 SQL oracle, 채점 로직 수정 후 재측정)

```text
question_total            640
accuracy                   100.0%  (목표 ≥95%; 이번 라운드 실측 이력: 77.5%→91.1%→94.5%→
                                     99.38%→100%(채점결함)→[채점 수정]→95.78%→97.19%→100%(진짜))
cross_scope_refusal_rate   0.0%   (목표 0% — 사용자 핵심 지시, 전 라운드 일관)
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
