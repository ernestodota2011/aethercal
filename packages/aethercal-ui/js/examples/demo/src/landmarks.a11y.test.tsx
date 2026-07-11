/**
 * Landmark + scrollable-region a11y guards for the public demo shell — web-qa findings N-1 and N-2.
 *
 * N-1 (scrollable-region-focusable): the quickstart `<pre class="demo-code">` blocks scroll
 * horizontally (overflow-x:auto, from the I-1 fix) but were not keyboard-focusable, so a keyboard-only
 * user could not scroll them. Each is now a focusable, labeled region (tabindex=0 + role=region +
 * a UNIQUE aria-label, so the extra landmarks stay unique).
 *
 * N-2 (region / content outside landmarks): the hero, hint, and quickstart sat outside every landmark
 * (only the calendar lived in a `<main>`). The body is now wrapped in a single top-level `<main>`, so
 * no page content is orphaned — without changing the visual layout.
 *
 * jsdom does no layout, so the live axe run over the deployed demo is the authoritative confirmation;
 * these tests lock the DOM invariants that make `scrollable-region-focusable` and `region` pass there.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";

afterEach(cleanup);

describe("quickstart code blocks are focusable, labeled regions (N-1)", () => {
  it("every .demo-code is a tabbable region with a non-empty aria-label", () => {
    const { container } = render(<App />);
    const blocks = Array.from(container.querySelectorAll<HTMLElement>(".demo-code"));
    expect(blocks.length).toBeGreaterThanOrEqual(3);
    for (const pre of blocks) {
      expect(pre.getAttribute("tabindex")).toBe("0");
      expect(pre.getAttribute("role")).toBe("region");
      expect(pre.getAttribute("aria-label")?.trim()).toBeTruthy();
    }
  });

  it("the code-block region labels are unique (no landmark-unique violation)", () => {
    const { container } = render(<App />);
    const labels = Array.from(container.querySelectorAll<HTMLElement>(".demo-code")).map((pre) =>
      pre.getAttribute("aria-label"),
    );
    expect(new Set(labels).size).toBe(labels.length);
  });
});

describe("no page content sits outside a landmark (N-2)", () => {
  it("has exactly one <main> landmark", () => {
    const { container } = render(<App />);
    expect(container.querySelectorAll("main")).toHaveLength(1);
  });

  it("wraps the hero, hint, and quickstart inside the <main> landmark", () => {
    const { container } = render(<App />);
    for (const selector of [".demo-hero", ".demo-hint", ".demo-quickstart", ".demo-status"]) {
      const el = container.querySelector(selector);
      expect(el, `missing ${selector}`).not.toBeNull();
      expect(el!.closest("main"), `${selector} is outside <main>`).not.toBeNull();
    }
  });

  it("keeps the header and footer as their own landmarks (banner / contentinfo), not inside main", () => {
    const { container } = render(<App />);
    const header = container.querySelector(".demo-header");
    const footer = container.querySelector(".demo-footer");
    expect(header?.closest("main")).toBeNull();
    expect(footer?.closest("main")).toBeNull();
  });
});
