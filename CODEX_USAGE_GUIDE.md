# Codex 실행·인계 사용 가이드

기준일: 2026-08-03  
대상: 이 패키지를 Codex에서 이어 개발·검증·배포 준비하는 팀원

상태: **DRAFT only.** local 검증 현황을 기록한 문서이며, 실제 HCX·Docker image·public TLS·
Git release가 닫히기 전에는 FINAL 인계서나 제출 승인으로 사용하지 않습니다.

## 1. 이 가이드의 원칙

OpenAI의 공식 Codex 가이드는 작업 요청을 `Goal / Context / Constraints / Done when`으로
구체화하고, 저장소에 계속 적용할 규칙은 루트 `AGENTS.md`에 두도록 안내합니다.
Codex는 작업 시작 전에 적용 가능한 `AGENTS.md`를 읽으므로, 이 저장소에서는 다음처럼
역할을 나눕니다.

- `AGENTS.md`: 언제나 지켜야 하는 출처 우선순위, 금지사항, 검증·동결 규칙
- `CODEX_MASTER_PROMPT.md`: 이번 대회 구현의 목표·현재 상태·작업순서·완료조건
- 이 문서: 사람이 압축파일을 열고 Codex를 시작해 release까지 가는 실제 사용 순서

공식 참고: [Codex best practices](https://learn.chatgpt.com/guides/best-practices),
[Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

## 2. 패키지 풀기와 저장소 열기

최종 전달 압축파일을 받은 경우:

```bash
mkdir -p mirae-agent-work
tar -xzf mirae_financial_agent_codex_ready.tar.gz -C mirae-agent-work
cd mirae-agent-work/mirae_financial_agent_codex_prebrief
```

이미 디렉터리 형태로 받은 경우 그 디렉터리를 저장소 루트로 엽니다. 반드시
`AGENTS.md`, `CODEX_MASTER_PROMPT.md`, `Makefile`, `app/`, `etl/`, `registry/`, `tests/`가
같은 루트 아래 있는지 확인합니다.

Codex CLI를 쓴다면 저장소 루트에서 `codex`를 실행합니다. IDE·Codex 앱을 쓴다면 이
디렉터리 자체를 프로젝트로 엽니다. 상위 폴더나 `app/` 하위만 열지 않습니다. 그래야
루트 `AGENTS.md`와 전체 데이터·계약·테스트를 함께 볼 수 있습니다.

전달 압축파일에는 Git 이력이 없을 수 있습니다. `.git`이 없으면 Codex가 임의로 공개
repository를 만들거나 push하게 하지 않습니다. 팀이 사용할 주최 측 private repository와
branch가 확정된 뒤 그곳에 이 패키지를 가져오고, 최초 commit의 SHA를 release 추적에
사용합니다. `.git`이 있으면 작업 전에 `git status --short`와 diff를 확인해 기존 사용자
변경을 보존합니다.

## 3. 첫 요청에 넣을 프롬프트

`CODEX_MASTER_PROMPT.md` 전체를 첫 요청에 첨부하거나, 아래 시작 프롬프트를 그대로
전달합니다. 이 형식은 목표, 필요한 문맥, 절대 제약, 종료 판정을 분리합니다.

```text
Goal
- 이 저장소의 기존 금융상품 Agent 실행 MVP를 재사용해 제출 가능한 E2E release candidate로 완성하라.
- 문서만 제안하지 말고, 확인된 결함과 미구현 항목을 직접 수정하고 관련 테스트로 검증하라.

Context
- 먼저 루트 AGENTS.md와 CODEX_MASTER_PROMPT.md를 끝까지 읽고 적용하라.
- 다음으로 docs/11_IMPLEMENTATION_HANDOFF.md, docs/06_TEST_REPORT.md,
  docs/04_DATASET_REPORT.md, docs/02_REQUIREMENTS_BASELINE.md를 읽어 현재 구현과 외부 blocker를 구분하라.
- 이 저장소는 설계-only scaffold가 아니다. app/, etl/, registry/, contracts/, tests/의 기존 구현이 기준이다.
- organizer PDF/ZIP과 이후 공식 설명회·서면 공지가 최상위 source of truth다.

Constraints
- 제출 runtime의 언어모델은 HyperCLOVA X만 허용한다. Codex는 개발 도구일 뿐 runtime에 넣지 마라.
- 기존 ETL, DuckDB, QueryPlan, evidence, adapter, safety 경로를 먼저 검사·수정하라.
  같은 목적의 새 저장소, 새 DB 계층, 새 planner, 새 API를 병렬로 만들지 마라.
- 원본 PDF/ZIP/XLSX는 수정하지 말고 manifest hash로 식별하라.
- 외부·보강 데이터가 주최 측 제공 데이터와 충돌하면 주최 데이터를 우선하고, 공식
  요구사항끼리 충돌하면 원문·날짜·영향을 기록한 뒤 최신·구체 공지를 운영 기준으로 삼아라.
- 결측=0, 단위·기간·통화 혼합, 가짜 실시간, 전망·보장·단정 추천, 근거 없는 값,
  chain-of-thought 노출, raw SQL 실행을 금지한다.
- 결과를 바꾸는 조건이 없으면 추정하지 말고 2~4개의 실제 가능한 선택지로 역질문하라.
  이름 중복 후보만 서버 catalog 근거로 최대 12개를 허용한다.
- return-period 선택지는 scope registry의 실제 사용 가능 기간만 제시하고, 해외 ETP에는
  가짜 장기수익률 기간을 만들지 마라.
- exact-target source-backed explain, scope별 distinct count, bounded catalog filter,
  multi-metric NULL 보존과 모든 metric·blocking limitation 렌더링 경계를 유지하라.
- PDF의 API 예시는 잠정 계약이다. HCX-007도 TEAM_DECISION baseline이지 주최 측 확정값이 아니다.
- 공식 미확정 사항, secret, 유료 자원, 공개 배포 권한은 임의로 확정하지 마라.
- unrelated 파일과 사용자 변경을 보존하고, 작은 변경마다 가장 가까운 테스트를 먼저 실행하라.

Done when
- source hash, full ETL reconciliation, lint, fast/full tests, gold/policy, runtime compliance가 통과한다.
- local real HTTP와 fresh Docker build/run/restart/same-result가 통과한다.
- 실제 허용 HCX model·credential로 structured plan과 네 상품군 E2E를 검증한다.
- field-level evidence, clarification follow-up, forbidden-answer gate가 회귀 없이 통과한다.
- exact-target explain, safe cross-count, complex catalog filter, `sum/avg/min/max`, 다중 metric
  우선순위·NULLS LAST가 source evidence와 함께 통과한다.
- README, OpenAPI, 기술제안서, test report, FINAL release manifest가 같은 Git SHA·image digest와 일치한다.
- public TLS endpoint와 운영 조건을 검증하고 freeze 이후 결과 변경이 없도록 고정한다.
- 외부 입력이 없어 검증할 수 없는 항목은 완료라고 쓰지 말고 정확한 blocker와 필요한 입력을 보고한다.

먼저 파일 구조와 version-control 상태를 읽어라. Git metadata가 있으면 status와 diff를
확인하고, 없으면 임의로 공개 repo를 만들거나 push하지 마라. 기존 구현을 중복 생성하지
않는 실행 계획을 세운 뒤
안전하게 계속 진행하라. 결과 보고에는 실제 변경, 실행 명령, 통과/실패, 남은 외부 blocker를 구분하라.
```

`CODEX_START_PROMPT.md`는 짧은 호환용 바로가기입니다. 새 장기 작업은 위 프롬프트나
`CODEX_MASTER_PROMPT.md`를 기준으로 합니다.

## 4. 최초 로컬 검증

Python 3.12 환경을 기준으로 합니다. 실제 secret은 필요하지 않습니다.

```bash
make setup
make verify
make lint
make test-fast
make compliance
```

동봉된 `data/serving/mirae_agent.duckdb`가 있고 source·ETL·registry가 바뀌지 않았다면
일상적인 코드 수정마다 전체 DB를 다시 만들 필요는 없습니다. 다음 중 하나면 반드시
`make build-data`를 실행합니다.

- serving DB가 없거나 열리지 않음
- `inputs/`, `etl/`, `registry/`가 변경됨
- 설명회에서 data universe·field·metric·quarantine 정책이 변경됨
- release candidate를 새로 만듦

전체 원본 clean rebuild 검증은 다음 명령입니다.

```bash
.venv/bin/python -m pytest -q tests/test_etl.py
```

테스트 개수는 문서의 과거 숫자를 믿지 말고 해당 실행 결과와 최신
`docs/06_TEST_REPORT.md`를 기준으로 기록합니다.

## 5. 로컬 real HTTP E2E

첫 번째 터미널:

```bash
make run
```

두 번째 터미널:

```bash
make smoke
```

`make run`은 `APP_ENV=development`, `PLANNER_MODE=deterministic`으로 실행됩니다. 이 parser는
회귀·E2E를 위한 비-LLM 개발 경로이며 제출 runtime의 HCX 대체재가 아닙니다. 로컬 HTTP가
통과해도 live HCX와 public endpoint가 통과했다고 표시하지 않습니다.

## 6. Docker E2E

Docker가 있는 깨끗한 환경에서:

```bash
docker build --pull --no-cache -t mirae-agent:rc .
docker run --rm -p 8080:8080 \
  -e APP_ENV=test \
  -e PLANNER_MODE=deterministic \
  --name mirae-agent-local \
  mirae-agent:rc
```

다른 터미널에서 `make smoke`와 `/health/ready`를 확인합니다. 그다음 컨테이너를 종료하고
같은 image로 재기동한 뒤, 같은 요청의 상품 ID·순서·수치·answerability가 동일한지
비교합니다. runtime image에는 원본 Excel/PDF/ZIP이나 ETL build 도구가 아니라 검증된
serving DB와 실행 코드만 있어야 합니다. builder는 full `requirements.txt`를 사용하지만 최종
runtime stage는 DuckDB·FastAPI·HTTPX·Pydantic·Uvicorn만 담은 `requirements-runtime.txt`를
사용합니다. 원본 source와 `etl/`, 개발·감사 dependency가 runtime image에 들어가면 실패입니다.

저비용 초기 운영안은 embedded DuckDB를 사용하는 단일 VM 또는 managed container,
2 vCPU·RAM 2~4 GiB, Uvicorn worker 1개입니다. `deploy/compose.yaml`의 상한은 2 CPU·3 GiB로
맞추고, platform TLS를 우선 사용하며 제공되지 않을 때만 Caddy를 붙입니다. replica·worker를
먼저 늘리지 말고 실제 부하와 HCX quota를 확인한 뒤 조정합니다.

## 7. local과 production을 섞지 않는 법

| 구분 | local 회귀 | 제출 production |
|---|---|---|
| `APP_ENV` | `development` 또는 `test` | `production` |
| `PLANNER_MODE` | `deterministic` | `hcx` |
| 언어모델 호출 | 없음 | HyperCLOVA X만 |
| HCX key | 불필요 | secret manager에서 runtime 주입 |
| signing key | 개발 기본값 가능 | 별도 고강도 secret 필수 |
| 의미 | 코드·데이터 회귀 | 실제 제출 동작 |

현재 `HCX_MODEL_ID=HCX-007`과 공식 CLOVA Studio host는 `TEAM_DECISION` baseline입니다.
8월 6일 설명회나 이후 서면 공지에서 다른 model ID·endpoint가 확정되면 config 한 줄만
몰래 바꾸지 않습니다. `app/config.py`, HCX mock·live test, manifest generator, 문서,
배포 설정을 함께 변경하고 requirement label을 `BRIEFING_CONFIRMED`로 갱신합니다.

production 기동에 필요한 비밀값은 다음 두 개입니다.

- `CLOVA_STUDIO_API_KEY`
- `CLARIFICATION_SIGNING_KEY` (clarification state를 사용할 때 최소 24 random bytes)

실제 값을 `.env`, Dockerfile, compose 파일, shell script, Git, 로그, manifest, 대화 프롬프트에
붙여넣지 않습니다. 배포 플랫폼의 secret manager로 주입하고, Codex에는 값이 아니라
환경변수 이름과 주입 여부만 알려줍니다. `HCX_BASE_URL`, timeout, retry, result limit 같은
비밀이 아닌 설정도 release manifest의 config digest와 일치시킵니다.

## 8. 설명회 녹취를 받은 뒤 쓰는 프롬프트

원본 음성·녹취·사진을 예를 들어 `inputs/briefing_20260806/`에 변경 없이 보존하고 hash를
기록한 뒤, Codex에 아래를 전달합니다.

```text
Goal
- 2026-08-06 금융상품 Agent 설명회 원문을 현재 공식 기준선과 대조하고,
  확정된 변경만 기존 MVP의 contract/registry/config/test/runbook에 반영하라.

Context
- 먼저 AGENTS.md와 docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md를 읽어라.
- inputs/briefing_20260806/의 원본은 불변 source다.
- 현재 기준선은 docs/02_REQUIREMENTS_BASELINE.md이고, 기존 구현은 app/, etl/, registry/,
  contracts/, tests/에 있다.

Constraints
- 원문을 수정하지 말고 hash·발화자·시각·청취불가를 보존하라.
- 발화를 MUST/MUST_NOT/SHOULD/MAY/EXAMPLE/FUTURE_NOTICE/OPINION으로 분류하라.
- 현장 개인 의견을 공식 확정으로 승격하지 마라. 불충분하면 OPEN_QUESTION으로 유지하라.
- 먼저 source-by-source diff와 영향 파일·테스트를 작성한 뒤 구현하라.
- 기존 MVP를 다시 만들거나 문서 전체를 재작성하지 말고 영향받는 adapter, registry,
  config, fixture와 문서만 최소 변경하라.
- 금지사항 완화, API 계약 변경, HCX model 변경, 데이터 의미 변경은 regression test와
  요구사항 추적표 없이 반영하지 마라.

Done when
- 원문 보존본, 교정 전사, requirement extraction, source diff가 서로 추적된다.
- P0 질문마다 confirmed/open/conflict와 근거 발화가 기록된다.
- 확정 변경이 contract/registry/config/test/docs에 일관되게 반영된다.
- source verification, lint, 관련 테스트, full regression, real HTTP가 통과한다.
- live HCX·Docker·public 배포가 필요한 항목은 실제 검증과 blocker를 구분해 보고한다.
```

설명회에서 구두로 들었지만 확정 수준이 불명확한 내용은 바로 코드에 넣지 말고 Q&A
게시판의 서면 확인 항목으로 남깁니다.

## 9. 구현 우선순위

새 기능 수보다 심사 가능한 한 줄 E2E가 먼저입니다.

1. 네 공식 데이터셋의 재현 가능한 ETL·정확한 모집단
2. lookup/search/filter/rank/compare/aggregate와 제한적 cross-scope
3. 수치·상품·정렬 결과의 field-level evidence와 answerability
4. 구체적인 역질문과 안전한 확인 불가
5. HCX structured planning과 local schema/allowlist
6. public GET adapter·Docker·real HTTP·운영 안정성
7. gold/blind/safety/fault/load 회귀와 기술제안서
8. UI·외부 데이터·고급 기능은 release gate 이후

다음은 MVP 안정 전 시작하지 않습니다: runtime multi-agent, GraphDB, 포트폴리오 최적화,
개인화 추천, live market 연동, 대형 UI, 공식 mapping 없는 통합 risk score, 통화 환산 없는
cross-scope AUM 순위.

## 10. Release candidate와 freeze

release 직전에는 다음 순서를 한 번의 동일 candidate에 대해 실행합니다.

```bash
make verify
make build-data
make lint
make test-fast
.venv/bin/python -m pytest -q tests/test_etl.py
.venv/bin/python -m pytest -q
make compliance
```

그 뒤 fresh Docker build, local real HTTP, restart/same-result, live HCX, public TLS·DNS·timeout을
검증합니다. 최종 test report에는 실제 명령, pass/fail/skip, 시각, Git SHA, image digest를
기록합니다.

현재 local 증빙은 source XLSX 8/8 검증, fast 153/153(14.90초), full
158/158(104.57초), gold/policy 50/50(40 plan subset·103 assertion), compliance
28 files/0 findings, HTTP E2E 15/15입니다. 100요청·동시성 10 부하는 failure 0,
p95 131.75ms였습니다. serving DB SHA-256은
`4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`입니다. external
gate가 남아 있으므로 report와 manifest는 **DRAFT only**이며 FINAL 허용 상태가 아닙니다.

같은 기준선의 DB reconciliation은 raw 145,393, logical 60,913, serving 60,903,
quarantine 10, fund attributes 95,618, metric evidence 1,156,332입니다. 국내 ETF 1Y는
source-present 986·quality-valid 951·공통 최신일 `2026-06-15` rankable 940이고, 펀드는
full 11,138/API 기본 공모 11,115를 구분합니다. 공모 기본 universe의 위험등급 보유
8,564·결측 2,551, 판매중 8,445·판매완료 2,670을 release 회귀에서 함께 확인합니다.

FINAL manifest의 DB hash는 host의 우연한 DB가 아니라 immutable image에서 꺼낸 DB를
사용합니다. 실제 Git SHA, registry가 반환한 image digest, 최신 test report와 실제 통과
개수를 넣습니다.

```bash
docker create --name mirae-agent-manifest mirae-agent:rc
docker cp mirae-agent-manifest:/app/data/serving/mirae_agent.duckdb /tmp/mirae_agent.rc.duckdb
docker rm mirae-agent-manifest

APP_ENV=production PLANNER_MODE=hcx HCX_MODEL_ID=HCX-007 \
  .venv/bin/python scripts/generate_release_manifest.py \
  --final \
  --git-sha '<40자리 실제 git sha>' \
  --image-digest 'sha256:<64자리 실제 registry digest>' \
  --image-ref '<registry/name>@sha256:<같은 64자리 registry digest>' \
  --passed '<실제 통과 수>' \
  --failed 0 \
  --skipped '<실제 skip 수>' \
  --test-report artifacts/test_report_<release>.json \
  --serving-database /tmp/mirae_agent.rc.duckdb
```

현재 generator가 승인 baseline으로 검사하는 `HCX-007`과 공식 host가 설명회에서 바뀌면,
generator·schema·config·테스트를 먼저 함께 갱신한 뒤 FINAL을 만듭니다. placeholder SHA·digest,
실패 테스트, 외부 gate가 남은 test report, host DB로는 FINAL을 만들지 않습니다.
generator는 report의 `pytest_summary`와 CLI 수치를 대조하고, 모든 external gate가 PASS인지,
Git SHA가 현재 HEAD인지, 추출 DB가 현재 registry에 대해 ready인지 확인합니다. image digest의
실재 여부는 `docs/10_RELEASE_FREEZE_RUNBOOK.md`에 따라 registry 조회 결과를 두 사람이
독립 대조합니다.

남은 외부 gate는 (1) 실제 HCX credential을 사용한 E2E, (2) Docker fresh
build/run/restart와 immutable image digest, (3) public TLS/domain, (4) 실제 Git SHA,
(5) 2026-08-06 주최 측 설명회의 최종 API contract·허용 model 확인입니다. key를 받은 뒤에는
응답 header의 실제 QPM/TPM 한도를 기록하고 더 작은 내부 한도를 적용합니다. 추정 quota를
FINAL manifest에 넣지 않습니다.

내부 freeze는 2026-09-05, 공식 제출 운영 기준은 2026-09-06 23:59입니다. 제출 뒤에는
commit, push, 새 image build, deploy, prompt/data/code/registry/config/artifact 변경을 하지
않습니다. 장애복구 허용범위가 공식 확인된 경우에만 동일 image digest와 동일 config
digest를 재기동하고 운영 일지에 남깁니다. 허용 여부가 불명확하면 먼저 Q&A의 서면 승인을
받습니다.

## 11. 완료라고 말하기 전 최종 확인

- local 통과와 Docker/live HCX/public 통과를 구분했는가?
- `OFFICIAL_*`, `PDF_EXAMPLE`, `TEAM_DECISION`, `OPEN_QUESTION`을 섞지 않았는가?
- 모든 숫자·상품명·정렬·집계가 evidence에 있는가?
- exact-target 설명의 전략·benchmark, complex filter의 raw label, 교차 count의 scope별 ID
  field가 evidence에 있는가?
- 다중 metric에서 secondary 결측 상품이 사라지지 않고 모든 metric·blocking limitation이
  답변에 표시되는가?
- 역질문 선택지가 실제 해당 scope·metric에서 가능한 값인가?
- 데이터에 없는 field는 추정 대신 `UNAVAILABLE`인가?
- 기간·통화·단위·위험척도 혼합이 fail-closed인가?
- runtime dependency·endpoint·key에 비-HCX LLM 흔적이 0인가?
- access log·APM·error에 question과 secret이 남지 않는가?
- 최종 문서·OpenAPI·test report·manifest가 동일 release를 말하는가?
- 설명회·credential·Docker·배포권한이 없는데 완료라고 과장하지 않았는가?

하나라도 아니면 `완료`가 아니라 `부분 통과 + 정확한 blocker`로 보고합니다.
