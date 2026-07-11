/**
 * Time-grid stylesheet for the week/day views (AetherCal-06 §5/§7), injected separately from the
 * month view's base stylesheet so F2-B touches no shared F2-A/F2-C file. It reuses the neutral
 * `--ac-*` base tokens from `styles.ts` and adds a few time-grid-only tokens (all overridable, all
 * brand-neutral — no lavender/cyan/glow, reference_anti_ai_slop_doctrina). The dark/preset system
 * is F2-E.
 *
 * The three grid rows (header, all-day, body) share one column template
 * (`gutter + N day columns`) via `--ac-tg-cols`, which the view sets inline (1 for day, 7 for week),
 * so the axis gutter stays aligned across rows. Event blocks are absolutely positioned inside a
 * day column using the fractions computed by `@aethercal/calendar-core` (top/height/left/width as
 * percentages).
 */
export const TIME_GRID_STYLE_ELEMENT_ID = "aethercal-timegrid-styles";

export const TIME_GRID_CSS = `
:where(.aethercal-timegrid) {
  --ac-tg-gutter: 56px;
  --ac-tg-body-height: 640px;
  --ac-tg-hour-min-height: 44px;
  --ac-tg-line: var(--ac-border);
  --ac-tg-now: #dc2626;
  --ac-tg-event-bg: var(--ac-event-bg);
  --ac-tg-event-fg: var(--ac-event-fg);
  --ac-tg-event-accent: var(--ac-event-accent);
}
.aethercal-timegrid { display: flex; flex-direction: column; }
.aethercal-tg-head,
.aethercal-tg-allday,
.aethercal-tg-body {
  display: grid;
  grid-template-columns: var(--ac-tg-gutter) repeat(var(--ac-tg-cols, 7), minmax(0, 1fr));
}
.aethercal-tg-head { border-bottom: 1px solid var(--ac-border); }
.aethercal-tg-corner { border-right: 1px solid var(--ac-border); }
.aethercal-tg-colhead {
  padding: 8px 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ac-header-fg);
  text-align: center;
  border-right: 1px solid var(--ac-border);
}
.aethercal-tg-colhead:last-child { border-right: none; }
.aethercal-tg-colhead.is-today { color: var(--ac-fg); }
.aethercal-tg-colhead.is-today .aethercal-tg-colhead-date {
  display: inline-block;
  min-width: 22px;
  height: 22px;
  line-height: 22px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--ac-today-marker-bg);
  color: var(--ac-today-marker-fg);
}
.aethercal-tg-allday { border-bottom: 1px solid var(--ac-border); min-height: 28px; }
.aethercal-tg-rowhead {
  padding: 4px 6px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--ac-faint);
  border-right: 1px solid var(--ac-border);
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
.aethercal-tg-allday-cell {
  padding: 3px 4px;
  border-right: 1px solid var(--ac-border);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.aethercal-tg-allday-cell:last-child { border-right: none; }
.aethercal-tg-body {
  position: relative;
  height: var(--ac-tg-body-height);
  overflow-y: auto;
}
.aethercal-tg-gutter { position: relative; border-right: 1px solid var(--ac-border); }
.aethercal-tg-hour {
  position: absolute;
  right: 6px;
  transform: translateY(-50%);
  font-size: 10px;
  color: var(--ac-faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.aethercal-tg-col {
  position: relative;
  border-right: 1px solid var(--ac-border);
  min-height: calc(var(--ac-tg-hours, 24) * var(--ac-tg-hour-min-height));
}
.aethercal-tg-col:last-child { border-right: none; }
.aethercal-tg-col.is-today { background: color-mix(in srgb, var(--ac-today-marker-bg) 4%, transparent); }
.aethercal-tg-col.is-drop-target { outline: 2px dashed var(--ac-focus); outline-offset: -2px; }
.aethercal-tg-line {
  position: absolute;
  left: 0;
  right: 0;
  border-top: 1px solid var(--ac-tg-line);
  pointer-events: none;
}
.aethercal-tg-event {
  position: absolute;
  overflow: hidden;
  box-sizing: border-box;
  padding: 2px 6px;
  border-radius: calc(var(--ac-radius) - 4px);
  border-left: 3px solid var(--ac-tg-event-accent);
  background: var(--ac-tg-event-bg);
  color: var(--ac-tg-event-fg);
  font-size: 11px;
  line-height: 1.3;
  cursor: grab;
  min-height: 14px;
}
.aethercal-tg-event.is-locked { cursor: default; opacity: 0.75; }
.aethercal-tg-event-time { color: var(--ac-muted); font-size: 10px; font-variant-numeric: tabular-nums; }
.aethercal-tg-event-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.aethercal-now-indicator {
  position: absolute;
  left: 0;
  right: 0;
  height: 0;
  border-top: 2px solid var(--ac-tg-now);
  pointer-events: none;
  z-index: 2;
}
.aethercal-now-indicator::before {
  content: "";
  position: absolute;
  left: -3px;
  top: -4px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ac-tg-now);
}
`;

/** Inject the time-grid stylesheet once into <head>. No-op without a DOM (SSR) or when present. */
export function ensureTimeGridStyles(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(TIME_GRID_STYLE_ELEMENT_ID)) return;
  const style = document.createElement("style");
  style.id = TIME_GRID_STYLE_ELEMENT_ID;
  style.textContent = TIME_GRID_CSS;
  document.head.appendChild(style);
}
