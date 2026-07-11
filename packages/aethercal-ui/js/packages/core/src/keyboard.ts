/**
 * Headless keyboard-navigation geometry for the calendar (F2-E a11y, AetherCal-06 §3/§7).
 *
 * Pure, DOM-free helpers the React views use to drive keyboard navigation: moving the active cell
 * around a row × column grid (month = 6×7, week time grid = N day columns, day = 1 column). The
 * React layer maps a `keydown` to one of these intents and calls this to get the next index; it
 * never computes grid geometry itself (RF-23). Keyboard DRAG geometry (stepping a move/resize
 * target by whole days or snapped minutes) reuses `addCalendarDays` (dateMath) and the interaction
 * geometry in `interactions.ts` — this file is only the focus-navigation math.
 */

/** The navigation keys `nextGridIndex` understands (any other key is a no-op). */
export type GridNavKey =
  | "ArrowLeft"
  | "ArrowRight"
  | "ArrowUp"
  | "ArrowDown"
  | "Home"
  | "End";

/**
 * The next active cell index for a `rows × cols` grid, given the current index and a navigation key.
 *
 * Left/Right move WITHIN the current row and clamp at its edges (so a single-column grid — the day
 * time grid — treats them as no-ops). Up/Down move by a full row and clamp at the top/bottom edge
 * (no wrap). Home/End jump to the first/last cell of the CURRENT row. Any other key — or a move that
 * would leave the grid — returns the current index unchanged, so a view can call this on every
 * keydown without spurious focus moves.
 */
export function nextGridIndex(
  current: number,
  key: string,
  rows: number,
  cols: number,
): number {
  const total = rows * cols;
  if (total <= 0) return current;
  const clamped = Math.min(Math.max(current, 0), total - 1);
  const rowStart = clamped - (clamped % cols);
  const rowEnd = Math.min(rowStart + cols - 1, total - 1);

  switch (key) {
    case "ArrowLeft":
      return clamped > rowStart ? clamped - 1 : clamped;
    case "ArrowRight":
      return clamped < rowEnd ? clamped + 1 : clamped;
    case "ArrowUp": {
      const next = clamped - cols;
      return next >= 0 ? next : clamped;
    }
    case "ArrowDown": {
      const next = clamped + cols;
      return next < total ? next : clamped;
    }
    case "Home":
      return rowStart;
    case "End":
      return rowEnd;
    default:
      return clamped;
  }
}
