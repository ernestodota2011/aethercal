import type { CalendarEvent } from "@aethercal/calendar-core";
import * as React from "react";

/** Allow setting `--ac-*` custom properties via inline style (per-event accent color). */
type StyleWithVars = React.CSSProperties & Record<`--${string}`, string>;

interface EventChipProps {
  event: CalendarEvent;
  /** Pre-formatted, locale-aware time label, or null for all-day events. */
  timeLabel: string | null;
  onDragStart: (eventId: string) => void;
  onDragEnd: () => void;
}

/**
 * A single event chip inside a day cell: pointer-draggable (unless the event is not editable) and
 * themed through `--ac-event-*` tokens. Per-event `color` overrides the accent bar.
 *
 * F2-A a11y scope: the chip is a PRESENTATIONAL element with an accessible label — deliberately
 * NOT `role="button"`/focusable, because it has no keyboard action yet. Keyboard move/reschedule
 * (and the matching interactive role) land in F2-E; the list view is the accessible fallback
 * (AetherCal-06 §5/§7). Claiming a button role without a keyboard handler would be an ARIA lie.
 */
export function EventChip({ event, timeLabel, onDragStart, onDragEnd }: EventChipProps): React.JSX.Element {
  const editable = event.editable !== false;
  const style: StyleWithVars | undefined = event.color
    ? { "--ac-event-accent": event.color }
    : undefined;
  const accessibleLabel = timeLabel ? `${timeLabel} ${event.title}` : event.title;

  return (
    <div
      className={editable ? "aethercal-event" : "aethercal-event is-locked"}
      draggable={editable}
      data-event-id={event.id}
      aria-label={accessibleLabel}
      title={event.title}
      style={style}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", event.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart(event.id);
      }}
      onDragEnd={onDragEnd}
    >
      {timeLabel ? <time className="aethercal-event-time">{timeLabel}</time> : null}
      <span className="aethercal-event-title">{event.title}</span>
    </div>
  );
}
