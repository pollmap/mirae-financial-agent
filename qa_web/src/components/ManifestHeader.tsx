import {
  BrainCircuit,
  Database,
  GitCommitHorizontal,
  LogOut,
  Menu,
  ShieldCheck,
} from "lucide-react";

import type { QaStatus, TesterProfile } from "../types";
import { shortHash, statusEnvironment } from "../ui";

interface ManifestHeaderProps {
  status: QaStatus | null;
  profile: TesterProfile | null;
  onMenu: () => void;
  onLogout: () => void;
}

export function ManifestHeader({ status, profile, onMenu, onLogout }: ManifestHeaderProps) {
  const environment = statusEnvironment(status);
  const operational = status?.status?.toLowerCase() === "ready" || status?.ready === true;
  const fixtureMode = /fixture|test/i.test(environment.model_id || "");

  return (
    <header className="manifest-header">
      <div className="brand-row">
        {profile && (
          <button className="icon-button mobile-only" type="button" onClick={onMenu} aria-label="세션 메뉴 열기">
            <Menu aria-hidden="true" />
          </button>
        )}
        <div className="brand-lockup">
          <span className="eyebrow"><ShieldCheck aria-hidden="true" /> 팀 내부 인간 검증 환경</span>
          <strong>금융상품 Agent QA</strong>
          <small>공식 데이터 검증용 · 투자자문이 아닙니다</small>
          <small className="mobile-disclosure">팀 내부 인간 검증 환경 · 공식 데이터 · 비투자자문</small>
        </div>
        <span className={`runtime-state ${operational ? "is-ready" : "is-pending"} ${fixtureMode ? "is-fixture" : ""}`}>
          <span aria-hidden="true" />
          {fixtureMode ? "Fixture 검증" : operational ? "검증 준비" : "외부 검증 대기"}
        </span>
      </div>

      <dl className="manifest-metrics" aria-label="실행 환경 정보">
        <div title={environment.engine_git_sha || ""}>
          <dt><GitCommitHorizontal aria-hidden="true" /> 엔진</dt>
          <dd>{shortHash(environment.engine_git_sha)}</dd>
        </div>
        <div title={environment.data_hash || ""}>
          <dt><Database aria-hidden="true" /> 데이터</dt>
          <dd>{environment.data_snapshot_date || "기준일 확인 대기"} · {shortHash(environment.data_hash)}</dd>
        </div>
        <div title={environment.model_id || ""}>
          <dt><BrainCircuit aria-hidden="true" /> HCX</dt>
          <dd>{environment.model_id || "모델 확인 대기"}</dd>
        </div>
        <div title={`${environment.planner_stage || "-"} · ${environment.vector_status || "-"}`}>
          <dt>플래너 · Vector</dt>
          <dd>{environment.planner_stage || "-"} · {environment.vector_status || "-"}</dd>
        </div>
      </dl>

      {profile && (
        <div className="profile-actions">
          <span>{profile.alias}</span>
          <button className="icon-button" type="button" onClick={onLogout} aria-label="로그아웃">
            <LogOut aria-hidden="true" />
          </button>
        </div>
      )}
    </header>
  );
}
