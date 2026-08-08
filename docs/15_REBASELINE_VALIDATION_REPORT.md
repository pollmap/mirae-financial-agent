# 재기준화 검증 리포트 (W1-W4 실측 + 2회의 사후 리뷰)

기준: 2026-08-08, branch `briefing-rebaseline-v2`, HEAD는 이 문서 커밋 시점 `git log -1`.
`docs/14_BRIEFING_REBASELINE_PLAN.md`에서 정의한 게이트의 실측 결과다. 측정하지 않은
항목은 "미측정"으로 명시하고 추정치를 대신 적지 않는다.

**중요 — §0, §0-2, §0-3을 먼저 읽을 것.** 이 문서의 최초 버전(W1-W3 시점)이
보고한 "640/640 100%, cross_scope 69/69 정답"은 사후 적대적 리뷰(§0)에서
**채점 로직 자체의 결함**으로 밝혀졌다. 거절률 0% 측정치는 그때도 지금도
유효하지만, "순위·값이 정확하다"는 부분은 당시 검증되지 않은 채로 통과
처리되고 있었다. 그 리뷰로 앱 코드를 고친 뒤, 사용자가 한 번 더 종합 점검을
지시했고(§0-2), 이번엔 제출 문서(기술제안서·README·다이어그램)가 재설계 이전
내용을 그대로 담고 있었던 것과 requirements traceability의 과장을 발견·
수정했다. 마지막으로 §0-2가 낮은 우선순위로 남겼던 두 항목(`normalize_party`
scope-blind 병합, `etl/kg.py` 단위테스트 부재)도 마저 처리했고, 그 과정에서
실 데이터에 존재하던 진짜 병합 버그를 발견·수정했다(§0-3). §0·§0-2·§0-3에
전체 경위를 기록한다.

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

## 0-2. 최종 종합 점검에서 추가로 발견·수정한 것 (2026-08-08, 커밋 `e577107`)

`5c85af5` 커밋 직후 사용자가 "남은 것·미흡한 것·개선할 것을 전부 체크해서
개발하라"고 재지시했다. Docker Desktop 데몬이 이 시점에 살아나 있어 이전에
못 했던 fresh build/restart 검증을 실제로 완료했고, 이전 3-agent 리뷰가
다루지 않은 4개 영역(제출 문서 정확성, API 보안/견고성, 이전 리뷰의 보류
항목 재검토, 배포/설정 준비도)을 병렬 에이전트로 감사했다.

**Docker (실제로 빌드·실행·재시작해서 검증, §6의 "실측 못함" 목록에서 제거)**:
`docker build --no-cache`가 컨테이너 안에서 source verify→전체 ETL(KG·lexical
빌드 스테이지 포함)→compliance scan을 재현했고, 카운트가 §5와 정확히 일치했다
(kg_nodes 71,671/kg_edges 206,274/kg_aliases 249,857, lex_docs 80,670). 이미지
기본값(`APP_ENV=production`+`PLANNER_MODE=hcx`)은 실 키 없이 fail-closed로 즉시
종료함을 확인했다(의도된 설계). `PLANNER_MODE=deterministic` override로 재실행한
컨테이너는 `docker restart` 전후 15-case smoke가 동일했다.

**제출 문서 정확성 — 가장 심각한 발견**: `docs/12_TECHNICAL_PROPOSAL_DRAFT.md`
(주최 요구 기술제안서 본문)가 재설계 이전 단일단계 아키텍처를 그대로 서술하고
있었고, §7은 "통화·기간·단위·위험척도가 필요한 교차 rank·compare는 기존
fail-closed 정책을 유지합니다"라고 **명시적으로 틀린 주장**을 하고 있었다 —
이 프로젝트 전체를 재설계하게 만든 사용자의 핵심 지시("교차 질의는 어떠한
제한이나 한계가 있어도 안 됨")와 정면으로 배치되고, 실측(교차 거절률 0%)과도
모순됐다. `README.md`와 `docs/04_PRODUCT_ARCHITECTURE_SPEC.md`도 각각 같은
계열의 낡은/거짓 문장("호환되지 않는 지표의 교차 순위는 계속 차단", "14.5만
행은 GraphDB가 필요한 규모가 아님" — 뒤 문장은 이후 실제로 KG를 만들면서
모순됨)을 담고 있었다. `docs/diagrams/architecture.mmd`·`request-sequence.mmd`
(저장소의 유일한 아키텍처 다이어그램, 제안서에 들어갈 가능성이 가장 높은
자료)도 KG/federated retrieval/grounder 노드가 전혀 없는 구버전이었다. 전부
현재 아키텍처와 실측 수치로 재작성했다.

**`artifacts/requirements_traceability.csv`의 SEM-002/SEM-003 과장**: 이전
리뷰(§0)가 "federated retrieval 모듈 대부분이 실 호출자 없음"을 발견했는데,
이번 라운드는 이를 grep으로 독립 재확인해 더 엄격한 사실을 확인했다 —
`graph_retriever.traverse()`(WITH RECURSIVE 순회)와 party 조회 함수들
(`resolve_party_nodes`/`products_for_party`), `router.route_theme_query`는
실 요청 경로뿐 아니라 **자체 단위테스트에서도 호출자가 0건**이다. 그런데
traceability CSV는 이 함수들을 SEM-002("Knowledge Graph")·SEM-003
("Federated Retrieval")의 구현 근거로 인용하고 있었다. 문구를 실제 상태가
드러나게 다시 썼다: 무엇이 live인지(SQL exact/alias/LIKE + BM25 fallback,
이제 RRF 경유), 무엇이 격리 단위테스트만 있는지(router/fusion), 무엇이
테스트조차 없는지(graph traversal/party 함수)를 구분했고, 640문항 eval이
lexical fallback을 단 한 번도 트리거하지 않는다는 사실(§1의 disclosure_rate
산정 근거 데이터에서 확인됨)도 명시해 "eval 640/640"을 federated retrieval의
증거로 오인하지 않도록 했다.

**API 보안/견고성 감사**: 라이브 요청 경로 대부분이 깨끗했다 — SQL은 전부
parameterized(f-string은 서버측 allow-list 필드명에만 사용), `limit`/`top_n`은
Pydantic `QueryPlan` 모델 레벨(`Field(ge=1, le=50)`)에서 모든 생성 경로에
강제되고 있어 grounder.py의 clamp는 중복 방어였음이 확인됐다, 클라이언트 500
응답은 절대 내부 정보를 노출하지 않는다. 다만 두 가지는 고쳤다: (1) uvicorn에
연결 동시성 상한이 없어 재시도 폭주가 NCP 크레딧(주최 미보전)을 무한정 소진할
수 있었다 — `--limit-concurrency 64`를 Dockerfile과 compose.yaml 양쪽에 추가.
(2) Starlette가 예외 핸들러 실행 후 재발생시켜 uvicorn 자체 로거가 전체
트레이스백을 컨테이너 stderr에 남기는 것을 확인했다 — 클라이언트에는 유출되지
않지만, 앱 자체 로거에 예외 **타입명만**(원문·트레이스백 제외) 남기는 안전한
신호를 추가해 향후 코드가 실수로 예외 메시지에 사용자 텍스트를 넣더라도
운영진이 로그에서 상황을 알 수 있게 했다.

**배포/설정 감사**: `.env.example`에 W2/W3에서 추가된 `PLANNER_STAGE`·
`VECTOR_ENABLED`·`HCX_TPM_BUDGET`이 누락돼 있어 추가했다. **`artifacts/
release_manifest.generated.json`이 `4afc169`(W3 완료 시점, crash 버그·무공시
오답 버그 수정 3커밋 전)를 참조하고 있어 이후 상태를 전혀 반영하지 못하고
있었다** — freeze 직전이 아니라 지금 재생성해 `e577107` 기준으로 갱신했다
(여전히 `DRAFT`). `scan_runtime_compliance.py`가 `requirements-dev.txt`와
`scripts/`·`tests/`·`eval/`·`deploy/`를 스캔 범위 밖에 두고 있어(44 files만
스캔) 확장했다(84 files) — 확장 직후 스캐너가 **자기 자신을 오탐지**하는
버그를 발견했다: `non_hcx_secret` 규칙의 `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/
`COHERE_API_KEY` 세 패턴이 (이미 `"open"+"ai"`처럼 분할 처리된 다른 패턴들과
달리) 문자열 분할 없이 그대로 박혀 있어서, `scripts/`가 스캔 대상에 들어가자
스캐너 자신의 소스 코드(패턴 정의 그 자체)가 자기 규칙에 걸렸다. 같은 분할
기법을 적용해 수정하고 재스캔으로 0 findings를 확인했다.

**추가 개선**: `_lexical_entity_fallback`(entity 해석 3단 폴백)이 `lexical_
retriever.search` 결과를 그대로 반환하던 것을 `reciprocal_rank_fusion` 경유로
바꿨다. RRF 점수 `1/(k+rank)`는 단일 채널 내에서 rank에 대해 항상 단조감소이므로
채널이 하나뿐인 현재는 순서에 수학적으로 no-op임을 증명한 뒤 적용했다 — 즉
이 변경은 어떤 답변도 바꾸지 않지만, "federated retrieval"이 fusion.py를 실제로
호출하지 않는 열망적 주장에서 실제로 호출하는 참인 주장으로 바뀐다.

**낮은 우선순위로 재확인만 하고 보류한 것** (§0의 목록과 동일한 것들을 이번엔
더 구체적인 조치안까지 검토): `etl/kg.py`의 role/merge 로직 자체를 검증하는
전용 단위테스트는 여전히 없음(구조적 invariant만 검증) — 60-90분 정도의 합성
데이터 테스트로 가능하다고 판단했지만, 그래프 함수 실 호출자가 0건이라
리스크도 0이라 그래프 기능을 실제로 라이브 배선하기 전으로 미뤘다.
`normalize_party`는 여전히 scope 구분 없이 정규화명만으로 병합한다(예:
"Value Partners Ltd"와 "Value Partners LLC"가 실제로는 다른 회사여도 병합될 수
있음) — 수정 자체는 `kg.py`의 groupby에 `scope`를 추가하는 한 줄이지만,
node_id 구성과 edge의 dst_node_id 참조도 함께 바꿔야 하고 재빌드로 카운트가
바뀌므로, 호출자가 없어 리스크가 0인 지금 서둘러 절반만 고치기보다 그래프
기능을 라이브로 연결하는 시점에 함께 하기로 했다.

**재검증**: 이 라운드의 모든 코드 변경 후 전체 스위트를 다시 실행했다(이전
실행값 재사용 아님) — pytest 238/238, eval 640/640(100%, 거절률 0%, 공시율
98.55%), metamorphic 137/137, ruff clean, compliance 84 files/0 findings.

## 0-3. 보류 항목 마무리 (2026-08-08, 커밋 `c9efb67`)

사용자가 §0-2에서 낮은 우선순위로 남긴 두 항목("보류한 것도 마저 해!!!")을
마저 처리하라고 재지시했다. 둘 다 그래프 party 함수의 실 호출자가 0건이라
지금까지는 리스크가 0이었지만, 방치할 이유는 없었다.

**`normalize_party` scope-blind 병합 수정**: `etl/kg.py`의 party 노드 병합이
`groupby("normalized")`만 사용해 스코프를 무시하고 있었다 — 채권 발행사와
국내 ETP 운용사가 우연히 같은 정규화 문자열로 귀결되면(예: "Value Partners
Ltd"와 "Value Partners LLC" — `normalize_party`가 Ltd/LLC를 같은 접미사로
취급) 서로 다른 두 실제 법인이 party 노드 하나로 병합될 수 있었다.
`groupby(["scope", "normalized"])`로 수정하고 `node_id`를
`party:<normalized>`에서 `party:<scope>:<normalized>`로 바꿨다(연쇄적으로
edge의 `dst_node_id`, alias의 `node_id` 구성도 함께 수정 — 세 곳 다 고쳐야
일관성이 유지됨을 확인하고 진행). **실 데이터로 재빌드한 결과 kg_node
71,671→71,683(+12), kg_alias 249,857→249,874(+17)로 실제로 바뀌었다** —
즉 이론적 위험이 아니라 60,903개 실 상품 데이터 안에 스코프간 이름충돌이
12건 실재했고, 지금까지 조용히 잘못 병합되고 있었다는 뜻이다. kg_edge 총
206,274건은 변화 없음(어떤 party 노드를 가리키는지만 바뀌었을 뿐 edge 자체
개수·타입 분포는 무관).

**`etl/kg.py` 전용 단위테스트 추가**: `tests/unit/test_kg.py` 8개 — 스코프별
역할 배정(채권/ETN→issuedBy, ETF→managedBy), 동일 스코프 내 표기변형 병합,
서로 다른 이름은 병합 안 됨(`한국투자` vs `한국투자증권`), **위 수정의 회귀
테스트**(동일 정규화명이 스코프가 다르면 병합되지 않음을 직접 검증), OFFICIAL/
NORMALIZED alias 태깅, 해외 benchmark sentinel 제외, 펀드는 manager_code
전용 노드(이름 발명 안 함), 구조적 invariant. `test_lexical.py`와 같은
스타일로 `duckdb.connect(":memory:")` 합성 데이터를 써서 14.5만행 실 ETL을
매번 돌릴 필요 없이 이 스테이지 자체의 로직만 빠르게 검증한다.

**테스트 작성 중 실제로 두 번째 버그를 잡았다**: 펀드 전용(채권/ETP 0건) 합성
데이터로 `build_kg`를 호출했더니 `kg_edge`를 만드는 첫 `CREATE TABLE ... AS
SELECT`가 빈 결과셋이라 DuckDB가 텍스트 컬럼 하나를 INT32로 잘못 추론했고,
바로 다음 INSERT(실제 문자열 데이터)가 변환 에러로 죽었다. 14.5만행 실
데이터에서는 채권·ETP가 issuer/manager 없이 전부 비는 일이 있을 수 없어 절대
발생하지 않지만, 원인 규명 후 해당 CREATE TABLE의 모든 텍스트 표현식에
`CAST(... AS VARCHAR)`를 명시해 방어했다(비용 거의 0, 실 데이터 경로는
동작 무변화 — 전체 스위트 재실행으로 확인).

**재검증**: pytest **246/246**(238 + 신규 kg 테스트 8개), eval 640/640(100%,
KG graph 경로는 아직 live 요청 경로 밖이라 이 결과는 무변화 — 예상된 결과),
metamorphic 137/137, ruff clean. 이걸로 §0-2의 두 보류 항목이 모두
해소됐다 — 남은 "낮은 우선순위" 항목은 없다.

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
kg_node                    71,683   (scope-aware party 병합 수정 후, §0-3 — 이전 71,671)
kg_edge                   206,274   (managedBy 6,782 · issuedBy 42,145 · hasAssetType 60,895
                                      · hasRiskGrade 52,692 · inRegion 18,501 · tracksBenchmark 14,129
                                      · managedByCode 11,130)
kg_alias                  249,874   (scope-aware party 병합 수정 후 — 이전 249,857)
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
