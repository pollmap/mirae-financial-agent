# HCX 인간검증 챗봇 디자인 QA

검수일: 2026-08-09

대상: 팀 내부 fixture preview (`HCX-FIXTURE-NO-LIVE`)

범위: 시각 위계, 채팅 흐름, 조건·근거·검색경로 검사, 반응형, 키보드·WCAG

## 캡처

- 데스크톱: `C:/Users/lch68/.codex/visualizations/2026/08/08/019fe181-173e-7500-9b7a-6d35e1f28d28/mirae-human-chat-final/qa-desktop.png`
- 모바일 320px: `C:/Users/lch68/.codex/visualizations/2026/08/08/019fe181-173e-7500-9b7a-6d35e1f28d28/mirae-human-chat-final/qa-mobile-320.png`
- 200% 확대와 동등한 1280→640 CSS px reflow:
  `C:/Users/lch68/.codex/visualizations/2026/08/08/019fe181-173e-7500-9b7a-6d35e1f28d28/mirae-human-chat-final/qa-reflow-200-equivalent.png`

## 판정

- 기존 데모의 짙은 남색·오렌지 강조색, 밀도 높은 금융 데이터 위계를 유지했다.
- 데스크톱은 세션·채팅·검사의 세 영역, 860px 이하는 세션 drawer와 검사 bottom sheet로
  전환된다.
- `팀 내부 인간 검증 환경`, fixture/live, 데이터 기준일·해시, 엔진 SHA, HCX 모델,
  Vector 상태, 비투자자문 고지가 모든 폭에서 남는다.
- 320px에서 가로 overflow가 없고 질문 전송, Markdown/JSON export, 삭제, 검사 열기가
  모두 접근 가능하다.
- session drawer와 inspector는 `role=dialog`, `aria-modal`, 배경 `inert`, focus trap,
  Escape 닫기와 trigger focus 복원을 사용한다.
- 대화 영역은 `role=log`, 모든 핵심 터치 대상은 44px 이상이며 키보드 focus 표시가 있다.
- 실제 브라우저 axe WCAG A/AA/2.1/2.2 AA 결과는 violation 0이다. textarea가 다른
  요소에 일부 가려져 색을 계산할 수 없다는 incomplete 1건은 토큰 대비 자동 테스트로
  4.5:1 이상을 별도 확인했다. 브라우저 오류 수집 결과는 0이다.
- 320px 실제 viewport와 1280px 환경의 절반 CSS viewport reflow를 통과했다. 인앱
  브라우저는 native zoom capability를 제공하지 않으므로 Windows 브라우저의 실제 200%
  확대와 NVDA·고대비는 사람 파일럿 체크리스트에 남긴다.
- 캡처의 fixture 표시는 실제 HCX 정확도 증거가 아니며 live gate가 통과할 때까지
  `PENDING_EXTERNAL`이다.

final result: passed
