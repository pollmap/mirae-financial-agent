# 제출·평가 Release Freeze Runbook

라벨: `OFFICIAL_PDF` 마감 후 변경 시 실격 + `TEAM_DECISION` 운영 통제

## 목적

제출 마감 이후 결과를 바꾸는 commit, push, 서버 배포, 코드·데이터·프롬프트·설정
변경이 일어나지 않도록 하나의 검증된 release를 고정합니다. 자동 restart·failover·secret
rotation의 허용범위는 설명회에서 확인하고, 확인 전에는 결과가 바뀌지 않는 동일 image
재기동만 설계합니다.

## T-48h: release candidate

1. 기본 branch와 제출 branch 차이를 확인합니다.
2. `python scripts/verify_sources.py`를 통과합니다.
3. clean multi-stage build로 container image를 만듭니다. builder는 full dependency와 원본
   source·ETL을 사용하지만 runtime은 `requirements-runtime.txt`, app·registry·검증된 DB만
   포함해야 합니다.
4. current real HTTP 15-case smoke, 100-request bounded load smoke, 네 상품군 gold, safety,
   fault, schema test를 실행합니다.
5. 다른 LLM endpoint·SDK·secret·fallback이 없는지 검사합니다.
6. public staging에서 실제 GET과 restart를 검증합니다.
7. `artifacts/test_report_20260803.json` 형식으로 test report를 만들고 proposal·README·API
   spec의 version을 맞춥니다.
8. runtime image에 원본 PDF·ZIP·XLSX, `etl/`, 개발·감사 dependency가 없는지 검사합니다.

## T-24h: immutable candidate

1. Git SHA를 고정하고 signed tag 후보를 만듭니다.
2. image를 digest로 pin합니다. mutable tag만으로 배포하지 않습니다.
3. 데이터·ETL·Metric Registry·QueryPlan schema·prompt·renderer·safety policy hash를
   계산합니다.
4. `artifacts/release_manifest.template.json`을 실제 값으로 채우고
   `contracts/release-manifest.schema.json`으로 검증합니다.
5. 운영 환경에서 같은 digest를 pull하고 readiness·GET를 재검증합니다.
6. autoscaling·restart가 같은 digest와 같은 config digest만 사용하도록 고정합니다.

## T-2h: 제출

1. 제출 repository URL·branch·commit SHA를 두 사람이 교차 확인합니다.
2. 소스코드, 재현환경, README, 기술제안서, endpoint URL, API 명세 3종을 확인합니다.
3. endpoint의 TLS·DNS·public access·timeout·response schema를 외부망에서 확인합니다.
4. NCP credit 잔액·알림·운영 종료 예정일을 확인합니다.
5. GitHub push 완료시각과 제출 화면을 보존합니다.
6. release manifest·image digest·test report hash를 운영 일지에 기록합니다.

## Freeze 이후

허용 여부를 설명회에서 확인한 범위 안에서 같은 release의 상태만 복구합니다.

- 코드·데이터·프롬프트·모델 ID·Metric Registry·config 변경 없음
- 새 image build·새 deploy 없음
- database serving file 교체 없음
- feature flag·환경변수로 결과 변경 없음
- 장애복구가 필요하면 동일 image digest·동일 config digest만 사용하고 시각·사유·담당자를 기록
- 비공개 평가 query는 access log·APM·analytics에서 redaction

결과 변경 가능성이 있는 조치가 필요하면 임의 수행하지 않고 주최 측 Q&A로 서면 확인을
받습니다.

## 운영 모니터링

- `/health/live`: process 생존만 확인
- `/health/ready`: DB open, 필수 serving object, snapshot/source hash, 상품·metric 행수와
  metric ID가 현재 코드의 registry와 맞는지 확인
- `/health/ready`만으로 Git SHA, container digest, 전체 registry 내용 hash, HCX credential의
  실제 호출 성공까지 확인된다고 보지 않음. 아래 release 검증과 live HCX smoke를 별도로 수행
- 지표: request count, status, latency, HCX status, schema failure, answerability
- 수집 제외: 원문 question, retrieved_context, 최종 answer, 개인 식별정보
- 알림: readiness 실패, error-rate, p95, credit, certificate, disk

## FINAL manifest의 독립 검증 절차

호스트에서 다시 만든 DB가 아니라 **배포할 immutable image 안의 DB**를 꺼내 hash합니다.
아래의 `<image-ref>`는 registry가 반환한 digest를 붙인
`registry/name@sha256:...` 형식이어야 합니다.

```bash
docker pull <image-ref>
docker image inspect <image-ref> --format '{{json .RepoDigests}}'

cid=$(docker create <image-ref>)
mkdir -p /tmp/mirae-release-verify
docker cp "$cid:/app/data/serving/mirae_agent.duckdb" \
  /tmp/mirae-release-verify/mirae_agent.duckdb
docker rm "$cid"

git rev-parse HEAD
git status --porcelain

python scripts/generate_release_manifest.py --final \
  --git-sha <40-char-HEAD> \
  --image-digest sha256:<64-hex-digest> \
  --image-ref <image-ref> \
  --serving-database /tmp/mirae-release-verify/mirae_agent.duckdb \
  --passed <full-pytest-pass-count> --failed 0 --skipped 0 \
  --test-report artifacts/test_report_<release-date>.json \
  --output artifacts/release_manifest.final.json
```

`--final`은 Git HEAD 일치, report 상태·pytest 수치·모든 external gate PASS,
DB readiness와 non-placeholder 식별자를 검증합니다. 담당자 두 명이 `RepoDigests`, Git HEAD,
생성된 manifest의 `container_image_digest`, `serving_database_sha256`을 다시 대조합니다.

## 종료

PDF에는 API 활성 기간이 09.07~09.20, 예선평가가 09.07~09.30으로 적혀 있습니다.
정확한 종료일을 설명회에서 확정하기 전에는 09.30까지 동일 release를 유지하는 것이
내부 운영 기준입니다.
