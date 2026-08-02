# Windows PC 로컬·Docker 검증 기록

검증일: 2026-08-03  
환경: Windows 11 Home, Python 3.12.10 (py -3.12), Docker Desktop 29.3.1 (linux/amd64)  
패키지: `mirae_financial_agent_release_candidate_20260803.tar.gz` → `Desktop\mirae_financial_agent_codex_prebrief`

이 기록은 `COMPLIANCE_AUDIT_REPORT_20260803.md` 6절에서 `미검증`이던 Docker 항목을
로컬 daemon에서 실증한 결과이다. 실제 HCX credential·public TLS·GitHub push·FINAL
manifest는 여전히 외부 gate로 남는다.

## 1. 원본 데이터 provenance 교차 확인

- Downloads의 `1.금융상품-20260728T011039Z-1-001.zip`은 Google Drive 래퍼 zip
  (과제 PDF + `1.금융상품/data/1.금융상품.zip`)이다.
- 내부 `1.금융상품.zip` SHA-256 = `c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163`
  → 패키지 `inputs/official_data.zip`과 **바이트 동일**.

## 2. 로컬 (Windows venv) 검증

```text
scripts/verify_sources.py                  PASS (PDF·ZIP·email 해시, XLSX 8개)
ruff check app deploy etl scripts tests    PASS
scripts/scan_runtime_compliance.py         PASS (28 files, 0 findings)
pytest -q                                  PASS (158 passed, 138.69s)
uvicorn deterministic dev server           /health/ready = ready, snapshot 2026-07-11
scripts/e2e_smoke.py (HTTP)                15/15 PASS
scripts/load_smoke.py 100req/conc10        100/100, p50 74.4ms, p95 175.9ms, p99 196.5ms, 0 failure
DEMO-BOND / DEMO-ETF-RANK / DEMO-CLARIFY(+후속) 수동 확인 PASS
```

Windows 주의: Git Bash `curl`은 한글 query를 인코딩 오류로 깨뜨려 역질문이 반환된다.
PowerShell `curl.exe` 또는 Python `httpx`를 사용할 것.

## 3. Docker fresh build / run / restart 검증

```text
docker build --no-cache -t mirae-financial-agent:rc-20260803 .
  - data-builder stage: verify_sources PASS → build_data(145,393행 전체 ETL) PASS
    → scan_runtime_compliance PASS (컨테이너 내부 재실행)
  - image ID: sha256:62cdc151093c2b1979bac182ffdd7bd502beaac5cd8500c2f9f48ec961a32aaa
    (로컬 image ID. registry push 후의 repo digest는 이와 다르며 FINAL manifest에는
     push된 registry digest를 기록해야 함)

docker run (APP_ENV=development, PLANNER_MODE=deterministic, 127.0.0.1:8081)
  - /health/ready = ready, snapshot 2026-07-11
  - e2e_smoke 15/15 PASS

docker restart 후 동일 질의 재실행
  - DOCKER-BOND answer SHA-256 before == after
    (a15a77cd8331b7abf0e14de15631418530f68086ee74d543df2a364aab474716)
  - e2e_smoke 재실행 15/15 PASS

production fail-closed 확인
  - 기본 env(production/hcx)로 키 없이 기동 시
    "CLOVA_STUDIO_API_KEY is required for HCX planning"으로 즉시 종료 (정상 fail-closed)

runtime 이미지 최소성 확인
  - /app에 app/, registry/, data/serving/mirae_agent.duckdb, requirements-runtime.lock만 존재
  - inputs/(원본 PDF·ZIP·XLSX), etl/, tests/, 개발 의존성 미포함 (runbook 8항 충족)
```

## 4. HCX 모드 전체 파이프라인 검증 (모의 CLOVA Studio)

`deploy/mock_clova_studio.py`(개발 전용)는 CLOVA Studio v3 Chat Completions
Structured Outputs 계약(경로·Bearer 헤더·status envelope·finishReason·usage)을 재현하고,
질문 해석은 내부적으로 결정론적 planner를 사용한다. 이를 통해 실제 key 없이
`PLANNER_MODE=hcx` 전체 스택(HTTP adapter·retry·schema 검증·plan guard·DuckDB·근거·역질문)을
실서버 형태로 검증했다.

```text
app(PLANNER_MODE=hcx, HCX_BASE_URL=mock:8099) 기동     PASS
think_trace planner=HCX-007 확인                        PASS (실제 HCX HTTP adapter 경유)
e2e_smoke 15/15 (HCX mode)                              PASS
load_smoke 40req/conc5 (HCX mode, 깨끗한 rate window)   40/40, p95 141.47ms, 0 failure
장애 시 controlled 503 + fallback_llm=none              기존 계약 테스트로 커버
```

### ⚠ 운영 발견: 기본 HCX_TPM_BUDGET으로는 분당 4건만 처리 가능

`HcxQueryPlanner`는 요청마다 system prompt(7,059B) + QueryPlan JSON schema(2,924B)
+ 고정 3,072B = **요청당 13,055 토큰 상당을 보수적으로 예약**한다.
기본 `HCX_TPM_BUDGET=60000`(compose 기본값 동일)에서는 **분당 4건**을 넘는 요청이
25초 deadline까지 대기하다 controlled 503으로 떨어진다. 로컬 smoke에서 실제 재현됨
(연속 실행 시 케이스 3건씩 503).

→ 평가 트래픽이 분당 5건만 넘어도 실패하므로, **key 수령 후 provider 실제 quota를
확인해 `HCX_TPM_BUDGET`(예: 600000)과 `HCX_QPM_LIMIT`를 반드시 상향 조정해야 한다.**
이번 HCX 모드 검증은 `HCX_TPM_BUDGET=600000 HCX_QPM_LIMIT=60`으로 수행했다.

## 5. 웹 데모 UI (개발 전용)

`web/index.html` + `app/main.py`의 dev-gated `GET /demo` 라우트
(`environment != "production"`에서만 등록, 제출 runtime 이미지에는 `web/` 미포함).
브라우저에서 모호한 질문 → 시장 역질문 → 기간 역질문 → 최종 순위 답변의
전체 다단계 clarification 흐름과 근거·think_trace 표시를 실제로 확인했다.

## 6. 제출 준비물 현황

```text
git repository            main @ ddf277588645a95928ada85b4c8e534ce5f4bdaf (로컬; org push 대기)
.env.production           생성 (CLARIFICATION_SIGNING_KEY 실난수, CLOVA key placeholder, git-ignored)
release manifest          artifacts/release_manifest.generated.json — DRAFT,
                          실제 git SHA·로컬 image ID 반영 (FINAL은 registry digest 필요)
```

## 7. 남은 외부 gate (변경 없음)

1. 2026-08-06 설명회: HCX model ID·API contract 확정
2. 실제 CLOVA_STUDIO_API_KEY로 `deploy/live_hcx_plan_smoke.py` → 네 상품군 live E2E
3. Public HTTPS endpoint 배포 및 외부망 smoke
4. Private GitHub Organization push → 실제 Git SHA
5. registry push 후 immutable digest로 FINAL release manifest 생성 → freeze
