import { describe, expect, it } from "vitest";

import css from "../styles.css?raw";

const light = css.match(/^:root\s*{([^}]*)}/s)?.[1] || "";
const dark = css.match(/@media \(prefers-color-scheme: dark\)\s*{\s*:root\s*{([^}]*)}/s)?.[1] || "";

function token(block: string, name: string): string {
  const value = block.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))?.[1];
  if (!value) throw new Error(`missing CSS token ${name}`);
  return value;
}

function luminance(hex: string): number {
  const channels = hex.slice(1).match(/../g)?.map((value) => Number.parseInt(value, 16) / 255) || [];
  const [red, green, blue] = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(foreground: string, background: string): number {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

describe("QA palette contrast", () => {
  it("keeps primary actions and light-theme accents at WCAG AA", () => {
    expect(contrast("#172331", token(light, "accent"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(light, "accent-dark"), token(light, "panel"))).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps all dark-theme semantic labels at WCAG AA", () => {
    const panel = token(dark, "panel");
    const semantic = ["accent-dark", "info", "success", "warning", "danger"];
    for (const name of semantic) {
      expect(contrast(token(dark, name), panel), name).toBeGreaterThanOrEqual(4.5);
    }
  });
});
