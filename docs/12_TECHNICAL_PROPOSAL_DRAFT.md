# 기술제안서 초안 — 근거 추적형 금융상품 Analyst

상태: **DRAFT — 8/6 설명회 반영 federated semantic rebaseline(W1-W4)+최종 적대적
리뷰 완료 시점 기준 재작성, 외부 release gate(실 HCX 키·public 배포·9/6 freeze)
미완료**  
최신 실측 수치·외부 gate 목록은 `HANDOFF_CURRENT_STATUS.md`가 항상 최신이며
이 문서와 상충하면 그쪽을 따른다. 제출 형식·페이지 제한 확인 후 PDF로 편집

## 1. Executive summary

금융상품 데이터는 상품군마다 필드·식별자·갱신주기·결측 의미가 다릅니다. 사용자는
“1년 수익률 높은 국내 ETF”, “판매중인 공모펀드”, “AUM 큰 해외 ETF”처럼 자연어로
질문하지만, 단순 LLM 답변은 결측을 0으로 오인하거나 기간·통화가 다른 값을 섞고,
근거 없는 상품·숫자를 생성할 위험이 있습니다.

본 제안은 HyperCLOVA X를 자연어 해석에만 사용하고, 상품 선택·필터·계산·순위는
검증된 QueryPlan과 DuckDB가 수행하는 `grounded financial product analyst`입니다.
모든 결과는 원본 파일·sheet·Excel row·field·raw value로 추적되며, 답할 수 없는 질문은
구체적 조건을 역질문하거나 데이터 부재·비교 불가능 사유를 설명합니다.

## 2. 문제 정의

### 사용자 문제

- 네 상품군을 각각 다른 화면·용어로 찾아야 합니다.
- 자연어의 복합조건, 기간, 상위 N, 판매상태, 지역, 자산군을 수작업으로 바꿔야 합니다.
- 결과가 적거나 많은 이유, 값의 기준일·coverage를 알기 어렵습니다.
- 서로 다른 상품군의 수익률·AUM·위험등급을 같은 기준으로 오인하기 쉽습니다.

### 데이터 문제

- 공식 master 4개는 145,393행·207필드이며 schema가 다릅니다.
- 국내·해외 ETF master에 ETN이 함께 있습니다.
- 해외 ISIN은 공란·중복이 있어 PK가 아닙니다.
- 펀드 95,619행은 상품 95,619개가 아니라 11,139개 `itm_no`와 다중 속성입니다.
- field별 결측·0·sentinel·상수·이상치·기준일 의미가 다릅니다.
- 일부 지표는 낮은 coverage이거나 원천에 아예 없습니다.

### 기술 문제

- LLM의 자유문을 SQL로 직접 실행하면 injection과 잘못된 field 선택 위험이 있습니다.
- LLM이 수치 계산과 상품선택까지 하면 결정성과 재현성이 떨어집니다.
- 답변 문장이 result/evidence 밖으로 확장되면 금융 리스크가 커집니다.

## 3. 제안 솔루션

8/6 설명회는 Ontology Grounding·Knowledge Graph·Federated Retrieval(SQL+Graph+
BM25+Vector)·2단계 플래닝을 기술스펙으로 제시했고, 저희는 이를 전면 채택한
federated semantic rebaseline을 완료했습니다(branch `briefing-rebaseline-v2`,
`docs/14_BRIEFING_REBASELINE_PLAN.md`). 핵심 동기는 **상품군 교차 질의를
무조건 답변**하는 것입니다 — 통화·기간·단위가 다른 상품을 억지로 섞지 않으면서도,
비교 불가능하다는 이유로 거절하지 않습니다.

```text
GET compatibility adapter
-> input/safety guard
-> pre-router (정확 코드·ISIN·ticker는 HCX 생략, 결정론 lookup 직행)
-> Stage-1 HyperCLOVA X: 스코프 중립 "개념" 플랜만 출력
   (물리 field/metric 이름은 절대 노출하지 않음 — 프롬프트/스키마 경량화)
-> Stage-2 grounder: registry/semantic/의 concept_catalog·comparability_matrix·
   value_aliases로 개념->물리 QueryPlan fail-closed grounding
-> local schema/semantic/allowlist validation (기존 QueryPlan 계약 무변경)
-> 단일 스코프: parameterized deterministic DuckDB 실행
   교차 스코프: 스코프별 서브플랜 분해 -> 각각 기존 엔진 그대로 실행 -> 통합순위/
   분리제시/설명전용 중 comparability matrix가 정한 방식으로 융합 + 필수 공시
-> entity 해석 3단 안전망: exact code/ISIN -> KG exact alias -> LIKE 부분일치 ->
   (그래도 없으면) BM25 lexical fallback, RRF 경유 — 어느 단계든 후보가 여럿이면
   항상 기존 역질문 계약으로 흐르고 모호한 매치를 답으로 승격하지 않음
-> EvidenceBundle + Answerability
-> evidence-only renderer
-> strict five-field JSON adapter
```

핵심 분업:

- HyperCLOVA X: 자연어 intent·scope·entity·filter·metric·period·sort·missing slot을
  스코프 중립 개념으로 해석(물리 스키마는 서버만 앎)
- Ontology/semantic 계층(`app/semantics/`): 개념->물리 필드 grounding, 상품군 간
  비교가능성(통합/분리/설명전용/부재) 판정 — registry.py의 구 하드 거절 게이트를
  대체
- Knowledge Graph(`etl/kg.py`): 상품-발행사/운용사 관계를 매 빌드마다 노드·엣지·
  별칭으로 materialize; 현재 실 요청 경로는 이 중 exact-alias 조회만 사용하고
  다중 홉 그래프 순회는 아직 실 서비스에서 호출되지 않는 상태(§11 참고 — 과장
  없이 그대로 밝힙니다)
- Federated retrieval(`app/retrieval/`): 순수 SQL BM25 lexical 인덱스가 entity
  해석의 3단 폴백으로 실제 동작하며 RRF(Reciprocal Rank Fusion)를 경유; vector
  채널은 코드 완성·설계상 비활성(실 임베딩 키 필요)
- DuckDB: 상품 선택·집계·Decimal 계산·정렬·stable tie
- Evidence builder: source row/field/raw/normalized/as-of/quality/coverage 연결
- Renderer: evidence 안의 상품명·숫자만 출력 + 교차상품군 답변은 단위상태·기준일·
  coverage·통화 가정을 의무 공시
- Safety: 전망·보장·단정 추천·fake realtime·missing=0 차단

Docker는 multi-stage로 분리합니다. builder가 full `requirements.txt`와 원본 source·ETL로
hash 검증·full build(KG·lexical 인덱스 포함)·compliance scan을 수행하고, 최종 runtime은
최소 `requirements-runtime.txt`(DuckDB·FastAPI·HTTPX·Pydantic·Uvicorn), app·registry·검증된
serving DB만 포함합니다. 원본 PDF·ZIP·XLSX와 ETL build code는 runtime image에 남기지 않습니다.

## 4. 데이터 모델

### 계층

1. raw: 원본 cell, source locator, row hash
2. clean: Unicode/공백 정규화와 quality state
3. canonical: 공통 product catalog, metric long table, fund attribute bridge
4. serving: quarantine 제외 조회 대상

### 실증 결과

| 항목 | 값 |
|---|---:|
| raw row | 145,393 |
| raw field | 207 |
| logical product/listing | 60,913 |
| quarantine | 10 |
| serving product | 60,903 |
| serving fund attribute | 95,618 |
| metric policy | 59 |
| serving metric evidence | 1,156,332 |

source verify는 내부 XLSX 8/8을 통과했습니다. 현재 serving DB SHA-256은
`4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`입니다.

### 상품 수

| 상품군 | serving |
|---|---:|
| 국내채권 | 42,394 |
| 국내 ETP | 1,733 |
| 해외 ETP | 5,638 |
| 펀드 | 11,138 |

### 품질 상태

현재 1,156,332개 metric evidence는 `VALID` 599,036, `MISSING_NULL` 467,927,
`UNAVAILABLE` 39,328, `ZERO_UNKNOWN` 38,211, `UNUSABLE_CONSTANT` 10,890,
`SENTINEL` 908, `SUSPECT_OUTLIER` 29, `PARTIAL` 3으로 분리합니다. 상품 quarantine 10개는
metric 상태와 별도의 catalog 계층에서 관리합니다.

여기서 raw row는 공식 파일의 물리 행, logical product는 상품키로 중복 제거한 격리 전
상품, serving product는 격리 제외 상품입니다. metric policy의 `raw_denominator`는 격리 전
논리 상품/listing 분모입니다. 따라서 펀드의 `raw_denominator=11,139`와 물리 attribute
95,619행을 같은 분모로 섞지 않습니다.

## 5. QueryPlan과 실행

QueryPlan은 다음만 표현합니다.

- intent: lookup/search/rank/compare/aggregate/explain/clarify/unsupported
- scopes: bond/domestic_etp/overseas_etp/fund
- entity name/code/alias
- AND 조건과 제한된 OR group
- allowlisted metric·aggregation·sort·group
- limit 최대 50
- assumptions와 clarification fields

`explain`은 정확한 상품명·코드·ticker로 단일 상품을 식별한 경우에만 실행합니다. source
field에 있는 상품사실·원본 운용전략·benchmark를 deterministic renderer가 설명하고, target이
없거나 모호하면 역질문합니다. exact target이라도 요청 field가 없으면
`PARTIAL_WITH_COVERAGE/SOURCE_FIELD_ABSENT`로 누락 항목을 밝힙니다. 데이터 밖의 개방형
금융교육·투자 의견은 생성하지 않습니다.

모델이 출력한 raw SQL·URL·도구명·임의 함수·표현식은 실행하지 않습니다. 집계는
`count/sum/avg/min/max` allow-list만 허용합니다. 모든 조건은 canonical registry와 metric
policy를 통과해야 하며 parameterized query로만 컴파일합니다. 순위의 NULL은 마지막,
동률은 `product_uid ASC`로 고정합니다.

다중 metric 순위는 명시된 우선순위를 사용합니다. 1차 metric은 `INNER JOIN`으로 유효
모집단을 정의하고 2차 이후는 `LEFT JOIN`으로 붙여 secondary 결측 상품을 제거하지 않습니다.
각 secondary는 `NULLS LAST`, 최종 tie는 `product_uid ASC`이며 답변에 모든 요청 metric과
누락 상태를 표시합니다. `sum/avg/min/max`는 filter 후 같은 metric·통화의 최신 사용 가능
기준일 universe와 Decimal을 사용하고 row count·as-of·unit을 evidence로 남깁니다.

현재 ETL과 실행 엔진은 같은 `registry/metric_policy_v1.csv` 59행을 읽습니다. canonical
source field 207개는 `canonical_fields_v1.csv`로 매핑합니다. Structured Outputs용 planner
allow-list도 동일 59개와 맞춰 관리하고, registry 변경 시 prompt·contract·fixture를 함께
동기화합니다.

## 6. 역질문

정보가 부족한 질문을 임의 완성하지 않습니다.

예: “수익률 높은 ETF 3개”

1. `market` 누락을 감지
2. 국내/해외 선택지 제시
3. 원 질문과 limit=3 보존
4. 서명된 token 발급
5. follow-up에서 시장을 적용
6. 수익률 기간도 없으면 다음으로 `return_period` 질문
7. 완성된 plan만 실행

명확화 응답에는 `missing_slots`, `options`, `preserved_plan`, `clarification_token`이
들어갑니다. token은 HMAC 서명과 만료를 검증합니다.

일반 missing slot의 선택지는 2~4개입니다. 실제 catalog 검색으로 동일·유사 상품명 후보를
구분하는 `product_identity` 명확화만 최대 12개까지 허용합니다. 이 후보는 서버 데이터에서
만들며 HCX가 임의로 상품을 생성하는 예외가 아닙니다.

기간 선택지는 scope의 Metric Registry에서 실제 source·품질·ranking policy가 허용하는 값만
만듭니다. 해외 ETP처럼 장기수익률 field가 없으면 가짜 기간을 묻지 않고 `UNAVAILABLE`과
AUM·종가·거래량 대안을 제공합니다. 자산유형·지역·위험등급·연금 가능 조건도 scope별
bounded catalog resolver의 정확한 원본 label로만 실행합니다.

## 7. Evidence와 답변 완결성

각 상품 field는 다음을 가집니다.

- product UID, name, rank
- source table/file/sheet/Excel row/field
- raw value, normalized value, unit
- field as-of 또는 `as_of_date=null`·`DATASET_SNAPSHOT_ONLY`; 필드 자체가 없으면
  answerability `UNAVAILABLE`
- source row hash, quality flags

집계에는 source tables/fields/row count/query hash가 들어갑니다. coverage는 raw, serving,
present, valid, rankable을 나누고 질문에 실제 적용된 분모를 표시합니다.

단순 교차 상품 수는 scope별 `COUNT(DISTINCT product_uid)`로 분리해 각 source ID field를
근거화합니다. 통화·기간·단위·위험척도가 필요한 교차 rank·compare는 **더 이상 거절하지
않습니다.** `registry/semantic/comparability_matrix_v1.csv`가 개념별로 `UNIFIED_RANK`
(예: 통화가 동일하거나 자동판별되는 지표는 하나의 순위로 통합) / `SPLIT_PRESENTATION`
(통화·척도가 달라 통합할 수 없으면 상품군별로 나눠 나란히 제시) / `EXPLAIN_ONLY`(수치
비교 자체가 무의미한 개념은 설명형으로) / 대안 제시(해당 개념이 일부 상품군에
아예 없으면 `ABSENT`로 표시하고 있는 상품군의 결과 + 대안을 제시) 중 하나를 결정하고,
어느 경로든 단위상태·기준일·통화가정·coverage를 의무 공시합니다. 이 스키마 자체에
"거절" 값이 없습니다 — 상품군 수만으로 답을 막는 예전 하드 게이트는 폐기했습니다.
renderer는 첫 limitation만 고르지 않고 모든 blocking limitation을 중복 제거해
답변에 표시합니다.

광범위 lookup은 대량 field evidence를 만들기 전에 entity cardinality를 확인합니다. 모호한
대상은 최대 12개의 source-backed 후보와 빈 result items를 가진 역질문으로 전환합니다.
최종 answer 30,000자·serialized context 500,000자·clarification token 10,000자·동적
clarification question 500자 상한은 deterministic 축약 또는 stateless fail-closed로 지킵니다.

## 8. 금융 안전·리스크 관리

| 리스크 | 통제 |
|---|---|
| 환각 상품·숫자 | deterministic result와 field evidence 밖 claim 금지 |
| 결측=0 오인 | missing/zero 별도 상태, imputation 요청 차단 |
| 기간·통화 혼합 | metric policy comparability fail-closed |
| snapshot 실시간 오인 | 2026-07-11 및 field date 표시, live 요청 UNAVAILABLE |
| 미래 수익 보장 | forecast classifier와 renderer validation |
| 단정 추천 | 정보형 비교만 허용 |
| raw SQL injection | schema+allowlist+parameterized SQL |
| 비-HCX LLM 사용 | dependency/endpoint/key scan, 다른 provider 코드 없음 |
| 비공개 평가문제 유출 | GET access log off, no-store; 500 응답도 질문 원문을 재출력할 뿐 서버 로그엔 예외 타입만 남기고 원문·트레이스백은 남기지 않음 |
| 마감 후 결과 변경 | image digest+source/prompt/config manifest, freeze runbook |
| eval 하네스 자체의 채점 결함(자체 발견) | 3-agent 적대적 리뷰로 cross_rank 정답판정 미반영·behavior 공시문구-만으로-통과 허점을 발견·수정, 640문항 전수 재검증(`docs/15` §0) — green 숫자를 액면가로 신뢰하지 않고 채점 로직 자체를 주기적으로 재검증 |

## 9. 대표 시나리오

### 정확 조회

“채권 코드 KR101501DA16의 상세 정보” → 정확한 상품 한 개와 `PD_NO`, `PD_NM` 근거.

### 복합 순위

“공모이면서 판매중인 펀드를 1년 수익률 높은 순으로 3개” → 공모+판매중 universe
8,445개, 1Y 보유 6,936개, deterministic top 3, 이상치·share class limitation.

펀드 full serving 11,138과 API 기본 공모 universe 11,115는 분리합니다. API 기본 공모
universe의 위험등급 보유는 8,564·결측 2,551, 판매상태는 판매중 8,445·판매완료
2,670입니다. 국내 ETF 1Y는 source-present 986, quality-valid 951, 공통 최신 원천일
`2026-06-15` 기준 rankable 940으로 단계별 분모를 구분합니다.

### 데이터 품질 차단

“국내 ETF 총보수 낮은 5개” → ETF 1,201개 중 값 217, zero 150의 의미 미확정이므로
임의로 zero를 최저비용이라 보지 않고 `DATA_QUALITY_BLOCKED`.

### 데이터 부재

“해외 ETF 1년 수익률 최고 상품” → 원본 field 없음, `UNAVAILABLE`, AUM·종가·거래량
대안 제시.

### 역질문

“수익률 높은 ETF 3개” → 국내/해외와 기간 확인 후 실행.

### 금융 안전

“내 전재산을 넣을 상품 하나를 반드시 추천” → 단정 추천 거부, 조건 기반 정보형 비교 안내.

## 10. 평가기준 대응

| 평가축 | 구현 근거 |
|---|---|
| 문제정의 | 자연어 편의와 금융 데이터 의미·리스크를 동시에 해결 |
| 기술완성도·성능 | 단일 서비스, DuckDB, typed plan, parameterized SQL, Docker; TPM 예약량
  2단계 플래닝으로 −58.6%(§12) |
| 창의성·확장성 | explicit answerability, field provenance, registry-driven schema 확장,
  Ontology grounding(개념->물리 fail-closed), Knowledge Graph, Federated
  retrieval(BM25 live+RRF, vector 배선 완료·키 대기) |
| 정확성·완결성 | 40 gold+10 policy fixture, exact product/order/value/evidence assertions;
  독립 SQL oracle 기반 640문항 eval 100%, metamorphic(동의어/어순 불변) 137/137 |
| 현업 활용성·리스크 | coverage/as-of/quality/comparability/safety/freeze controls; 상품군 교차
  질의 무거절(거절률 0%) + 의무 공시(98.55%) |
| 설명회 기술스펙 반영 | Ontology(SEM-001)·Knowledge Graph(SEM-002)·Federated Retrieval
  (SEM-003)·2단계 플래닝(SEM-004) — `artifacts/requirements_traceability.csv`에
  실 요청 경로에서 무엇이 live이고 무엇이 코드 완성·미배선 상태인지 과장 없이 명시 |

## 11. 검증 결과

federated semantic rebaseline(W1-W4) 완료 후, 코드를 읽기만 하는 게 아니라 실제로
실행해 검증하는 3-agent 적대적 리뷰를 자체 발주했습니다. 크래시 버그 1건(교차상품군
분리 제시 결과가 EvidenceBundle 50건 제한을 우회), 무공시 오답 버그 1건(스코프
1개짜리 개념이 통합순위를 시도하다 빈 답변), eval 하네스 자체의 채점 결함(정답 여부를
계산만 하고 통과 판정에 미반영)을 찾아 모두 수정했습니다. 채점을 고치자 정확도가
100%→95.78%로 드러났고, 27건을 전수조사해 앱 버그가 아니라 오라클이 앱의 기존
정책 2가지를 놓치고 있었음을 확인·수정한 뒤 100%로 정직하게 재수렴했습니다
(`docs/15_REBASELINE_VALIDATION_REPORT.md` §0). 아래는 이 리뷰 이후 재검증한 수치입니다.

- full-source ETL build 통과(KG·lexical 인덱스 빌드 스테이지 포함)
- v1 metric policy 59행과 serving metric evidence 1,156,332행 재현
- source XLSX 8/8 검증
- 전체 pytest **238/238** 통과
- 독립 SQL oracle 기반 eval **640/640(100%)**, 상품군 교차 거절률 **0.0%**, 공시율
  **98.55%**
- metamorphic(동의어·어순 불변) **137/137**
- gold/policy fixture 50/50, plan subset 40/40, 선언 assertion 103개 통과
- API contract·역질문 후속 통과
- HCX mock success/length/429 통과; Stage-1/2단계 플래너 결과 동일성 mock-HCX E2E로
  실증(단, 640문항 전체를 2단계 경로로 재현하는 A/B는 아직 미실행 — eval 하네스가
  결정론 플래너를 직접 구동하기 때문)
- runtime non-HCX scan **84 files/0 findings**(scripts·tests·eval·deploy까지 스캔
  범위 확장)
- current-code real local HTTP E2E 15/15
- 100요청·동시성 10 부하 smoke 100/100, failure 0, p95 131.75ms
- **Docker fresh --no-cache build/run/restart parity 통과**: 컨테이너 내부에서
  source verify→full ETL(KG 71,683 node/206,274 edge/249,874 alias, lexical
  80,670 doc)→compliance scan까지 재현, 15-case smoke가 restart 전후 동일 결과,
  production 모드(`APP_ENV=production`+`PLANNER_MODE=hcx`)는 실 키 없이 fail-closed로
  즉시 종료함을 확인(정상 설계)

아직 실행하지 못한 외부 gate:

- 실제 팀 HCX credential을 사용한 live plan·네 상품군 E2E
- immutable image digest를 포함한 release manifest FINAL 전환
- public TLS/domain deployment
- 실제 release Git SHA
- 2026-08-06 주최 측 설명회의 최종 API contract·허용 model 확인
- 임베딩 캐시 생성 후 vector 채널 활성화(`VECTOR_ENABLED=true`)

`HCX-007`은 현재 Native Structured Outputs를 위한 `TEAM_DECISION` 기본값입니다. 주최 PDF는
HyperCLOVA X 사용만 확정했으므로 정확한 허용 model ID를 공식 조건으로 단정하지 않습니다.

## 12. 운영·비용

- 제출 runtime의 언어모델은 HyperCLOVA X만 사용하며 Codex는 개발 도구일 뿐 runtime에 넣지 않음
- embedded read-only DuckDB, 단일 VM 또는 managed container, 2 vCPU·RAM 2~4 GiB, worker 1
- Compose 상한 2 CPU·3 GiB; platform TLS 우선, 제공되지 않을 때만 Caddy 사용
- 현재 team baseline HCX planner 출력은 1,024 token 이하, temperature 0, thinking off
- 2단계 플래닝(Stage-1은 개념만 출력하는 축소 스키마)으로 요청당 TPM 예약량
  13,013B→5,383B(**−58.6%**) 실측, 기본 `PLANNER_STAGE=one`은 검증 완료된 구
  스키마를 유지하며 `two`는 opt-in
- 429/5xx 제한 재시도, 다른 LLM fallback 없음
- result limit 50, 입력 2,000자
- 질문 원문 access log 비활성화; 서버 예외도 원문·트레이스백을 별도 로그에 남기지 않음
- uvicorn `--limit-concurrency 64`로 재시도 폭주·하네스 버그로 인한 NCP 크레딧
  초과(주최 미보전) 리스크에 상한
- 실제 key 발급 후 provider response header의 QPM/TPM을 기록하고 더 작은 내부 budget 적용

## 13. 확장 로드맵

P0는 정확한 API E2E입니다. 그 다음에만 다음을 검토합니다.

- 공식 참고질의 기반 synonym/plan 회귀 확장
- 승인된 외부 데이터의 별도 source namespace
- 상품명 semantic retrieval 또는 reranking
- 현업용 UI와 비교표
- 공식 mapping이 확보된 share-class family 보기

개인화 추천·portfolio optimization·실시간 시세는 별도 법무·데이터·모델 검증 없이
현재 제품에 넣지 않습니다.

## 14. 제출 전 확인

- 설명회 변경 반영 뒤 동일 release candidate에서 full pytest·gold/policy fixture·
  15-case real HTTP·640문항 eval·137그룹 metamorphic을 다시 실행하되, 변경으로
  test/문항 수가 달라지면 최신 수집값을 report와 함께 갱신
- 설명회 API/model/data 답변 반영
- optional clarification pair의 최종 API 허용 여부와 request schema 확정
- 주최 허용 model ID·실제 HCX credential E2E
- ~~Docker fresh build/run/restart~~ — **완료**(§11). freeze 직전 최종 커밋 기준으로
  1회 더 재확인만 필요
- public TLS endpoint와 external smoke
- OpenAPI와 실제 response 동기화
- 기술제안서 형식·페이지·용량 확인
- 최신 `artifacts/eval_report.json`/`metamorphic_report.json`을 release 환경에서 재검증
- 실제 Git SHA, registry image digest, image-extracted DB, source/prompt/registry/test report로
  hardened FINAL manifest 생성(현재 DRAFT는 3커밋 전 SHA를 참조 중 — freeze 직전 반드시
  `scripts/generate_release_manifest.py` 재실행)
- 09.05 내부 freeze, 이후 결과 변경 금지
