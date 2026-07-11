import { type AgendaEntry, type CalendarEvent, buildAgenda } from "@aethercal/calendar-core";
import * as React from "react";
import { formatAgendaDayHeading, formatEventTime } from "./labels";

export interface AgendaViewProps {
  events: readonly CalendarEvent[];
  locale: string;
  /** Time-column label for a real all-day event (locale strings live in F2-E). */
  allDayLabel: string;
  /** Time-column label for a day a timed event passes fully through (started earlier, ends later). */
  continuesLabel: string;
  /** Time-column label for the last day of a timed multi-day event, given its formatted end time. */
  formatEndsLabel: (endTimeLabel: string) => string;
  /** Message shown when there are no events to list. */
  emptyLabel: string;
}

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Allow setting `--ac-*` custom properties via inline style (per-event accent color). */
type StyleWithVars = React.CSSProperties & Record<`--${string}`, string>;

interface RowLabels {
  allDayLabel: string;
  continuesLabel: string;
  formatEndsLabel: (endTimeLabel: string) => string;
}

/**
 * The honest time-column label for one row, given the event's relationship to THIS day:
 * a real all-day event reads all-day; a timed event reads its start time on its start day, the
 * "continues" label on a day it passes fully through, and its real end time on its final day.
 * Labeling a timed continuation as all-day (or showing a start time that belongs to another day)
 * would misrepresent it — this mirrors EventChip's "no ARIA lie" stance.
 */
function rowTimeLabel(entry: AgendaEntry, locale: string, labels: RowLabels): string {
  const { event, isContinuation, continuesAfter } = entry;
  if (event.allDay === true) return labels.allDayLabel;
  if (!isContinuation) return formatEventTime(event.start, locale); // start day (or single day)
  if (continuesAfter) return labels.continuesLabel; // a full pass-through day
  return labels.formatEndsLabel(formatEventTime(event.end, locale)); // the final day: ends here
}

/** One agenda row: a plain list item (no drag — interactions are F2-D) with an honest time label. */
function AgendaRow({
  entry,
  locale,
  labels,
}: {
  entry: AgendaEntry;
  locale: string;
  labels: RowLabels;
}): React.JSX.Element {
  const { event, isContinuation, continuesAfter } = entry;
  const timeLabel = rowTimeLabel(entry, locale, labels);
  const style: StyleWithVars | undefined = event.color
    ? { "--ac-event-accent": event.color }
    : undefined;

  return (
    <li
      className={cx("aethercal-agenda-event", isContinuation && "is-continuation")}
      data-event-id={event.id}
      aria-label={`${timeLabel} ${event.title}`}
      style={style}
      {...(event.allDay === true ? { "data-all-day": "" } : {})}
      {...(isContinuation ? { "data-continuation": "" } : {})}
      {...(continuesAfter ? { "data-continues-after": "" } : {})}
    >
      <span className="aethercal-agenda-event-time">{timeLabel}</span>
      <span className="aethercal-agenda-event-title">{event.title}</span>
    </li>
  );
}

/**
 * The list/agenda surface (AetherCal-06 §5): a chronological, day-grouped list — the accessible,
 * drag-free counterpart to the month/week grids and a density-friendly fallback.
 *
 * Structure uses real list semantics: each day is a labeled `group` (its date names the group) whose
 * events are a `list` of `listitem`s, so a screen reader announces the day then walks the events.
 * Day/time labels come from `Intl` via the caller's `locale` — no hardcoded English (full i18n is
 * F2-E). All geometry/grouping lives in `@aethercal/calendar-core` (RF-23); this file only renders.
 */
export function AgendaView({
  events,
  locale,
  allDayLabel,
  continuesLabel,
  formatEndsLabel,
  emptyLabel,
}: AgendaViewProps): React.JSX.Element {
  const days = React.useMemo(() => buildAgenda(events), [events]);
  const baseId = React.useId();
  const labels: RowLabels = { allDayLabel, continuesLabel, formatEndsLabel };

  if (days.length === 0) {
    return (
      <div className="aethercal-calendar aethercal-agenda" data-view="list">
        <p className="aethercal-agenda-empty">{emptyLabel}</p>
      </div>
    );
  }

  return (
    <div className="aethercal-calendar aethercal-agenda" data-view="list">
      {days.map((day) => {
        const titleId = `${baseId}-${day.date}`;
        return (
          <section
            key={day.date}
            className="aethercal-agenda-day"
            role="group"
            aria-labelledby={titleId}
            data-date={day.date}
          >
            <div className="aethercal-agenda-day-title" id={titleId}>
              {formatAgendaDayHeading(day.date, locale)}
            </div>
            <ul className="aethercal-agenda-day-events" role="list">
              {day.entries.map((entry, i) => (
                <AgendaRow key={`${entry.event.id}-${i}`} entry={entry} locale={locale} labels={labels} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
