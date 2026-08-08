# Build, verification, E2E, and release scripts

Run from the package root.

```bash
python scripts/verify_sources.py
python scripts/build_data.py --no-parquet
python scripts/run_gold.py
python scripts/scan_runtime_compliance.py
python scripts/e2e_smoke.py --base-url http://127.0.0.1:8080
python scripts/load_smoke.py --base-url http://127.0.0.1:8080 --requests 100 --concurrency 10
python scripts/generate_release_manifest.py --passed 262 --failed 0 --skipped 0 \
  --test-report artifacts/test_report_20260803.json

python scripts/profile_source_data.py \
  --zip inputs/official_data.zip \
  --output artifacts/raw_profile_regenerated
```

Source verification and data build fail closed on a source hash or structural mismatch. The profiler also requires
exactly eight XLSX members, one datarows/schema pair per dataset, official row/column counts, and
schema/header set equality.

- `build_data.py`: raw/clean/canonical/serving DuckDB and optional Parquet
- `run_gold.py`: 40 gold + 10 cross/safety fixture, 40 plan subset과 103 선언 assertion 검증
- `scan_runtime_compliance.py`: bounded non-HCX LLM SDK/endpoint/key dependency gate
- `production_preflight.py`: secret 값을 출력하지 않는 production env, immutable image/DB,
  HCX model/base URL, `PLANNER_STAGE=two`, sanitized 20-question live A/B report,
  process, budget, optional public live/ready gate
- `e2e_smoke.py`: 15 real HTTP cases against an already-running server; four scopes, clarification,
  safety, safe cross-count, complex catalog filter, exact-target explain, explain clarification,
  signed multi-turn follow-up 포함
- `load_smoke.py`: bounded real HTTP load smoke; default 100 requests/concurrency 10 and response-contract validation
- `generate_release_manifest.py`: non-secret release fingerprint; report의 pytest 수치와 실행 check,
  serving DuckDB readiness를 다시 검증합니다. `--final`은 실제 Git HEAD, 모든 external gate PASS,
  digest가 붙은 `--image-ref`, immutable image에서 추출한 `--serving-database`를 요구합니다.
  정확한 image 추출·검증 명령은 `docs/10_RELEASE_FREEZE_RUNBOOK.md`를 따릅니다.
