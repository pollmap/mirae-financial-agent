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

## 4. 남은 외부 gate (변경 없음)

1. 2026-08-06 설명회: HCX model ID·API contract 확정
2. 실제 CLOVA_STUDIO_API_KEY로 `deploy/live_hcx_plan_smoke.py` → 네 상품군 live E2E
3. Public HTTPS endpoint 배포 및 외부망 smoke
4. Private GitHub Organization push → 실제 Git SHA
5. registry push 후 immutable digest로 FINAL release manifest 생성 → freeze
