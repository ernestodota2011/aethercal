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
  /** An optimistic mutation is in flight for this event (pending affordance). */
  isPending?: boolean;
  /** This event's mutation was just reverted (rollback flash). */
  isRolledBack?: boolean;
  /** Click the chip (F2-D). */
  onClick?: () => void;
  /** Right-click / context-menu on the chip (F2-D). */
  onContextMenu?: () => void;
  /**
   * Stable DOM id so the grid's `aria-activedescendant` can point at this chip (F2-E keyboard nav).
   */
  id?: string;
  /**
   * The chip has a real keyboard action (grab-to-move or click) — expose `role="button"` so it is
   * announced as actionable. Left off when the event is unwired/locked (no ARIA lie).
   */
  interactive?: boolean;
  /** This chip is the grid's active descendant (keyboard focus ring). */
  isActive?: boolean;
  /** This chip is currently grabbed for a keyboard move (stronger ring). */
  isGrabbed?: boolean;
}

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/**
 * A single event chip inside a day cell / all-day rail: pointer-draggable (unless the event is not
 * editable) and themed through `--ac-event-*` tokens. Per-event `color` overrides the accent bar.
 * F2-D adds optional click / context-menu handlers and the optimistic status classes (`is-pending`
 * / `is-rolledback`) the reconciliation layer drives; when no `onClick` is supplied the chip stays a
 * presentational element (no dishonest interactive role — keyboard activation is F2-E).
 */
export function EventChip({
  event,
  timeLabel,
  onDragStart,
  onDragEnd,
  isPending,
  isRolledBack,
  onClick,
  onContextMenu,
  id,
  interactive,
  isActive,
  isGrabbed,
}: EventChipProps): React.JSX.Element {
  const editable = event.editable !== false;
  const style: StyleWithVars | undefined = event.color
    ? { "--ac-event-accent": event.color }
    : undefined;
  const accessibleLabel = timeLabel ? `${timeLabel} ${event.title}` : event.title;

  return (
    <div
      className={cx(
        "aethercal-event",
        !editable && "is-locked",
        isPending && "is-pending",
        isRolledBack && "is-rolledback",
        isActive && "is-active",
        isGrabbed && "is-grabbed",
      )}
      {...(id ? { id } : {})}
      {...(interactive ? { role: "button" } : {})}
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
      onClick={onClick}
      onContextMenu={
        onContextMenu
          ? (e) => {
              e.preventDefault();
              e.stopPropagation();
              onContextMenu();
            }
          : undefined
      }
    >
      {timeLabel ? <time className="aethercal-event-time">{timeLabel}</time> : null}
      {/* A real whitespace text node between the time and the title (the visual gap is a flex `gap`,
          which is NOT text): without it the chip's visible text is "9:00Consulta" while its
          accessible name is "9:00 Consulta", tripping axe's label-content-name-mismatch (WCAG 2.5.3,
          finding M-2). The space matches the two, changes no layout, and keeps the SR name intact. */}
      {timeLabel ? " " : null}
      <span className="aethercal-event-title">{event.title}</span>
    </div>
  );
}
