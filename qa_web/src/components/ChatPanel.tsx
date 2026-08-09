import {
  Bot,
  Download,
  FileJson,
  LoaderCircle,
  PanelRightOpen,
  RotateCcw,
  Send,
  Trash2,
  User,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import type {
  AssistantMessage,
  ChatMessage,
  FeedbackRecord,
  GuidedScenario,
  QaSession,
} from "../types";
import { formatDate, isClarificationOpen, messageAssistant, STATUS_LABEL } from "../ui";
import { FeedbackControl } from "./FeedbackControl";

export interface ClarificationReply {
  assistantMessageId: string;
  clarificationId: string;
  optionValue?: string;
}

interface ChatPanelProps {
  session: QaSession | null;
  scenario?: GuidedScenario;
  busy: boolean;
  canChat: boolean;
  onSend: (text: string, clarification?: ClarificationReply) => Promise<boolean>;
  onInspect: (assistant: AssistantMessage) => void;
  onFeedback: (assistantId: string, feedback: FeedbackRecord) => Promise<void>;
  onExport: (format: "json" | "markdown") => void;
  onDelete: () => void;
  onOpenInspector: () => void;
}

function previousUserText(messages: ChatMessage[], messageId: string): string {
  const index = messages.findIndex((message) => message.id === messageId);
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    if (messages[cursor].role === "user") return messages[cursor].content;
  }
  return "";
}

export function ChatPanel({
  session,
  scenario,
  busy,
  canChat,
  onSend,
  onInspect,
  onFeedback,
  onExport,
  onDelete,
  onOpenInspector,
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const [spentClarifications, setSpentClarifications] = useState<Set<string>>(new Set());
  const [clock, setClock] = useState(() => Date.now());
  const endRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const latestAssistantRef = useRef<HTMLElement>(null);

  const messages = session?.messages || [];
  const activeClarification = useMemo(() => {
    const message = messages[messages.length - 1];
    return message && isClarificationOpen(message, messages, spentClarifications, clock) ? message : null;
  }, [messages, spentClarifications, clock]);

  useEffect(() => {
    setDraft(scenario?.starter || "");
    setSpentClarifications(new Set());
  }, [session?.id, scenario?.starter]);

  useEffect(() => {
    const now = Date.now();
    setClock(now);
    const futureExpiries = messages
      .map((message) => message.assistant?.clarification?.expires_at)
      .filter((value): value is string => Boolean(value))
      .map((value) => new Date(value).valueOf())
      .filter((value) => !Number.isNaN(value) && value > now);
    if (futureExpiries.length === 0) return;
    const delay = Math.min(Math.max(Math.min(...futureExpiries) - now + 25, 25), 2_147_000_000);
    const timer = window.setTimeout(() => setClock(Date.now()), delay);
    return () => window.clearTimeout(timer);
  }, [messages]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
    if (messages.at(-1)?.role === "assistant") latestAssistantRef.current?.focus({ preventScroll: true });
  }, [messages.length]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || !session || busy || !canChat) return;
    const reply = activeClarification?.assistant
      ? {
          assistantMessageId: activeClarification.assistant.id,
          clarificationId: activeClarification.assistant.clarification!.id,
        }
      : undefined;
    if (reply) setSpentClarifications((current) => new Set(current).add(reply.clarificationId));
    if (await onSend(text, reply)) setDraft("");
  };

  const selectClarification = async (
    assistant: AssistantMessage,
    option: { label: string; value: string },
  ) => {
    const clarification = assistant.clarification;
    if (!clarification || spentClarifications.has(clarification.id) || busy) return;
    setSpentClarifications((current) => new Set(current).add(clarification.id));
    const ok = await onSend(option.label, {
      assistantMessageId: assistant.id,
      clarificationId: clarification.id,
      optionValue: option.value,
    });
    if (!ok) {
      setSpentClarifications((current) => {
        const next = new Set(current);
        next.delete(clarification.id);
        return next;
      });
    }
  };

  const handleComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  if (!session) {
    return (
      <main className="chat-panel empty-chat">
        <div className="empty-chat-card">
          <Bot aria-hidden="true" />
          <span className="section-kicker">HCX 전용 인간 검증</span>
          <h1>검증할 대화를 선택하세요</h1>
          <p>왼쪽에서 자유 테스트를 시작하거나 필수 가이드 시나리오를 선택할 수 있습니다.</p>
        </div>
      </main>
    );
  }

  return (
    <main className="chat-panel">
      <div className="chat-toolbar">
        <div>
          <span className="section-kicker">{session.mode === "guided" ? "가이드 테스트" : "자유 테스트"}</span>
          <h1>{session.title || scenario?.title || "금융상품 질의 검증"}</h1>
          {scenario && <p>{scenario.description}</p>}
        </div>
        <div className="toolbar-actions">
          <button className="icon-button inspector-trigger" type="button" onClick={onOpenInspector} aria-label="검사 패널 열기">
            <PanelRightOpen aria-hidden="true" />
          </button>
          <div className="export-menu">
            <button className="icon-button" type="button" onClick={() => onExport("markdown")} aria-label="Markdown으로 내보내기">
              <Download aria-hidden="true" />
            </button>
            <button className="icon-button" type="button" onClick={() => onExport("json")} aria-label="JSON으로 내보내기">
              <FileJson aria-hidden="true" />
            </button>
          </div>
          <button className="icon-button danger-button" type="button" onClick={onDelete} aria-label="세션 삭제">
            <Trash2 aria-hidden="true" />
          </button>
        </div>
      </div>

      <section className="message-log" role="log" aria-live="polite" aria-relevant="additions text" aria-label="대화 기록">
        {messages.length === 0 && (
          <div className="conversation-start">
            <Bot aria-hidden="true" />
            <h2>질문을 보내기 전에 확인하세요</h2>
            <p>실명, 연락처, 계좌번호나 실제 보유자산을 입력하지 마세요. 질문과 응답은 익명으로 최대 14일 보관됩니다.</p>
            {scenario && (
              <div className="scenario-brief">
                <strong>{scenario.title}</strong>
                <span>{scenario.description}</span>
              </div>
            )}
          </div>
        )}

        {messages.map((message, index) => {
          const assistant = messageAssistant(message);
          const clarificationOpen = isClarificationOpen(message, messages, spentClarifications, clock);
          const isLatestAssistant = message.role === "assistant" && !messages.slice(index + 1).some((item) => item.role === "assistant");
          return (
            <article
              key={message.id}
              className={`message-row is-${message.role}`}
              ref={isLatestAssistant ? latestAssistantRef : undefined}
              tabIndex={isLatestAssistant ? -1 : undefined}
            >
              <div className="message-avatar" aria-hidden="true">
                {message.role === "assistant" ? <Bot /> : <User />}
              </div>
              <div className="message-content">
                <div className="message-meta">
                  <strong>{message.role === "assistant" ? "금융상품 Agent" : "테스터"}</strong>
                  <time dateTime={message.created_at}>{formatDate(message.created_at)}</time>
                  {assistant && (
                    <span className={`answer-status status-${assistant.status.toLowerCase()}`}>
                      {STATUS_LABEL[assistant.status] || assistant.status}
                    </span>
                  )}
                </div>
                <div className="message-bubble">{assistant?.content || message.content}</div>

                {assistant?.clarification && (
                  <div className="clarification-block" role="group" aria-label="추가 조건 선택">
                    <strong>{assistant.clarification.question}</strong>
                    <div className="clarification-options">
                      {assistant.clarification.options.map((option) => (
                        <button
                          type="button"
                          key={`${assistant.clarification!.id}-${option.value}`}
                          disabled={!clarificationOpen || busy}
                          onClick={() => void selectClarification(assistant, option)}
                        >
                          <span>{option.label}</span>
                          {option.description && <small>{option.description}</small>}
                        </button>
                      ))}
                    </div>
                    {!clarificationOpen && <p className="clarification-closed">이 추가 질문은 응답 완료 또는 만료되어 다시 사용할 수 없습니다.</p>}
                  </div>
                )}

                {assistant && (
                  <div className="message-actions">
                    <button className="text-button" type="button" onClick={() => onInspect(assistant)}>
                      근거와 실행 경로 검사
                    </button>
                    {assistant.status === "RETRYABLE_ERROR" && (
                      <button
                        className="text-button"
                        type="button"
                        onClick={() => { setDraft(previousUserText(messages, message.id)); composerRef.current?.focus(); }}
                      >
                        <RotateCcw aria-hidden="true" /> 질문 다시 준비
                      </button>
                    )}
                  </div>
                )}

                {assistant && assistant.status !== "NEEDS_CLARIFICATION" && (
                  <FeedbackControl
                    initial={assistant.feedback}
                    disabled={busy}
                    onSave={(feedback) => onFeedback(assistant.id, feedback)}
                  />
                )}
              </div>
            </article>
          );
        })}

        {busy && (
          <div className="processing-indicator" role="status">
            <LoaderCircle aria-hidden="true" /> 공식 데이터와 실행 근거를 확인하고 있습니다.
          </div>
        )}
        <div ref={endRef} />
      </section>

      <form className="composer" onSubmit={submit}>
        {activeClarification && <p className="composer-context">현재 추가 조건에 답하는 중입니다.</p>}
        {!canChat && <p className="composer-warning">HCX live gate 또는 파일럿 개방이 완료되지 않아 새 질문이 잠겨 있습니다.</p>}
        <div className="composer-row">
          <label className="visually-hidden" htmlFor="qa-message">금융상품 질문</label>
          <textarea
            ref={composerRef}
            id="qa-message"
            value={draft}
            rows={2}
            maxLength={2000}
            disabled={busy || !canChat}
            placeholder={activeClarification ? "필요한 조건을 입력하세요" : "금융상품에 관해 검증할 질문을 입력하세요"}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleComposerKey}
          />
          <button className="send-button" type="submit" disabled={!draft.trim() || busy || !canChat} aria-label="질문 보내기">
            {busy ? <LoaderCircle aria-hidden="true" /> : <Send aria-hidden="true" />}
          </button>
        </div>
        <div className="composer-footnote">
          <span>Enter 전송 · Shift+Enter 줄바꿈</span>
          <span>{draft.length.toLocaleString()} / 2,000</span>
        </div>
      </form>
    </main>
  );
}
