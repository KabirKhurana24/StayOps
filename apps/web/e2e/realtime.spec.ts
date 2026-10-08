import { expect, test } from "@playwright/test";

import { createReservationViaUi, editDatesViaUi, openReservation, requireDemoCredentials, shiftDate, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

test("create, reschedule and check in from browser A; browser B's table and calendar follow without a refresh", async ({ browser }) => {
  test.setTimeout(240_000);
  // Two isolated browser sessions (separate cookies), same organization.
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const tableB = await contextB.newPage();

  await signIn(pageA);
  await signIn(tableB);
  const booking = uniqueBooking();

  // Browser B watches a filtered reservations table (initially empty) …
  await tableB.goto("/reservations");
  await expect(tableB.getByTestId("realtime-status")).toHaveAttribute("data-status", "live");
  await tableB.getByLabel("Search reservations").fill(booking.lastName);
  await expect(tableB.getByText("No reservations match these filters")).toBeVisible();
  // … and the calendar for the booking's month, in a second tab of the same session.
  const calendarB = await contextB.newPage();
  await calendarB.goto(`/calendar?date=${booking.checkIn}`);
  await expect(calendarB.getByTestId("calendar-grid")).toBeVisible();
  await expect(calendarB.getByTestId("realtime-status")).toHaveAttribute("data-status", "live");

  let reloads = 0;
  tableB.on("load", () => reloads++);
  calendarB.on("load", () => reloads++);

  // 1) A creates the reservation through the UI.
  await expect(pageA.getByTestId("realtime-status")).toHaveAttribute("data-status", "live");
  await createReservationViaUi(pageA, booking);

  const rowB = tableB.getByTestId("reservation-row").filter({ hasText: booking.lastName });
  await expect(rowB).toHaveCount(1, { timeout: 20_000 });
  await expect(rowB).toHaveAttribute("data-check-in", booking.checkIn);
  const barB = calendarB.getByTestId("calendar-bar").filter({ hasText: booking.lastName });
  await expect(barB).toHaveCount(1, { timeout: 20_000 });
  await expect(barB).toHaveAttribute("data-check-in", booking.checkIn);

  // 2) A moves the stay one day later.
  const moved = { checkIn: shiftDate(booking.checkIn, 1), checkOut: shiftDate(booking.checkOut, 1) };
  await pageA.goto("/reservations");
  await openReservation(pageA, booking.lastName);
  await editDatesViaUi(pageA, moved.checkIn, moved.checkOut);

  await expect(rowB).toHaveAttribute("data-check-in", moved.checkIn, { timeout: 20_000 });
  await expect(barB).toHaveAttribute("data-check-in", moved.checkIn, { timeout: 20_000 });
  await expect(barB).toHaveAttribute("data-check-out", moved.checkOut);

  // 3) A checks the guest in.
  await pageA.getByRole("button", { name: "Check in" }).click();
  await expect(pageA.getByText(/Check in: E2E/)).toBeVisible();

  await expect(rowB).toContainText("Checked in", { timeout: 20_000 });
  await expect(barB).toHaveAttribute("data-status", "CHECKED_IN", { timeout: 20_000 });

  expect(reloads).toBe(0);
  await contextA.close();
  await contextB.close();
});
