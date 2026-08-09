# HCX Human QA Chatbot UI

팀 내부 인간 검증용 React + TypeScript + Vite 화면입니다. 대회용 `GET /answer`를 브라우저에서 직접 호출하지 않고, 동일 출처의 `/qa/api/v1` QA Gateway만 사용합니다.

## Local development

```powershell
npm ci
npm run dev
```

개발 서버는 `127.0.0.1:5174`에서 열리고 `/qa/api`를 `127.0.0.1:8090`으로 프록시합니다.

## Verification

```powershell
npm test
npm run build
```

프로덕션 산출물은 `dist/`입니다. 실제 인간 파일럿에서는 fake engine을 표시하거나 사용할 수 없으며, QA Gateway의 live gate와 `pilot_chat_enabled`가 준비되지 않으면 작성기가 잠깁니다.
