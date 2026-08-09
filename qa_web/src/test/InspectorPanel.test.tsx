import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { InspectorPanel } from "../components/InspectorPanel";
import type { AssistantMessage } from "../types";

const assistant: AssistantMessage = {
  id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  status: "FULL",
  content: "검증된 답변",
  evidence: {
    snapshot_date: "2026-08-03",
    result_count: 1,
    condition_ledger: [
      {
        condition_id: "scope-1",
        kind: "scope",
        requested_text: "국내 ETF",
        status: "grounded",
        grounded_fields: ["product.scope"],
        note: "공식 스코프로 확인",
      },
    ],
    condition_changes: [
      {
        kind: "scope",
        previous: ["국내 ETP"],
        current: "해외 ETP",
        reason: "explicit_user_correction",
      },
    ],
    items: [
      {
        product_uid: "domestic_etp:1",
        name: "테스트 ETF",
        fields: [
          {
            metric_id: "return_1y",
            source_file: "국내ETF.xlsx",
            source_sheet: "상품목록",
            source_excel_row: 42,
            source_field: "1년수익률",
            raw_value: "12.3",
            normalized_value: 12.3,
            unit: "%",
            source_row_hash: "abcdef1234567890",
            quality_flags: [],
          },
        ],
      },
    ],
    aggregates: [
      {
        aggregate_id: "aggregate-1",
        group_key: "domestic_etp",
        value: 100,
        unit: "count",
        source_table_ids: ["domestic_etp"],
        source_fields: ["product_uid"],
        source_row_count: 100,
      },
    ],
    retrieval_channels: [
      {
        channel: "graph",
        status: "fallback",
        reason: "KG 커버리지 부족",
        scope: "domestic_etp",
        candidate_count: 4,
        verified_count: 3,
        latency_ms: 7,
        fallback_reason: "SQL authoritative fallback",
        evidence_refs: ["ev-1"],
      },
    ],
  },
};

describe("InspectorPanel backend evidence contract", () => {
  it("shows sanitized condition, source row, aggregate, and retrieval fields", async () => {
    render(<InspectorPanel assistant={assistant} open={false} modal={false} onClose={vi.fn()} />);

    const changes = screen.getByLabelText("조건 변경 내역");
    expect(within(changes).getByText("국내 ETP")).toBeInTheDocument();
    expect(within(changes).getByText("해외 ETP")).toBeInTheDocument();
    expect(within(changes).getByText("사용자 정정")).toBeInTheDocument();
    expect(screen.getByText("국내 ETF")).toBeInTheDocument();
    expect(screen.getByText("product.scope")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "근거" }));
    expect(screen.getByRole("region", { name: "테스트 ETF 근거" })).toBeInTheDocument();
    expect(screen.getByText("테스트 ETF")).toBeInTheDocument();
    expect(screen.getByText("domestic_etp:1")).toBeInTheDocument();
    expect(screen.getByText("상품목록")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("1년수익률")).toBeInTheDocument();
    expect(screen.getByText("집계 domestic_etp")).toBeInTheDocument();
    expect(screen.getByText("domestic_etp", { selector: "dd" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "검색 경로" }));
    expect(screen.getByText("SQL authoritative fallback")).toBeInTheDocument();
    expect(screen.getByText("ev-1")).toBeInTheDocument();
    expect(screen.getByText("SQL 재검증")).toBeInTheDocument();
    expect(screen.getByText("3", { selector: "dd" })).toBeInTheDocument();
  });

  it("acts as a focus-trapped modal only in overlay mode and restores its trigger", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>검사 열기</button>
          <InspectorPanel assistant={assistant} open={open} modal onClose={() => setOpen(false)} />
        </>
      );
    }
    const user = userEvent.setup();
    render(<Harness />);

    const trigger = screen.getByRole("button", { name: "검사 열기" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "답변 검사" });
    const close = within(dialog).getByRole("button", { name: "검사 패널 닫기" });
    await waitFor(() => expect(close).toHaveFocus());

    await user.tab({ shift: true });
    expect(within(dialog).getByRole("tab", { name: "조건" })).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("remains a non-modal side panel on desktop", () => {
    render(<InspectorPanel assistant={assistant} open modal={false} onClose={vi.fn()} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("답변 검사 패널")).toBeInTheDocument();
  });

  it("removes a closed overlay inspector from focus and the accessibility tree", () => {
    render(<InspectorPanel assistant={assistant} open={false} modal onClose={vi.fn()} />);
    const panel = document.querySelector(".inspector-panel");
    expect(panel).toHaveAttribute("inert");
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("button", { name: "검사 패널 닫기" })).not.toBeInTheDocument();
  });
});
