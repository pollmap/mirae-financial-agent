import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoginScreen } from "../components/LoginScreen";

describe("LoginScreen runtime labeling", () => {
  it("states plainly that fixture preview is not a live HCX call", () => {
    render(
      <LoginScreen
        retentionDays={14}
        fixtureMode
        busy={false}
        onRedeem={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByText(/실제 HCX 호출이 아닙니다/)).toBeInTheDocument();
  });
});
