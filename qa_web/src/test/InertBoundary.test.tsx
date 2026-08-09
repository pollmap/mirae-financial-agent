import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InertBoundary } from "../components/InertBoundary";

describe("InertBoundary", () => {
  it("hides and inerts background content only while a modal is active", () => {
    const { rerender } = render(
      <InertBoundary active className="test-background">
        <button type="button">배경 동작</button>
      </InertBoundary>,
    );
    const boundary = screen.getByText("배경 동작", { selector: "button" }).parentElement!;
    expect(boundary).toHaveAttribute("inert");
    expect(boundary).toHaveAttribute("aria-hidden", "true");

    rerender(
      <InertBoundary active={false} className="test-background">
        <button type="button">배경 동작</button>
      </InertBoundary>,
    );
    expect(boundary).not.toHaveAttribute("inert");
    expect(boundary).not.toHaveAttribute("aria-hidden");
  });
});
