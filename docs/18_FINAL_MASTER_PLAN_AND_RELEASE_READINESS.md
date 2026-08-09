# 금융상품 Agent 최종 마스터플랜·전수감사·Release Readiness

작성일: 2026-08-09
기준 브랜치: `codex/federated-completion-v3`
런타임 소스 기준 커밋: `c7c07c9bf6747affd56263e4eb1972e29e72cc56`
현재 총판정: **`PENDING_EXTERNAL` — 로컬 완성 후보, 실서비스 완료 아님**

이 문서는 **대회 엔진** 현재 판정의 단일 진입점이다. main 전체와 팀 인간검증
챗봇까지 포함한 첫 진입점은 `docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md`다. 수치는
`artifacts/release_evidence_v4.json`에서 가져온다. 오래된 문서의 158·238·246·262·
275 테스트, 28·84·88·93 compliance, 1,000 direct/200 flow, 과거 Docker digest는
각 당시 커밋의 `HISTORICAL` 기록이며 현재 합격 근거가 아니다.

## 1. 결론부터

현재 코드는 공식 네 상품 스코프를 대상으로 조회·복합필터·정렬·비교·집계·설명·
교차질의·역질문·근거 추적을 실제로 실행한다. 2단계 HCX 플래너를 기본값으로 하고,
서버가 모든 조건을 허용 필드와 공식 데이터에 grounding한 뒤 DuckDB가 상품과 숫자를
결정한다. Exact/Alias, SQL, Graph 1–2 hop, BM25, 선택적 Vector/RRF가 조건별로 연결돼
있으며 최종 숫자와 근거 행의 권위 원천은 SQL이다.

그러나 “완벽히 완료” 또는 “실서비스 검증 완료”는 아니다. HCX와 CLOVA Embedding 실제
자격증명, 확정 endpoint/model, NCP 공개 HTTPS 인프라, 사람의 제출·freeze 승인이 없기
때문이다. 이 네 가지는 코드로 대신할 수 없고, 모두 끝날 때까지 release 상태는
`PENDING_EXTERNAL`이다.

## 2. 판정 근거의 권위

| 우선순위 | 분류 | 자료 | 적용 원칙 |
|---:|---|---|---|
| 1 | `OFFICIAL_PDF` / `OFFICIAL_DATA` | 과제 소개 PDF와 공식 금융상품 ZIP | 평가·상품·근거·제출 판단의 최우선 원천 |
| 2 | `BRIEFING_GUIDANCE` | 설명회 녹취, NCP 서비스·크레딧 자료 | Graph·Ontology·Federated·grounding 방향과 운영 참고 |
| 3 | `TEAM_DECISION` | 마스터플랜과 이 저장소의 gate | 공식보다 엄격할 수 있으나 공식 요구라고 표현하지 않음 |
| 4 | `PENDING_EXTERNAL` | 최종 공지, 계정·키·NCP 상태 | 확인 전에는 가정으로 release를 통과시키지 않음 |

다운로드 폴더의 원본 PDF·ZIP·DOCX는 수정하지 않았다. 공식 원본은 저장소 `inputs/`의
manifest와 SHA-256으로 식별하며, 파일명의 Unicode 표시나 첫 번째 glob 결과에 의존하지
않는다. 기존 마스터플랜은 중요한 역사·팀 설계 문서지만 공식 자료와 충돌하면 공식 자료가
우선한다.

공식 평가 문항 수는 공개되지 않았다. 이 프로젝트의 20·100·200·300·640·1,200·1,500·
2,100·5,000은 모두 내부 release gate 수치이며 주최 측 공식 평가량이 아니다.

## 3. 공식 요구와 실제 구현

| 요구 | 실제 실행 경로 | 현재 상태 |
|---|---|---|
| 네 상품군 자연어 조회 | 국내채권·국내 ETP·해외 ETP·공모펀드 typed plan | `VERIFIED_LOCAL` |
| 조건검색·상세·비교·정렬·집계 | allow-list plan → parameterized SQL, Decimal·기준일·결측 정책 | `VERIFIED_LOCAL` |
| 상품군 교차질의 | scope별 후보와 서브플랜을 유지하고 통합/분리/설명으로 끝까지 답변 | `VERIFIED_LOCAL` |
| 데이터 근거 | source file/sheet/Excel row/field/hash까지 연결한 EvidenceBundle | `VERIFIED_LOCAL` |
| 정보 부족 시 역질문 | 가장 판별력 높은 질문 하나, 서명 상태, 2·3·4턴 누적·변조 차단 | `VERIFIED_LOCAL` |
| HCX-only | 운영 LLM adapter는 HyperCLOVA X 하나, 다른 LLM fallback 없음 | 정적·mock은 `VERIFIED_LOCAL`, 실제 HCX는 `PENDING_EXTERNAL` |
| Graph·Semantic retrieval | 관계 1–2 hop, BM25, SQL 재검증, Vector 1,024차원 계약 | Graph/BM25 `VERIFIED_LOCAL`, Vector `VERIFIED_FIXTURE` |
| 공개 GET API | `/answer`와 다섯 문자열 필드, healthcheck, no-store·access-log 차단 | 로컬 `VERIFIED_LOCAL`, 공개 TLS `PENDING_EXTERNAL` |

## 4. 최종 아키텍처

```text
GET /answer
  -> 입력·안전 가드
  -> exact code/name fast path 또는 HCX Stage 1 semantic plan
  -> server grounding + ConditionLedger
  -> RetrievalPlan
       Exact/Alias | SQL | Graph(1-2 hop) | BM25 | optional Vector/RRF
  -> SQL authority 재검증·계산·정렬·집계
  -> evidence + answerability + condition ledger
  -> 결정론적 renderer
  -> 5-string response
```

2단계가 기본이다. HCX는 의도·스코프·개념·조건·정렬·집계만 만들고 물리 필드·SQL·
도구를 결정하지 않는다. 서버 grounder가 ontology와 허용 레지스트리에 결속한다.
1단계는 `PLANNER_STAGE=one`을 명시했을 때만 쓰는 수동 롤백이며 자동 fallback도, 동일
질문에 대한 중복 HCX 호출도 없다. 코드와 정확한 상품명은 결정적 fast path로 HCX를
생략한다.

### ConditionLedger

모든 질문은 스코프, 상품유형, 운용사·발행사, 지역, 자산유형, 전략, 벤치마크, 기간,
지표, 조건, 정렬, 집계를 ledger에 남긴다. 각 조건은 다음 중 정확히 하나다.

- `grounded`: 공식 데이터와 실행계획에 결속됨
- `clarification_required`: 결과를 바꾸는 정보가 부족해 사용자 확인 필요
- `unavailable`: 공식 데이터에 해당 값/필드가 없음
- `not_comparable`: 단위·통화·기간·기준일 차이로 직접 비교할 수 없음

중요 조건이 설명 없이 사라지면 `FULL` 답변을 허용하지 않는다. 이 규칙은 “배당 인컴
전략과 비슷한 해외 ETF”를 무조건 첫 5개로 바꾸거나 “미래에셋·미국 주식형” 중 일부를
조용히 버리는 기존 결함을 직접 막는다.

### 역질문과 다중대화

“좋은 상품 알려줘”는 우선 상품 범위를 묻고, “좋은 해외 ETF 알려줘”처럼 범위가 이미
있으면 수익률·규모·거래량 등 판단 기준을 묻는다. “수익률 높은 ETF”는 시장과 기간 중
가장 판별력이 높은 조건부터 하나씩 묻는다. 상품명 별칭이 여러 개면 실제 catalog 후보만
제시한다. 공식 데이터에서 유일한 기본값을 증명할 수 있을 때만 자동 적용하고 공개한다.

교차질의는 정보가 부족하다는 이유로 거부하지 않는다. 필요한 조건을 질문해 완료하고,
통화·기준일·단위가 다르면 값을 억지로 합치지 않고 scope별로 분리해 설명한다. 서명된
대화 상태는 사용자 답변과 정정을 단조롭게 누적하며 만료·변조·불완전 parameter pair는
통제된 오류로 차단한다.

## 5. Federated Retrieval의 정직한 상태

| 채널 | 언제 사용 | 권위·fallback | 상태 |
|---|---|---|---|
| Exact/Alias | 코드·ISIN·ticker·정확 상품명·별칭 | exact가 우선, 모호하면 역질문 | `VERIFIED_LOCAL` |
| SQL | 숫자·필터·정렬·집계·최종 근거 행 | 모든 후보의 최종 권위 원천 | `VERIFIED_LOCAL` |
| Graph | 운용사·발행사·지역·자산·위험·벤치마크 | 실제 1–2 hop 후 scope/role 격리, SQL 원본행 재검증 | `VERIFIED_LOCAL` |
| BM25 | 전략·벤치마크 설명·퍼지 상품명 | KG/Vector 부재 시 정상 주경로 | `VERIFIED_LOCAL` |
| Vector/RRF | 유효한 1,024차원 cache와 embedder가 모두 있을 때 | 차원이 다르면 거부, zero-padding 없음, 장애 시 BM25 | `VERIFIED_FIXTURE` |

KG는 고아 노드·alias 충돌·잘못된 역할·순환을 검사한다. Graph의 후보가 SQL 조건과
불일치하거나 cover하지 못하면 SQL/BM25로 폴백하고 이유를 `RetrievalTrace`에 남긴다.
Trace에는 채널, 라우팅 사유, 후보 수, fallback, 검증 결과, 데이터 hash·시간만 담고
원문 prompt·비밀키·민감 헤더·비공개 chain-of-thought는 담지 않는다.

## 6. 데이터·통화·안전 정책

- source raw 145,393행, 논리상품 60,913, serving 60,903, quarantine 10
- metric evidence 1,156,332행
- KG 71,683 node / 206,274 edge / 249,874 alias
- lexical 82,249 doc / 1,291,823 posting / 43,932 vocabulary
- vector embedding cache 0: credential가 없으므로 의도적으로 비활성

결측·0·sentinel은 서로 다른 상태이며 서로 치환하지 않는다. 통화 기본값은 요청 지표를
가진 필터 결과가 단일 통화임을 데이터로 증명할 때만 적용한다. 그 밖에는 재질문하거나
scope별로 분리한다. snapshot을 실시간으로 표현하지 않고, 데이터에 없는 미래 수익률·
가격 예측, 수익 보장, 단정적 매수 추천, 허위 근거, prompt/SQL/키 유출을 차단한다.

## 7. 최종 로컬·fixture 증거

| 검증 | 결과 | 상태·해석 |
|---|---:|---|
| 전체 pytest | 288/288 | `VERIFIED_LOCAL` |
| Ruff | PASS | `VERIFIED_LOCAL` |
| runtime compliance | 102 files / 0 findings | `VERIFIED_LOCAL`; 범위가 정해진 scanner임 |
| 기존 독립 SQL oracle | 640/640, 교차거부 0 | `VERIFIED_LOCAL` |
| metamorphic | 137/137 | `VERIFIED_LOCAL` |
| 기존 Federated | holdout 100/100, Graph 120/120, BM25 20/20, A–E PASS | local/fixture 혼합 |
| v4 holdout | 200/200 | `VERIFIED_FIXTURE`; 아래 방법론 주의 |
| 신규 offline assurance | 5,000/5,000, 10군×500 | `VERIFIED_FIXTURE`, live provider 0 |
| extensive direct | 1,200/1,200, 근거·정책 1,200/1,200 | local deterministic baseline |
| 다중대화 | 300/300, 총 900 API 요청 | 2·3·4턴 각 100, 5개 scope 각 60 |
| 실제 HTTP | 15/15 | 최신 브랜치 새 프로세스 |
| 부하 | 100/100, 동시 10, 5xx/계약 오류 0, p95 112.45ms | 동일 기준 115.89ms 대비 2.97% 개선 |
| fresh Docker | no-cache+pull build, read-only healthy, smoke 15/15×2, restart 동일 답·근거 | `VERIFIED_LOCAL`; local image `sha256:f17c04…9459a4` |

v4 holdout은 과장하지 않는다. 첫 200문항 초안은 corpus 자체가 유효하지 않아 폐기했고,
수정·동결한 corpus가 count 모집단 버그를 실제로 발견했다. 버그를 고친 뒤 같은 동결
corpus로 200/200을 얻었다. 따라서 최종 결과는 유용한 회귀 증거지만 “수정 전 완전 blind
200문항에서 처음부터 100%”라고 주장하지 않는다. 폐기·버그 발견·최종 보고서를 모두
보존했다.

5,000문항은 Exact/별칭/퍼지/없는 상품, 범위·부정·복수조건·단위·기준일, 집계·그룹·
정렬·동률, KG 1–2 hop·역할·alias, BM25·Vector fixture·RRF, 교차 통화·비교가능성,
모호성·후속정정, 인젝션·유출·허위근거, Unicode·오타·혼용·초과입력, KG/Vector/HCX/DB
장애·read-only의 10군에 각 500개다. 유한 테스트가 모든 자연어를 증명하지는 않으므로
군별 oracle과 실패면을 분리했다.

## 8. 실제 HCX gate

실제 credential을 넣을 때는 비용과 장애 확산을 막기 위해 다음 순서를 고정한다.

1. 20문항 one/two parity smoke: 40 provider call
2. 100문항 two-stage canary
3. 1,200개 독립 의미 단일질의
4. 300개 대화 흐름: 2-turn 100, 3-turn 100, 4-turn 100, 총 900 API call

전체 호출은 단일 1,200 + 대화 900 = 2,100이다. 앞 단계가 실패하면 다음 단계는 실행하지
않는다. 단일질의 배분은 Exact/Alias 200, 복합필터 200, 집계·순위 150, Graph 관계 150,
BM25/Vector 의미검색 150, 교차스코프 200, 모호성 100, 안전성 50이다. 각 문항은 서로 다른
의미 명세이며 문구만 바꾼 중복 500×2 구조가 아니다.

대화는 국내채권·국내 ETP·해외 ETP·공모펀드·교차질의에 각 60개다. 조건 보충,
대명사, 지표 변경, 사용자 정정, 없는 선택지, 근거 요구, 만료·변조 state, 후속
인젝션을 포함한다. 결과 파일에는 질문·prompt·plan·answer·token·상품 UID·key를 저장하지
않고 집계와 corpus hash만 남긴다.

## 9. Release 상태표

| 항목 | 상태 | 통과 조건 |
|---|---|---|
| 코드·데이터·로컬 HTTP·Docker | `VERIFIED_LOCAL` | no-cache build·health·restart·smoke·100×10 load 통과 |
| Vector/RRF | `VERIFIED_FIXTURE` | 실제 1,024차원 cache 전에는 live라고 부르지 않음 |
| 실제 HCX 20→100→1,200+300 | `PENDING_EXTERNAL` | 사용자 확정 model/endpoint/key와 sanitized PASS report |
| CLOVA Embedding | `PENDING_EXTERNAL` | credential, 전체 cache, 차원 검사, live smoke |
| NCP 공개 HTTPS | `PENDING_EXTERNAL` | credit·VPC/server·ACG·domain/TLS·외부 smoke |
| 사람 제출·freeze | `PENDING_EXTERNAL` | 최종 공지 재확인, branch/image/endpoint 승인 |
| 이전 수치·digest 문서 | `HISTORICAL` | 현재 release 판정에 사용하지 않음 |
| 비-HCX runtime 경로 | `NOT_APPLICABLE` | 만들지 않음 |

`HCX-007`은 현재 팀 기본값일 뿐 주최 측 최종 지정값으로 표현하지 않는다. 운영 전 사용자가
확인한 model과 endpoint를 release manifest에 잠근다. FINAL manifest는 최종 Git commit과
immutable registry image가 존재한 뒤 저장소 밖에서 생성한다. 생성 시 SHA가 HEAD와 다르거나
live report가 하나라도 없으면 production preflight가 실패한다.

## 10. 사용자에게만 남는 네 단계

1. HCX 자격증명과 확정 모델·endpoint를 주입해 20 → 100 → 1,200+300 live gate 실행
2. CLOVA Embedding 자격증명을 주입해 정확히 1,024차원 전체 cache 생성·live smoke
3. NCP credit, VPC/server, ACG, domain/TLS를 개설해 공개 `/answer` 배포
4. 주최 측 최종 운영 공지를 확인하고 사람이 제출 브랜치·image·서버를 승인·freeze

이 네 단계 전에는 데모·README·제안서 어디에서도 “실서비스 검증 완료”, “제출 완료”,
“완벽히 완료”라고 표시하지 않는다.

## 11. 최종 실행 순서

```powershell
.venv\Scripts\python.exe scripts\verify_sources.py
.venv\Scripts\python.exe scripts\build_data.py --no-parquet
.venv\Scripts\python.exe -m ruff check app deploy etl eval scripts tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m eval.run_eval
.venv\Scripts\python.exe -m eval.metamorphic
.venv\Scripts\python.exe -m eval.federated_eval
.venv\Scripts\python.exe scripts\run_v4_holdout.py
.venv\Scripts\python.exe scripts\run_offline_assurance.py
.venv\Scripts\python.exe deploy\live_hcx_extensive_e2e_gate.py --local-verify
.venv\Scripts\python.exe scripts\scan_runtime_compliance.py
```

Docker는 이 명령 뒤 `--no-cache --pull` build → read-only start → health → `/answer`
smoke → restart → 동일 답·근거와 smoke → 100×10 load 순으로 검증했다. builder 내부
ETL과 compliance 102/0도 통과했다. Docker p95는 447.99ms로 이전 동일 Docker 기준
473.98ms보다 5.48% 낮았다. 이 local digest는 registry에 push된 immutable 제출 digest가
아니므로 FINAL manifest에는 쓰지 않는다.
