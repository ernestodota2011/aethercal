/**
 * AetherCal public demo — a static React playground for `@aethercal/calendar-react` (AetherCal-06
 * §9). It mounts the batteries-included `OptimisticCalendar` with realistic sample data and exposes
 * live controls: the four views, the four theme presets, the ES/EN locale, and a reconciliation
 * mode toggle that flips the mocked "server" between accept and reject so a visitor can watch the
 * optimistic update commit — or roll back with the flash animation. No backend: the mutation is a
 * client-side promise, so the whole thing builds to a portable static bundle.
 */
import {
  OptimisticCalendar,
  PRESET_NAMES,
  PRESETS,
  type CalendarEvent,
  type CalendarMutation,
  type CalendarView,
  type ContextMenuPayload,
  type EventClickPayload,
  type MutationResult,
  type RangeSelectPayload,
  type ThemePreset,
} from "@aethercal/calendar-react";
import * as React from "react";
import { buildSampleEvents } from "./sampleData";

const REPO_URL = "https://github.com/ernestodota2011/aethercal";

type Locale = "es" | "en";
const VIEWS: readonly CalendarView[] = ["month", "week", "day", "list"];

/** Demo-chrome copy, localized alongside the calendar itself (neutral Spanish "tú", no voseo). */
const UI = {
  es: {
    tagline: "Infraestructura de calendario y agendamiento, de código abierto (MIT).",
    lead:
      "Un componente headless-first: motor de geometría puro + capa React. Cuatro vistas, " +
      "arrastrar / redimensionar / seleccionar, reconciliación optimista, temas por tokens e i18n.",
    view: "Vista",
    theme: "Tema",
    language: "Idioma",
    recon: "Servidor simulado",
    accept: "Aceptar",
    reject: "Rechazar",
    reconHelp:
      "Arrastra un evento (o redimensiónalo en Semana/Día). El servidor simulado responde tras un " +
      "instante: en “Aceptar” confirma el cambio; en “Rechazar” lo revierte con la animación de rollback.",
    lastAction: "Última interacción",
    idle: "Arrastra, redimensiona o haz clic en un evento para verlo aquí.",
    reset: "Restablecer datos",
    quickstartTitle: "Empieza en 3 pasos",
    step1: "Instala el paquete y sus pares de React.",
    step2: "Importa el componente listo para reconciliar.",
    step3: "Pásale tus eventos y una función de mutación.",
    quickstartNote:
      "En este demo el componente se consume desde el monorepo (workspace). La publicación a npm " +
      "acompaña al anuncio público.",
    repo: "Ver el código en GitHub",
    views: { month: "Mes", week: "Semana", day: "Día", list: "Agenda" },
    presets: { light: "Claro", dark: "Oscuro", midnight: "Medianoche", high_contrast: "Alto contraste" },
    footer: "Proyecto de código abierto (MIT). Pre-alpha: la API puede cambiar.",
  },
  en: {
    tagline: "Open-source calendar and appointment-scheduling infrastructure (MIT).",
    lead:
      "A headless-first component: a pure geometry engine + a React layer. Four views, " +
      "drag / resize / select, optimistic reconciliation, token theming, and i18n.",
    view: "View",
    theme: "Theme",
    language: "Language",
    recon: "Mock server",
    accept: "Accept",
    reject: "Reject",
    reconHelp:
      "Drag an event (or resize it in Week/Day). The mock server answers after a beat: on “Accept” " +
      "it commits the change; on “Reject” it reverts it with the rollback animation.",
    lastAction: "Last interaction",
    idle: "Drag, resize, or click an event to see it here.",
    reset: "Reset data",
    quickstartTitle: "Get started in 3 steps",
    step1: "Install the package and its React peers.",
    step2: "Import the batteries-included reconciling component.",
    step3: "Hand it your events and a mutate function.",
    quickstartNote:
      "In this demo the component is consumed from the monorepo (workspace). npm publishing ships " +
      "with the public announcement.",
    repo: "View the source on GitHub",
    views: { month: "Month", week: "Week", day: "Day", list: "Agenda" },
    presets: { light: "Light", dark: "Dark", midnight: "Midnight", high_contrast: "High contrast" },
    footer: "Open-source project (MIT). Pre-alpha: the API may change.",
  },
} as const;

const CODE_INSTALL = "pnpm add @aethercal/calendar-react react react-dom";
const CODE_IMPORT = 'import { OptimisticCalendar } from "@aethercal/calendar-react";';
const CODE_USE = `<OptimisticCalendar
  view="week"
  locale="es"
  theme="light"
  events={events}
  mutate={async ({ payload }) => {
    const saved = await api.reschedule(payload); // your backend
    return { id: saved.id, start: saved.start, end: saved.end, revision: saved.revision };
  }}
/>`;

function isDarkPreset(preset: ThemePreset): boolean {
  return preset === "dark" || preset === "midnight";
}

function initialPreset(): ThemePreset {
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

interface SegmentedProps<T extends string> {
  legend: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}

function Segmented<T extends string>({ legend, value, options, onChange }: SegmentedProps<T>): React.JSX.Element {
  return (
    <fieldset className="demo-segmented">
      <legend>{legend}</legend>
      <div className="demo-segmented-track" role="group" aria-label={legend}>
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className="demo-seg"
            aria-pressed={opt.value === value}
            onClick={() => onChange(opt.value)}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function App(): React.JSX.Element {
  const today = React.useMemo(() => new Date(), []);
  const [events, setEvents] = React.useState<CalendarEvent[]>(() => buildSampleEvents(today));
  const [view, setView] = React.useState<CalendarView>("month");
  const [preset, setPreset] = React.useState<ThemePreset>(initialPreset);
  const [locale, setLocale] = React.useState<Locale>("es");
  const [reject, setReject] = React.useState(false);
  const [lastAction, setLastAction] = React.useState<string | null>(null);
  // Bumped on reset to remount the calendar (dropping its in-flight optimistic state + timers).
  const [resetNonce, setResetNonce] = React.useState(0);

  const t = UI[locale];

  // Refs so the stable `mutate`/`generateId` callbacks see the latest toggles without re-subscribing.
  const rejectRef = React.useRef(reject);
  rejectRef.current = reject;
  const revRef = React.useRef(1);
  const idRef = React.useRef(0);
  // Generation counter: bumped on reset so a mutation already in flight can detect that the board
  // was reset underneath it and drop its write instead of corrupting the fresh data.
  const genRef = React.useRef(0);

  const titleOf = React.useCallback(
    (id: string): string => events.find((e) => e.id === id)?.title ?? id,
    [events],
  );

  // The mocked server: an async mutation resolved (or rejected) client-side, so the demo needs no
  // backend. On accept it advances the authoritative events with a fresh monotonic revision — the
  // same shape a real server returns — which lets the reconciliation layer commit and prune.
  const mutate = React.useCallback(async (mutation: CalendarMutation): Promise<MutationResult> => {
    const { payload } = mutation;
    // Snapshot the decision inputs at SUBMIT time, so the outcome is fixed when the gesture is made
    // (like a real server deciding on the request) instead of drifting if the user flips a toggle
    // during the simulated round-trip.
    const gen = genRef.current;
    const shouldReject = rejectRef.current;
    await sleep(650);
    if (shouldReject) {
      throw new Error("mock-server-rejected");
    }
    const revision = (revRef.current += 1);
    // If the board was reset while this mutation was in flight, drop the write: the calendar was
    // remounted with fresh data, so committing a stale move here would silently relocate an event.
    if (genRef.current === gen) {
      setEvents((prev) =>
        prev.map((e) =>
          e.id === payload.id ? { ...e, start: payload.start, end: payload.end, revision } : e,
        ),
      );
    }
    return { id: payload.id, start: payload.start, end: payload.end, revision };
  }, []);

  // Deterministic idempotency ids (avoids crypto.randomUUID's secure-context requirement on plain http).
  const generateId = React.useCallback((): string => `cm-${(idRef.current += 1)}`, []);

  const onRangeSelect = React.useCallback(
    (p: RangeSelectPayload) => {
      const suffix = p.allDay ? (locale === "es" ? " (todo el día)" : " (all day)") : "";
      setLastAction(`${locale === "es" ? "Selección" : "Selection"}: ${p.start} → ${p.end}${suffix}`);
    },
    [locale],
  );
  const onEventClick = React.useCallback(
    (p: EventClickPayload) =>
      setLastAction(`${locale === "es" ? "Evento" : "Event"}: ${titleOf(p.id)}`),
    [locale, titleOf],
  );
  const onContextMenu = React.useCallback(
    (p: ContextMenuPayload) =>
      setLastAction(
        `${locale === "es" ? "Menú contextual" : "Context menu"}: ${p.id ? titleOf(p.id) : p.start}`,
      ),
    [locale, titleOf],
  );

  const resetData = React.useCallback(() => {
    genRef.current += 1;
    revRef.current = 1;
    // Rebuild from the SAME immutable anchor the calendar renders (`today`), so the this-week cluster
    // stays aligned with the visible range even if the wall-clock day rolled over since mount.
    setEvents(buildSampleEvents(today));
    setLastAction(null);
    setResetNonce((n) => n + 1);
  }, [today]);

  const rootStyle = PRESETS[preset];

  return (
    <div
      className={`demo-root ${isDarkPreset(preset) ? "demo-mode-dark" : "demo-mode-light"}`}
      style={rootStyle}
    >
      <div className="demo-shell">
        <header className="demo-header">
          <div className="demo-brand">
            <span className="demo-logo" aria-hidden="true" />
            <span className="demo-wordmark">AetherCal</span>
          </div>
          <a className="demo-repo-link" href={REPO_URL} target="_blank" rel="noreferrer noopener">
            {t.repo}
          </a>
        </header>

        <section className="demo-hero">
          <h1 className="demo-title">{t.tagline}</h1>
          <p className="demo-lead">{t.lead}</p>
        </section>

        <div className="demo-controls" role="region" aria-label={locale === "es" ? "Controles" : "Controls"}>
          <Segmented
            legend={t.view}
            value={view}
            options={VIEWS.map((v) => ({ value: v, label: t.views[v] }))}
            onChange={setView}
          />
          <Segmented
            legend={t.theme}
            value={preset}
            options={PRESET_NAMES.map((p) => ({ value: p, label: t.presets[p] }))}
            onChange={setPreset}
          />
          <Segmented
            legend={t.language}
            value={locale}
            options={[
              { value: "es", label: "ES" },
              { value: "en", label: "EN" },
            ]}
            onChange={setLocale}
          />
          <Segmented
            legend={t.recon}
            value={reject ? "reject" : "accept"}
            options={[
              { value: "accept", label: t.accept },
              { value: "reject", label: t.reject },
            ]}
            onChange={(mode) => setReject(mode === "reject")}
          />
        </div>

        <p className="demo-hint">{t.reconHelp}</p>

        <main className="demo-calendar-wrap">
          <div className="demo-calendar-scroll">
            <OptimisticCalendar
              key={resetNonce}
              events={events}
              mutate={mutate}
              generateId={generateId}
              view={view}
              locale={locale}
              theme={preset}
              anchor={today}
              firstDayOfWeek={1}
              dayStartHour={6}
              dayEndHour={24}
              rollbackFlashMs={1100}
              onRangeSelect={onRangeSelect}
              onEventClick={onEventClick}
              onContextMenu={onContextMenu}
            />
          </div>
        </main>

        <div className="demo-status" aria-live="polite">
          <div>
            <span className="demo-status-label">{t.lastAction}</span>
            <span className="demo-status-value">{lastAction ?? t.idle}</span>
          </div>
          <button type="button" className="demo-reset" onClick={resetData}>
            {t.reset}
          </button>
        </div>

        <section className="demo-quickstart">
          <h2 className="demo-section-title">{t.quickstartTitle}</h2>
          <ol className="demo-steps">
            <li>
              <p className="demo-step-text">{t.step1}</p>
              <pre className="demo-code">
                <code>{CODE_INSTALL}</code>
              </pre>
            </li>
            <li>
              <p className="demo-step-text">{t.step2}</p>
              <pre className="demo-code">
                <code>{CODE_IMPORT}</code>
              </pre>
            </li>
            <li>
              <p className="demo-step-text">{t.step3}</p>
              <pre className="demo-code">
                <code>{CODE_USE}</code>
              </pre>
            </li>
          </ol>
          <p className="demo-note">{t.quickstartNote}</p>
          <a className="demo-cta" href={REPO_URL} target="_blank" rel="noreferrer noopener">
            {t.repo}
          </a>
        </section>

        <footer className="demo-footer">{t.footer}</footer>
      </div>
    </div>
  );
}

export default App;
