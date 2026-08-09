import { AlertTriangle, LoaderCircle, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  createSession,
  deleteSession,
  exportSession,
  getMe,
  getSession,
  getStatus,
  listSessions,
  logout,
  redeemInvite,
  saveFeedback,
  sendMessage,
} from "./api";
import { ChatPanel, type ClarificationReply } from "./components/ChatPanel";
import { InertBoundary } from "./components/InertBoundary";
import { InspectorPanel } from "./components/InspectorPanel";
import { LoginScreen } from "./components/LoginScreen";
import { ManifestHeader } from "./components/ManifestHeader";
import { SessionSidebar } from "./components/SessionSidebar";
import { GUIDED_SCENARIOS } from "./scenarios";
import { MessageAttemptLedger } from "./messageAttempts";
import type {
  AssistantMessage,
  FeedbackRecord,
  QaSession,
  QaStatus,
  SessionSummary,
  TesterProfile,
} from "./types";
import { messageAssistant, statusEnvironment } from "./ui";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429 && error.retryAfter) return `${error.message} ${error.retryAfter}초 후 다시 시도해 주세요.`;
    if (error.status === 409) return "다른 화면에서 세션이 변경되었습니다. 최신 대화를 다시 불러왔습니다.";
    return error.message;
  }
  return "예상하지 못한 오류가 발생했습니다.";
}

function latestAssistant(session: QaSession | null): AssistantMessage | null {
  if (!session) return null;
  for (let index = session.messages.length - 1; index >= 0; index -= 1) {
    const assistant = messageAssistant(session.messages[index]);
    if (assistant) return assistant;
  }
  return null;
}

function useMediaQuery(query: string): boolean {
  const getMatch = () => typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(getMatch);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);

  return matches;
}

export function App() {
  const [status, setStatus] = useState<QaStatus | null>(null);
  const [profile, setProfile] = useState<TesterProfile | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [session, setSession] = useState<QaSession | null>(null);
  const [inspectorAssistant, setInspectorAssistant] = useState<AssistantMessage | null>(null);
  const [booting, setBooting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const messageAttempts = useRef(new MessageAttemptLedger());
  const sidebarOverlay = useMediaQuery("(max-width: 860px)");
  const inspectorOverlay = useMediaQuery("(max-width: 1180px)");
  const sidebarModalOpen = sidebarOpen && sidebarOverlay;
  const inspectorModalOpen = inspectorOpen && inspectorOverlay;

  const refreshSessions = useCallback(async () => {
    const next = await listSessions();
    setSessions(next);
    return next;
  }, []);

  const selectSession = useCallback(async (id: string) => {
    setBusy(true);
    setError("");
    try {
      const selected = await getSession(id);
      setSession(selected);
      setInspectorAssistant(latestAssistant(selected));
      setSidebarOpen(false);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [statusResult, meResult] = await Promise.allSettled([getStatus(), getMe()]);
      if (cancelled) return;
      if (statusResult.status === "fulfilled") setStatus(statusResult.value);
      else setError(errorMessage(statusResult.reason));

      if (meResult.status === "fulfilled") {
        setProfile(meResult.value);
        try {
          const recent = await refreshSessions();
          if (recent[0]) await selectSession(recent[0].id);
        } catch (caught) {
          if (!cancelled) setError(errorMessage(caught));
        }
      } else if (!(meResult.reason instanceof ApiError && [401, 403].includes(meResult.reason.status))) {
        setError(errorMessage(meResult.reason));
      }
      if (!cancelled) setBooting(false);
    })();
    return () => { cancelled = true; };
  }, [refreshSessions, selectSession]);

  useEffect(() => {
    const refreshStatus = async () => {
      try {
        setStatus(await getStatus());
      } catch {
        // Keep the last verified status; message submission remains server-gated.
      }
    };
    const timer = window.setInterval(() => void refreshStatus(), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    messageAttempts.current.reset();
  }, [session?.id]);

  const scenario = useMemo(
    () => GUIDED_SCENARIOS.find((item) => item.id === session?.scenario_id),
    [session?.scenario_id],
  );
  const canChat = Boolean(
    status?.pilot_chat_enabled &&
    (status.status?.toLowerCase() === "ready" || status.ready === true) &&
    status.engine?.status?.toLowerCase() !== "unavailable",
  );
  const fixtureMode = /fixture|test/i.test(statusEnvironment(status).model_id || "");

  const handleRedeem = async (code: string) => {
    setBusy(true);
    setError("");
    try {
      const tester = await redeemInvite(code);
      setProfile(tester);
      await refreshSessions();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setProfile(null);
      setSession(null);
      setSessions([]);
      setInspectorAssistant(null);
      setBusy(false);
    }
  };

  const handleCreate = async (mode: "guided" | "free", scenarioId?: string) => {
    setBusy(true);
    setError("");
    try {
      let created = await createSession(mode, scenarioId);
      if (!created.messages) created = await getSession(created.id);
      setSession(created);
      setInspectorAssistant(null);
      await refreshSessions();
      setSidebarOpen(false);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleSend = async (text: string, clarification?: ClarificationReply): Promise<boolean> => {
    if (!session) return false;
    const clientId = messageAttempts.current.acquire({
      sessionId: session.id,
      text,
      replyToMessageId: clarification?.assistantMessageId,
      clarificationId: clarification?.clarificationId,
      clarificationOptionValue: clarification?.optionValue,
    });
    setBusy(true);
    setError("");
    try {
      await sendMessage(session.id, {
        text,
        client_message_id: clientId,
        expected_session_version: session.session_version,
        ...(clarification
          ? {
              reply_to_message_id: clarification.assistantMessageId,
              ...(clarification.optionValue
                ? { clarification_option_value: clarification.optionValue }
                : {}),
            }
          : {}),
      });
      const updated = await getSession(session.id);
      setSession(updated);
      setInspectorAssistant(latestAssistant(updated));
      await refreshSessions();
      messageAttempts.current.complete(clientId);
      return true;
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) {
        try {
          const updated = await getSession(session.id);
          setSession(updated);
          setInspectorAssistant(latestAssistant(updated));
        } catch {
          // The original controlled error remains visible.
        }
      }
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleFeedback = async (assistantId: string, feedback: FeedbackRecord) => {
    setError("");
    try {
      const saved = await saveFeedback(assistantId, feedback);
      setSession((current) => current ? {
        ...current,
        messages: current.messages.map((message) =>
          message.assistant?.id === assistantId
            ? { ...message, assistant: { ...message.assistant, feedback: saved } }
            : message,
        ),
      } : current);
    } catch (caught) {
      setError(errorMessage(caught));
      throw caught;
    }
  };

  const handleDelete = async () => {
    if (!session || !window.confirm("이 세션과 연결된 질문, 답변, 근거, 피드백을 즉시 삭제할까요?")) return;
    setBusy(true);
    try {
      await deleteSession(session.id);
      setSession(null);
      setInspectorAssistant(null);
      await refreshSessions();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleExport = async (format: "json" | "markdown") => {
    if (!session) return;
    try {
      await exportSession(session.id, format);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  };

  if (booting) {
    return <div className="app-loading" role="status"><LoaderCircle aria-hidden="true" /> 검증 환경을 확인하고 있습니다.</div>;
  }

  if (!profile) {
    return (
      <>
        <ManifestHeader status={status} profile={null} onMenu={() => undefined} onLogout={() => undefined} />
        <LoginScreen
          retentionDays={status?.retention_days || 14}
          fixtureMode={fixtureMode}
          busy={busy}
          error={error}
          onRedeem={handleRedeem}
        />
      </>
    );
  }

  return (
    <div className="app-shell">
      <InertBoundary active={inspectorModalOpen || sidebarModalOpen} className="manifest-background">
        <ManifestHeader status={status} profile={profile} onMenu={() => setSidebarOpen(true)} onLogout={() => void handleLogout()} />
        {error && (
          <div className="global-alert" role="alert">
            <AlertTriangle aria-hidden="true" /><span>{error}</span>
            <button className="icon-button" type="button" onClick={() => setError("")} aria-label="알림 닫기"><X aria-hidden="true" /></button>
          </div>
        )}
      </InertBoundary>
      <div className="workspace-grid">
        {sidebarModalOpen && <div className="sidebar-scrim" onClick={() => setSidebarOpen(false)} aria-hidden="true" />}
        <InertBoundary active={inspectorModalOpen} className="sidebar-background">
          <SessionSidebar
            sessions={sessions}
            selectedId={session?.id}
            open={sidebarOpen}
            modal={sidebarOverlay}
            busy={busy}
            onClose={() => setSidebarOpen(false)}
            onSelect={(id) => void selectSession(id)}
            onCreate={(mode, scenarioId) => void handleCreate(mode, scenarioId)}
          />
        </InertBoundary>
        <InertBoundary active={inspectorModalOpen || sidebarModalOpen} className="chat-background">
          <ChatPanel
            session={session}
            scenario={scenario}
            busy={busy}
            canChat={canChat}
            onSend={handleSend}
            onInspect={(assistant) => { setSidebarOpen(false); setInspectorAssistant(assistant); setInspectorOpen(true); }}
            onFeedback={handleFeedback}
            onExport={(format) => void handleExport(format)}
            onDelete={() => void handleDelete()}
            onOpenInspector={() => { setSidebarOpen(false); setInspectorOpen(true); }}
          />
        </InertBoundary>
        <InertBoundary active={sidebarModalOpen} className="inspector-background">
          <InspectorPanel
            assistant={inspectorAssistant}
            open={inspectorOpen}
            modal={inspectorOverlay}
            onClose={() => setInspectorOpen(false)}
          />
        </InertBoundary>
      </div>
    </div>
  );
}
