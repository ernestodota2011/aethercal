import { type AgendaEntry, type CalendarEvent, buildAgenda } from "@aethercal/calendar-core";
import * as React from "react";
import type { CalendarMessages } from "./i18n";
import { formatAgendaDayHeading, formatEventTime } from "./labels";
import type { ThemeTokens } from "./theme";

export interface AgendaViewProps {
  events: readonly CalendarEvent[];
  locale: string;
  /** All user-facing strings (locale-resolved by AetherCalendar): all-day / continues / ends / empty. */
  messages: CalendarMessages;
  /** Inline `--ac-*` theme overrides applied to the root (a preset or a custom object). */
  themeVars?: ThemeTokens;
}

function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Allow setting `--ac-*` custom properties via inline style (per-event accent color). */
type StyleWithVars = React.CSSProperties & Record<`--${string}`, string>;

/**
 * The honest time-column label for one row, given the event's relationship to THIS day:
 * a real all-day event reads all-day; a timed event reads its start time on its start day, the
 * "continues" label on a day it passes fully through, and its real end time on its final day.
 * Labeling a timed continuation as all-day (or showing a start time that belongs to another day)
 * would misrepresent it — this mirrors EventChip's "no ARIA lie" stance.
 */
function rowTimeLabel(entry: AgendaEntry, locale: string, messages: CalendarMessages): string {
  const { event, isContinuation, continuesAfter } = entry;
  if (event.allDay === true) return messages.allDay;
  if (!isContinuation) return formatEventTime(event.start, locale); // start day (or single day)
  if (continuesAfter) return messages.continues; // a full pass-through day
  return messages.endsAt(formatEventTime(event.end, locale)); // the final day: ends here
}

/** One agenda row: a plain list item (no drag/keyboard — the list is the read-only surface). */
function AgendaRow({
  entry,
  locale,
  messages,
}: {
  entry: AgendaEntry;
  locale: string;
  messages: CalendarMessages;
}): React.JSX.Element {
  const { event, isContinuation, continuesAfter } = entry;
  const timeLabel = rowTimeLabel(entry, locale, messages);
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
 * events are a `list` of `listitem`s, so a screen reader announces the day then walks the events —
 * native keyboard reading, no grid pattern needed and no dishonest interactive roles (RNF-7). All
 * strings come from the locale message pack + `Intl` (no hardcoded English); geometry/grouping lives
 * in `@aethercal/calendar-core` (RF-23), this file only renders.
 */
export function AgendaView({
  events,
  locale,
  messages,
  themeVars,
}: AgendaViewProps): React.JSX.Element {
  const days = React.useMemo(() => buildAgenda(events), [events]);
  const baseId = React.useId();

  if (days.length === 0) {
    return (
      <div className="aethercal-calendar aethercal-agenda" data-view="list" style={themeVars}>
        <p className="aethercal-agenda-empty">{messages.noEvents}</p>
      </div>
    );
  }

  return (
    <div className="aethercal-calendar aethercal-agenda" data-view="list" style={themeVars}>
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
                <AgendaRow
                  key={`${entry.event.id}-${i}`}
                  entry={entry}
                  locale={locale}
                  messages={messages}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
