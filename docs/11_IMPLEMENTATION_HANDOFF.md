# 실행 MVP 현황·Codex 인계서

기준일: 2026-08-03  
상태: **DRAFT — 설명회 전 실행 MVP·v1 local gate 통과; 외부 release gate 미검증**

## 1. 결론

이 저장소는 더 이상 설계 문서만 있는 패키지가 아닙니다. 공식 ZIP 전수 ETL, DuckDB
결정론적 실행, HyperCLOVA X planner adapter, field-level evidence, 역질문, 금융 안전정책,
FastAPI GET endpoint와 자동 테스트가 구현되어 있습니다.

현재 `HCX-007`은 Native Structured Outputs를 위한 `TEAM_DECISION` 기본값입니다.
`OFFICIAL_PDF`가 확정한 것은 HyperCLOVA X만 허용한다는 점이며, 주최 측이 지급·허용하는
정확한 model ID는 설명회 전 `OPEN_QUESTION`입니다. 실제 credential을 사용한 live 호출,
Docker daemon의 fresh image build, 공개망 배포는 환경·권한이 없어 아직 검증되지 않았습니다.
Codex는 개발·검증 전용이며 제출·평가 runtime에는 포함하지 않습니다. contest runtime의
유일한 언어모델 경로는 HyperCLOVA X이고, 장애 시 다른 LLM으로 fallback하지 않습니다.

저비용 배포 기준선은 embedded DuckDB, 단일 managed container 또는 VM, 2 vCPU·RAM
2~4 GiB, worker 1입니다. Compose 상한은 2 CPU·3 GiB이며 platform TLS를 우선하고 필요한
경우에만 Caddy를 사용합니다. 실제 QPM/TPM은 key 발급 후 provider response header로
확인하기 전까지 확정하지 않습니다.

## 2. 구현·검증 matrix

| 영역 | 구현 | 실제 검증 | 상태 |
|---|---|---|---|
| source outer/inner hash | 예 | 내부 XLSX 8/8 source verify | 통과 |
| raw/clean/canonical/serving ETL | 예 | 145,393행 full build | 통과 |
| 상품·attribute reconciliation | 예 | 60,913→60,903, attr95,618 | 통과 |
| quarantine·partial | 예 | 10개 exact row/ID | 통과 |
| canonical field registry | 예 | 207/207 source field | 통과 |
| metric policy | 예 | v1 59행; ETL·runtime 동일 registry 사용 | 통과 |
| deterministic QueryPlan | 예 | gold 40 plan subset + registry period/catalog guard | 통과 |
| deterministic execution/evidence | 예 | 50 fixture·40 plan subset·103 assertions | 통과 |
| exact-target explain | 예 | raw strategy·benchmark + target 누락 역질문 | 통과 |
| safe cross-scope count | 예 | scope별 distinct count·source evidence | 통과 |
| complex catalog filter | 예 | asset/region/risk/pension raw label | 통과 |
| multi-metric rank | 예 | primary INNER·secondary LEFT/NULLS LAST·전 metric 렌더 | 통과 |
| numeric aggregate | 예 | 각 상품 snapshot·혼합 기준일 null·단일 통화 sum/avg/min/max | 통과 |
| 명시적 역질문+후속 | 예 | API contract test | 통과 |
| safety | 예 | forecast/recommend/missing/live | 통과 |
| HCX adapter | 예 | HCX-007 team baseline mock success/length/429 | 통과 |
| HCX live 호출 | 예 | credential 없음 | 미검증 |
| GET API | 예 | ASGI five-field·역질문 flow | 통과 |
| full pytest | 예 | fast 153/153(14.90초); full 158/158(104.57초) | 통과 |
| runtime compliance | 예 | 28 files/0 findings | 통과 |
| reproducible dependency locks | 예 | runtime/build lock 새 venv 설치·pip check·import | 통과 |
| real process HTTP | 예 | E2E 15/15; 100/100·동시성 10·failure 0·p95 131.75ms | 통과 |
| Docker | multi-stage·runtime 최소 dependency | daemon build 필요 | 미검증 |
| public TLS deployment | 설계 | target/권한 없음 | blocker |
| release manifest | hardened generator | DB/v1 hash와 local report의 schema-valid **DRAFT only**; Git/image digest·외부 gate 미완료 | final 대기 |

## 3. 자동 실증값

```text
raw rows                 145,393
logical products          60,913
serving products          60,903
bond                       42,394
domestic ETP                1,733
overseas ETP                5,638
fund                       11,138
quarantine                     10
fund attributes            95,618
metric policy rows              59
serving metric rows      1,156,332
gold/policy fixtures            50
pytest collected                158
pytest passed                   158
fast pytest                 153/153 (14.90s)
full pytest                 158/158 (104.57s)
gold/policy passed              50/50
gold plan subsets                  40
gold declared assertions           103
runtime scan          28 files/0 findings
real HTTP E2E                  15/15
HTTP load smoke            100/100 (concurrency 10, failure 0, p95 131.75ms)
```

`raw rows`는 물리 datarows, `logical products`는 격리 전 상품키 단위,
`serving products`는 격리 제외 상품입니다. metric policy의 `raw_denominator`는 격리 전
논리 상품 분모이며 펀드 물리 attribute 95,619행과 혼용하지 않습니다.

현재 serving DB SHA-256은
`4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`입니다. 국내 ETF 1Y는
source-present 986, quality-valid 951, 공통 최신 원천일 `2026-06-15` 기준 rankable 940입니다.
펀드 full serving 11,138과 API 기본 공모 universe 11,115를 구분합니다. 공모 기본 universe는
위험등급 보유 8,564·결측 2,551, 판매중 8,445·판매완료 2,670입니다.

## 4. P0 다음 작업

1. 팀 HCX key를 secret으로 주입해 실제 credential plan-only smoke와 네 상품군 E2E를 검증하고,
   provider response header의 실제 QPM/TPM을 기록합니다.
2. Docker 환경에서 fresh build/run/smoke/restart/same-result를 검증하고 immutable image
   digest를 확보합니다.
3. 승인된 cloud target·domain에서 public TLS endpoint를 검증합니다.
4. 실제 release Git SHA를 확정합니다.
5. 8월 6일 주최 측 설명회에서 최종 API contract와 허용 HCX model을 확인하고 diff로 반영합니다.
6. 동일 candidate의 image-extracted DB·Git SHA·image digest·외부 gate 증빙으로만 FINAL
   manifest를 생성한 뒤 freeze합니다.

## 5. MVP에서 의도적으로 하지 않은 것

- 개인화 투자추천·portfolio optimization
- 수익률 전망·실시간 시세
- GraphDB·복잡한 runtime multi-agent
- 외부 금융데이터 혼합
- 대형 UI
- 설명회 전 교차 metric 환산·통합 risk score
- target 없는 개방형 금융교육·투자해설

이 항목은 심사 핵심 E2E가 안정된 뒤 P2로 검토합니다.

## 6. 실행 명령

```bash
make verify
make build-data
make test-fast
.venv/bin/python -m pytest -q tests/test_etl.py
make compliance
make run
.venv/bin/python scripts/e2e_smoke.py --base-url http://127.0.0.1:8080
```

처음 인계받는 개발자는 루트의 `CODEX_USAGE_GUIDE.md` 순서로 환경을 열고,
`CODEX_MASTER_PROMPT.md` 전체를 Codex에 전달합니다.
