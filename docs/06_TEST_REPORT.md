# 최신 테스트·릴리스 gate 보고서

> **`HISTORICAL`**: 이 문서는 2026-08-03 prebrief의 158-test 기록이다. 현재 release
> 판정에 사용하지 않는다. 최신 기계 판독 결과는 `artifacts/release_evidence_v4.json`,
> 최신 해석은 `docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`를 따른다.

기준일: 2026-08-03  
상태: v1 registry·DB 기준 로컬 `DRAFT` gate 통과, 외부 gate 대기

이 문서는 현재 코드 기준 로컬 `DRAFT` 검증 상태의 단일 요약입니다. 기계 판독 증빙 파일은
`artifacts/test_report_20260803.json`이며, 사용 전에 아래 test 수치와 DB hash 일치를
확인해야 합니다. 과거 기록인
`artifacts/test_report_v0_historical_20260802.json`은 v0 DB·27개 테스트 시점이므로 현재
릴리스 통과 증빙으로 사용하지 않습니다.

## 1. 현재 수집·실행된 테스트

개별 디렉터리의 수를 과거 고정값으로 복제하지 않고 실제 gate 명령의 최종 수집 결과를
기준으로 합니다.

이번 로컬 검증 재실행 결과는 다음과 같습니다.

- fast gate(unit·contract·integration·HCX mock): **153 passed / 14.90초**
- full pytest(full-source ETL 포함): **158 passed / 104.57초**
- gold 40 + policy 10 fixture: **50/50**, plan subset **40/40**, 선언 assertion **103개**, failure 0

## 2. 현재 확인된 독립 gate

| gate | 실제 결과 | 상태 |
|---|---|---|
| source verification | 내부 XLSX 8개 SHA-256·행·열·header 일치 | 통과 |
| v1 full rebuild | raw 145,393 / logical 60,913 / serving 60,903 / quarantine 10 | 통과 |
| fund attribute / metric evidence | 95,618행 / 1,156,332행 | 통과 |
| serving DB hash | `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450` | 통과 |
| Ruff | findings 0 | 통과 |
| non-HCX runtime compliance scan | 현재 28 files, findings 0 | 통과* |
| minimal runtime requirements | pinned dependency 5개, full requirements 누락 0 | 통과 |
| fast pytest | 153/153, 14.90초 | 통과 |
| 전체 pytest | 158/158, 104.57초 | 통과 |
| 50 gold/policy fixture | 50/50; plan subset 40; assertion 103 | 통과 |
| current-code real process HTTP | 15/15 | 통과 |
| HTTP load smoke | 100/100, concurrency 10, failure 0, p95 131.75ms | 통과 |
| current-code Docker fresh build/restart | daemon 검증 없음 | 외부 gate |
| 실제 HCX key E2E | 팀 credential·공식 model 결정 없음 | 외부 gate |
| public TLS | target/domain/권한 없음 | 외부 gate |
| Git SHA·container image digest | immutable build 산출물 없음 | 외부 gate |
| 8월 6일 주최 측 contract | 설명회 전 | 외부 gate |

주의: compliance의 28 files/0 findings는 현재 allow-list 기반 스캔 결과입니다. 다른 LLM 사용
부재나 미래 상태를 절대적으로 증명하는 표시는 아닙니다. 제출 runtime의 유일한 LLM은
HyperCLOVA X이고 Codex는 개발 도구로만 사용합니다.

## 3. HCX 검증 범위

현재 mock test가 검증한 것은 Native Structured Outputs 요청 형식, 전체 aggregate plan
round-trip, HCX 출력 선택지 최대 4개, Bearer auth, 정상 JSON의 Pydantic validation,
length finish 차단, 429·5xx·transport bounded retry, 모든 retry의 QPM/TPM 예약, queue·retry·
validation을 포함한 전체 deadline, 오류 redaction입니다. HCX hop은 임시 TCP HTTP server를
실제로 거치며, live credential이나 provider 가용성을 검증한 것은 아닙니다.

- `OFFICIAL_PDF`: 제출 runtime의 LLM은 HyperCLOVA X만 허용
- `TEAM_DECISION`: 현 코드 기본·allow-list model ID는 `HCX-007`
- `OPEN_QUESTION`: 주최 측이 실제 지급·허용하는 model ID, credential, credit, QPM/TPM

따라서 mock 통과를 “주최 측이 HCX-007을 공식 지정했다” 또는 “live HCX E2E를 통과했다”로
표현하지 않습니다.

`explain`은 정확한 상품 target이 있을 때만 성공합니다. SPY exact-target 통합 테스트는
`cu_strtegy` 원문과 `cu_base_index` benchmark의 source evidence 및 답변 렌더링을 검증하고,
target이 없는 요청은 `explanation_target` 역질문으로 전환되는지 검증합니다. 이 범위는
개방형 금융교육·투자 의견 생성이 아닙니다. exact 국내 ETN에 요청 전략 field 값이 없을
때는 `PARTIAL_WITH_COVERAGE/SOURCE_FIELD_ABSENT`와 누락 `product.strategy`가 답변에
표시되는 것도 같은 통합 테스트가 검증합니다.

추가 integration·guard 검증은 다음을 포함합니다.

- 국내 ETF·공모펀드 각각의 distinct count와 scope별 source evidence
- registry의 실제 사용 가능 수익률 기간만 제시하고 해외 ETP 가짜 기간 선택지 0
- 자산유형·지역·위험등급·연금 가능의 scope별 exact raw catalog label
- 다중 metric primary `INNER`·secondary `LEFT/NULLS LAST`, secondary missing 상품 보존
- 요청한 모든 metric·누락값과 모든 blocking limitation 렌더링
- filtered latest-date·single-currency universe의 `sum/avg/min/max`, row count·as-of·unit
- 국내 ETF 1Y의 source-present 986/1,201, quality-valid 951/1,201, 공통 최신일
  2026-06-15 rankable 940/1,201 분리
- 펀드 전체 serving 11,138과 API 기본 공모 11,115 분리; 공모 위험등급
  valid 8,564·missing 2,551, 전체 위험등급 valid 8,565
- 공모 기본 모집단 판매중 8,445·판매완료 2,670

## 4. 역질문·API 계약 검증 범위

일반 missing slot은 선택지 2~4개를 요구합니다. 서버가 catalog 결과로 만드는
`product_identity` 후보만 2~12개를 허용합니다. 이 예외는 동일·유사 상품명 후보를
임의의 첫 행으로 선택하지 않기 위한 것입니다.

GET `/answer`의 현재 provisional 요청은 다음 규칙입니다.

- 필수: `question_id`, `question`
- 선택 extension: `clarification_token`, `clarification_response`
- 두 extension field는 함께 있거나 함께 없어야 함(`dependentRequired`)
- 200 응답은 PDF 예시 호환 5개 string field
- 400 입력 오류는 별도 `error`/`detail` object

PDF가 공식으로 보여준 request example은 앞의 필수 2개뿐입니다. 후속 pair는
`TEAM_DECISION`이며 최종 평가기가 허용하는지는 설명회에서 확정해야 합니다.

## 5. test artifact와 release manifest

- `artifacts/test_report_20260803.json`: 최신 재생성 시 full pytest 158개와 현재
  1,156,332 metric evidence, 위 DB SHA-256을 기록해야 하는 local-pass/external-gates-pending
  증빙
- `artifacts/test_report_v0_historical_20260802.json`: 27개 테스트와 828,357 metric evidence의 과거 기록
- `artifacts/release_manifest.generated.json`: 현재 DB·v1 registry hash를 담고 schema validation을
  통과해야 하는 `DRAFT`; test summary 158과 current report·DB hash를 재확인하고 Git SHA·image
  digest placeholder를 실제 값으로 교체해야 함
- final manifest 필수값: 현재 DB·v1 registry·planner prompt·schema·test report hash, 실제 Git SHA,
  container image digest, 공식 확정 HCX model ID

현재 generator는 report의 pytest 수치와 CLI 수치 불일치, non-PASS check, 빈·비-DuckDB
artifact, Git HEAD 불일치, digest와 맞지 않는 immutable `image-ref`, 미통과 external gate를
거부합니다. FINAL은 registry digest로 고정한 image에서 DB를 추출해 hash해야 합니다. 실제
Git SHA·image digest와 외부 gate가 없으면 final release 완료로 표시하지 않습니다. 현재
generator의 final HCX model check도 `HCX-007` TEAM_DECISION에 묶여 있으므로 설명회에서 다른
ID가 지정되면 config·generator·schema/test를 함께 갱신해야 합니다.

현재 로컬 산출물은 `DRAFT`이며 production-ready 또는 FINAL 상태가 아닙니다.

## 6. local gate 재현 명령

```bash
make verify
make build-data
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m ruff check app etl scripts tests
.venv/bin/python scripts/scan_runtime_compliance.py
make run
.venv/bin/python scripts/e2e_smoke.py --base-url http://127.0.0.1:8080
```

그 뒤 Docker fresh build→start→ready→GET→restart→same-result, 실제 HCX key plan/E2E,
public TLS external smoke, 실제 Git SHA·image digest 고정, 8월 6일 주최 측 contract 반영을
별도로 완료합니다. 외부 권한·credential·공식 입력이 없는 상태에서 이 gate를 통과로
추정하지 않습니다.
