import type { ChatMessage, EnvironmentView, QaStatus } from "./types";

export const STATUS_LABEL: Record<string, string> = {
  FULL: "완전 답변",
  NEEDS_CLARIFICATION: "추가 조건 필요",
  SAFE_LIMITED: "안전 제한",
  SAFETY_LIMITED: "안전 제한",
  PARTIAL_WITH_COVERAGE: "일부 정보 제한",
  UNAVAILABLE: "확인 불가",
  NO_RESULT: "조회 결과 없음",
  INCOMPARABLE: "비교 불가",
  DATA_QUALITY_BLOCKED: "데이터 확인 필요",
  RETRYABLE_ERROR: "재시도 가능",
};

export function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function shortHash(value?: string): string {
  if (!value) return "확인 대기";
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}

export function statusEnvironment(status?: QaStatus | null): EnvironmentView {
  if (!status) return {};
  return {
    engine_git_sha: status.environment?.engine_git_sha || status.engine_git_sha,
    image_digest: status.environment?.image_digest,
    data_hash: status.environment?.data_hash || status.data_hash,
    model_id: status.environment?.model_id || status.model_id,
    planner_stage: status.environment?.planner_stage || status.planner_stage,
    vector_status: status.environment?.vector_status || status.vector_status,
    data_snapshot_date:
      status.environment?.data_snapshot_date || status.data_snapshot_date,
  };
}

export function isClarificationOpen(
  message: ChatMessage,
  messages: ChatMessage[],
  spentIds: ReadonlySet<string>,
  now = Date.now(),
): boolean {
  const clarification = message.assistant?.clarification;
  if (
    message.role !== "assistant" ||
    message.assistant?.status !== "NEEDS_CLARIFICATION" ||
    !clarification ||
    clarification.consumed ||
    spentIds.has(clarification.id)
  ) {
    return false;
  }
  if (clarification.expires_at) {
    const expiry = new Date(clarification.expires_at).valueOf();
    if (!Number.isNaN(expiry) && expiry <= now) return false;
  }
  const index = messages.findIndex((candidate) => candidate.id === message.id);
  return index >= 0 && index === messages.length - 1;
}

export function messageAssistant(message: ChatMessage) {
  if (message.assistant) return message.assistant;
  if (message.role !== "assistant") return undefined;
  return {
    id: message.id,
    status: "UNAVAILABLE" as const,
    content: message.content,
    created_at: message.created_at,
  };
}
