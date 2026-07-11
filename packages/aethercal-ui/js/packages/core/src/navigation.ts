/**
 * Headless period navigation for the calendar core (F2-NAV).
 *
 * A calendar has to move between periods — the previous/next month, week, or day — and a consumer
 * (the Reflex wrapper, the admin, the demo) has to know exactly WHICH period is on screen so it can
 * load that period's events. Both answers are pure geometry, so they live here in `calendar-core`
 * (RF-23 / AetherCal-06 §3), never in the React layer or a host app:
 *
 * - `getVisibleRange` returns the PERIOD as `{ view, from, to }` — the payload the component emits on
 *   `on_range_change` / `on_view_change`. `from` is inclusive (midnight of the period's first day);
 *   `to` is EXCLUSIVE (midnight of the day after the last), the same DTEND convention the rest of the
 *   core uses for an event's `end`. This is the LOGICAL period (the calendar month for `month`/`list`,
 *   the week for `week`, the day for `day`), NOT the physical grid extent — the month grid also
 *   renders a few leading/trailing days of the adjacent months. The period range is what it is so
 *   that `from` doubles as a valid ANCHOR (setting anchor = a range's `from` and recomputing
 *   reproduces the identical range — the controlled round-trip), which a grid-extent range could not
 *   guarantee (its first day may belong to the previous month). A consumer that fetches data for the
 *   grid should therefore load a slightly WIDER window than this period (see the admin's `_window`),
 *   so events on the visible spillover days are still fetched — period range vs. data range.
 * - `stepAnchor` moves the anchor one period earlier/later. Component-based (never a raw millisecond
 *   add), so it is DST-safe and never overflows a short month (stepping a month from Jan 31 is Feb 1,
 *   not a rolled-over Mar 3).
 */
import { formatLocalDateTime, startOfWeek } from "./dateMath";
import type { CalendarView, FirstDayOfWeek, ViewChangePayload } from "./types";

/** Default first day of the week (Monday), matching the rest of the core. */
const DEFAULT_FIRST_DAY_OF_WEEK = 1;

/**
 * The visible date range for `view` anchored at `anchor`, as `{ view, from, to }` naive-local ISO
 * strings. `from` is inclusive (period start at midnight); `to` is exclusive (start of the day after
 * the period's last visible day). `month` and `list` span the calendar month; `week` spans the
 * `firstDayOfWeek`-aligned week; `day` spans the single day.
 */
export function getVisibleRange(
  view: CalendarView,
  anchor: Date,
  firstDayOfWeek: FirstDayOfWeek = DEFAULT_FIRST_DAY_OF_WEEK,
): ViewChangePayload {
  const y = anchor.getFullYear();
  const m = anchor.getMonth();
  const d = anchor.getDate();
  let from: Date;
  let to: Date;
  switch (view) {
    case "week": {
      from = startOfWeek(anchor, firstDayOfWeek);
      to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 7);
      break;
    }
    case "day": {
      from = new Date(y, m, d);
      to = new Date(y, m, d + 1);
      break;
    }
    // month + list (agenda over the visible month) share the calendar-month range.
    default: {
      from = new Date(y, m, 1);
      to = new Date(y, m + 1, 1);
      break;
    }
  }
  return { view, from: formatLocalDateTime(from), to: formatLocalDateTime(to) };
}

/**
 * The anchor one period (`delta` = -1 previous / +1 next) from `anchor`, at midnight. `month`/`list`
 * step to the first of the adjacent month (overflow-safe); `week` steps by exactly 7 days; `day` by
 * one day. DST-safe (component arithmetic). The result is a valid anchor for `getVisibleRange`.
 */
export function stepAnchor(anchor: Date, view: CalendarView, delta: number): Date {
  const y = anchor.getFullYear();
  const m = anchor.getMonth();
  const d = anchor.getDate();
  switch (view) {
    case "week":
      return new Date(y, m, d + 7 * delta);
    case "day":
      return new Date(y, m, d + delta);
    // month + list step a whole month; anchoring on day 1 avoids short-month overflow.
    default:
      return new Date(y, m + delta, 1);
  }
}
