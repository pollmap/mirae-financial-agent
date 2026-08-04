# 인계 문서 — 다른 AI 에이전트/세션이 이어받을 때 가장 먼저 읽을 파일

기준: 2026-08-04, HEAD `d2ea24c842a99bc1993c56e19cb08656d102ead6` (branch `main`)
저장소: `https://github.com/pollmap/mirae-financial-agent` (**private**)

이 문서는 이 저장소를 처음 보는 AI 에이전트가 별도 대화 맥락 없이도 "지금 뭐가
되어 있고, 뭐가 안 되어 있고, 다음에 뭘 해야 하는지"를 정확히 파악하도록 쓴
단일 진입점입니다. 프로젝트 배경 문서(`00_START_HERE.md`, `AGENTS.md`,
`docs/11_IMPLEMENTATION_HANDOFF.md`)는 2026-08-03 아침 시점에서 멈춰 있으며,
그 이후 이 문서에 적힌 작업(Docker 검증, HCX 모드 E2E, 실 LLM 연결, 웹 데모,
GitHub push)이 추가로 진행됐습니다. **내용이 상충하면 이 파일과
`artifacts/windows_docker_verification_20260803.md`가 최신입니다.**

## 0. 한 줄 요약

미래에셋증권 AI Festival 예선용 "금융상품 Agent" — 자연어 질문을
HyperCLOVA X가 typed QueryPlan으로 바꾸고, 결정론적 DuckDB 엔진이 검색·필터·
비교·집계하고, 원본 엑셀 행까지 추적되는 근거와 안전한 한국어 답변을 GET API로
반환한다. **핵심 기능·데이터·안전정책·테스트는 완성됐고, 남은 것은 전부
"진짜 HCX API 키가 있어야만 되는" 외부 gate뿐이다.**

## 1. 지금 100% 확실하게 되어 있는 것 (재현 명령 포함)

### 1-1. 데이터·코드 본체
- 원본 PDF/ZIP 무수정 보존, 내부 XLSX 8개(145,393행/207필드) 전수 ETL →
  DuckDB serving DB (`data/serving/mirae_agent.duckdb`, SHA-256
  `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`)
- Downloads의 Google Drive 래퍼 zip 내부 데이터 zip이 `inputs/official_data.zip`과
  **바이트 단위 동일**함을 이 세션에서 직접 재확인함 (SHA
  `c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163`)
- 국내채권/국내 ETF·ETN/해외 ETF·ETN/공모펀드 4개 상품군: 상세조회·검색·필터·
  정렬·Top-N·비교·집계·안전하게 분리된 교차 count·필드 단위 근거·역질문(다단계)·
  안전정책(미래예측/추천/실시간/0-치환 차단) 전부 구현됨

### 1-2. 로컬 검증 (Windows, Python 3.12 venv, 이 PC)
```bash
cd mirae_financial_agent_codex_prebrief
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe scripts/verify_sources.py        # PASS
./.venv/Scripts/python.exe -m ruff check app deploy etl scripts tests   # PASS
./.venv/Scripts/python.exe scripts/scan_runtime_compliance.py           # 28 files / 0 findings
./.venv/Scripts/python.exe -m pytest -q                                 # 158 passed
```
- deterministic 모드 서버 기동 → `/health/ready` PASS → HTTP E2E 15/15,
  부하 100/100(p95 176ms)
- **Windows 주의**: Git Bash `curl`은 한글 query를 깨뜨림 (역질문이 잘못
  반환됨). PowerShell `curl.exe` 또는 Python `httpx` 사용할 것.

### 1-3. Docker fresh build/restart 검증 (이전에 "미검증"이던 gate를 닫음)
```bash
docker build --no-cache -t mirae-financial-agent:rc-20260803 .
# 컨테이너 안에서 원본 검증 → 145,393행 ETL 재빌드 → compliance 스캔까지 재통과
```
- 로컬 image ID `sha256:62cdc151093c2b1979bac182ffdd7bd502beaac5cd8500c2f9f48ec961a32aaa`
  (레지스트리 push 전 로컬 ID. push 후 repo digest로 교체 필요)
- run → smoke 15/15 → **restart 후 동일 질의 답변이 SHA-256까지 바이트 동일**
- production env(키 없이)로 기동 시 `CLOVA_STUDIO_API_KEY is required` 즉시 종료
  → **fail-closed 정상 확인**
- runtime 이미지에 원본 PDF/ZIP/`etl/`/`tests/` 미포함 확인 (freeze runbook 요건)
- 상세: `artifacts/windows_docker_verification_20260803.md` §1-3

### 1-4. HCX 모드 전체 파이프라인 E2E (모의 CLOVA Studio 서버)
`deploy/mock_clova_studio.py`(개발 전용, **저장소에 포함됨** — 결정론적 planner를
재포장한 것이라 안전)가 CLOVA Studio v3 계약(경로/Bearer 인증/status envelope/
finishReason/usage)을 재현. `PLANNER_MODE=hcx`로 앱을 기동해 진짜 HCX HTTP
어댑터(재시도·retry·schema 검증 포함)를 실제 경유시킴.
- E2E 15/15, 부하 스모크 40/40(p95 141ms) 통과
- 상세: 같은 파일 §4

**⚠ 운영상 중요 발견 (아직 코드로 고정하지 않고 문서·주석으로만 경고 처리)**:
`HcxQueryPlanner`가 요청마다 system prompt + QueryPlan schema + 고정
오버헤드로 **약 13,055 토큰을 보수적으로 예약**한다. 기본
`HCX_TPM_BUDGET=60000`(compose 기본값도 동일)에서는 **분당 4건**만 처리
가능하고 그 이상은 25초 대기 후 controlled 503으로 떨어진다. 실키를 받으면
provider 실제 quota 확인 후 `HCX_TPM_BUDGET`(예: 600000)과 `HCX_QPM_LIMIT`를
반드시 올려야 한다. → `deploy/env.production.example`에 경고 주석 추가함.

### 1-5. 실제 LLM(로컬 Ollama qwen3:8b) 연결 검증
`devtools/real_llm_clova_facade.py`(**저장소에는 미포함, `.gitignore`
처리됨** — 대회 규정상 제출 코드에 비-HCX LLM이 있으면 실격이므로 로컬
개발 머신에만 존재해야 함)로 CLOVA facade 뒤에 진짜 언어모델(Ollama +
qwen3:8b, 무료·로컬)을 연결해 **제출 코드 무수정으로** 10개 자연어 질문을
평가함.

- HTTP 200 9/10 (1건은 CPU 추론이 280초 deadline 초과 → 규정대로 503)
- **환각 0건, schema 검증 실패 0건**, 안전 차단 정상 동작
- **실사용 중 schema 결함을 발견해서 즉시 수정함** (아래 1-6)
- 상세: 같은 파일 §7 (`devtools/real_llm_eval.py`도 함께 참고, 이것도
  gitignore 대상)

### 1-6. 이번 세션에서 수정한 실제 버그
`app/planner/schema.py`의 `clarification_options` 필드가 `anyOf`(빈 배열
또는 2~4개 객체 배열)로 정의돼 있었는데, 실 LLM의 constrained decoding이
이 구조를 지키지 못해 문자열 배열을 반환 → 로컬 검증 실패로 이어짐.
HCX의 `anyOf` 지원 여부도 미확정(`OPEN_QUESTION`)이었으므로 실전에서 같은
사고가 날 위험이 있었음.
→ **평탄한 배열로 수정**(엄격한 0-or-2..4 규칙은 `QueryPlan.semantic_shape`
Pydantic 검증이 그대로 강제하므로 안전성 손실 없음). 커밋 `d2ea24c`.
mock으로는 절대 못 찾는 결함이었고, 실 LLM 연결의 존재 이유를 증명한 사례.

### 1-7. 웹 데모 UI (개발 전용, dev-gated)
`web/index.html` + `app/main.py`의 `GET /demo` 라우트
(`environment != "production"`에서만 등록되고, production 이미지에는
`web/` 디렉터리가 아예 안 들어감). 브라우저에서 모호한 질문 → 시장
역질문 → 기간 역질문 → 최종 순위 답변의 다단계 clarification 흐름과
근거·think_trace를 실제로 클릭해서 확인함.

### 1-8. GitHub push
- `pollmap` 계정, **private** 저장소 `pollmap/mirae-financial-agent`
- `gh auth status`로 이미 로그인되어 있었음 (토큰 scope: repo/workflow/etc.)
- `.env.production`(서명키 실난수, CLOVA 키는 placeholder)은 git-ignore
  확인 후 로컬에만 유지, **커밋되지 않음**
- `devtools/`(실 LLM 브릿지)도 git-ignore로 저장소에 미포함
- 시크릿 스캔: `git ls-files | grep -i env` → `.env.example`만 나옴 (안전)

## 2. 아직 안 된 것 — 전부 "코드로는 더 못 닫는" 외부 gate

| 항목 | 상태 | 왜 못 닫았나 |
|---|---|---|
| 실제 `CLOVA_STUDIO_API_KEY`로 live E2E | ❌ | 키 자체가 없음. `deploy/live_hcx_plan_smoke.py`가 준비돼 있어 키만 넣으면 바로 실행 가능 |
| 공식 HCX model ID 확정 | ❌ | `HCX-007`은 팀 baseline(`TEAM_DECISION`). 2026-08-06 설명회에서 공식 확정 예정 |
| Public HTTPS 배포 | ❌ | `deploy/compose.yaml`, `deploy/Caddyfile.example` 준비됐지만 실제 서버/도메인이 없음 |
| GitHub **Organization** push | ⚠️ 부분 | 개인 계정 `pollmap`의 private repo에는 push됨. 대회가 요구하는 팀 Org로는 아직 안 옮김 (Org가 아직 없거나 미확정일 수 있음) |
| FINAL release manifest freeze | ❌ | `artifacts/release_manifest.generated.json`은 여전히 `release_status: DRAFT`. registry push 후 실제 immutable image digest가 있어야 FINAL 가능 |
| 화면 UI (정식) | ❌ | `web/index.html`은 **개발 데모용**일 뿐, 대회 제출 화면 요구사항이 확정되면 그때 정식으로 만들어야 함 (README에 "설명회 전 보류" 항목으로 명시돼 있었음) |
| HCX_TPM_BUDGET 운영값 확정 | ❌ | 실키 발급 후 provider 응답 헤더로 실제 QPM/TPM을 확인해서 값을 올려야 함 (1-4 참고) |
| 설명회(8/6) 반영 | ❌ | 아직 열리지 않음. 반영 절차는 `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`에 정리돼 있음 |

## 3. 다음 AI 에이전트가 할 일 우선순위

1. **키가 생기면 가장 먼저**: `.env.production`에 실키 채우고
   `deploy/live_hcx_plan_smoke.py` 실행 → 성공하면 `PLANNER_MODE=hcx`
   실서버로 `scripts/e2e_smoke.py` 재실행 → provider 응답 헤더에서 실제
   QPM/TPM 확인 → `HCX_TPM_BUDGET`/`HCX_QPM_LIMIT` 조정
2. **설명회(8/6) 이후**: `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
   절차대로 `OPEN_QUESTION` 항목들을 `BRIEFING_CONFIRMED`로 갱신하고
   API 계약·모델 ID 확정 반영
3. **배포**: `deploy/compose.yaml` 기준 Docker 이미지를 레지스트리에
   push → public HTTPS 앞단(Caddy 등) → 외부망에서 `/health/ready`,
   `/answer` 재검증
4. **제출 직전**: `docs/10_RELEASE_FREEZE_RUNBOOK.md` 순서대로 Git SHA
   고정 → image digest pin → `scripts/generate_release_manifest.py --final`
   → freeze

## 4. 이 문서를 쓰는 법 (다른 에이전트에게)

- 이 파일부터 읽고, 세부 증빙은 `artifacts/windows_docker_verification_20260803.md`
  (Docker·HCX mock·실 LLM 검증 원본 로그 수준 기록)를 봐라.
- `docs/` 아래 01~13번 문서는 프로젝트 배경·요구사항·설계 근거로는 여전히
  유효하지만, "지금 뭐가 됐는지" 최신 현황은 이 문서와 `artifacts/*.json`,
  `git log`를 우선해라.
- `devtools/`는 로컬에만 있고 이 저장소(GitHub)에는 없다. 다른 머신에서
  실 LLM으로 재검증하려면 이 문서 1-5, 1-6 절을 참고해 새로 만들어야
  한다 (Ollama 설치 → 모델 pull → facade 작성, 이 세션에서 한 그대로).
