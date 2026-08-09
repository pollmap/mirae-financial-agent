import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ManifestHeader } from "../components/ManifestHeader";

describe("ManifestHeader", () => {
  it("does not expose a non-functional session menu before authentication", () => {
    render(
      <ManifestHeader
        status={{ status: "DISABLED", ready: false, pilot_chat_enabled: false, retention_days: 14 }}
        profile={null}
        onMenu={vi.fn()}
        onLogout={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "세션 메뉴 열기" })).not.toBeInTheDocument();
  });

  it("shows the session menu for an authenticated tester", () => {
    render(
      <ManifestHeader
        status={{ status: "READY", ready: true, pilot_chat_enabled: true, retention_days: 14 }}
        profile={{ id: "tester", alias: "테스터-01" }}
        onMenu={vi.fn()}
        onLogout={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "세션 메뉴 열기" })).toBeInTheDocument();
    expect(screen.getByText("검증 준비")).toBeInTheDocument();
    expect(screen.getByText(/팀 내부 인간 검증 환경 · 공식 데이터 · 비투자자문/)).toBeInTheDocument();
  });
});
