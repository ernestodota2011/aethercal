import * as React from "react";
import { EventChip } from "./EventChip";
import { computeDroppedRange, toDateOnly } from "./dateMath";
import type { CalendarEvent, EventDropPayload } from "./types";

interface CalendarGridProps {
  /** Date-only ("YYYY-MM-DD") strings, in display order. 7 for a week grid, 42 for a month grid. */
  days: string[];
  events: CalendarEvent[];
  onEventDrop?: (payload: EventDropPayload) => void;
  /** Row height in px; the only thing that differs between the month and week views. */
  minCellHeight: number;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function groupEventsByDay(events: CalendarEvent[]): Map<string, CalendarEvent[]> {
  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of events) {
    const key = toDateOnly(event.start);
    const existing = byDay.get(key);
    if (existing) {
      existing.push(event);
    } else {
      byDay.set(key, [event]);
    }
  }
  return byDay;
}

/** A 7-column date grid (used for both month and week views) with drag-to-reschedule. */
export function CalendarGrid({
  days,
  events,
  onEventDrop,
  minCellHeight,
}: CalendarGridProps): React.JSX.Element {
  const eventsByDay = React.useMemo(() => groupEventsByDay(events), [events]);

  const handleDrop = React.useCallback(
    (targetDateOnly: string) => (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const eventId = e.dataTransfer.getData("text/plain");
      const dragged = events.find((candidate) => candidate.id === eventId);
      if (!dragged || !onEventDrop) {
        return;
      }
      onEventDrop(computeDroppedRange(dragged, targetDateOnly));
    },
    [events, onEventDrop],
  );

  return (
    <div className="aethercal-grid" data-testid="aethercal-grid">
      <div
        className="aethercal-grid-header"
        style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)" }}
      >
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="aethercal-grid-header-cell" style={{ fontSize: 12 }}>
            {label}
          </div>
        ))}
      </div>
      <div
        className="aethercal-grid-body"
        style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)" }}
      >
        {days.map((dateOnly) => (
          <div
            key={dateOnly}
            className="aethercal-day-cell"
            data-date={dateOnly}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop(dateOnly)}
            style={{
              minHeight: minCellHeight,
              border: "1px solid #e5e7eb",
              padding: 4,
            }}
          >
            <div className="aethercal-day-number" style={{ fontSize: 12, opacity: 0.7 }}>
              {Number(dateOnly.slice(-2))}
            </div>
            {(eventsByDay.get(dateOnly) ?? []).map((event) => (
              <EventChip key={event.id} event={event} onDragStart={() => undefined} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
