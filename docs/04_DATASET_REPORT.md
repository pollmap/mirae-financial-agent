# 최신 데이터셋·serving 빌드 보고서

기준일: 2026-08-03  
상태: 설명회 전 `OFFICIAL_DATA` 전수 검증 및 v1 full rebuild 기준

이 문서는 현재 코드와 데이터 산출물의 숫자를 한곳에서 확인하기 위한 단일 요약입니다.
필드별 상세 profiling과 의미 분석은 `03_DATA_AUDIT_AND_SEMANTIC_MODEL.md`, 설계는
`04_PRODUCT_ARCHITECTURE_SPEC.md`를 함께 봅니다.

## 1. 권위 원본

| 원본 | SHA-256 |
|---|---|
| `inputs/official_task.pdf` | `3717441e091958b7214db710e0e4b9b8ae15ac6c205cad6e51721214798eb3de` |
| `inputs/official_data.zip` | `c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163` |

- ZIP 내부 XLSX: datarows 4개 + schema/sample 4개, 합계 8개
- source verification 결과 내부 XLSX 8개의 SHA-256·행·열·header 일치
- datarows 물리 행: 145,393
- 서로 다른 원본 필드: 207
- exact duplicate row: 0
- 데이터 스냅샷: 2026-07-11
- 원본은 수정하지 않으며, 모든 빌드 전에 외부 PDF/ZIP과 내부 XLSX의 hash·행·열·header를
  fail-closed로 검증합니다.

## 2. 물리 행·논리 상품·serving의 관계

| source | 물리 raw 행 | 격리 전 논리 상품/listing | quarantine | serving 상품 |
|---|---:|---:|---:|---:|
| PRBD01N001 국내채권 | 42,394 | 42,394 | 0 | 42,394 |
| PREF01N001 국내 ETP | 1,734 | 1,734 | 1 | 1,733 |
| PREF02N001 해외 ETP | 5,646 | 5,646 | 8 | 5,638 |
| PRFD01N001 펀드 | 95,619 | 11,139 | 1 | 11,138 |
| 합계 | 145,393 | 60,913 | 10 | 60,903 |

펀드만 물리 행과 상품 수가 크게 다릅니다. PRFD01N001의 95,619행은
`itm_no × prfd_attr_cd` 반복구조이며, 상품 계산은 `itm_no`로 중복 제거한 11,139개를
분모로 사용합니다. 손상행 하나를 제외한 serving attribute bridge는 95,618행이고,
11,138개 serving 펀드에 연결됩니다.

serving DuckDB SHA-256은
`4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`입니다. 이 hash가
다르면 아래 수치와 동일한 DB라고 간주하지 않습니다.

## 3. v1 registry와 실제 실행 연결

| registry | 데이터 행 | 역할 |
|---|---:|---|
| `registry/canonical_fields_v1.csv` | 207 | 공식 source field 전수 canonical mapping |
| `registry/metric_policy_v1.csv` | 59 | 지표별 source·coverage·단위·0·순위·교차비교 정책 |
| `registry/synonyms_ko_v1.csv` | 146 | 한국어 표현과 canonical field/metric mapping |
| `registry/quarantine_rules_v1.csv` | 13 | quarantine·PARTIAL·schema drift 규칙 |

59개 metric policy의 상품군별 구성은 채권 19, 국내 ETP 16, 해외 ETP 12, 펀드 12입니다.
현재 `etl/build.py`와 `app/execution/registry.py`가 모두
`registry/metric_policy_v1.csv`를 읽습니다. 실행 시 허용 연산은
`canonical_fields_v1.csv`와 결합합니다. HCX prompt의 allow-list는
`app/planner/schema.py`에도 명시되어 있으므로 registry를 바꿀 때 이 목록과 계약·테스트를
함께 동기화해야 합니다.

`artifacts/canonical_field_registry_v0.csv`와 `artifacts/metric_registry_v0.csv`는 이전 감사
산출물이며 현재 ETL·실행 정책의 source of truth가 아닙니다.

## 4. v1 serving metric evidence

v1 full rebuild의 `serving.product_metrics`는 총 **1,156,332행**입니다. 59개 정책 metric에
lookup 근거용 `product.id`, `product.name` 두 identity metric을 상품별로 더한 결과입니다.

| 상품군 | serving 상품 | 정책 metric | identity metric | evidence 행 |
|---|---:|---:|---:|---:|
| 채권 | 42,394 | 19 | 2 | 890,274 |
| 국내 ETP | 1,733 | 16 | 2 | 31,194 |
| 해외 ETP | 5,638 | 12 | 2 | 78,932 |
| 펀드 | 11,138 | 12 | 2 | 155,932 |
| 합계 | 60,903 | 59개 정책 집합 | 공통 2 | 1,156,332 |

격리 전 `canonical.product_metrics`는 1,156,476행이고, 격리 상품에 해당하는 144행을
제외하면 serving 1,156,332행이 됩니다. 이전 문서의 828,357행은 v0 metric registry로 만든
과거 DB 수치이므로 현재 기준으로 사용하지 않습니다.

### metric evidence 품질 상태

| quality status | 행 |
|---|---:|
| VALID | 599,036 |
| MISSING_NULL | 467,927 |
| UNAVAILABLE | 39,328 |
| ZERO_UNKNOWN | 38,211 |
| UNUSABLE_CONSTANT | 10,890 |
| SENTINEL | 908 |
| SUSPECT_OUTLIER | 29 |
| PARTIAL | 3 |
| 합계 | 1,156,332 |

`UNAVAILABLE`, 결측, 0, 상수, sentinel은 서로 다른 상태입니다. 행이 존재한다는 이유만으로
필터·순위에 쓸 수 있는 것은 아니며, 실제 허용 여부는 metric policy와 질문의 모집단에서
다시 판정합니다.

## 5. 분모 용어 사전

| 용어 | 정확한 의미 |
|---|---|
| physical raw rows | 공식 datarows XLSX의 실제 행 수; 펀드 속성 반복 포함 |
| logical products/listings | 상품키로 중복 제거한 격리 전 상품/listing |
| serving products | logical product에서 `QUARANTINE`을 제외한 조회 모집단 |
| API default public funds | 전체 serving 펀드에서 `public_private=공모`를 적용한 기본 모집단 |
| raw_denominator | metric policy의 격리 전 **논리 상품/listing** 분모 |
| present_count | raw_denominator 중 source cell이 존재하는 수 |
| serving_denominator | 격리 제외 논리 상품/listing 분모 |
| serving_present_count | serving_denominator 중 값이 존재하는 수 |
| type_specific_denominator | ETF·ETN 등 질문에 실제 적용한 subtype serving 분모 |
| rankable_denominator | 단위·0·품질·정책 gate까지 통과해 순위에 쓸 수 있는 수; `OPEN`이면 미확정 |
| fund attribute rows | 펀드 상품 수와 분리한 속성 bridge 물리 행 수 |

특히 펀드 `raw_denominator=11,139`는 물리 raw attribute 95,619행과 같은 뜻이 아닙니다.
API Evidence의 `basis`에는 어느 분모와 필터를 사용했는지 문장으로 기록해야 합니다.

## 6. 핵심 품질 제한

- 국내 ETP placeholder 1개, 해외 ETP placeholder형 8개, 펀드 손상행 1개를 raw에는
  보존하고 serving에서 격리합니다.
- TBF, EMOP.K, KRG597100145는 identity를 사용할 수 있어 `PARTIAL`로 serving에 남깁니다.
- 국내 ETF 1년 수익률은 source-present 986/1,201, quality-valid 951/1,201입니다. 순위는
  공통 최신 기준일 2026-06-15의 rankable 940/1,201만 사용합니다. 세 수치를 같은
  coverage로 표현하지 않습니다.
- 국내 ETF 총보수는 217/1,201만 존재하고 0값 150개의 의미가 미확정입니다.
- 국내 ETN AUM은 값이 있는 409건이 전부 0이어서 크기 순위를 금지합니다.
- 해외 ETF 1일 수익률은 serving ETF 5,329건이 전부 0이어서 순위를 금지합니다.
- 해외 ETP 1개월·3개월·6개월·1년·YTD 수익률 source field는 없습니다.
- 해외 AUM은 serving ETP 5,451건, 그중 serving ETF 5,395건이 존재하지만 통화·단위
  context가 확정되지 않아 교차통화 순위를 금지합니다.
- 펀드 전체 serving은 11,138개이지만 일반 API의 기본 모집단은 공모 11,115개입니다.
  공모 기본 모집단의 판매중은 8,445개, 판매완료는 2,670개입니다.
- 공모 위험등급은 valid 8,564/11,115, missing 2,551입니다. 전체 serving 위험등급은
  valid 8,565/11,138이며, 공식 순서 확정 전 등급 순위를 금지합니다.
- 펀드 보수·설정일·NAV·보유종목·환매조건·개별 as-of field는 원본에 없습니다.
- 큰 수익률 이상치는 삭제·clipping하지 않고 원문과 경고를 함께 보존합니다.

## 7. 재현 명령과 통과 기준

```bash
make verify
make build-data
```

빌드 성공만으로 릴리스 완료가 아닙니다. 다음을 함께 확인합니다.

- reconciliation이 145,393 → 60,913 → 60,903과 quarantine 10을 정확히 만족
- `metric_policy_v1.csv` 데이터 행이 59개
- `serving.product_metrics`가 1,156,332행
- `serving.fund_attribute`가 95,618행
- serving DB SHA-256이
  `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`
- 테스트·runtime·release manifest가 동일 DB와 v1 registry hash를 사용
