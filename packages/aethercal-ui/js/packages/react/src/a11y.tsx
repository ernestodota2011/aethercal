/**
 * Shared accessibility primitives for the calendar views (F2-E, RNF-7).
 *
 * `LiveRegion` is a visually-hidden polite live region the keyboard-drag code writes to, so a screen
 * reader announces grab / move / resize / drop / cancel as they happen. It uses `aria-live` on a
 * plain element (no `role="status"`) so it stays out of the landmark/role tree — the views assert
 * they expose no stray `status` role. `KeyboardHint` renders the visually-hidden usage instructions
 * a grid references via `aria-describedby`. Both use the `.aethercal-sr-only` utility (present in the
 * accessibility tree, invisible on screen — never display:none, which would mute them).
 */
import * as React from "react";

/** A visually-hidden polite live region; set `message` to announce it to assistive tech. */
export function LiveRegion({ message }: { message: string }): React.JSX.Element {
  return (
    <div className="aethercal-sr-only" aria-live="polite" aria-atomic="true">
      {message}
    </div>
  );
}

/** Visually-hidden keyboard-usage instructions, referenced by a grid's `aria-describedby`. */
export function KeyboardHint({ id, text }: { id: string; text: string }): React.JSX.Element {
  return (
    <div id={id} className="aethercal-sr-only">
      {text}
    </div>
  );
}
