import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FeedbackControl } from "../components/FeedbackControl";

describe("FeedbackControl", () => {
  it("uses the backend verdict values and 500-character note limit", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<FeedbackControl onSave={onSave} />);

    await userEvent.click(screen.getByText("이 답변 평가"));
    await userEvent.click(screen.getByText("잘못됨"));
    expect(screen.getByRole("button", { name: "평가 저장" })).toBeDisabled();
    expect(screen.getByText(/문제 유형을 하나 이상/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("조건 누락"));
    expect(screen.getByRole("button", { name: "평가 저장" })).toBeEnabled();
    const note = screen.getByLabelText(/검토 메모/);
    expect(note).toHaveAttribute("maxlength", "500");
    await userEvent.type(note, "조건 하나가 결과에서 사라짐");
    await userEvent.click(screen.getByRole("button", { name: "평가 저장" }));

    expect(onSave).toHaveBeenCalledWith({
      verdict: "incorrect",
      tags: ["MISSING_CONDITION"],
      note: "조건 하나가 결과에서 사라짐",
    });
  });
});
