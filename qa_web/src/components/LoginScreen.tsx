import { KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { FormEvent, useState } from "react";

interface LoginScreenProps {
  retentionDays: number;
  fixtureMode?: boolean;
  busy: boolean;
  error?: string;
  onRedeem: (code: string) => Promise<void>;
}

export function LoginScreen({ retentionDays, fixtureMode = false, busy, error, onRedeem }: LoginScreenProps) {
  const [code, setCode] = useState("");
  const [consent, setConsent] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (code.trim() && consent && !busy) void onRedeem(code.trim());
  };

  return (
    <main className="login-screen">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-mark"><ShieldCheck aria-hidden="true" /></div>
        <span className="eyebrow">팀 내부 전용</span>
        <h1 id="login-title">금융상품 Agent 인간 검증</h1>
        <p className="login-lead">
          {fixtureMode
            ? "결정적 Fixture 응답의 화면·조건·근거 표시를 확인하는 개발 검증 공간입니다. 실제 HCX 호출이 아닙니다."
            : "초대받은 테스터가 HCX 답변의 조건, 공식 근거와 검색 경로를 함께 확인하는 공간입니다."}
        </p>

        <div className="privacy-summary">
          <LockKeyhole aria-hidden="true" />
          <div>
            <strong>익명 기록 · {retentionDays}일 후 자동 삭제</strong>
            <p>질문, 답변, 근거와 피드백은 암호화해 보관합니다. 실명, 연락처, 계좌번호, 실제 보유자산은 입력하지 마세요.</p>
          </div>
        </div>

        <form onSubmit={submit}>
          <label htmlFor="invite-code">테스터 초대 코드</label>
          <div className="code-field">
            <KeyRound aria-hidden="true" />
            <input
              id="invite-code"
              type="password"
              autoComplete="one-time-code"
              value={code}
              maxLength={256}
              disabled={busy}
              onChange={(event) => setCode(event.target.value)}
              placeholder="발급받은 코드를 입력하세요"
              required
            />
          </div>
          <label className="consent-check">
            <input type="checkbox" checked={consent} disabled={busy} onChange={(event) => setConsent(event.target.checked)} />
            <span>위 수집·보관 정책에 동의하며 개인정보나 실제 금융정보를 입력하지 않겠습니다.</span>
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary-button full-width" type="submit" disabled={!code.trim() || !consent || busy}>
            {busy ? "확인 중" : "검증 공간 입장"}
          </button>
        </form>
      </section>
    </main>
  );
}
