import { FlaskConical, MessageSquarePlus, PanelLeftClose, ShieldAlert } from "lucide-react";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useLayoutEffect, useRef } from "react";

import { GUIDED_SCENARIOS } from "../scenarios";
import type { SessionSummary } from "../types";
import { formatDate } from "../ui";

interface SessionSidebarProps {
  sessions: SessionSummary[];
  selectedId?: string;
  open: boolean;
  modal: boolean;
  busy: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: (mode: "guided" | "free", scenarioId?: string) => void;
}

export function SessionSidebar({
  sessions,
  selectedId,
  open,
  modal,
  busy,
  onClose,
  onSelect,
  onCreate,
}: SessionSidebarProps) {
  const sidebarRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const modalOpen = modal && open;

  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useLayoutEffect(() => {
    const sidebar = sidebarRef.current;
    if (!sidebar) return;
    if (modal && !open) sidebar.setAttribute("inert", "");
    else sidebar.removeAttribute("inert");
  }, [modal, open]);
  useEffect(() => {
    if (!modalOpen) return;
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const focusTimer = window.setTimeout(() => closeRef.current?.focus(), 0);
    const escapeListener = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCloseRef.current();
    };
    window.addEventListener("keydown", escapeListener);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", escapeListener);
      const target = restoreFocusRef.current;
      window.setTimeout(() => {
        if (target?.isConnected) target.focus();
      }, 0);
    };
  }, [modalOpen]);

  const trapModalFocus = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (!modalOpen || event.key !== "Tab") return;
    const focusable = Array.from(
      sidebarRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]):not([tabindex='-1']), [href]:not([tabindex='-1']), [tabindex]:not([tabindex='-1'])",
      ) || [],
    ).filter((element) => !element.hasAttribute("hidden"));
    if (focusable.length === 0) {
      event.preventDefault();
      closeRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <aside
      ref={sidebarRef}
      className={`session-sidebar ${open ? "is-open" : ""}`}
      aria-label={modalOpen ? undefined : "테스트 세션"}
      aria-labelledby={modalOpen ? "session-sidebar-title" : undefined}
      aria-hidden={modal && !open ? true : undefined}
      role={modalOpen ? "dialog" : undefined}
      aria-modal={modalOpen || undefined}
      onKeyDown={trapModalFocus}
    >
      <div className="sidebar-heading">
        <div>
          <span className="section-kicker">테스트 작업공간</span>
          <h2 id="session-sidebar-title">세션</h2>
        </div>
        <button ref={closeRef} className="icon-button mobile-only" type="button" onClick={onClose} aria-label="세션 메뉴 닫기">
          <PanelLeftClose aria-hidden="true" />
        </button>
      </div>

      <button className="primary-button full-width" type="button" disabled={busy} onClick={() => onCreate("free")}>
        <MessageSquarePlus aria-hidden="true" /> 새 자유 테스트
      </button>

      <section className="sidebar-section" aria-labelledby="recent-sessions">
        <h3 id="recent-sessions">최근 세션</h3>
        {sessions.length === 0 ? (
          <p className="empty-note">아직 저장된 세션이 없습니다.</p>
        ) : (
          <ul className="session-list">
            {sessions.map((session) => (
              <li key={session.id}>
                <button
                  type="button"
                  className={selectedId === session.id ? "is-selected" : ""}
                  aria-current={selectedId === session.id ? "page" : undefined}
                  onClick={() => onSelect(session.id)}
                >
                  <strong>{session.title || "제목 없는 테스트"}</strong>
                  <span>{session.mode === "guided" ? "가이드" : "자유"} · {formatDate(session.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="sidebar-section scenario-section" aria-labelledby="guided-scenarios">
        <h3 id="guided-scenarios"><FlaskConical aria-hidden="true" /> 필수 가이드 12개</h3>
        <ul className="scenario-list">
          {GUIDED_SCENARIOS.map((scenario, index) => (
            <li key={scenario.id}>
              <button type="button" disabled={busy} onClick={() => onCreate("guided", scenario.id)}>
                <span className="scenario-index">{String(index + 1).padStart(2, "0")}</span>
                <span>
                  <strong>{scenario.title}</strong>
                  <small>{scenario.category}</small>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <div className="non-advice-note">
        <ShieldAlert aria-hidden="true" />
        <p>공식 스냅샷 데이터 검증용이며 투자 자문이나 실시간 정보가 아닙니다.</p>
      </div>
    </aside>
  );
}
