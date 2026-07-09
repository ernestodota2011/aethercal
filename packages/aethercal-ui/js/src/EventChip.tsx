import * as React from "react";
import type { CalendarEvent } from "./types";

interface EventChipProps {
  event: CalendarEvent;
  onDragStart: (event: CalendarEvent) => void;
}

/** A single draggable event chip rendered inside a day cell. */
export function EventChip({ event, onDragStart }: EventChipProps): React.JSX.Element {
  return (
    <div
      className="aethercal-event"
      draggable
      data-event-id={event.id}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", event.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart(event);
      }}
      style={{
        backgroundColor: event.color ?? "#3b82f6",
        color: "#ffffff",
        borderRadius: 4,
        padding: "2px 6px",
        marginTop: 2,
        fontSize: 12,
        cursor: "grab",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
      title={event.title}
    >
      {event.title}
    </div>
  );
}
