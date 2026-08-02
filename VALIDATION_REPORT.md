# Pre-briefing package validation report

검증일: 2026-08-03

> 아래 `통과 항목`에는 최초 사전설계 패키지 검증 기록도 포함됩니다. 현재 코드·v1 DB의
> 최신 실행 상태는 `docs/06_TEST_REPORT.md`가 기준입니다. 기계 판독 증빙과 release
> manifest는 아래 최신 수치·DB hash로 재검증해야
> 하며, 외부 gate 전까지 상태는 `DRAFT`입니다. `artifacts/test_report_v0_historical_20260802.json`은
> v0 DB·27-test 시점의 과거 기록입니다.

## 실행 MVP 추가 검증

- 전체 source ETL: raw 145,393 / logical 60,913 / serving 60,903 / quarantine 10
- fund attribute 95,618 / v1 metric policy 59 / serving metric evidence 1,156,332
- serving DB SHA-256:
  `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`
- fast gate 153/153 통과(14.90초), full-source 포함 전체 pytest 158/158 통과(104.57초)
- gold 40 + policy 10 fixture 50/50, plan subset 40개와 선언 assertion 103개 통과
- API five-field contract·명시적 역질문·후속 token 흐름 통과
- HCX adapter mock success/length/429 통과
- current-code local real HTTP smoke 15/15 통과
- 실제 HTTP 부하 smoke 100/100 성공·동시성 10·0 failure(p95 131.75ms)
- Ruff findings 0, non-HCX runtime 현재 scan 28 files/findings 0
- compliance 0 findings는 현재 allow-list 기반 스캔 결과이며 다른 LLM 사용 부재나 향후
  정책 준수를 절대적으로 증명하지 않음
- runtime/build lock을 각각 새 격리 venv에 설치하고 `pip check`·핵심 import 검증 통과
- scope별 distinct 교차 count, registry-backed return-period 선택지, bounded catalog filter 통과
- exact-target source-backed explain·target 누락 역질문, 다중 metric NULL 보존·전 metric 렌더링 통과
- 지표·필터별 공통 최신 기준일 기반 `sum/avg/min/max`,
  다른 통화 금액 비교 차단, 모든 blocking limitation 렌더링 통과
- 국내 ETF 1Y source-present 986/1,201·quality-valid 951/1,201·공통 최신일
  2026-06-15 rankable 940/1,201 구분 검증
- 펀드 전체 serving 11,138과 API 기본 공모 11,115 구분; 공모 위험등급
  valid 8,564·missing 2,551, 전체 위험등급 valid 8,565 검증
- ZIP 내부 XLSX 8개 source verification 통과
- 실제 HCX key E2E·Docker fresh build/restart·public TLS·Git/image digest·8월 6일 주최 측
  contract는 외부 gate로 미통과 상태

## 통과 항목

- official task PDF SHA-256·size 일치
- official data ZIP SHA-256·size 일치
- team email normalized text SHA-256·size 일치
- ZIP 내부 XLSX 정확히 8개, 각 file size·SHA-256 일치
- 4 datarows 공식 행·열 수 일치
- datarows header와 schema field set 4/4 일치
- 총 원본 145,393행, 207필드, exact duplicate 0
- 강화된 profiler 재실행 결과 CSV·JSON 바이트 동일 재현
- Python verification/profile scripts compile
- 패키지 내 모든 JSON syntax valid
- 패키지 내 모든 CSV rectangular parse valid
- JSON Schema 5개 Ajv 2020 compile·sample validation 통과
- QueryPlan integer condition `value: 3` validation 통과
- provisional OpenAPI YAML parse·`GET /answer` 구조 확인
- gold JSONL 40개, policy JSONL 10개 parse·ID uniqueness 통과
- fixture의 metric·group_by·aggregation field가 registry allow-list와 전부 일치
- fixture answerability가 Evidence Bundle enum과 전부 일치
- Excel workbook 10 sheets; `Checks`의 8개 reconciliation과 overall이 모두 `OK`
- workbook `Metric Policy` 59행과 `Canonical Registry` 207행이 현재 v1 CSV와 동기화
- workbook 전체 formula 33개, formula error scan 0
- workbook의 데이터 감사 표·check가 실행 source of truth를 요약하며 실제 runtime은
  `registry/metric_policy_v1.csv`와 `registry/canonical_fields_v1.csv`를 직접 읽음

## Workbook 시각 확인

artifact-tool PNG renderer의 설치 font에는 Hangul glyph가 없어 이 환경에서 한글 표시를
시각 확정하지 못했습니다. workbook cell의 Unicode Korean string과 `Malgun Gothic` 설정은
보존되어 있으므로 한국어 Excel·LibreOffice 환경에서 최종 시각 확인이 필요합니다.

Workbook Cover와 기계 판독 증빙에 반영해야 할 최신 기준은 `pytest full 158 / PASS`,
`gold/policy 50 / PASS`, `HTTP E2E 15 / PASS`입니다. Workbook 또는 artifact가 이 값이나 위
DB SHA-256과 다르면 현재 release 증빙으로 사용하지 않습니다.

## 아직 확정하지 않은 항목

- PDF의 API schema example이 최종 고정 contract인지
- `retrieved_context`·`think_trace` 정확한 채점 형식
- 주최 측 허용 정확한 HCX model ID·quota·credit (`HCX-007`은 현재 `TEAM_DECISION`)
- 09.20/09.30 실제 API 종료일
- 지표 단위·0·sentinel·등급순서
- ETN 포함범위·펀드 codebook·cross-source equivalence
- freeze 이후 restart·failover 허용범위
- optional clarification request pair를 평가기가 허용하는지
- Docker fresh build/run/restart, 실제 HCX key E2E, public TLS deployment
- 8월 6일 주최 측 최종 API·평가 contract
- DRAFT manifest용 최신 local test report·DB hash 재확인, 실제 Git SHA·registry image
  digest·image-extracted DB hash

위 항목은 임의 확정하지 않고 `OPEN_QUESTION`과 fail-closed policy로 유지했습니다. 따라서
이 보고서는 production-ready 또는 FINAL release 증명이 아닙니다.
