import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "../components/SessionSidebar";

describe("SessionSidebar mobile dialog behavior", () => {
  it("inerts the closed drawer", () => {
    render(
      <SessionSidebar
        sessions={[]}
        open={false}
        modal
        busy={false}
        onClose={vi.fn()}
        onSelect={vi.fn()}
        onCreate={vi.fn()}
      />,
    );
    const sidebar = document.querySelector(".session-sidebar");
    expect(sidebar).toHaveAttribute("inert");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("button", { name: "새 자유 테스트" })).not.toBeInTheDocument();
  });

  it("moves and traps focus, closes with Escape, and restores the trigger", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>세션 열기</button>
          <SessionSidebar
            sessions={[]}
            open={open}
            modal
            busy={false}
            onClose={() => setOpen(false)}
            onSelect={vi.fn()}
            onCreate={vi.fn()}
          />
        </>
      );
    }

    const user = userEvent.setup();
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "세션 열기" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "세션" });
    const close = within(dialog).getByRole("button", { name: "세션 메뉴 닫기" });
    await waitFor(() => expect(close).toHaveFocus());

    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: /12 프롬프트 인젝션/ })).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "세션" })).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
