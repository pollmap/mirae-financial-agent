import axe from "axe-core";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "../components/ChatPanel";
import { InspectorPanel } from "../components/InspectorPanel";
import { LoginScreen } from "../components/LoginScreen";
import { ManifestHeader } from "../components/ManifestHeader";
import type { AssistantMessage, QaSession, QaStatus } from "../types";

describe("core workspace accessibility", () => {
  it("has no structural WCAG A/AA violations detectable in the chat and inspector", async () => {
    const assistant: AssistantMessage = {
      id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      status: "FULL",
      content: "공식 데이터에서 확인한 답변입니다.",
      evidence: { result_count: 1, condition_ledger: [], items: [], retrieval_channels: [] },
    };
    const session: QaSession = {
      id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      session_version: 1,
      title: "접근성 검증",
      mode: "free",
      messages: [{
        id: assistant.id,
        role: "assistant",
        content: assistant.content,
        assistant,
      }],
    };
    const status: QaStatus = {
      status: "READY",
      ready: true,
      pilot_chat_enabled: true,
      retention_days: 14,
      environment: {
        engine_git_sha: "a".repeat(40),
        data_hash: `sha256:${"b".repeat(64)}`,
        data_snapshot_date: "2026-07-11",
        model_id: "HCX-FIXTURE-NO-LIVE",
        planner_stage: "two_stage",
        vector_status: "DISABLED_FIXTURE_NO_LIVE_CACHE",
      },
    };
    const { container } = render(
      <div>
        <ManifestHeader
          status={status}
          profile={{ id: "tester", alias: "tester-a11y" }}
          onMenu={vi.fn()}
          onLogout={vi.fn()}
        />
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
        />
        <InspectorPanel assistant={assistant} open={false} modal={false} onClose={vi.fn()} />
      </div>,
    );

    const result = await axe.run(container, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
      // jsdom has no layout/canvas implementation. Palette contrast is
      // verified deterministically in contrast.test.ts and the built UI is
      // also run through axe in a real browser during design QA.
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

  it("has no structural WCAG A/AA violations on the consent screen", async () => {
    const { container } = render(
      <LoginScreen retentionDays={14} busy={false} onRedeem={vi.fn().mockResolvedValue(undefined)} />,
    );
    const result = await axe.run(container, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
      rules: { "color-contrast": { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
