/**
 * No-show (RF-25): marking it must NOT free the slot.
 *
 * The appointment time has passed; releasing the slot would corrupt history and permit a retroactive
 * booking over it. `Booking.occupies` is "status is not CANCELLED", so `no_show` occupies by
 * construction — and the partial index's `WHERE status <> 'cancelled'` predicate stays untouched.
 *
 * ⚠️ PENDING — NOT skipped-and-forgotten. The domain half exists in `main`
 * (`services/bookings.py::mark_no_show`, `BookingStatus.NO_SHOW`, migration `0005`), but **no
 * surface exposes it**: there is no `POST /api/v1/bookings/{id}/no-show` route in
 * `apps/server/.../api/bookings.py`, and the admin's no-show button is Wave 2. An end-to-end test
 * drives real surfaces — so this one cannot run yet, and it is marked `fixme` rather than quietly
 * asserting something weaker that would pass.
 *
 * TO ENABLE: land the route, delete the `test.fixme` call. The body below is the assertion set,
 * written against the contract the design doc specifies — not invented behaviour.
 */

import { expect, test } from "@playwright/test";

import { Api } from "../src/api.js";
import { runContext, stackConfig } from "../src/stack.js";

const stack = stackConfig();
const run = runContext();
const api = new Api(stack);

test("marking a booking as a no-show keeps its slot taken", async () => {
  test.fixme(
    true,
    "RF-25: no surface marks a no-show yet (no POST /api/v1/bookings/{id}/no-show route)",
  );

  const offered = await api.offeredStarts(run.eventTypeId);
  const start = offered.at(-1)!;

  const booking = await api.createBooking({
    event_type_id: run.eventTypeId,
    start,
    guest_name: "No Show",
    guest_email: `noshow-${run.runId}@e2e.test`,
    guest_timezone: "UTC",
    locale: "en",
  });
  expect(booking.status).toBe("confirmed");

  // The surface this test is waiting for.
  const response = await fetch(`${stack.apiUrl}/api/v1/bookings/${booking.id}/no-show`, {
    method: "POST",
    headers: { Authorization: `Bearer ${stack.apiKey}` },
  });
  expect(response.status).toBe(200);
  expect((await api.booking(booking.id)).status).toBe("no_show");

  // The whole point: a no-show still OCCUPIES its slot. If this slot came back on offer, someone
  // could book over an appointment that already happened.
  const afterNoShow = await api.offeredStarts(run.eventTypeId);
  expect(
    afterNoShow,
    "a no-show released its slot — a retroactive booking is now possible",
  ).not.toContain(start);
});
