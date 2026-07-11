/**
 * Deterministic regression guard for finding I-1 (mobile horizontal page overflow).
 *
 * The quickstart's wide `<pre class="demo-code">` (white-space: pre) lives inside a grid item
 * (`.demo-steps > li`). A grid item defaults to `min-width: auto`, which let the <pre> expand its
 * track past a 390px viewport and scroll the WHOLE page sideways. The fix pins the item to
 * `min-width: 0` (so the code block scrolls inside its own `overflow-x: auto` box) and caps the code
 * block at `max-width: 100%`.
 *
 * jsdom performs no layout, so `documentElement.scrollWidth` cannot be measured here (that is
 * verified live in a real 390px browser viewport during QA). This test instead locks the CSS
 * invariants that PREVENT the overflow, so removing either guard fails the suite.
 */
import { describe, expect, it } from "vitest";
import css from "./styles.css?raw";

/** Extract the declaration block for a selector (naive but adequate for this hand-authored sheet). */
function ruleBody(selector: string): string {
  const start = css.indexOf(selector);
  expect(start, `selector not found: ${selector}`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

describe("demo page has no horizontal overflow container (I-1)", () => {
  it("the quickstart grid item cannot be widened past its track by the code block", () => {
    expect(ruleBody(".demo-steps > li")).toMatch(/min-width:\s*0/);
  });

  it("the code block scrolls inside its own box and never exceeds its container", () => {
    const body = ruleBody(".demo-code {");
    expect(body).toMatch(/overflow-x:\s*auto/);
    expect(body).toMatch(/max-width:\s*100%/);
  });
});
