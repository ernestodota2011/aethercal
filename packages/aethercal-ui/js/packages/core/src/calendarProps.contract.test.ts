/**
 * Cross-language contract lock (AetherCal-06 §4, F2-D).
 *
 * `calendar-props.schema.json` is GENERATED from the Python payload TypedDicts (the single source of
 * truth; a Python drift test keeps it in sync with Python). This test closes the loop from the
 * TypeScript side: representative payloads — each `satisfies` its TS type, so a renamed/removed field
 * fails to compile — are validated against the schema, and every `$def`'s field-name set is asserted
 * equal to its exhaustive TS sample. Python == schema (Python drift test) and schema == TS (here),
 * so the two languages cannot silently drift apart.
 */
import { describe, expect, it } from "vitest";
import schemaJson from "./calendar-props.schema.json";
import type {
  CalendarEvent,
  ContextMenuPayload,
  EventClickPayload,
  EventDropPayload,
  EventResizePayload,
  RangeSelectPayload,
  ViewChangePayload,
} from "./types";

/* eslint-disable @typescript-eslint/no-explicit-any */
type JsonSchema = any;

// The imported JSON is typed as a narrow literal; treat it as a generic schema doc for dynamic access.
const schema: JsonSchema = schemaJson;

/** Minimal JSON-Schema validator for the subset this contract emits (object/scalar/array/$ref). */
function validate(node: JsonSchema, value: unknown, root: JsonSchema, path = "$"): string[] {
  if (node.$ref) {
    const name = String(node.$ref).split("/").pop() as string;
    return validate(root.$defs[name], value, root, path);
  }
  const errs: string[] = [];
  switch (node.type) {
    case "object": {
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        return [`${path}: expected object`];
      }
      const obj = value as Record<string, unknown>;
      for (const req of node.required ?? []) {
        if (!(req in obj)) errs.push(`${path}.${req}: missing required`);
      }
      if (typeof node.minProperties === "number" && Object.keys(obj).length < node.minProperties) {
        errs.push(`${path}: expected at least ${node.minProperties} propertie(s)`);
      }
      if (node.additionalProperties === false) {
        for (const key of Object.keys(obj)) {
          if (!(key in (node.properties ?? {}))) errs.push(`${path}.${key}: additional property`);
        }
      }
      for (const [key, val] of Object.entries(obj)) {
        const propSchema = node.properties?.[key];
        if (propSchema) errs.push(...validate(propSchema, val, root, `${path}.${key}`));
      }
      break;
    }
    case "array":
      if (!Array.isArray(value)) return [`${path}: expected array`];
      value.forEach((item, i) => errs.push(...validate(node.items, item, root, `${path}[${i}]`)));
      break;
    case "string":
      if (typeof value !== "string") errs.push(`${path}: expected string`);
      break;
    case "integer":
      if (typeof value !== "number" || !Number.isInteger(value)) errs.push(`${path}: expected integer`);
      break;
    case "number":
      if (typeof value !== "number") errs.push(`${path}: expected number`);
      break;
    case "boolean":
      if (typeof value !== "boolean") errs.push(`${path}: expected boolean`);
      break;
    default:
      break;
  }
  return errs;
}

// Exhaustive samples — one per $def — each `satisfies` its TS type. `satisfies` makes the compiler
// reject a sample with an unknown/misnamed field, so these are the TS-side field manifest.
const drop = { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00", revision: 2, client_mutation_id: "cm" } satisfies EventDropPayload;
const resize = { id: "e1", start: "2026-07-15T10:00:00", end: "2026-07-15T12:30:00", revision: 3, client_mutation_id: "cm" } satisfies EventResizePayload;
const range = { start: "2026-07-15T09:00:00", end: "2026-07-15T10:00:00", allDay: false } satisfies RangeSelectPayload;
const click = { id: "e1" } satisfies EventClickPayload;
const menu = { id: "e1", start: "2026-07-15T09:00:00" } satisfies ContextMenuPayload;
const viewChange = { view: "week", from: "2026-07-13", to: "2026-07-19" } satisfies ViewChangePayload;
const event = { id: "e1", title: "Consult", start: "2026-07-15T10:00:00", end: "2026-07-15T11:00:00", allDay: false, color: "#64748b", editable: true, revision: 1 } satisfies CalendarEvent;

// Every $def with a TS-typed sample that carries all of its fields (so each field's runtime value
// reflects its TS type). Used to lock field TYPES, not just names/optionality, against the schema.
const SAMPLES: Record<string, Record<string, unknown>> = {
  CalendarEvent: event,
  EventDropPayload: drop,
  EventResizePayload: resize,
  RangeSelectPayload: range,
  EventClickPayload: click,
  ContextMenuPayload: menu,
  ViewChangePayload: viewChange,
};

/** The JSON-Schema `type` a runtime value maps to (integers distinguished from other numbers). */
function jsonTypeOf(value: unknown): string {
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "string") return "string";
  return "unknown";
}

/**
 * Compile-time EXHAUSTIVE key tuple: the compiler rejects a tuple that omits ANY key of `T`
 * (including optional ones). So adding a field to a TS payload type WITHOUT adding it here fails
 * `pnpm typecheck` — closing the gap where a TS-only optional field could drift from the schema.
 */
const keysOf =
  <T>() =>
  <U extends readonly (keyof T)[]>(
    keys: U & ([keyof T] extends [U[number]] ? unknown : never),
  ): U =>
    keys;

// Regression proof that the exhaustive check bites: omitting a key (here `revision`) MUST be a
// compile error. `@ts-expect-error` fails typecheck if the line does NOT error — i.e. if the lock
// ever stops detecting a missing/added field, `pnpm typecheck` breaks here.
// @ts-expect-error - an incomplete key tuple must not type-check
void keysOf<EventDropPayload>()(["id", "start", "end", "client_mutation_id"]);

// Every payload type's full key set, exhaustively pinned by the compiler.
const KEY_TUPLES: Record<string, readonly string[]> = {
  CalendarEvent: keysOf<CalendarEvent>()(["id", "title", "start", "end", "allDay", "color", "editable", "revision"]),
  EventDropPayload: keysOf<EventDropPayload>()(["id", "start", "end", "revision", "client_mutation_id"]),
  EventResizePayload: keysOf<EventResizePayload>()(["id", "start", "end", "revision", "client_mutation_id"]),
  RangeSelectPayload: keysOf<RangeSelectPayload>()(["start", "end", "allDay"]),
  EventClickPayload: keysOf<EventClickPayload>()(["id"]),
  ContextMenuPayload: keysOf<ContextMenuPayload>()(["id", "start"]),
  ViewChangePayload: keysOf<ViewChangePayload>()(["view", "from", "to"]),
};

// The REQUIRED keys of T (a property without `?`). Optionality-off config: an optional property's
// value includes `undefined`, a required one's does not.
type RequiredKeys<T> = { [K in keyof T]-?: undefined extends T[K] ? never : K }[keyof T];

// Exhaustive REQUIRED-key tuple: the compiler rejects a tuple that omits any required key of T. So
// flipping a field between required and optional in TS changes RequiredKeys<T> and forces its tuple
// to change — then the assertion below catches any divergence from the schema's own `required` set.
const requiredKeysOf =
  <T>() =>
  <U extends readonly RequiredKeys<T>[]>(
    keys: U & ([RequiredKeys<T>] extends [U[number]] ? unknown : never),
  ): U =>
    keys;

// @ts-expect-error - omitting a required key (here `end`) must not type-check
void requiredKeysOf<EventDropPayload>()(["id", "start"]);

// Which keys each payload requires — pinned by the compiler so TS optionality can't drift.
const REQUIRED_TUPLES: Record<string, readonly string[]> = {
  CalendarEvent: requiredKeysOf<CalendarEvent>()(["id", "title", "start", "end"]),
  EventDropPayload: requiredKeysOf<EventDropPayload>()(["id", "start", "end"]),
  EventResizePayload: requiredKeysOf<EventResizePayload>()(["id", "start", "end"]),
  RangeSelectPayload: requiredKeysOf<RangeSelectPayload>()(["start", "end", "allDay"]),
  EventClickPayload: requiredKeysOf<EventClickPayload>()(["id"]),
  // ContextMenuPayload is an at-least-one UNION: no field is required in every branch, so the schema
  // has no flat `required` set (it uses `minProperties: 1` instead). It is excluded from the flat
  // required-keys tuple; its invariant is enforced by the union type + `minProperties` (tested
  // above), and the runtime compare below still catches any schema `required` that appears here.
  ContextMenuPayload: [] as readonly string[],
  ViewChangePayload: requiredKeysOf<ViewChangePayload>()(["view", "from", "to"]),
};

describe("calendar-props contract — TS payloads validate against the generated schema", () => {
  it("validates each representative payload against its $def", () => {
    expect(validate(schema.$defs.EventDropPayload, drop, schema)).toEqual([]);
    expect(validate(schema.$defs.EventResizePayload, resize, schema)).toEqual([]);
    expect(validate(schema.$defs.RangeSelectPayload, range, schema)).toEqual([]);
    expect(validate(schema.$defs.EventClickPayload, click, schema)).toEqual([]);
    expect(validate(schema.$defs.ContextMenuPayload, menu, schema)).toEqual([]);
    expect(validate(schema.$defs.ViewChangePayload, viewChange, schema)).toEqual([]);
    expect(validate(schema.$defs.CalendarEvent, event, schema)).toEqual([]);
  });

  it("accepts a minimal payload that omits the optional fields", () => {
    const minimalDrop = { id: "e1", start: "2026-07-16T10:00:00", end: "2026-07-16T11:00:00" } satisfies EventDropPayload;
    expect(validate(schema.$defs.EventDropPayload, minimalDrop, schema)).toEqual([]);
    const minimalMenu = { id: "e1" } satisfies ContextMenuPayload;
    expect(validate(schema.$defs.ContextMenuPayload, minimalMenu, schema)).toEqual([]);
  });

  it("rejects an unknown field (additionalProperties: false)", () => {
    expect(validate(schema.$defs.EventClickPayload, { id: "e1", bogus: 1 }, schema).length).toBeGreaterThan(0);
  });

  it("rejects a payload missing a required field", () => {
    expect(validate(schema.$defs.EventDropPayload, { id: "e1", start: "x" }, schema).length).toBeGreaterThan(0);
  });

  it("rejects the empty context-menu payload (minProperties: 1 — at least one of id/start)", () => {
    expect(validate(schema.$defs.ContextMenuPayload, {}, schema).length).toBeGreaterThan(0);
    expect(validate(schema.$defs.ContextMenuPayload, { id: "e1" }, schema)).toEqual([]);
    expect(validate(schema.$defs.ContextMenuPayload, { start: "2026-07-15T09:00:00" }, schema)).toEqual([]);
  });
});

describe("calendar-props contract — cross-language field-name lock", () => {
  it("every outbound event maps to a defined $def", () => {
    for (const [name, ref] of Object.entries(schema.events)) {
      const def = String((ref as { $ref: string }).$ref).split("/").pop() as string;
      expect(schema.$defs[def], `${name} -> ${def}`).toBeDefined();
    }
  });

  it("each $def's field names exactly match its TS type's exhaustive key set", () => {
    for (const [name, keys] of Object.entries(KEY_TUPLES)) {
      const schemaKeys = Object.keys(schema.$defs[name].properties).sort();
      expect(schemaKeys, `field-name drift in ${name}`).toEqual([...keys].sort());
    }
  });

  it("each $def's REQUIRED field set matches the TS type's required keys (optionality lock)", () => {
    for (const [name, keys] of Object.entries(REQUIRED_TUPLES)) {
      const schemaRequired = [...((schema.$defs[name].required as string[] | undefined) ?? [])].sort();
      expect(schemaRequired, `optionality drift in ${name}`).toEqual([...keys].sort());
    }
  });

  it("each field's schema TYPE matches its TS-typed sample value's JSON type", () => {
    // The samples `satisfies` their TS types, so a TS field type change forces the sample value to
    // change (or fail to compile), and this assertion then catches any divergence from the schema.
    for (const [name, sample] of Object.entries(SAMPLES)) {
      for (const [key, value] of Object.entries(sample)) {
        expect(schema.$defs[name].properties[key].type, `type drift in ${name}.${key}`).toBe(
          jsonTypeOf(value),
        );
      }
    }
  });

  it("fixes the revision semantics in the contract (F2-D acceptance criterion)", () => {
    expect(typeof schema.revisionSemantics).toBe("string");
    expect(schema.revisionSemantics).toMatch(/monotonic/i);
    expect(schema.revisionSemantics).toMatch(/discard/i);
  });
});
