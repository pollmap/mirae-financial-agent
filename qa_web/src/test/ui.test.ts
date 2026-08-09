import { describe, expect, it } from "vitest";

import { GUIDED_SCENARIOS } from "../scenarios";
import type { ChatMessage } from "../types";
import { isClarificationOpen } from "../ui";

function clarificationMessage(id: string, expiresAt?: string): ChatMessage {
  return {
    id,
    role: "assistant",
    content: "조건이 필요합니다.",
    assistant: {
      id,
      status: "NEEDS_CLARIFICATION",
      content: "조건이 필요합니다.",
      clarification: {
        id: `${id}-clarification`,
        question: "어느 범위인가요?",
        options: [{ value: "domestic", label: "국내" }],
        expires_at: expiresAt,
      },
    },
  };
}

describe("isClarificationOpen", () => {
  it("keeps only the final unspent and unexpired clarification active", () => {
    const old = clarificationMessage("old");
    const current = clarificationMessage("current", "2030-01-01T00:00:00Z");
    const messages = [old, { id: "user", role: "user", content: "국내" } as ChatMessage, current];

    expect(isClarificationOpen(old, messages, new Set(), Date.UTC(2029, 0, 1))).toBe(false);
    expect(isClarificationOpen(current, messages, new Set(), Date.UTC(2029, 0, 1))).toBe(true);
    expect(
      isClarificationOpen(current, messages, new Set(["current-clarification"]), Date.UTC(2029, 0, 1)),
    ).toBe(false);
  });

  it("rejects an expired clarification even when it is the latest message", () => {
    const expired = clarificationMessage("expired", "2028-01-01T00:00:00Z");
    expect(isClarificationOpen(expired, [expired], new Set(), Date.UTC(2029, 0, 1))).toBe(false);
  });
});

describe("guided scenarios", () => {
  it("contains 12 independent tasks and a concrete exact-name fixture", () => {
    expect(GUIDED_SCENARIOS).toHaveLength(12);
    expect(new Set(GUIDED_SCENARIOS.map((scenario) => scenario.id)).size).toBe(12);
    expect(GUIDED_SCENARIOS.find((scenario) => scenario.id === "exact-alias")?.starter).toContain(
      "미래에셋 TIGER NVDA-UST커버드콜증권상장지수투자신탁",
    );
  });
});
