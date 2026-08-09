# 미래에셋증권 금융상품 Agent

> **새 세션/새 에이전트의 첫 문서**: [`docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md`](docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md)를 먼저 읽으세요. 목적·공식 원본 보존·구조·검증 사실·외부 대기·재현 절차와 읽는 순서를 한 곳에 정리했습니다.

> **현재 정직한 상태**: 대회 엔진과 팀 내부 인간검증 챗봇은 로컬에서 검증됐지만, 실제 HCX/Embedding 키와 확정 모델·NCP 공개 HTTPS·사람의 제출 freeze는 아직 `PENDING_EXTERNAL`입니다. 로컬 챗봇은 실제 HCX로 표시하지 않습니다. 공식 평가 문항 수는 공개되지 않았으며 내부 20·100·640·1,200·5,000 gate를 공식 수치처럼 표현하지 않습니다.

제10회 2026 미래에셋증권 AI Festival `금융상품 Agent`용 실행 가능한 MVP입니다.

이 시스템은 국내채권·국내 ETF/ETN·해외 ETF/ETN·공모펀드 질문을 typed
`QueryPlan`으로 바꾸고, DuckDB가 결정론적으로 조회·필터·정렬·집계한 뒤 원본
Excel 행·필드까지 추적 가능한 근거와 안전한 한국어 답변을 반환합니다. 평가 runtime의
유일한 언어모델은 HyperCLOVA X입니다. Codex는 개발 도구일 뿐 runtime에 포함되지 않습니다.

## `HISTORICAL` — 2026-08-03 prebrief 실증 기록

아래 목록은 초기 구현의 역사 기록이다. 현재 release 판단이나 테스트 수치로 사용하지
않는다. 최신 상태는 `docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`와
`artifacts/release_evidence_v4.json`만 따른다.

- source verification에서 ZIP 내부 XLSX 8개의 SHA-256·행·열·header 검증 통과
- 공식 raw `145,393`행·`207`필드 전수 보존
- 논리 상품 `60,913`개, 품질 격리 10개, serving 상품 `60,903`개
- serving: 채권 42,394 / 국내 ETP 1,733 / 해외 ETP 5,638 / 펀드 11,138
- 펀드는 전체 serving 11,138개와 API 기본 공모 모집단 11,115개를 구분
- 펀드 attribute bridge raw 95,619 / serving 95,618
- 실행 정책 `registry/metric_policy_v1.csv` 59개(채권 19 / 국내 ETP 16 / 해외 ETP 12 /
  펀드 12), serving metric evidence `1,156,332`행
- serving DuckDB SHA-256:
  `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`
- 자동 테스트 fast 153/153(14.90초), full-source 포함 전체 158/158(104.57초) 통과
  — **2026-08-08 재기준화 이후 238/238로 갱신, 독립 SQL oracle 640문항 eval
  100%·metamorphic 137/137 추가. 최신 수치는 `HANDOFF_CURRENT_STATUS.md` 참고**
- 공식형 gold 40 + 교차·안전 policy 10 fixture 50/50, plan subset 40개와 선언 assertion
  103개 전부 통과
- Ruff 통과, runtime compliance 현재 스캔 28 files/0 findings(2026-08-08 기준 84
  files/0 findings로 스캔 범위 확장), 실제 HTTP E2E 15/15 통과
- compliance의 0 findings는 현재 allow-list 기반 스캔 결과이지 비-HCX 사용 부재의 절대적
  증명은 아님
- 실제 HTTP 부하 smoke 100/100 성공·동시성 10·0 failure(p95 131.75ms), 실제 TCP 모의 HCX
  → FastAPI → DuckDB E2E 계약 통과
- `HCX-007` Native Structured Outputs 성공·불완전 출력·429 재시도 mock 테스트 통과
  (`HCX-007`은 현재 `TEAM_DECISION`; 주최 측이 지정하는 정확한 HCX model ID는 설명회 확인 전
  `OPEN_QUESTION`)
- GET `/answer` 5-field provisional contract 및 역질문 후속 흐름 통과
- 단순 교차 상품군 수는 scope별 `COUNT(DISTINCT product_uid)`로 분리. **(2026-08-03
  시점엔 호환되지 않는 지표의 교차 순위를 차단했으나, 2026-08-08 재기준화로 이 문장은
  더 이상 사실이 아닙니다 — 지금은 통합순위/분리제시/설명전용/대안제시 중 하나로
  항상 답변하고 절대 거절하지 않습니다. 상세: `HANDOFF_CURRENT_STATUS.md` §2-2.)**
- 수익률 기간 선택지는 registry의 실제 사용 가능 기간만 제시; 장기 수익률 field가 없는
  해외 ETP에는 가짜 기간을 묻지 않고 AUM·종가·거래량 대안을 표시
- 자산유형·지역·위험등급·연금 가능 여부의 bounded catalog filter와 원본 label evidence 구현
- 정확한 상품 target이 있는 source-backed `explain`, 다중 metric 순위의 primary
  `INNER`·secondary `LEFT/NULLS LAST`, 모든 요청 metric·blocking limitation 렌더링 구현
- 국내 ETF 1Y는 source-present 986/1,201, quality-valid 951/1,201, 공통 최신일
  2026-06-15 rankable 940/1,201로 분모와 품질 단계를 분리
- 순위·집계는 지표·필터별 공통 최신 기준일 모집단을 사용하고 stale·무기준일 값을 임의로
  섞지 않는 통합 검증과 hardened DRAFT release manifest 검증 구현
- 다른 통화의 금액·가격 비교는 환율 자료가 없으므로 차단하고, 펀드 순자산 합계는 공식
  share-class 중복 제거 규칙이 확정될 때까지 금지

아직 통과로 표시하지 않는 외부 gate는 실제 HCX key E2E, Docker/Podman fresh build·restart,
public TLS 배포, 최종 Git SHA·container image digest, 8월 6일 주최 측 contract 확정입니다.
따라서 현재 산출물은 로컬 검증을 통과한 `DRAFT`이지 `FINAL`이나 production-ready 릴리스가
아닙니다. 상세 상태는 `docs/06_TEST_REPORT.md`를 기준으로 합니다.

설명회에서 확정되지 않은 단위·zero·교차비교·API 항목은 임의 해석하지 않고 fail-closed로
잠가 두었습니다. 이 상태는 “모든 질문에 답한다”가 아니라 “답할 수 있는 질문은 정확한
근거로 답하고, 나머지는 구체적으로 역질문하거나 확인 불가를 설명한다”는 뜻입니다.
`explain`은 정확한 상품명·코드·ticker로 한 상품이 식별될 때만 실행합니다. 상품 target이
없거나 여러 개면 역질문하고, 답변은 원본에 있는 상품 사실·운용전략·benchmark 같은 field와
그 source evidence만 사용합니다. 정확한 상품이라도 요청 field 값이 없으면
`PARTIAL_WITH_COVERAGE/SOURCE_FIELD_ABSENT`로 누락 항목을 밝히며 만들지 않습니다. 데이터
밖의 개방형 금융교육·투자해설은 지원하지 않습니다.

## 1. 가장 빠른 로컬 실행

Python 3.12 기준입니다.

```bash
make setup
make verify
make build-data
make test-fast
make run
```

다른 터미널에서:

```bash
make smoke
```

예시:

```bash
curl --get 'http://127.0.0.1:8080/answer' \
  --data-urlencode 'question_id=Q-001' \
  --data-urlencode 'question=국내 ETF만 대상으로 1년 수익률이 높은 3개를 알려줘.'
```

로컬 `PLANNER_MODE=deterministic`은 개발·회귀검사용 비-LLM parser입니다. 제출 서버는
반드시 다음처럼 HCX를 사용합니다.

```bash
export APP_ENV=production
export PLANNER_MODE=hcx
export CLOVA_STUDIO_API_KEY='secret-injected-at-runtime'
export CLARIFICATION_SIGNING_KEY='at-least-24-random-bytes'
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log
```

## 2. 핵심 흐름

```text
GET /answer compatibility adapter
  -> input/safety guard
  -> configured HyperCLOVA X Structured Outputs planner
  -> local Pydantic + allowlist validation
  -> parameterized DuckDB execution
  -> field-level EvidenceBundle
  -> evidence-only renderer
  -> strict five-string JSON response
```

상품 선택·숫자 계산·정렬은 LLM이 아니라 DuckDB가 수행합니다. HCX 출력에는 raw SQL,
URL, tool name, 임의 표현식을 허용하지 않습니다.

## 3. 역질문

결과를 바꾸는 조건이 없으면 추정하지 않습니다.

- “수익률 높은 ETF 3개” → 국내/해외 확인
- “국내 ETF 수익률 높은 상품” → 1일/1개월/3개월/1년 등 기간 확인
- 여러 metric을 섞어 순위를 요구하지만 우선순위가 없음 → ranking priority 확인
- 이름이 여러 상품과 일치 → 후보와 구분 조건 확인

수익률 기간 선택지는 해당 scope의 registry에서 실제 사용 가능한 기간만 만듭니다. 해외
ETP처럼 요청 가능한 기간수익률 field가 없으면 존재하지 않는 1개월·3개월·1년 선택지를
제시하지 않고 `UNAVAILABLE`과 가능한 AUM·종가·거래량 대안을 반환합니다.

일반 역질문은 선택지 2~4개를 반환합니다. 동일·유사 상품명처럼 서버가 실제 catalog에서
상품 후보를 생성하는 `product_identity` 명확화만 최대 12개까지 허용합니다. HCX가 임의로
상품을 선택하거나 후보를 만들어 내는 예외가 아닙니다.

응답 Evidence에는 `missing_slots`, `options`, `preserved_plan`, 서명된
`clarification_token`이 들어갑니다. 선택 응답을 `/answer`의 선택 parameter로 다시 보내면
원 질문과 이미 파악한 조건을 보존해 실행합니다. 공식 평가 계약이 확정되면 외부 adapter만
교체합니다.

후속 요청의 `clarification_token`과 `clarification_response`는 둘 다 보내거나 둘 다
생략해야 합니다. 이 pair는 PDF 예시에 없는 `TEAM_DECISION` extension이며, 평가기 허용
여부는 설명회 전 `OPEN_QUESTION`입니다.

## 4. 데이터 계층

- `raw.*`: 원본 cell 문자열, 파일·sheet·Excel row·row hash
- `clean.*`: Unicode/공백 정규화와 품질 상태; raw 값은 유지
- `canonical.*`: product catalog, metric long table, fund attribute, source locator, quarantine
- `serving.*`: 격리 제외 상품·metric·attribute

원본은 수정하지 않습니다. 모든 build 시작 전에 ZIP과 내부 8개 XLSX 해시, 행·열을
검증합니다. 펀드는 95,619개 상품이 아니라 `itm_no` 기준 11,139개 raw 논리 상품이며,
손상행 하나를 제외한 serving 상품은 11,138개입니다.

펀드 API의 기본 모집단은 이 전체 11,138개가 아니라 공모 11,115개입니다. 공모 기본
모집단에서 위험등급 유효값은 8,564개, 결측은 2,551개이며, 전체 serving 기준으로는
8,565/11,138입니다. 판매상태도 공모 기본 모집단 기준 판매중 8,445개·판매완료 2,670개로
표시합니다.

수치의 분모는 이름만 보고 섞지 않습니다. 이 저장소에서 `raw row`는 공식 파일의 물리 행,
`logical product`는 상품키로 중복 제거한 격리 전 상품/listing, `serving product`는 격리 제외
논리 상품을 뜻합니다. `metric_policy_v1.csv`의 `raw_denominator`는 격리 전 **논리 상품**
분모이므로, 펀드의 물리 raw attribute 95,619행과는 다른 값 11,139입니다. 속성행 수는
`fund_attribute` 분모로 별도 표시합니다.

## 5. 품질·금융 안전

다음은 절대 실행하지 않습니다.

- 결측을 0으로 치환
- 단위·기간·통화·위험척도가 다른 metric 혼합 순위
- snapshot을 실시간으로 표현
- 원본 이상치 삭제·winsorize·임의 clipping
- 데이터에 없는 보수·수익률·기준일 추정
- 미래 수익 보장·예측 또는 단정적 매수 추천
- 내부 chain-of-thought 노출
- 다른 회사 LLM fallback·judge·router

현재 metric evidence 품질 상태는 `VALID` 599,036, `MISSING_NULL` 467,927,
`UNAVAILABLE` 39,328, `ZERO_UNKNOWN` 38,211, `UNUSABLE_CONSTANT` 10,890,
`SENTINEL` 908, `SUSPECT_OUTLIER` 29, `PARTIAL` 3으로 분리됩니다. quarantine은 상품
catalog 계층에서 별도 관리합니다. coverage도 raw/present/valid/serving/type-specific/
common-latest-rankable 분모를 혼용하지 않습니다.

다중 metric 순위는 사용자가 우선순위를 명시해야 합니다. 1차 metric의 유효값이 순위
모집단을 정하고, 2차 이후 metric은 값이 없어도 상품을 제거하지 않고 `NULLS LAST`로
정렬합니다. 답변에는 요청한 모든 metric과 누락 상태, 모든 blocking limitation을 표시합니다.

## 6. Docker

```bash
docker build -t mirae-agent:local .
docker run --rm -p 8080:8080 \
  -e APP_ENV=test \
  -e PLANNER_MODE=deterministic \
  mirae-agent:local
```

Dockerfile은 multi-stage입니다. builder에서만 원본 source 검증·ETL을 수행하고 최종 runtime
image에는 `requirements-runtime.txt`의 DuckDB·FastAPI·HTTPX·Pydantic·Uvicorn,
`app/`, `registry/`, 검증된 serving DuckDB만 복사합니다.
원본 PDF·ZIP·XLSX와 ETL build source는 runtime image에 포함하지 않습니다.

제출 환경에서는 `APP_ENV=production`, `PLANNER_MODE=hcx`, secret manager로 주입한 HCX
키와 clarification signing key를 사용합니다. GET query에는 비공개 평가문제가 포함되므로
Docker command에서 access log를 끕니다.

HCX key 전 mock 검증, key 후 plan-only/live 검증, 단일 managed container 또는 VM+Caddy
저비용 배포, secret·budget·readiness preflight는
[`docs/13_HCX_AND_LOW_COST_DEPLOYMENT.md`](docs/13_HCX_AND_LOW_COST_DEPLOYMENT.md)를 따릅니다.
`deploy/`의 domain, image digest, secret은 모두 교체 전용 placeholder이며 실제 배포 완료를
뜻하지 않습니다.

## 7. 테스트·release 명령

```bash
make test-fast                 # unit/contract/gold/HCX mock
.venv/bin/python -m pytest -q tests/test_etl.py  # 전체 원본 재빌드 slow test
make hcx-mock-contract         # 실제 TCP mock HCX adapter -> DuckDB contract
make compliance               # 비-HCX LLM dependency/endpoint/key 검사
make production-preflight     # secret 비출력 production env/DB/budget gate
make production-readiness     # public HTTPS live/ready만 확인; HCX 호출 없음
make release-manifest         # source/data/prompt/config fingerprint
```

실제 HCX key가 발급된 뒤에는 20문항 parity, 100문항 two-stage canary에 이어 아래 강화
gate를 실행해야 production preflight가 통과합니다. `1,200`과 `300`은 주최 측 공식 평가
문항 수가 아니라 팀의 release 검증량입니다. direct는 1,200개의 독립 의미 명세이고,
대화는 2·3·4턴 각 100개입니다.

```bash
.venv/Scripts/python.exe deploy/live_hcx_extensive_e2e_gate.py `
  --confirm-direct-hcx-calls 1200 `
  --confirm-api-requests 2100
```

명령은 실제 HCX quota를 쓰며 결과물에는 digest·집계만 남긴다. 질문·prompt·답·token·상품 ID·
secret은 저장하지 않는다. 정확한 실행 순서와 비용/키 취급은
[`docs/13_HCX_AND_LOW_COST_DEPLOYMENT.md`](docs/13_HCX_AND_LOW_COST_DEPLOYMENT.md)를 따른다.

현재 로컬 기계 판독 증빙은 `artifacts/release_evidence_v4.json`입니다.
`artifacts/test_report_20260803.json`과 `docs/06_TEST_REPORT.md`의 158-pass 기록,
`artifacts/test_report_v0_historical_20260802.json`은 모두 `HISTORICAL`이며 현재 release의
통과 증빙으로 사용하지 않습니다.

설명회 후 공식 query set·API contract·HCX model/credit·metric unit/zero 의미가 들어오면
`docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md` 절차로 diff를 만든 뒤 관련 registry,
contract, fixture만 변경합니다.

## 8. 개발 시작점

- Codex 압축해제·첫 프롬프트·검증·freeze: `CODEX_USAGE_GUIDE.md`
- Codex 전체 작업지시: `CODEX_MASTER_PROMPT.md`
- 저장소 불변 규칙: `AGENTS.md`
- 요구사항 기준선: `docs/02_REQUIREMENTS_BASELINE.md`
- 데이터 의미·품질: `docs/03_DATA_AUDIT_AND_SEMANTIC_MODEL.md`
- 설명회 질문: `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
- 최신 데이터 빌드 보고서: `docs/04_DATASET_REPORT.md`
- 최신 테스트·외부 gate 보고서: `docs/06_TEST_REPORT.md`
- HCX 연결·저비용 배포: `docs/13_HCX_AND_LOW_COST_DEPLOYMENT.md`
- 운영 동결: `docs/10_RELEASE_FREEZE_RUNBOOK.md`

현재 API 5-field 형식은 PDF가 “예시”로 제시한 provisional adapter입니다. 8월 6일 설명회
확정 전에는 공식 고정 계약이라고 부르지 않습니다.
