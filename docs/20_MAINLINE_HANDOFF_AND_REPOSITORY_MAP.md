# 금융상품 Agent — main 인수인계·저장소 지도

작성일: 2026-08-09

대상: `main`으로 병합되는 `codex/human-qa-chatbot-v1`
현재 총판정: **`PENDING_EXTERNAL`** — 로컬 구현·검증은 완료했지만 실제 HCX/Embedding 및 공개 운영은 아직 사람이 실행해야 한다.

이 문서는 다른 Codex 계정이나 새로운 대화 세션이 맥락 없이 저장소를 열었을 때의
단일 출발점이다. 오래된 문서를 삭제하지 않는다. 각 시점의 오류·수정·검증은 다음
개발자가 같은 실수를 되풀이하지 않게 하는 감사 기록이기 때문이다. 대신 이 문서가
현재 사실, 역사 기록, 외부 대기 상태를 분리한다.

## 1. 육하원칙: 무엇을, 왜, 누가, 언제, 어디서, 어떻게

| 항목 | 답 |
|---|---|
| 무엇(What) | 네 공식 상품 스코프(국내채권·국내 ETP·해외 ETP·공모펀드)를 조회·필터·정렬·집계·비교·교차질의하고, 공식 원본 행까지 근거를 연결하는 금융상품 Agent다. 별도로 팀 내부 인간 검증용 QA 챗봇을 제공한다. |
| 왜(Why) | 대회 요구의 근거 기반 응답과 HCX-only 런타임을 지키면서, 조건 누락·환각·교차질의 거부를 구조적으로 막고 사람이 실제 응답을 검증할 수 있게 하기 위함이다. |
| 누가(Who) | 대회 Agent는 최종 사용자의 자연어 질문을 받는다. QA 챗봇은 초대받은 팀 테스터만 사용한다. Codex는 개발 도구일 뿐 런타임 LLM이 아니다. |
| 언제(When) | 2026-08-03 prebrief를 기준선으로, 08-06 설명회/감사 뒤 재설계했고, 08-08~09에 Federated Retrieval·검증·QA 챗봇을 완성했다. 외부 release gate는 아직 실행 전이다. |
| 어디서(Where) | 대회 엔진은 FastAPI/DuckDB에서, 인간 검증은 로컬 `127.0.0.1:8090` 또는 승인된 사설 LAN TLS에서만 동작한다. QA 서비스와 엔진은 Docker 내부망으로 연결한다. 공개 인터넷 배포는 하지 않는다. |
| 어떻게(How) | HCX가 의도/개념만 계획하고 서버가 ontology·allow-list로 grounding한다. Exact/Alias·SQL·Graph·BM25·선택적 Vector를 라우팅하되, 숫자·필터·정렬·최종 근거 행은 항상 SQL로 재검증한다. |

## 2. 먼저 읽을 순서

1. 이 문서 — 현재 지도, 경계, 재현 절차
2. [`docs/19_HCX_HUMAN_QA_CHATBOT.md`](19_HCX_HUMAN_QA_CHATBOT.md) — 별도 인간검증 제품의 UX·보안·운영 경계
3. [`docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`](18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md) — 대회 엔진의 최종 설계와 release gate
4. [`docs/17_OFFICIAL_CONFORMANCE_AND_ADVERSARIAL_ASSURANCE.md`](17_OFFICIAL_CONFORMANCE_AND_ADVERSARIAL_ASSURANCE.md) — 공식 요구와 적대적 검증의 연결
5. [`HANDOFF_CURRENT_STATUS.md`](../HANDOFF_CURRENT_STATUS.md) — 세부 상태·명령·이력
6. [`docs/16_MASTER_PROJECT_NARRATIVE.md`](16_MASTER_PROJECT_NARRATIVE.md) — 왜 설계가 바뀌었는지와 사용자 원칙 원문
7. [`AGENTS.md`](../AGENTS.md) — 저장소에서 작업하는 에이전트의 필수 규칙

문서가 충돌할 때의 판정 순서는 **공식 PDF/공식 데이터 → 이후 주최 측 공지·녹취 → 이 문서와 최신 상태 문서 → 팀 설계/역사 기록**이다. 공식 평가 문항 수는 공개되지 않았다. 20·100·640·1,200·5,000 등의 수치는 모두 내부 gate다.

## 3. 보존 대상과 정리 원칙

`inputs/`의 다음 원본은 대회 근거이므로 이름 변경·수정·삭제하지 않는다.

| 파일 | SHA-256 | 역할 |
|---|---|---|
| `inputs/official_task.pdf` | `3717441e091958b7214db710e0e4b9b8ae15ac6c205cad6e51721214798eb3de` | 공식 과제 소개 PDF (8쪽) |
| `inputs/official_data.zip` | `c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163` | 공식 배포 데이터 ZIP (XLSX 8개) |
| `inputs/team_email_raw.txt` | `b1202ebac3210335af11322eb3e5695110783f94628996e1104368421657fb56` | 주최 측 팀 메일 보존본 |

`artifacts/source_manifest.json`은 위 원본의 검증 계약이다. `data/serving/`,
`qa_web/dist/`, `qa_web/node_modules/`, `deploy/qa/secrets/`, 로그와 DB WAL은
재생성 가능하거나 비밀/로컬 상태이므로 Git이 추적하지 않는다. 이미 추적된
`artifacts/*.json`·CSV는 검증 근거다. 중복처럼 보여도 어떤 커밋·검증을 설명하는지
확인하기 전에는 지우지 말고 `HISTORICAL`로 분류한다.

## 4. 코드와 실행 구조

```text
대회 평가자 /answer
  -> app/main.py (입력·응답 계약)
  -> app/planner/ (exact fast path 또는 HCX Stage-1)
  -> app/semantics/ (grounding, ConditionLedger)
  -> app/retrieval/ (Exact/Alias | SQL | Graph | BM25 | optional Vector/RRF)
  -> app/execution/ (SQL authority, cross-scope execution)
  -> EvidenceBundle + 안전 renderer
  -> question_id/question/retrieved_context/think_trace/answer (모두 문자열)

팀 테스터 브라우저 127.0.0.1:8090 또는 사설 LAN TLS
  -> qa_chat/ FastAPI Gateway + qa_web/ React UI
  -> 내부 Docker network의 대회 엔진 /answer
  -> 암호화 SQLite (대화·피드백만, 14일 보존)
```

| 경로 | 책임 |
|---|---|
| `app/` | 대회용 엔진. `GET /answer`의 다섯 문자열 계약은 변경 금지. |
| `etl/`, `registry/`, `data/` | 공식 데이터의 ETL, ontology/KG/BM25/Vector 계약과 제공 DB. |
| `eval/`, `tests/` | 독립 SQL oracle, 적대적·회귀·계약·QA 보안 테스트. |
| `qa_chat/` | 초대·동의·세션·암호화·대화 참조·피드백. LLM을 호출하거나 답을 작성하지 않는다. |
| `qa_web/` | 접근 가능한 세 영역(세션/채팅/근거 검사) UI. 빌드 결과는 추적하지 않는다. |
| `deploy/`, `compose.qa.yaml`, `Dockerfile.qa` | 대회 엔진 배포와 내부 QA 로컬/LAN compose·release gate. |
| `docs/`, `artifacts/` | 요구 추적, 설계 결정, 검증 증거와 역사 기록. |

## 5. 현재 검증 사실과 외부 경계

### 로컬에서 확인된 것

- 실행 코드 기준 커밋 `a8b34aa66cbeaf0407f0f2a869a3c1c38e9498d6`에서 Python 전체 테스트 **365 passed**, Ruff 통과, runtime compliance **179 files / 0 findings**, 의존성 검사 통과.
- QA 웹은 **11 test files / 22 tests**와 production build를 통과했다.
- fresh `--no-cache --pull` Docker 빌드, 내부 engine/QA gateway 재시작, healthcheck, `/answer` 5-field smoke 15회, 동시 10·총 100 요청 load smoke(5xx·schema 오류 0, p95 372.08ms)를 실행했다.
- 대회 엔진 검증 corpus는 640/640 회귀, metamorphic 137/137, v4 holdout 200/200, offline 5,000/5,000, 독립 direct 1,200/1,200, 2·3·4턴 300 flow를 로컬/fixture gate로 보존한다. Graph/BM25는 로컬 검증, Vector/RRF는 결정적 fixture 검증이다.

위 수치는 코드·fixture·Docker를 로컬에서 실행한 결과이며, 실제 모델의 품질 보증이 아니다. 실행 당시 QA runtime은 `HCX-FIXTURE-NO-LIVE`, Vector disabled였다.

### 아직 완료라고 말하면 안 되는 것

1. 승인된 HCX endpoint/model과 API 키로 **20 smoke → 100 canary → 전체 live gate** 실행
2. CLOVA Embedding 자격증명으로 1,024차원 전체 cache 생성 및 live smoke
3. NCP 계정·VPC·ACG·도메인/TLS를 통한 대회용 공개 HTTPS 배포
4. 주최 측 최종 운영 조건 확인과 사람이 하는 제출 승인·freeze

위 네 항목이 끝나기 전에는 README, 데모, 제출물 어디에도 `실서비스 검증 완료`나
`완벽히 완료`라고 쓰지 않는다.

## 6. 재현·검증의 최소 절차 (Windows PowerShell)

```powershell
git checkout main
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/verify_sources.py
.\.venv\Scripts\python.exe scripts/build_data.py --no-parquet
.\.venv\Scripts\python.exe -m ruff check app deploy etl scripts tests eval qa_chat
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/scan_runtime_compliance.py
```

QA UI도 확인할 때는 별도로 다음을 실행한다.

```powershell
Push-Location qa_web
npm ci
npm test -- --run
npm run build
Pop-Location
```

로컬 QA compose·초대 코드·secret 생성, LAN TLS, release preflight는
[`deploy/qa/README.md`](../deploy/qa/README.md)를 따른다. 예제 환경 파일만
사용하고 실제 키를 `.env`, Git, 테스트 fixture, 로그에 넣지 않는다.

GitHub의 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)은 `main` push와
PR에서 비밀 없이 공식 원본 검증, Ruff, Python 회귀·compliance, QA 웹 테스트와
production build를 다시 실행한다. HCX·Embedding·NCP gate는 secret과 인프라가
필요하므로 이 CI가 통과해도 `PENDING_EXTERNAL` 상태는 바뀌지 않는다.

## 7. 다음 에이전트의 작업 규칙

- `AGENTS.md`와 위 읽는 순서를 지킨 뒤, 변경 전후 실제 테스트를 다시 실행한다.
- `ConditionLedger`의 중요한 조건을 조용히 버리는 `FULL` 답변을 만들지 않는다.
- 교차 스코프 질의는 거부하지 않는다. 비교 불가 값은 기준일·통화·단위를 설명하고 분리 제시한다.
- 다른 LLM을 런타임 경로나 자동 fallback으로 추가하지 않는다. HCX 이외는 개발 도구일 수 있어도 저장소·배포 산출물에 넣지 않는다.
- 대회 엔진 API 계약을 변경해야 한다면 QA 챗봇과 공식 계약 영향, migration, 회귀를 함께 명시한다.
- `main`은 현재 통합 기준선이다. 실험은 새 브랜치에서 하고, 공식 원본/검증 근거/사용자 데이터는 별도 확인 없이 삭제하지 않는다.

## 8. 커밋 이력 읽는 법

- `prebrief-v1` 태그: 재설계 전 기준선
- `briefing-rebaseline-v2`: 설명회 기준 재설계 시작점
- `codex/federated-completion-v3`: Federated Retrieval·ConditionLedger·대규모 gate까지의 대회 엔진 완성선
- `codex/human-qa-chatbot-v1`: 인간 검증 챗봇과 최종 runtime 검증을 더한 통합선, 이 문서와 함께 `main`으로 병합

원인을 파악할 때는 `git log --oneline --decorate --all`과
`docs/16_MASTER_PROJECT_NARRATIVE.md`의 타임라인을 함께 읽는다. 역사 문서의 오래된
테스트 수치나 당시 기본값은 현 상태 주장으로 인용하지 않는다.
