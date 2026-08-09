# HCX 연결 전·후 검증과 저비용 배포 Runbook

상태: **DRAFT only** `TEAM_DECISION` 운영안. 실제 HCX credential, 공개 도메인, cloud 권한이
없는 현재 환경에서는 **live 호출 또는 공개 배포를 완료했다고 주장하지 않는다**.

> **v3 운영 변경**: `PLANNER_STAGE=two`가 필수 기본값이다. production preflight는
> 실제 HCX 20문항 one/two 40-call parity 보고서, 100문항 two-stage E2E 보고서, 그리고
> 1,200 independent direct + 300 multi-turn 강화 E2E 보고서가 모두 없으면 실패한다.
> 20·100·1,200은
> 팀 내부 gate이며 공식 평가 문항 수가 아니다.
> Vector credential/cache는 선택 사항이며 없으면 Exact+SQL+Graph+BM25로
> 정상 동작한다.

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

최신 local 증빙은 source XLSX 8/8, eval 640/640, metamorphic 137/137, v4
200/200, Graph 120/120, BM25 20/20, offline assurance 5,000/5,000, runtime scan
102 files/0 findings, real HTTP E2E 15/15입니다. load smoke는
100/100·concurrency 10·failure 0·p95 112.45ms이고
serving DB SHA-256은
`a4110183646a691009130e4a0723a634e2e5a471677a06fc45917952363a89be`입니다.
fresh Docker `--no-cache --pull` build, read-only start, healthcheck, smoke 15/15,
restart 뒤 동일 답변·근거 및 smoke 15/15를 통과했습니다. 로컬 image digest는
`sha256:f17c04b295948b87ea4cddb5d9473aa025630f5c766c01b300fb3c2eed9459a4`이며,
immutable registry 제출 digest와 public network 검증은 여전히 `PENDING_EXTERNAL`입니다.

키를 쓰기 전에 1,200 direct와 300 multi-turn corpus 자체가 현재 데이터·결정론 planner·
독립 SQL oracle·signed API state에서 재현되는지도 다음 명령으로 먼저 확인한다. 이 명령은
HCX를 호출하거나 credential을 읽지 않고, 질문 원문/답/token을 파일로 저장하지 않는다.

```bash
.venv/bin/python deploy/live_hcx_extensive_e2e_gate.py --local-verify
```

## 4. HCX key를 받은 후: 작은 호출부터 단계적으로

실제 key는 Git, `.env.example`, command argument, shell trace, CI log에 넣지 않는다. platform
secret manager를 우선 사용하고, 임시 protected env file을 쓰면 repository 밖에 mode `0600`으로
두며 `set +x` 상태에서만 load한다.

### Gate A — HCX 20문항 one/two A/B live gate (40회)

이 호출은 실제 quota/비용을 쓸 수 있으므로 명시적 확인 flag 없이는 실행되지 않는다.

```bash
export CLOVA_STUDIO_API_KEY='<secret-manager-injected>'
export APPROVED_HCX_MODEL_ID='<human-confirmed-hcx-model>'
export HCX_MODEL_ID="$APPROVED_HCX_MODEL_ID"
export APPROVED_HCX_BASE_URL='https://clovastudio.stream.ntruss.com'
export HCX_BASE_URL='https://clovastudio.stream.ntruss.com'
.venv/bin/python deploy/live_hcx_plan_smoke.py --confirm-live-calls 40
```

고정된 비민감 질문 20개를 1단계와 2단계에 각각 전송한다. 보고서는 case id,
validation/match count, model, token 합계만 저장하고 key, 질문 원문, plan 전문,
request ID는 기록하지 않는다. 20건 모두 local `QueryPlan` validation과 canonical
일치 검사를 통과해야 PASS다.

### Gate A1 — 100문항 two-stage planner→evidence smoke

```bash
.venv/bin/python deploy/live_hcx_e2e_gate.py --confirm-live-calls 100
```

rank 35, filter 25, aggregate 20, cross-scope 20을 실제 HCX two-stage planner와
독립 SQL oracle로 검사한다. 정확도 98% 이상, HCX 계획·원천 근거 100/100, 교차질의
거부 0, 질문·prompt·plan·answer·상품 ID·secret의 비저장을 요구한다.

### Gate A2 — 1,200 independent direct + 300 multi-turn live HCX API gate

100문항은 빠른 release smoke다. 실제 key를 받은 뒤에는 아래 강화 gate를 별도로 실행한다.
이 gate는 문구만 바꾼 중복이 아닌 **1,200개 독립 의미 direct 질의**가 실제 HCX
two-stage planner를 거쳤는지 확인한다. 이어서 2-turn·3-turn·4-turn 흐름을 각각 100개씩
같은 공개 GET `/answer` API로 실행한다. 대화 API 요청은 900개, 전체 요청은 2,100개다.

```bash
.venv/bin/python deploy/live_hcx_extensive_e2e_gate.py \
  --confirm-direct-hcx-calls 1200 \
  --confirm-api-requests 2100
```

direct 결과는 독립 SQL oracle로 98% 이상이어야 하며 HCX·근거·5-field response contract는
각각 1,200/1,200이어야 한다. 재질문 흐름은 300/300에서 signed token, server-side monotonic
state, 최종 HCX, 원천 행 근거, deterministic baseline과의 evidence signature 일치를 모두
요구한다. 결과 보고서는 digest와 집계값만 저장하고 질문·prompt·계획·답·token·상품 ID·key는
저장하지 않는다. provider 재시도에 따른 실제 HTTP 호출 수는 provider 측에서 별도로 확인하며,
이 명령의 1,200은 성공 조건으로 추적하는 서로 다른 direct HCX-planned response 수다.

### Gate B — production preflight

실제 domain과 digest-pinned image가 정해진 뒤 다음 gate를 실행한다.

```bash
make production-preflight
```

이 gate는 secret 값을 출력하지 않고 다음을 fail-closed로 확인한다.

- `APP_ENV=production`, `PLANNER_MODE=hcx`, `PLANNER_STAGE=two`
- sanitized 20문항 A/B report가 PASS이고 model/call/count/privacy contract가 일치
- sanitized 100문항 E2E 및 1,200 direct + 300 multi-turn E2E report가 각각 PASS이며
  정해진 HCX/근거/5-field contract와 privacy contract가 일치
- 사람이 확인한 `APPROVED_HCX_MODEL_ID`·`APPROVED_HCX_BASE_URL`과 실제 runtime 값의 일치
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

key 발급 뒤 20문항 A/B gate 응답 header에서 실제 QPM/TPM을 기록하고, 반드시 그 공식 quota와
팀이 감당 가능한 금액 중 더 작은 값을 선택한다. header를 확인하기 전에는 추정 숫자를 FINAL
config나 manifest에 넣지 않는다. provider가 hard budget을 제공하면 50%/80% 경보와 100%
차단을 설정한다. hard cap이 없다면 `MONTHLY_COST_CAP_KRW`라는 환경변수만으로 비용이
막힌다고 생각하면 안 된다. provider usage를 별도 집계하고 QPM을 더 낮추며, credit 소진 시
다른 LLM fallback 없이 controlled unavailable을 유지한다.

운영 지표에는 request count, status, latency, HCX status, schema failure, token usage 합계만
남긴다. 질문 원문, GET query string, evidence, answer, secret은 남기지 않는다.

**(2026-08-08 v3)** 2단계 플래닝(`PLANNER_STAGE=two`)은 물리 field/metric 이름과 값
리터럴이 없는 개념 전용 스키마로 보수적 요청 예약량을 13,013B→6,333B
(**−51.3%**)로 줄였다. `two`가 운영 기본이고 `one`은 수동 롤백 전용이며 자동
fallback은 없다. mock-HCX lookup/cross/aggregate, 640 회귀, holdout 100을 검증했고
실 20문항 A/B·100문항 smoke·1,200 direct+300 multi-turn gate가 credential 대기다. 별도로 `scripts/build_embeddings.py`가 읽는
`CLOVA_EMBEDDING_URL`/`CLOVA_EMBEDDING_MODEL_ID`는 이 표의 QPM/TPM guardrail과 무관한
오프라인 1회성 임베딩 생성 전용 변수로, 실 키 발급 후 그때만 실행한다(스크립트 자체
docstring에 문서화됨).

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
