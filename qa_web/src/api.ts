import type {
  FeedbackRecord,
  QaSession,
  QaStatus,
  SessionSummary,
  TesterProfile,
  TurnResponse,
} from "./types";

const API_ROOT = "/qa/api/v1";
let csrfToken = "";

function cookieValue(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  const match = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : "";
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly retryAfter?: string | null;

  constructor(message: string, status: number, code?: string, retryAfter?: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

function rememberCsrf(response: Response, payload: unknown): void {
  const header = response.headers.get("x-csrf-token");
  const bodyToken =
    payload && typeof payload === "object" && "csrf_token" in payload
      ? String((payload as { csrf_token?: unknown }).csrf_token || "")
      : "";
  if (header || bodyToken) csrfToken = header || bodyToken;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  const csrf = csrfToken || cookieValue("qa_csrf");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrf) {
    headers.set("X-CSRF-Token", csrf);
  }

  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...init,
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError("QA 서비스에 연결할 수 없습니다.", 0, "NETWORK_ERROR");
  }

  const raw = await response.text();
  let payload: unknown = {};
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      throw new ApiError("서버 응답 형식을 확인할 수 없습니다.", response.status, "INVALID_JSON");
    }
  }
  rememberCsrf(response, payload);

  if (!response.ok) {
    const detail =
      payload && typeof payload === "object"
        ? (payload as { detail?: unknown; message?: unknown; code?: unknown; error?: unknown })
        : {};
    const nested = detail.detail && typeof detail.detail === "object"
      ? detail.detail as { message?: unknown; code?: unknown }
      : undefined;
    const responseCode = typeof nested?.code === "string"
      ? nested.code
      : typeof detail.code === "string"
        ? detail.code
        : typeof detail.error === "string"
          ? detail.error
          : undefined;
    const knownMessage: Record<string, string> = {
      INVALID_INVITE: "초대 코드가 유효하지 않거나 이미 사용되었습니다.",
      FORBIDDEN: "인증 또는 요청 출처를 확인해 주세요.",
      NOT_FOUND: "요청한 세션을 찾을 수 없거나 보관 기간이 지났습니다.",
      CONFLICT: "다른 화면에서 세션이 변경되었습니다.",
      RATE_LIMITED: "호출 한도에 도달했습니다.",
      PILOT_DISABLED: "팀 파일럿이 현재 중지되어 있습니다.",
      RELEASE_GATE_PENDING: "HCX live gate가 완료되지 않았습니다.",
      INVALID_REQUEST: "입력 형식을 확인해 주세요.",
      INTERNAL_ERROR: "서버에서 요청을 완료하지 못했습니다.",
    };
    const message =
      typeof detail.detail === "string"
        ? detail.detail
        : typeof nested?.message === "string"
          ? nested.message
        : typeof detail.message === "string"
          ? detail.message
          : responseCode && knownMessage[responseCode]
            ? knownMessage[responseCode]
          : `요청을 처리하지 못했습니다 (${response.status}).`;
    throw new ApiError(
      message,
      response.status,
      responseCode,
      response.headers.get("retry-after"),
    );
  }
  return payload as T;
}

function collection<T>(payload: unknown, keys: string[]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (!payload || typeof payload !== "object") return [];
  for (const key of keys) {
    const value = (payload as Record<string, unknown>)[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

export async function getStatus(): Promise<QaStatus> {
  return request<QaStatus>("/status");
}

export async function getMe(): Promise<TesterProfile> {
  const payload = await request<TesterProfile | { tester: TesterProfile }>("/me");
  return "tester" in payload ? payload.tester : payload;
}

export async function redeemInvite(inviteCode: string): Promise<TesterProfile> {
  const payload = await request<TesterProfile | { tester: TesterProfile; csrf_token?: string }>(
    "/invites/redeem",
    {
      method: "POST",
      body: JSON.stringify({ code: inviteCode, consent: true, consent_version: "v1" }),
    },
  );
  return "tester" in payload ? payload.tester : payload;
}

export async function logout(): Promise<void> {
  await request<Record<string, unknown>>("/logout", { method: "POST" });
  csrfToken = "";
}

export async function listSessions(): Promise<SessionSummary[]> {
  const payload = await request<SessionSummary[] | { sessions?: SessionSummary[]; items?: SessionSummary[] }>(
    "/sessions",
  );
  return collection<SessionSummary>(payload, ["sessions", "items"]).map((session) => ({
    ...session,
    session_version: session.session_version ?? session.version ?? 0,
  }));
}

function normalizedSession(payload: QaSession | { session: QaSession; messages?: QaSession["messages"] }): QaSession {
  const base = "session" in payload ? payload.session : payload;
  const outerMessages = "session" in payload ? payload.messages : undefined;
  return {
    ...base,
    session_version: base.session_version ?? base.version ?? 0,
    messages: outerMessages || base.messages || [],
  };
}

export async function createSession(
  mode: "guided" | "free",
  scenarioId?: string,
): Promise<QaSession> {
  const payload = await request<QaSession | { session: QaSession }>("/sessions", {
    method: "POST",
    body: JSON.stringify({ mode, ...(scenarioId ? { scenario_id: scenarioId } : {}) }),
  });
  return normalizedSession(payload);
}

export async function getSession(sessionId: string): Promise<QaSession> {
  const payload = await request<QaSession | { session: QaSession }>(`/sessions/${sessionId}`);
  return normalizedSession(payload);
}

export interface SendMessageInput {
  text: string;
  client_message_id: string;
  expected_session_version: number;
  reply_to_message_id?: string;
  clarification_option_value?: string;
}

export async function sendMessage(sessionId: string, input: SendMessageInput): Promise<TurnResponse> {
  return request<TurnResponse>(`/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function saveFeedback(
  assistantMessageId: string,
  feedback: FeedbackRecord,
): Promise<FeedbackRecord> {
  const payload = await request<FeedbackRecord | { feedback: FeedbackRecord }>(
    `/messages/${assistantMessageId}/feedback`,
    { method: "PUT", body: JSON.stringify(feedback) },
  );
  const stored = "feedback" in payload ? payload.feedback : payload;
  return { ...feedback, ...stored, note: feedback.note };
}

export async function deleteSession(sessionId: string): Promise<void> {
  await request<Record<string, unknown>>(`/sessions/${sessionId}`, { method: "DELETE" });
}

export async function exportSession(sessionId: string, format: "json" | "markdown"): Promise<void> {
  const response = await fetch(`${API_ROOT}/sessions/${sessionId}/export?format=${format}`, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: format === "json" ? "application/json" : "text/markdown" },
  });
  if (!response.ok) throw new ApiError("세션을 내보내지 못했습니다.", response.status);
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `qa-session-${sessionId}.${format === "json" ? "json" : "md"}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}
