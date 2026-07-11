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

const SAMPLES: Record<string, object> = {
  CalendarEvent: event,
  EventDropPayload: drop,
  EventResizePayload: resize,
  RangeSelectPayload: range,
  EventClickPayload: click,
  ContextMenuPayload: menu,
  ViewChangePayload: viewChange,
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
});

describe("calendar-props contract — cross-language field-name lock", () => {
  it("every outbound event maps to a defined $def", () => {
    for (const [name, ref] of Object.entries(schema.events)) {
      const def = String((ref as { $ref: string }).$ref).split("/").pop() as string;
      expect(schema.$defs[def], `${name} -> ${def}`).toBeDefined();
    }
  });

  it("each $def's field names exactly match its TS type's field names", () => {
    for (const [name, sample] of Object.entries(SAMPLES)) {
      const schemaKeys = Object.keys(schema.$defs[name].properties).sort();
      expect(schemaKeys, `field-name drift in ${name}`).toEqual(Object.keys(sample).sort());
    }
  });

  it("fixes the revision semantics in the contract (F2-D acceptance criterion)", () => {
    expect(typeof schema.revisionSemantics).toBe("string");
    expect(schema.revisionSemantics).toMatch(/monotonic/i);
    expect(schema.revisionSemantics).toMatch(/discard/i);
  });
});
