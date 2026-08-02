# HCX 연결 전·후 검증과 저비용 배포 Runbook

상태: **DRAFT only** `TEAM_DECISION` 운영안. 실제 HCX credential, 공개 도메인, cloud 권한이
없는 현재 환경에서는 **live 호출 또는 공개 배포를 완료했다고 주장하지 않는다**.

## 1. 먼저 고정할 경계

- `OFFICIAL_PDF`: 제출·평가 runtime의 언어모델은 HyperCLOVA X만 사용한다.
- `TEAM_DECISION`: Codex는 이 저장소를 개발·검증하는 도구일 뿐 container와 평가 runtime의
  모델, fallback, judge, router가 아니다.
- `TEAM_DECISION`: HCX는 자연어를 typed `QueryPlan`으로 바꾸는 역할만 한다. 상품 선택,
  filtering, 계산, 정렬, aggregation은 allow-list와 parameterized SQL을 쓰는 DuckDB가 한다.
- `TEAM_DECISION`: HCX 장애 시 다른 LLM으로 넘어가지 않고 controlled 503으로 닫는다.
- `OPEN_QUESTION`: 설명회가 확정할 정확한 HCX model ID, 지급 credential, credit, QPM/TPM,
  허용 지역이다. 현재 `HCX-007`은 Native Structured Outputs를 위한 baseline이지 주최 측의
  최종 지정 모델이라는 뜻이 아니다.

운영 구성은 다음 하나의 경로를 유지한다.

```text
public HTTPS GET /answer
  -> one FastAPI/Uvicorn process
  -> HyperCLOVA X plan only
  -> embedded DuckDB opened read-only
  -> evidence-only answer
```

2026-08-03에 다시 확인한 NAVER Cloud 공식 문서상 Structured Outputs는
`POST /v3/chat-completions/{modelName}`이고 현재 HCX-007에서만 사용할 수 있습니다. 일부
JSON Schema keyword만 지원하며 Structured Outputs와 추론·Function Calling을 함께 요청할 수
없습니다. 공식 예시는 추론을 끄는 `thinking: {"effort":"none"}`과 `responseFormat.type=json`을
함께 사용하므로 현재 adapter도 그 예시를 따릅니다. 문서 설명과 실제 계정 기능 차이를 잡기
위해 key 수령 직후 plan-only live gate를 반드시 둡니다.

- [NAVER Cloud Structured Outputs 공식 문서](https://api.ncloud-docs.com/docs/en/clovastudio-chatcompletionsv3-so)
- [NAVER Cloud 이용량 제어 공식 문서](https://guide.ncloud-docs.com/docs/en/clovastudio-ratelimiting)

현재 serving DB 파일은 344,469,504 bytes(약 329 MiB)이며 Python runtime과 query working
memory가 추가된다.
따라서 처음부터 분산 DB, Kubernetes, 여러 API replica를 두지 않는다.

## 2. 가장 작은 합리적 운영 단위

권장 순서는 다음과 같다.

1. **작은 managed container 1개**: platform TLS, secret manager, health check, restart를 쓸 수
   있으면 최우선이다.
2. **단일 VM 1대**: managed container가 없을 때 2 vCPU, RAM 2~4 GiB, Caddy를 사용한다.
   2 GiB는 시작 가능한 최소선이고, DuckDB sort와 동시 요청 여유를 위해 4 GiB가 안전하다.
3. traffic 측정 전에는 replica/autoscaling을 켜지 않는다. in-process HCX QPM limiter가 worker별로
   분리되므로 worker나 replica를 늘리면 총 호출 상한도 의도치 않게 늘어난다.

초기값은 API container 1개, Uvicorn worker 1개, 2 vCPU, RAM 3 GiB, HCX concurrency 3,
DB concurrency 8이다. `deploy/compose.yaml`의 container limit은 2 CPU·3 GiB다. 부하 측정으로
부족함이 확인될 때만 하나씩 바꾼다.

runtime image에는 `app/`, `registry/`, runtime dependency, 검증된 DuckDB만 둔다. 원본 PDF,
ZIP/XLSX, ETL, test, Codex 또는 다른 LLM SDK를 넣지 않는다. DuckDB connection은 코드에서
`read_only=True`이고 Compose 예시는 root filesystem도 read-only로 두며 `/tmp`만 tmpfs로 연다.

## 3. HCX key를 받기 전: 돈을 쓰지 않는 E2E

### 3.1 결정론적 실제 HTTP E2E

```bash
make verify
make build-data
make test-fast
make compliance
make run
```

다른 terminal에서 다음을 실행한다.

```bash
make smoke
make load-smoke
```

이 단계는 실제 Uvicorn의 `/health/live`, `/health/ready`, 공개 GET `/answer`를 loopback HTTP로
호출한다. planner는 개발 전용 deterministic parser이며, HCX를 흉내 내는 제출용 대체 모델이
아니다. 네 상품군, 역질문 후속, safety, evidence, response contract와 DuckDB까지 검증한다.

### 3.2 실제 TCP mock HCX contract E2E

```bash
make hcx-mock-contract
```

`tests/contract/test_hcx_app_e2e.py`는 process 내부 함수 monkeypatch로 HCX 결과를 끼워 넣지
않는다. 임시 TCP HTTP server가 CLOVA Studio 응답 contract를 제공하고, 실제 `httpx` HCX
adapter가 `Authorization`, request ID, `/v3/chat-completions/HCX-007`, Structured Outputs
payload를 보내며, 응답은 local schema validation 후 DuckDB 조회와 최종 5-field 응답까지 간다.
공개 API 쪽 transport는 test process의 ASGI transport이고 HCX hop은 실제 HTTP라는 범위를
정확히 구분한다.

다음 셋을 모두 통과해야 “key 전 E2E 완료”라고 기록한다.

- deterministic Uvicorn real HTTP smoke
- HCX adapter real TCP mock contract E2E
- non-HCX runtime compliance scan

현재 local 증빙은 source XLSX 8/8, fast 153/153(14.90초), full 158/158(104.57초),
gold/policy 50/50(40 plan subset·103 assertion), runtime scan 28 files/0 findings, real HTTP
E2E 15/15입니다. load smoke는 100/100·concurrency 10·failure 0·p95 131.75ms입니다. serving
DB SHA-256은 `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`입니다.
이는 live HCX·Docker image·public network 검증을 대신하지 않습니다.

## 4. HCX key를 받은 후: 작은 호출부터 단계적으로

실제 key는 Git, `.env.example`, command argument, shell trace, CI log에 넣지 않는다. platform
secret manager를 우선 사용하고, 임시 protected env file을 쓰면 repository 밖에 mode `0600`으로
두며 `set +x` 상태에서만 load한다.

### Gate A — plan-only live call 1회

이 호출은 실제 quota/비용을 쓸 수 있으므로 명시적 확인 flag 없이는 실행되지 않는다.

```bash
export CLOVA_STUDIO_API_KEY='<secret-manager-injected>'
export HCX_MODEL_ID='HCX-007'
export HCX_BASE_URL='https://clovastudio.stream.ntruss.com'
.venv/bin/python deploy/live_hcx_plan_smoke.py --confirm-live-call
```

고정된 비민감 질문 한 건만 전송하고 출력에는 model, intent, scope, metric, token usage만 남긴다.
key, 질문 원문, plan 전문, request ID는 기록하지 않는다. 성공 조건은 HTTP 성공만이 아니라
HCX JSON이 local `QueryPlan` validation을 통과하는 것이다.

### Gate B — production preflight

실제 domain과 digest-pinned image가 정해진 뒤 다음 gate를 실행한다.

```bash
make production-preflight
```

이 gate는 secret 값을 출력하지 않고 다음을 fail-closed로 확인한다.

- `APP_ENV=production`, `PLANNER_MODE=hcx`
- `HCX_MODEL_ID=HCX-007` baseline과 공식 HTTPS base URL
- HCX key 20 bytes 이상, clarification signing key 24 bytes 이상, placeholder 아님
- embedded DB 파일 존재·read 가능·snapshot/source hash/schema/count readiness
- image가 mutable tag가 아니라 non-placeholder `@sha256` reference
- 공개 base URL이 HTTPS이고 example/placeholder domain이 아님
- `/health/live`, `/health/ready`, GET `/answer` 운영 contract
- worker 1, access log off
- timeout/retry/concurrency와 QPM/TPM/monthly-cost budget 선언

환경 template는 의도적으로 placeholder를 포함하므로 복사 직후 preflight가 실패하는 것이
정상이다. 실제 값으로 교체되어야만 통과한다.

### Gate C — 배포 후 무비용 readiness

```bash
make production-readiness
```

`PUBLIC_BASE_URL`의 liveness와 readiness만 호출한다. `/answer`와 HCX를 호출하지 않아 token을
쓰지 않는다. readiness는 DB snapshot date `2026-07-11`까지 확인한다.

### Gate D — live `/answer` 1회와 제한된 full E2E

먼저 비민감 lookup 한 건만 호출한다.

```bash
curl --fail --silent --show-error --get "$PUBLIC_BASE_URL/answer" \
  --data-urlencode 'question_id=LIVE-BOND-001' \
  --data-urlencode 'question=채권 코드 KR101501DA16의 상세 정보를 알려줘.'
```

HTTP 200, 정확히 5개 string field, `think_trace`의 HCX planner 표시, evidence의 단일 상품과
source locator를 확인한다. 그 뒤 budget 안에서만 전체 smoke를 실행한다.

```bash
.venv/bin/python scripts/e2e_smoke.py --base-url "$PUBLIC_BASE_URL"
```

full smoke는 여러 HCX 호출을 만들 수 있다. plan-only 1회가 실패한 상태에서 반복하지 않고,
429/5xx의 bounded retry가 끝나면 원인을 확인한다. 이 네 gate가 통과하기 전에는 “live HCX
E2E 완료”라고 표시하지 않는다.

## 5. managed container 배포

platform이 HTTPS certificate와 edge proxy를 제공하면 Caddy를 추가하지 않는다. 다음 설정을
platform UI/IaC에 그대로 옮긴다.

- image: release candidate를 registry에 push한 뒤 얻은 digest reference
- command: Dockerfile 기본 command 또는 `deploy/compose.yaml`의 one-worker command
- port: container `8080`; public HTTPS는 platform ingress가 termination
- readiness: `GET /health/ready`, interval 30s, timeout 3s, start grace 30s
- liveness: `GET /health/live`; readiness와 liveness를 혼동하지 않음
- resource: 2 vCPU, memory 2~4 GiB(초기 3 GiB), replica 1
- restart: readiness 실패 시 같은 digest·같은 config로 restart
- secret env: HCX key와 clarification signing key만 secret manager에서 주입
- access/APM: query string과 response body 수집 금지; Uvicorn access log off
- network: inbound HTTPS만, outbound CLOVA Studio HTTPS와 필요한 DNS만
- filesystem: read-only image, `/tmp` tmpfs; 외부 writable DB volume 없음

platform은 `deploy/compose.yaml`을 직접 쓰지 않아도 된다. 중요한 것은 그 파일의 process,
resource, health, secret, security contract를 같은 값으로 구성하는 것이다.

## 6. 단일 VM + Compose, Caddy는 필요한 경우만

`deploy/compose.yaml`은 agent port를 기본적으로 VM loopback에만 bind한다. cloud firewall에서도
SSH 관리 IP와 필요한 80/443 외에는 열지 않는다.

```bash
cp deploy/env.production.example deploy/.env.production
chmod 600 deploy/.env.production
# protected editor로 모든 placeholder를 실제 값으로 교체
set +x
set -a
. ./deploy/.env.production
set +a

make production-preflight
docker compose --env-file deploy/.env.production \
  -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env.production \
  -f deploy/compose.yaml up -d
make production-readiness
```

platform TLS가 없을 때만 Caddy overlay를 추가한다. DNS A/AAAA가 VM을 향하고 80/443이 열려
있어야 자동 certificate 발급이 가능하다. Caddy image도 tag가 아니라 digest로 pin한다.

```bash
docker compose --env-file deploy/.env.production \
  -f deploy/compose.yaml -f deploy/compose.caddy.yaml up -d
```

`Caddyfile.example`에는 access log directive가 없다. edge/provider의 기본 HTTP log도 별도로
확인하여 `/answer?...` query string을 저장하지 않도록 해야 한다. TLS termination을 Caddy와
platform 양쪽에 중복 구성하지 않는다.

## 7. 비용·quota guardrail

정확한 가격, 주최 credit, QPM/TPM은 아직 `OPEN_QUESTION`이다. 아래 값은 비용 보전을
약속하지 않으며, 숫자가 있는 항목도 낮게 시작하기 위한 `TEAM_DECISION` 예시다.

현재 공식 표의 HCX-007 최대치는 웹·테스트 key가 60 QPM/60,000 TPM, service app이
180 QPM/300,000 TPM입니다. 다만 service app 한도는 웹·테스트와 별도로 산정되고 실제 가입
한도는 응답의 `x-ratelimit-*` header로 확인해야 합니다. TPM은 실제 출력량이 아니라
`입력 토큰 + maxCompletionTokens` 기준으로 계산되므로 completion 상한을 불필요하게 크게
두면 안 됩니다. production template는 이 최대치를 그대로 쓰지 않고 30 QPM/60,000 TPM으로
보수적으로 시작합니다.

| 항목 | 시작값 | 실제 강제 위치 | 의미 |
|---|---:|---|---|
| `HCX_MAX_CONCURRENCY` | 3 | app | 동시에 진행되는 HCX 호출 수 |
| `HCX_QPM_LIMIT` | key 발급 후 실제 header 이하 | app, 단일 process | 60초 rolling 요청 시작 수를 모든 retry까지 포함해 강제 |
| `HCX_TPM_BUDGET` | key 발급 후 실제 header 이하 | app, 단일 process | UTF-8 byte 기반 보수적 입력 추정+schema/system/framing+최대 출력 예약을 60초 rolling으로 강제 |
| `MONTHLY_COST_CAP_KRW` | 팀 승인액 | provider budget/credit alarm 필요 | preflight가 선언만 검증; 실제 hard cap 아님 |
| completion limit | 1,024 tokens | HCX client | plan 출력 최대치 |

key 발급 뒤 plan-only 1회 응답 header에서 실제 QPM/TPM을 기록하고, 반드시 그 공식 quota와
팀이 감당 가능한 금액 중 더 작은 값을 선택한다. header를 확인하기 전에는 추정 숫자를 FINAL
config나 manifest에 넣지 않는다. provider가 hard budget을 제공하면 50%/80% 경보와 100%
차단을 설정한다. hard cap이 없다면 `MONTHLY_COST_CAP_KRW`라는 환경변수만으로 비용이
막힌다고 생각하면 안 된다. provider usage를 별도 집계하고 QPM을 더 낮추며, credit 소진 시
다른 LLM fallback 없이 controlled unavailable을 유지한다.

운영 지표에는 request count, status, latency, HCX status, schema failure, token usage 합계만
남긴다. 질문 원문, GET query string, evidence, answer, secret은 남기지 않는다.

## 8. Freeze와 장애복구

freeze 전에는 다음을 하나의 release manifest로 고정한다.

- Git SHA와 immutable image digest
- image 내부 DuckDB hash와 source hash
- HCX model/base URL, prompt/schema/registry hash
- QPM/TPM/cost budget을 포함한 non-secret config digest
- mock/live/Docker/restart/public TLS gate 결과

freeze 후에는 코드, data, prompt, model ID, registry, environment, image를 바꾸지 않는다. 새
build/tag 재지정/autoscaling/secret을 이용한 feature change도 하지 않는다. 장애 시 허용 범위
안에서 **같은 image digest와 같은 config**만 restart하고 시각·원인·담당자를 기록한다. 결과를
바꿀 수 있는 조치는 주최 측 서면 확인 없이 수행하지 않는다.

## 9. 최종 판정표

| Gate | key 필요 | HCX 비용 가능 | 통과 증거 |
|---|---|---|---|
| deterministic real HTTP | 아니오 | 아니오 | 15/15 smoke 결과 |
| real HTTP mock HCX contract | 아니오 | 아니오 | contract pytest PASS |
| plan-only live HCX | 예 | 1회 | redacted PASS metadata |
| production preflight | 예 | 아니오 | config+DB PASS |
| public TLS live/ready | 아니오 | 아니오 | HTTPS health PASS |
| live `/answer` + E2E | 예 | 예 | response contract/evidence PASS |
| restart determinism | 예 | 예 | 동일 digest/config 전후 결과 비교 |

credential, domain, cloud target이 없는 상태에서 앞의 두 gate만 통과했다면 정확한 보고는
“local deterministic와 mock HCX E2E 통과; live HCX/public TLS/restart는 외부 gate”이다.

FINAL로 전환하기 전에 실제 HCX credential E2E, Docker fresh build/run/restart와 immutable
image digest, public TLS/domain, 실제 Git SHA, 2026-08-06 주최 측 최종 API contract·허용
model 확인을 모두 닫아야 한다.
