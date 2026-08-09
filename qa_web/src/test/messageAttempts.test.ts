import { describe, expect, it, vi } from "vitest";

import { MessageAttemptLedger } from "../messageAttempts";

describe("MessageAttemptLedger", () => {
  it("reuses an id only for the same failed session, body, and clarification payload", () => {
    const generate = vi.fn()
      .mockReturnValueOnce("message-1")
      .mockReturnValueOnce("message-2")
      .mockReturnValueOnce("message-3")
      .mockReturnValueOnce("message-4");
    const ledger = new MessageAttemptLedger(generate);
    const original = {
      sessionId: "session-a",
      text: "국내 ETF",
      replyToMessageId: "assistant-a",
      clarificationId: "clarification-a",
      clarificationOptionValue: "domestic_etp",
    };

    expect(ledger.acquire(original)).toBe("message-1");
    expect(ledger.acquire(original)).toBe("message-1");
    expect(generate).toHaveBeenCalledTimes(1);

    expect(ledger.acquire({ ...original, text: "해외 ETF" })).toBe("message-2");
    expect(ledger.acquire({ ...original, sessionId: "session-b" })).toBe("message-3");
    expect(ledger.acquire({ ...original, clarificationOptionValue: "public_fund" })).toBe("message-4");
  });

  it("discards the retained id after success or an explicit session reset", () => {
    let counter = 0;
    const ledger = new MessageAttemptLedger(() => `message-${++counter}`);
    const attempt = { sessionId: "session-a", text: "질문" };

    const first = ledger.acquire(attempt);
    ledger.complete(first);
    expect(ledger.acquire(attempt)).toBe("message-2");
    ledger.reset();
    expect(ledger.acquire(attempt)).toBe("message-3");
  });
});
