# 제품·아키텍처 설계서

상태: 설명회 전 v1.1 실행 기준  
성격: `TEAM_DECISION` 중심 문서

## 1. 제품 정의

사용자의 자연어 질의를 HyperCLOVA X가 검증 가능한 `QueryPlan`으로 변환하고,
정형 데이터 엔진이 네 상품 마스터를 결정론적으로 검색·필터·정렬·계산한 뒤,
필드 단위 근거와 안전한 한국어 답변을 공개 GET API로 반환합니다.

### 핵심 가치

- 정확한 상품 집합
- 정확한 순위와 계산
- 재현 가능한 근거
- 데이터 한계의 정직한 표현
- 금융 리스크 통제
- 평가 API 안정성

### 제품이 하지 않는 것

- 개인별 적합성 판단
- 매수·매도 지시
- 미래수익 예측
- 실시간 가격 보장
- 포트폴리오 최적화
- 범용 금융상담

## 2. 설계 원칙

1. LLM은 해석하고, 코드는 선택·계산합니다.
2. 원본값·정제값·파생값을 분리합니다.
3. 모든 숫자와 상품명은 evidence에 존재해야 합니다.
4. `NULL`, 공백, 0, sentinel, 적용대상 아님을 구분합니다.
5. 같은 의미·기간·단위·통화인 metric만 비교합니다.
6. 모호성은 임의 가정보다 명확화가 우선입니다.
7. API 계약은 핵심 엔진에서 격리합니다.
8. 평가기간에는 한 개의 immutable release만 운영합니다.

## 3. 권장 전체 구조

```text
Client / Organizer Evaluator
        |
        v
GET Compatibility Adapter
        |
Input Guard and Normalizer
        |
HyperCLOVA X Planner
        |
Typed QueryPlan Validator -----> Clarification / Unsupported response
        |
Canonical Query Compiler
        |
DuckDB + Parquet Serving Views
        |
Metric and Formula Registry
        |
Evidence Bundle Builder
        |
Deterministic Evidence Renderer
        |
Claim, Safety, and Contract Validator
        |
Strict JSON Response Adapter
```

초기 구현은 단일 FastAPI 서비스와 단일 DuckDB 파일을 권장합니다. 14.5만 정적 행은
분산 시스템이나 GraphDB가 필요한 규모가 아닙니다. 단일 프로세스가 재현성, 장애
지점, 배포속도, 정량 정확도 면에서 가장 유리합니다.

## 4. 데이터 계층

### Raw

원본 보존과 감사 목적입니다.

- source file, sheet, table
- Excel row number
- raw cell values
- source row hash
- source file SHA-256
- ingestion version

### Clean

- Unicode NFC 정규화
- 원본 문자열과 trim 문자열 병존
- 숫자 명시적 cast
- 날짜 명시적 parse
- 공백·NULL·sentinel 분류
- 코드값 정규화
- invalid·outlier·damaged flag

### Canonical

- 상품 identity
- 상품군별 상세 속성
- 공통 분류축
- canonical metric
- alias
- provenance
- data-quality status

### Serving

- 평가 API의 read-only DuckDB view
- stable product key와 deterministic tie-breaker
- quarantine 기본 제외
- precomputed search text와 alias

## 5. 권장 데이터 모델

### product_catalog

```text
product_uid
official_product_group
internal_product_type
source_table_id
source_product_id
listing_id
instrument_id
product_family_id
name_ko
name_en
short_name
ticker
isin
exchange
manager_or_issuer
asset_class_raw
asset_class_normalized
region_raw
region_normalized
product_currency
trading_currency
risk_raw
risk_normalized
sale_status
trading_status
snapshot_date
record_as_of
source_row_hash
quality_status
```

### bond_snapshot

- 발행일·만기일·잔존일
- 채권 유형
- 표면금리
- 신용등급
- 위험등급
- 발행잔액
- 듀레이션·컨벡서티
- 평가가격
- 적용수익률
- 매수·세전·세후 수익률
- 매수가능수량

### domestic_etp_snapshot

- 내부 유형 ETF·ETN
- 투자자산군·지역
- 운용사·전략·기초지수
- 위험등급·연금분류
- 1D·1M·3M·6M·1Y·YTD 수익률
- 순자산·AUM·NAV
- 가격·거래량·거래대금
- 총보수·기타비용
- 판매·거래 상태

### overseas_etp_snapshot

- 내부 유형 ETF·ETN
- 티커·ISIN·상장시장·통화
- 자산군·지역
- 운용사·기초지수·영문 전략
- 총보수
- AUM·NAV·가격·거래량
- inverse·tracking·replication 속성

원본에 1M·3M·1Y 장기수익률 열이 없으므로 생성하지 않습니다.

### fund_product

- `itm_no` 기준 상품
- 상품명·유형·지역·통화
- 벤치마크
- 환헤지
- 공모·사모
- 판매상태
- 위험등급
- 순자산
- 1W·1M·3M·6M·18M·1Y·2Y·3Y·5Y 수익률
- 대표펀드 후보 코드

### fund_attribute

- fund_product_uid
- `prfd_attr_cd`
- attribute code
- source row number

공모펀드 원본 95,619행은 11,139개 `itm_no`와 다중 속성 태그의 결합입니다.
상품과 속성을 분리하지 않으면 count, 평균, AUM이 반복 횟수만큼 왜곡됩니다.
손상행 1개를 제외한 serving bridge는 95,618행입니다. 228개 코드의 완전한 설명
사전은 제공되지 않았으므로, codebook을 받기 전에는 코드 자체를 사용자 의미와
임의 연결하지 않습니다.

### metric_registry

```text
metric_id
display_name_ko
aliases
applicable_scopes
source_table
source_field
data_type
unit
currency_policy
metric_currency
horizon
basis
as_of_field
coverage_count
coverage_ratio
missing_policy
zero_semantics
sentinel_policy
comparability_group
ranking_allowed
aggregation_allowed
cross_product_allowed
quality_grade
registry_version
```

### source_locator

```text
product_uid
metric_id
source_table_id
source_file
source_sheet
source_excel_row
source_field
raw_value
normalized_value
unit
as_of_date
source_row_hash
```

`record_as_of`가 원본에 없는 경우 추정값을 채우지 않습니다. 이때
값은 존재하지만 개별 날짜가 없으면 `as_of_date=null`,
`as_of_status=DATASET_SNAPSHOT_ONLY`, `snapshot_date=2026-07-11`로 기록하고 답변에는
개별 기준일 미제공을 제한사항으로 표시합니다. 요청 필드·값 자체가 없을 때의
answerability만 `UNAVAILABLE`입니다.

## 6. Product identity

```text
BOND:PRBD01N001:{PD_NO}
KR_ETP:PREF01N001:{pd_itm_no}
GLOBAL_ETP:PREF02N001:{pd_itm_no}
FUND:PRFD01N001:{itm_no}
```

- 국내채권 `PD_NO`는 전 행 고유입니다.
- 국내 ETP `pd_itm_no`와 `pd_itm_no_ma`는 각각 전 행 고유입니다.
- 해외 ETP `pd_itm_no`는 고유입니다.
- 해외 ISIN은 공란 9건과 중복 초과행 50건이 있어 단독 PK로 사용할 수 없습니다.
- 펀드 `itm_no`는 상품 key, `prfd_attr_cd`는 다중 속성 tag입니다.
- 손상된 펀드 1행은 격리하고 canonical 상품에서 제외합니다.
- 이름·약어가 여러 상품에 매칭되면 코드·거래소·상품군을 역질문하거나 후보를 모두
  반환합니다. 임의의 한 상품을 선택하지 않습니다.
- ETP master와 공모펀드 master에서 경제적으로 같은 상품처럼 보이는 행은 자동 병합하지
  않고 `cross_source_equivalence_status=UNVERIFIED`로 둡니다.

## 7. Typed QueryPlan v1.1

HCX가 raw SQL이나 원본 컬럼명을 자유 생성하지 않도록 합니다.

```json
{
  "version": "1.1",
  "intent": "lookup|search|rank|compare|aggregate|explain|clarify|unsupported",
  "scopes": ["bond|domestic_etp|overseas_etp|fund"],
  "entities": [
    {"name": null, "code": null, "scope": null}
  ],
  "filter_groups": [
    {
      "join": "AND",
      "conditions": [
        {
          "field": "product.risk_grade",
          "op": "eq|ne|gt|gte|lt|lte|between|in|contains|is_null|is_not_null",
          "value": 3,
          "value2": null,
          "unit": null
        }
      ]
    }
  ],
  "groups_join": "OR",
  "metrics": ["domestic_etp.return_1y", "domestic_etp.aum_last"],
  "aggregations": [],
  "sort": [
    {"field": "domestic_etp.return_1y", "direction": "desc", "nulls": "last"}
  ],
  "group_by": [],
  "limit": 5,
  "assumptions": [],
  "needs_clarification": false,
  "clarification_question": null,
  "missing_slots": [],
  "clarification_options": [],
  "preserved_plan": null
}
```

`explain`은 정확한 상품명·코드·ticker로 단일 상품 target이 확정될 때만 실행합니다.
target이 없거나 여러 상품이 매칭되면 `explanation_target` 또는 `product_identity`를
역질문합니다. 답변은 canonical product fact와 source-backed 원본 운용전략·benchmark를
deterministic renderer가 구성하며, 공식 데이터 밖의 개방형 금융교육·투자 의견은 생성하지
않습니다. exact target이어도 요청한 field 값이 없으면
`PARTIAL_WITH_COVERAGE/SOURCE_FIELD_ABSENT`와 누락 metric을 표시합니다.

### 검증 규칙

- intent, scope, field, metric, operator allow-list
- aggregate function·field·distinct·상품단위 universe 검증
- 최대 filter 수와 limit 상한
- 상품군별 지원 field 확인
- metric 기간·단위·통화·기준일 확인
- raw SQL·URL·tool name·임의 수식 금지
- Structured Output 또는 local semantic validation 실패 시 임의 계획으로 보정하지 않고
  controlled unavailable로 fail-closed
- 429·5xx transport retry와 invalid plan의 의미적 repair를 구분; 현재 의미적 repair는 구현하지 않음
- 무한 agent loop 금지

## 8. Query execution

검색 순서:

1. 정확한 상품 ID·ticker·코드
2. 정규화된 exact name·alias
3. token·trigram·fuzzy lexical search
4. 영문 전략 등 장문 의미검색은 보조 retrieval

숫자 필터와 순위는 parameterized SQL로 수행합니다. Vector 검색은 숫자 비교의
주 경로로 사용하지 않습니다.

결정론 규칙:

- NULL은 기본 `last`
- 마지막 tie-breaker는 `product_uid ASC`
- 같은 validated QueryPlan·release·data에서 같은 순서 반환
- result limit 상한
- quarantine 기본 제외
- SQL과 parameter, row count, metric version 기록
- Decimal 기반 계산과 명시적 반올림·동률 규칙 적용
- 수익률 기간 선택지는 Metric Registry의 scope별 실제 source·품질·ranking policy에서 생성;
  해외 ETP에는 가짜 장기 기간 선택지 없이 실제 가능한 대안 반환
- 자산유형·지역·위험등급·연금 가능 여부는 bounded catalog resolver의 정확한 scope label만 허용
- 다중 metric rank는 primary metric `INNER JOIN`, secondary metric `LEFT JOIN`; secondary
  결측 상품을 보존해 `NULLS LAST`, 모든 요청 metric을 evidence와 답변에 표시
- `sum/avg/min/max`는 filter 후 같은 metric·통화의 최신 사용 가능 기준일 universe에서 계산

JSON Schema는 구조를 검증하고, 실제 field·metric allow-list와 `between`·`in`·NULL
연산의 값 형태는 semantic validator가 별도로 검증합니다. 현재 ETL과 runtime의 source of
truth는 `registry/canonical_fields_v1.csv` 207행과 `registry/metric_policy_v1.csv` 59행입니다.
`etl/build.py`와 `app/execution/registry.py`가 v1 policy를 직접 읽습니다.
`app/planner/schema.py`의 HCX allow-list는 Structured Outputs 제약상 명시 목록으로 유지되므로
registry 변경 시 계약·prompt·테스트와 함께 동기화합니다. `artifacts/*_v0.csv`는 이전 감사
산출물이며 현재 실행 정책이 아닙니다.

## 9. Answerability Engine

실행 전 다음을 검사합니다.

1. 대상 상품군이 범위에 있는가
2. 요구 metric이 존재하는가
3. 기간·단위·통화·basis가 정의됐는가
4. 유효 coverage가 있는가
5. 0과 결측의 의미가 정의됐는가
6. 비교 대상이 같은 comparability group인가
7. 기준일이 비교 가능한가
8. 필터 후 유효 표본이 충분한가
9. quarantine이 결과에 포함되는가
10. 전망·단정적 투자권유 요청인가
11. 모호성이 결과를 실질적으로 바꾸는가

결과 상태:

- `FULL`
- `PARTIAL_WITH_COVERAGE`
- `NEEDS_CLARIFICATION`
- `NO_RESULT`
- `UNAVAILABLE`
- `INCOMPARABLE`
- `SAFETY_LIMITED`
- `DATA_QUALITY_BLOCKED`

`UNAVAILABLE`은 요청한 필드·시점 데이터 자체가 원본에 없을 때 사용합니다.
필드는 있지만 등급 순서·0 의미·상수값·극저 coverage 같은 품질 또는 metric 정책 때문에
실행을 잠그는 경우에는 `DATA_QUALITY_BLOCKED`를 사용하고, 구체 사유는 별도
`policy_reason` 코드로 기록합니다. `UNUSABLE_CONSTANT` 같은 값 상태는 answerability
enum이 아니라 `policy_reason`입니다.

## 10. 상품군 교차비교 규칙

단순 상품 수 질문은 metric 비교가 아니므로 scope별 `COUNT(DISTINCT product_uid)`로 분리
집계할 수 있습니다. 예를 들어 국내 ETF와 공모펀드 수는 각각 1,201개와 11,115개이며,
각 aggregate에 source table·상품 ID field·row count·query hash를 따로 남깁니다. 아래처럼
통화·기간·단위·척도가 필요한 cross-metric rank·compare는 계속 fail-closed합니다.

| 지표 | 허용 범위 | 제한 |
|---|---|---|
| 기간수익률 | 동일 horizon·basis·단위·기준일 | 해외 ETP에는 장기수익률 열 없음 |
| AUM·순자산 | 같은 정의와 통화 | 환율·환산기준 없으면 통화 혼합순위 금지 |
| 총보수 | 설명회 전 교차비교 잠금 | 국내 coverage·0 의미 미확정, 공모펀드 보수 없음 |
| 위험등급 | 상품군 내부 | 척도 동등성 확인 전 교차순위 금지 |
| 채권 수익률 | 채권 내부 | ETF·펀드 기간수익률과 동일 성과지표 취급 금지 |
| 신용등급 | 채권 내부 | 펀드·ETF 위험등급과 통합 금지 |

## 11. Evidence Bundle

```json
{
  "version": "1.1",
  "execution_id": "exec-...",
  "data_snapshot_date": "2026-07-11",
  "answerability": "PARTIAL_WITH_COVERAGE",
  "reason_code": "AUM_UNIT_UNCONFIRMED",
  "result_count": 3,
  "universe": {
    "scope": "overseas_etp",
    "raw_count": 5646,
    "serving_count": 5638,
    "eligible_count": 5579,
    "excluded_count": 59,
    "filter_summary": "serving ETF after non-metric filters"
  },
  "coverage": {
    "metric_id": "overseas_etp.aum_last",
    "raw_count": 5646,
    "serving_count": 5638,
    "present_count": 5395,
    "valid_count": 5395,
    "rankable_count": 5395,
    "numerator": 5395,
    "denominator": 5579,
    "basis": "serving overseas ETF products after non-metric filters"
  },
  "calculation": {
    "operation": "sort",
    "formula": "aum DESC, product_uid ASC",
    "formula_version": "metric-policy-v1",
    "rounding": null,
    "tie_breakers": ["product_uid"]
  },
  "aggregates": [],
  "items": [
    {
      "product_uid": "GLOBAL_ETP:PREF02N001:VOO",
      "name": "Vanguard 500 Index Fund;ETF",
      "rank": 1,
      "fields": [
        {
          "evidence_id": "ev-...",
          "metric_id": "overseas_etp.aum_last",
          "source_table_id": "PREF02N001",
          "source_file": "PREF02N001_해외ETF마스터_20260711_datarows.xlsx",
          "source_sheet": "datarows",
          "source_excel_row": 589,
          "source_field": "du_last_aum",
          "raw_value": "995484630000.00",
          "normalized_value": "995484630000.00",
          "unit": "UNCONFIRMED",
          "as_of_date": null,
          "as_of_status": "DATASET_SNAPSHOT_ONLY",
          "source_row_hash": "0123456789abcdef0123456789abcdef",
          "quality_flags": []
        }
      ]
    }
  ],
  "limitations": ["pd_trd_ccy=USD는 거래통화이며 AUM 값의 통화를 보장하지 않으므로 단위를 확정하지 않음"]
}
```

답변 순서:

1. 해석한 조건
2. 결론·상품 결과
3. 비교값과 근거
4. 기준일·유효 모집단
5. 제한사항 또는 추가 질문

현재 조회·랭크·비교·집계·exact-target 설명과 답변 문장은 deterministic evidence renderer가
만듭니다. 상품명·숫자·순서·원본 전략·benchmark·제한사항은 Evidence Bundle 밖에서 추가하지
않습니다. 요청한 모든 metric과 결측 상태, 모든 blocking limitation을 중복 제거 후
렌더링합니다. 개방형 설명형 HCX renderer는 현재 runtime 경로가 아니며, 도입하려면
claim-to-evidence validator와 별도 회귀 gate가 먼저 필요합니다.

## 12. HCX integration

- `HcxClient`/planner adapter로 모델 호출 격리
- JSON Schema 기반 Native Structured Outputs와 local Pydantic·semantic validation
- 기본 1회 planner 호출; invalid output에 임의 fallback·repair 없음
- timeout·429·5xx에 설정된 bounded transport retry(기본 retry 2회, 총 최대 3 attempts)
- 답변은 현재 Evidence Bundle 기반 deterministic renderer가 작성
- 다른 LLM fallback 없음

`OFFICIAL_PDF`는 HyperCLOVA X만 요구합니다. 현 코드의 `HCX-007` 기본값과 고정 검사는
Native Structured Outputs를 사용하기 위한 `TEAM_DECISION`이며 주최 측 공식 지정으로
표현하지 않습니다. 정확한 허용 model ID·credential·credit·QPM·TPM은 설명회 확인 전
`OPEN_QUESTION`이고, 확정 뒤 config·allow-list·문서·테스트를 함께 고정합니다.

## 13. 보안과 운영

- secret은 환경변수 또는 secret manager
- `.env`와 key를 commit·image에 포함하지 않음
- parameterized SQL
- outbound host allow-list
- 질문을 instruction이 아니라 데이터로 취급
- structured output과 plan allow-list
- query length·result count·execution time 상한
- 로그에 secret·전체 prompt·민감한 chain-of-thought 미기록
- API 응답과 내부 로그에 release version 포함

## 14. ADR

### ADR-001 - DuckDB + Parquet

채택. 정적 14.5만 행에 충분하고, SQL 분석·재현·Docker 배포가 단순합니다.

### ADR-002 - 단일 오케스트레이터

채택. 멀티에이전트보다 실패 지점과 latency가 적고 감사 가능성이 높습니다.

### ADR-003 - typed QueryPlan

채택. raw Text-to-SQL보다 field·operator·unit 통제가 강하고 테스트하기 쉽습니다.

### ADR-004 - external data

MVP 보류. 주최 데이터 기준 정량평가를 먼저 최적화합니다.

### ADR-005 - UI

예선 API 통과 이후. 결선 라이브 시연을 위한 최소 UI만 후속 구현합니다.

### ADR-006 - vector search

보조 기능. 상품명·영문전략 semantic retrieval에만 사용하고 숫자 조건은 SQL로 처리합니다.
