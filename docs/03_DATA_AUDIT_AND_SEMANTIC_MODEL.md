# 배포 데이터 전수감사·시맨틱 모델

원본 ZIP SHA-256: `c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163`  
원본은 수정하지 않았습니다.

## 1. 감사 결과 요약

| table | 공식 상품군 | 원본 행 | 열 | 논리 단위 | key |
|---|---|---:|---:|---:|---|
| PRBD01N001 | 국내채권 | 42,394 | 40 | 42,394 | `PD_NO` |
| PREF01N001 | 국내 ETF | 1,734 | 73 | 1,734 listing | `pd_itm_no` |
| PREF02N001 | 해외 ETF | 5,646 | 49 | 5,646 listing | `pd_itm_no` |
| PRFD01N001 | 공모펀드 | 95,619 | 45 | 11,139 `itm_no` | `itm_no` + attribute bridge |

- 원본 총 행: 145,393
- 원본 field: 207개
- 4개 datarows 모두 PDF의 공식 행 수와 일치
- 4개 schema의 column set과 datarows header set이 일치
- 네 데이터셋 모두 exact duplicate row 0
- 논리적 raw 상품·listing 수: 60,913
- 명백한 placeholder·손상행 10개를 격리하면 1차 serving 후보는 60,903

격리 대상은 국내 ETP placeholder 1개, 해외 ETP placeholder성 행 8개, 펀드 손상행
1개입니다. 국내 partial 1개와 해외 partial 2개는 quality status를 붙여 보존합니다.
v1 ETL reconciliation 결과는 `VALID 60,900 + PARTIAL 3 = serving 60,903`으로 확정됐습니다.
현재 재빌드 상세 수치는 `04_DATASET_REPORT.md`를 기준으로 합니다.

PDF의 95,619 `공모펀드 건수`는 고유 펀드 수가 아니라 원본 행 수입니다.

## 2. 파일 구조

ZIP에는 8개 XLSX가 있습니다.

- 상품군별 `datarows` 4개
- 상품군별 `schema` 4개
- datarows sheet: `datarows`
- schema sheets: `Sheet1_Schema`, `Sheet2_Sample`

schema workbook의 `Sheet1_Schema`는 컬럼명·타입·한글명·예시를 제공합니다. PK/FK
표시는 대부분 비어 있으므로 실제 고유도 검증으로 key를 정해야 합니다.

`Sheet2_Sample`의 `axis_*`는 100행 샘플 파생분류입니다. 전체 datarows의 공식 label로
사용하면 안 됩니다. taxonomy few-shot 또는 mapping 후보로만 사용합니다.

파일명은 Unicode NFD일 수 있습니다. ingestion 시 NFC 이름을 만들되 원본 파일명도
보존합니다.

## 3. 국내채권 PRBD01N001

### Identity

- 42,394행 × 40열
- `PD_NO` 42,394개, 결측 0, 완전 고유
- exact duplicate row 0

### 분포

시장:

- 장내 24,749
- 장외 17,645

대분류:

- 회사채 31,447
- 특수채 8,755
- 국공채 2,137
- 개인투자용국채 49
- 외화채권-회사채 5
- 외화채권-금융채 1

통화:

- KRW 42,372
- USD 19
- EUR 1
- JPY 1
- 코드 `000` 1

### 결측과 0

| field group | 유효 | 결측 | 주의 |
|---|---:|---:|---|
| BUY_YIELD 및 매수·세전·세후 계열 | 881 | 41,513 | 전체의 2.08% |
| BUYABLE_QUANTITY | 881 | 41,513 | 881 중 556이 0, 양수 325 |
| AVG_ANNUAL_TAX_YIELD | 881 | 41,513 | 유효 881건 전부 0, 순위 사용 불가 |
| CRD_GRD | 24,750 | 17,644 | 41.62% 결측 |
| PD_EVCO_CRD_GRD | 24,966 | 17,428 | 41.11% 결측 |
| REMAINING_DAYS | 31,749 | 10,645 | 0 값도 별도 의미 확인 |
| EVAL_PRICE | 31,823 | 10,571 | 24.94% 결측 |
| DUR·COV | 29,018 | 13,376 | 31.55% 결측 |

### 구현 정책

- 매수수익률 rank는 유효 881행 모집단을 표시합니다.
- 매수가능 조건은 양수 수량 325행만 강하게 표현합니다.
- 신용등급 결측은 등급 조건을 자동 통과시키지 않습니다.
- `0`, `99991231` 등 날짜·수치 sentinel을 실제 값으로 자동 변환하지 않습니다.
- 통화 `000`과 이상 범위는 삭제하지 않고 quality flag를 부여합니다.
- 채권 신용등급과 타 상품군 위험등급을 같은 scale로 합치지 않습니다.
- 상품정보갱신일 최대값은 2026-02-24, 신용등급일 최대값은 2026-06-09입니다.
- 2026-07-11 기준 과거 만기 상품 중 원본 `REMAINING_DAYS`가 양수인 사례가 있으므로,
  원본 잔존일과 `MAT_DT - snapshot_date` 재계산 값을 별도 metric으로 보존합니다.

## 4. 국내 ETP PREF01N001

공식 명칭은 국내 ETF 마스터지만 실제 데이터에는 ETN이 포함됩니다.

### Identity와 유형

- 1,734행 × 73열
- ETF 1,202
- ETN 532
- 약어는 1,734행 중 1,727개 고유하므로 이름만으로 임의 1건을 선택하지 않습니다.
- `pd_itm_no` 1,734개 고유
- `pd_itm_no_ma` 1,734개 고유
- `pd_nm` 1,734개 고유
- exact duplicate row 0

placeholder 1행이 있습니다.

- Excel row 1,155
- `pd_itm_no='KR'`
- `pd_nm='.'`
- 시장·가격·날짜 등 핵심정보 공란

raw에는 보존하고 기본 serving universe에서는 격리합니다. 정상 상품은 1,733개이며
ETF 1,201개와 ETN 532개입니다.

`pd_exg_mkt_cd + pd_itm_no + pd_itm_no_ma`는 한 행에서 거래소 코드가 비어 있습니다.
기본 listing key는 완전한 `pd_itm_no`를 사용합니다.

### 주요 coverage

| field | 유효 | 결측 | 0 | 해석 |
|---|---:|---:|---:|---|
| `cu_base_index` | 58 | 1,676 | - | 기초지수 매우 제한적 |
| `cu_charge_rt` | 217 | 1,517 | 150 | 총보수 coverage 12.51% |
| `du_er_1y` | 1,377 | 357 | 14 | 극단값 품질검사 필요 |
| `du_last_aum` | 1,453 | 281 | 411 | 0 semantics 확인 필요 |
| `pd_net_tamt` | 1,551 | 183 | 0 | AUM 후보 의미 확인 필요 |

100% 결측 field:

- `nru_mkt_diff_rt`
- `nru_mkt_inav`
- `pd_dvid_cycl`
- `pd_sect_nm`
- `ru_mkt_price`
- `ru_mkt_volume`

추가 상수형 품질:

- 기타비용요율은 존재 1,553건이 모두 0
- 추적오차율은 존재 1,551건이 모두 0
- 괴리율은 존재 1,517건이 모두 0
- 배당금·배당수익률은 존재 1,551건이 모두 0
- ETN의 `du_last_aum` 유효 409건은 모두 0

### 구현 정책

- `ETF` 질의의 기본 universe는 ETF만, `ETN`은 ETN만 포함합니다.
- `ETP` 또는 `ETF·ETN` 질의에서 둘을 합칩니다.
- 총보수·기초지수·괴리·추적오차 답변은 coverage와 품질을 표시합니다.
- 총보수 0을 실제 무보수라고 자동 표현하지 않습니다.
- AUM canonical field 우선순위는 설명회에서 의미 확인 후 고정합니다.
- `pd_tr_yn=1`의 거래상태 의미를 확인하기 전 강한 표현을 사용하지 않습니다.
- 실시간 가격·거래량 요청은 제공 데이터로 확인 불가 처리합니다.

## 5. 해외 ETP PREF02N001

공식 명칭은 해외 ETF 마스터지만 실제 데이터에는 ETN이 포함됩니다.

### Identity와 유형

- 5,646행 × 49열
- ETF 5,587
- ETN 59
- `pd_itm_no` 5,646개 고유
- `pd_itm_no_ma` 5,646개 고유
- ISIN 유효 5,637, 고유 5,587
- ISIN 결측 9, 중복 초과행 50
- 상품명은 5,646행 중 5,630개 고유하므로 ticker·거래소·상품군으로 모호성을 해소합니다.
- exact duplicate row 0

ISIN은 단독 PK로 사용할 수 없습니다. listing key는 `pd_itm_no`입니다. ISIN은
검증된 동일 instrument·alias grouping의 후보로만 사용합니다.

`pd_curr_cd`와 `pd_trd_ccy`는 서로 다른 원본 field입니다. canonical model에서
product currency·trading currency·metric currency를 합치지 않습니다.

placeholder성 8행은 상장일 `00000000`과 가격·판매상태 결측이 결합되어 기본 serving
universe에서 격리합니다. 메타데이터 일부만 있는 2행은 `PARTIAL` 상태로 보존합니다.

### 주요 coverage

| field | 유효 | 결측 | 0 | 해석 |
|---|---:|---:|---:|---|
| `cu_charge_rt` | 5,646 | 0 | 363 | 총보수 값 존재, zero 확인 필요 |
| `cu_lev_fector` | 0 | 5,646 | - | 배율 확인 불가 |
| `du_er_1d` | 5,388 | 258 | 5,388 | 모두 0, rank 사용 불가 |
| `du_last_aum` | 5,459 | 187 | 8 | 통화·단위 확인 필요 |
| `du_last_nav` | 682 | 4,964 | 0 | coverage 12.08% |
| `du_diff_rt` | 3 | 5,643 | 확인 필요 | coverage 0.05%, 비교·순위 불가 |
| `pd_lst_price` | 5,636 | 10 | 5,635 | 실질적으로 사용 불가 |

해외 ETP에는 1M·3M·6M·1Y 장기수익률 field 자체가 없습니다. 위험등급 field도
없습니다.

가격 기준일은 2025-07-28~2026-06-16, 상품정보 기준일은 주로 2026-06-14입니다.
따라서 추출일 2026-07-11만 표시하지 말고 field 기준일을 함께 표시합니다.

### 자산군

- Equity 2,500
- Alternatives 1,800
- Bond 1,084
- Mixed Assets 138
- Commodity 107
- Money Market 9
- 결측 8

### 주요 지역

- United States of America 4,196
- Global 718
- Global Ex US 303
- Global Emerging Markets 175

### 구현 정책

- 1일수익률은 `UNUSABLE_CONSTANT` 처리합니다.
- 장기수익률·위험등급 질의는 `UNAVAILABLE` 처리합니다.
- leverage factor를 상품명이나 inverse flag만으로 추정하지 않습니다.
- 영문 strategy는 semantic retrieval과 설명에 활용합니다.
- AUM·거래량은 coverage·단위 제한을 표시해 상품군 내부 filter/rank에 사용할 수 있습니다.
- 총보수는 값 조회는 가능하지만 0 의미와 단위가 확정되기 전 filter/rank를 잠급니다.
- 통화가 다른 AUM을 공식 환율 없이 한 순위로 합치지 않습니다.

## 6. 공모펀드 PRFD01N001

### 원본 구조

- 95,619행 × 45열
- `itm_no` 고유 11,139
- 정상 12자리 영숫자 `itm_no` 11,138
- `(itm_no, prfd_attr_cd)` 95,619개 전부 고유
- `prfd_attr_cd` 228종
- `prfd_attr_cd`를 제외하면 동일 `itm_no`의 나머지 44개 field 충돌 0
- exact duplicate row 0

동일 상품이 속성 tag별로 반복됩니다.

- 평균 8.584행/상품
- 중앙값 8
- 최대 16

반복 횟수 분포:

- 1회 1개
- 4회 299개
- 5회 492개
- 6회 1,014개
- 7회 1,821개
- 8회 1,991개
- 9회 1,815개
- 10회 1,559개
- 11회 1,048개
- 12회 655개
- 13회 343개
- 14회 86개
- 15회 13개
- 16회 2개

따라서 다음 두 테이블로 분리합니다.

```text
fund_product
  key = itm_no
  scalar fields = prfd_attr_cd를 제외한 동일 값

fund_attribute
  key = itm_no + prfd_attr_cd
  raw attribute rows = 95,619
  serving attribute rows = 95,618
  quarantine rows = 1
```

원본 95,619행을 그대로 COUNT·AVG·SUM하면 속성 수가 많은 펀드가 과대가중됩니다.

### 손상행

정확히 1개 손상행이 확인됩니다.

- pandas index 84,561
- Excel row 84,563
- `itm_no`가 `"`
- 여러 field가 좌우로 밀림
- 예: `exchdg_yn=00080008`, `thco_sale_yn=KRZ50226929C`
- 위험코드 위치 `00020054`, 위험명 위치 `06`

자동 복구하지 않고 raw 보존 후 quarantine합니다.

`prfd_attr_cd`는 228종이지만 전체 코드 의미 사전은 배포 파일에 없습니다. schema의
파생축·100행 sample을 전체 codebook으로 간주하지 않고, 설명회에서 공식 정의를
요청합니다.

schema sample에는 상장지수투자신탁 성격의 펀드도 보입니다. ETP master와 경제적으로
동일한 상품인지 공식 연결키가 없으므로 자동 병합하지 않고 별도 source record로 유지합니다.

### 상품단위 분포

- 전체 정상 serving 11,138
- 전체 기준 판매중 8,445, 판매완료 2,693
- 공모 11,115, 사모 15, 공모·사모 구분 결측 8
- 공모 기본 universe 기준 판매중 8,445, 판매완료 2,670
- 손상 1은 위 serving 수에서 제외

`공모펀드` 질의의 기본 universe는 `prvo_pbff_desc='공모'`로 제한하는 것이 안전합니다.

### 대표펀드·share class 후보

- 정상 대표코드가 있는 고유 `itm_no`: 10,654
- 정상 대표코드: 2,626
- 대표코드별 share class: 1~15개, 중앙 3개
- 대표 그룹 2,626개 중 복수 class 그룹 1,877개
- placeholder 대표코드 상품 476
- 결측 9
- 미배정 상품을 개별 그룹으로 유지하면 3,111개 후보 family

`rptt_ksd_itm_no`의 공식 family 의미를 설명회에서 확인하기 전 경제적 원펀드 수를
확정하지 않습니다. 평가의 기본 상품 단위는 `itm_no`입니다.

### 정상 상품 11,138개 기준 coverage

| metric | 유효 상품 | coverage |
|---|---:|---:|
| 순자산 | 9,290 | 83.41% |
| 1주 수익률 | 7,600 | 68.24% |
| 1개월 수익률 | 7,570 | 67.97% |
| 3개월 수익률 | 7,497 | 67.31% |
| 6개월 수익률 | 7,340 | 65.90% |
| 18개월 수익률 | 6,879 | 61.76% |
| 1년 수익률 | 7,017 | 63.00% |
| 2년 수익률 | 6,360 | 57.10% |
| 3년 수익률 | 6,102 | 54.78% |
| 5년 수익률 | 5,580 | 50.10% |

전체 serving에서 위험등급은 8,565/11,138, 결측은 2,573개입니다. API 기본 공모
universe에서는 8,564/11,115, 결측은 2,551개입니다. 순자산 유효상품 중 298개는 값이
0이므로 실제 0인지 placeholder인지 Metric Registry에서 분리합니다.

### 원시행 기준 결측

| metric | 결측률 |
|---|---:|
| 1주 수익률 | 27.37% |
| 1개월 수익률 | 27.64% |
| 3개월 수익률 | 28.32% |
| 6개월 수익률 | 29.75% |
| 18개월 수익률 | 33.90% |
| 1년 수익률 | 32.62% |
| 2년 수익률 | 39.21% |
| 3년 수익률 | 41.63% |
| 5년 수익률 | 46.83% |
| 순자산 | 13.11% |
| 위험등급명 | 19.26% |

이 비율은 속성 tag 반복이 반영된 원시행 기준입니다. serving 품질표는 11,138개
usable `itm_no`로 축약해 다시 계산합니다.

### 제공되지 않는 주요 정보

- 비용·보수
- 설정일
- NAV·기준가
- 보유종목
- 환매조건
- 운용사명
- 개별 row 기준일

펀드 수익률에는 통상적인 누적수익률 범위를 크게 벗어나는 원본값이 존재합니다.
단순 clipping이나 삭제를 하지 않고 `SUSPECT_OUTLIER`를 부여해 순위 답변에서 경고
또는 제외정책을 명시합니다.

### 구현 정책

- count·평균·AUM 집계는 `fund_product` view에서 수행합니다.
- 속성 조건은 `fund_attribute` bridge의 EXISTS로 처리합니다.
- 보수 field가 없으므로 공모펀드 보수 비교를 수행하지 않습니다.
- 사모·결측·손상은 공모 기본 universe에서 제외합니다.
- 동일 horizon 수익률만 비교합니다.
- 대표펀드 family 집계는 공식 의미 확인 후 추가합니다.

## 7. Missing·zero·sentinel 상태모델

현재 1,156,332개 metric evidence의 serving quality state는 다음과 같습니다.

| state | 행 수 | 계산 정책 |
|---|---:|---|
| `VALID` | 599,036 | 지표별 허용 정책과 기준일 조건 안에서 사용 |
| `MISSING_NULL` | 467,927 | 0으로 대체하지 않음 |
| `UNAVAILABLE` | 39,328 | 확인 불가로 응답 |
| `ZERO_UNKNOWN` | 38,211 | source-present에는 포함할 수 있으나 순위·필터·집계에서는 제외 |
| `UNUSABLE_CONSTANT` | 10,890 | 정보량이 없는 상수 필드로 계산 차단 |
| `SENTINEL` | 908 | 특수 표식 가능성 때문에 계산 차단 또는 별도 검토 |
| `SUSPECT_OUTLIER` | 29 | 원문·단위 경고와 함께 제한적으로 사용 |
| `PARTIAL` | 3 | 한계를 명시해 사용 |

기준일 상태는 `AVAILABLE` 227,347, `DATASET_SNAPSHOT_ONLY` 421,730,
`UNAVAILABLE` 507,255입니다. 행 기준일이 있는 순위·집계는 필터된 후보군 안의 공통
최신일자만 사용하며, 최신일자가 존재하면 오래된 값과 무일자 값은 제외합니다.

0을 전체 field에서 일괄 NULL로 바꾸지 않습니다. Metric Registry에 field별
`zero_semantics`를 둡니다.

## 8. 비교 가능성

| 질문 | 상태 | 이유 |
|---|---|---|
| 상품명·ID 상세조회 | FULL | 네 상품군 identity 존재 |
| 유형·지역 조건검색 | FULL 또는 PARTIAL_WITH_COVERAGE | field coverage에 따라 표시 |
| 국내 ETF 1Y 수익률 rank | PARTIAL_WITH_COVERAGE | source-present 986/1,201·quality-valid 951·공통 최신일 2026-06-15 rankable 940·극단값 검사 |
| 해외 ETP 총보수 rank | DATA_QUALITY_BLOCKED | 값은 존재하나 zero 의미 확인 전 잠금 |
| 해외 ETP 1Y 수익률 rank | UNAVAILABLE | field 없음 |
| 해외 ETP 1D 수익률 rank | DATA_QUALITY_BLOCKED | 유효값 전부 0; reason=`UNUSABLE_CONSTANT` |
| 펀드 기간수익률 rank | PARTIAL_WITH_COVERAGE | horizon별 결측 |
| 펀드 보수 비교 | UNAVAILABLE | 보수 field 없음 |
| 채권 매수수익률 rank | PARTIAL_WITH_COVERAGE | 881행만 유효 |
| 실시간 가격·거래 | UNAVAILABLE | snapshot·실시간 field 품질 부족 |
| 국내 ETF·공모펀드 각각의 상품 수 | FULL | scope별 `COUNT(DISTINCT product_uid)` 분리; 1,201·11,115 |
| 교차 위험순위 | INCOMPARABLE | 척도 다름 |
| 교차 AUM 순위 | INCOMPARABLE | 정의·통화 확인 전 fail-closed |
| 미래수익·확정 추천 | SAFETY_LIMITED | 공식 금지 |

수익률 기간 역질문은 이 표를 하드코딩하지 않고 v1 Metric Registry의 scope·source field·
ranking policy에서 실제 사용 가능한 기간만 계산합니다. 해외 ETP는 장기수익률 field가
없으므로 1개월·3개월·1년 같은 가짜 선택지를 만들지 않고 `UNAVAILABLE`과 AUM·종가·거래량
대안을 반환합니다.

자산유형·지역·위험등급·연금 가능 여부는 bounded catalog resolver가 scope별 실제 source
label로만 치환합니다. raw label과 normalized label을 evidence에 함께 보존하며, 정확한
label이 없거나 scope가 불명확하면 추측하지 않습니다.

## 9. Serving view

```text
product_catalog
product_alias
bond_snapshot
domestic_etp_snapshot
overseas_etp_snapshot
fund_product
fund_attribute
canonical_metric
metric_registry
source_locator
data_quality_issue
quarantine
```

모든 결과에는 stable `product_uid`가 있고 원본 table·file·sheet·Excel row·field로
돌아갈 수 있어야 합니다.

## 10. 감사 산출물

`artifacts/raw_profile/`에 다음을 생성했습니다.

- `dataset_profile.csv`
- `field_profile.csv`
- `key_profile.csv`
- `profile_summary.json`

`scripts/profile_source_data.py`로 원본 ZIP에서 다시 생성할 수 있습니다. field profile의
통계는 schema 의미를 대신하지 않으며, 단위·zero·sentinel 의미는 Metric Registry와
설명회 확인을 거쳐 고정합니다.
