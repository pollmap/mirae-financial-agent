# Immutable organizer inputs

- `official_task.pdf`: 참가팀 배포 과제 PDF의 바이트 동일 사본
- `official_data.zip`: 참가팀 배포 데이터 ZIP의 바이트 동일 사본
- `team_email_raw.txt`: 사용자가 붙여 넣은 참가팀 운영 메일의 정규화 평문 보존본

두 파일은 수정하지 않습니다. 표시 파일명은 Unicode NFC/NFD에 따라 달라질 수 있으므로
개발 코드에서는 원래 이름으로 검색하지 않고 `../artifacts/source_manifest.json`의
ASCII 경로와 SHA-256을 사용합니다.

검증:

```bash
python scripts/verify_sources.py
```
