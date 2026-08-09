import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "../components/ChatPanel";
import type { ChatMessage, QaSession } from "../types";

function clarification(id: string, label: string): ChatMessage {
  return {
    id,
    role: "assistant",
    content: `${label} 조건을 선택해 주세요.`,
    created_at: "2026-08-09T10:00:00Z",
    assistant: {
      id,
      status: "NEEDS_CLARIFICATION",
      content: `${label} 조건을 선택해 주세요.`,
      clarification: {
        id: `${id}0000000000000000000000000000`.slice(0, 32),
        question: `${label} 조건은 무엇인가요?`,
        options: [{ value: label, label }],
        expires_at: "2030-01-01T00:00:00Z",
      },
    },
  };
}

describe("ChatPanel clarification lifecycle", () => {
  it("renders a chronological log and disables stale clarification options per message", async () => {
    const messages: ChatMessage[] = [
      clarification("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "국내"),
      { id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", role: "user", content: "국내" },
      clarification("cccccccccccccccccccccccccccccccc", "1년"),
    ];
    const session: QaSession = {
      id: "dddddddddddddddddddddddddddddddd",
      session_version: 1,
      title: "추가 질문 검증",
      mode: "free",
      messages,
    };
    const onSend = vi.fn().mockResolvedValue(true);

    render(
      <ChatPanel
        session={session}
        busy={false}
        canChat
        onSend={onSend}
        onInspect={vi.fn()}
        onFeedback={vi.fn().mockResolvedValue(undefined)}
        onExport={vi.fn()}
        onDelete={vi.fn()}
        onOpenInspector={vi.fn()}
      />,
    );

    expect(screen.getByRole("log", { name: "대화 기록" })).toBeInTheDocument();
    expect(screen.getByLabelText("금융상품 질문")).toHaveAttribute("maxlength", "2000");
    expect(screen.getByRole("button", { name: "국내" })).toBeDisabled();
    const current = screen.getByRole("button", { name: "1년" });
    expect(current).toBeEnabled();

    await userEvent.click(current);
    expect(onSend).toHaveBeenCalledWith("1년", expect.objectContaining({
      assistantMessageId: "cccccccccccccccccccccccccccccccc",
      optionValue: "1년",
    }));
    expect(current).toBeDisabled();
  });

  it("disables the current option when expires_at passes without another interaction", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2029-01-01T00:00:00Z"));
    const message = clarification("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "국내");
    message.assistant!.clarification!.expires_at = "2029-01-01T00:00:01Z";
    const session: QaSession = {
      id: "ffffffffffffffffffffffffffffffff",
      session_version: 0,
      title: "만료 검증",
      mode: "free",
      messages: [message],
    };

    render(
      <ChatPanel
        session={session}
        busy={false}
        canChat
        onSend={vi.fn().mockResolvedValue(true)}
        onInspect={vi.fn()}
        onFeedback={vi.fn().mockResolvedValue(undefined)}
        onExport={vi.fn()}
        onDelete={vi.fn()}
        onOpenInspector={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "국내" })).toBeEnabled();

    act(() => vi.advanceTimersByTime(1_100));
    expect(screen.getByRole("button", { name: "국내" })).toBeDisabled();
    vi.useRealTimers();
  });
});
