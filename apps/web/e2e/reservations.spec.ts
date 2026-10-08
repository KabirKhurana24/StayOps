import { expect, test } from "@playwright/test";

import { createReservationViaUi, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

test("sign in, view Command Center, create a reservation, see it listed", async ({ page }) => {
  await signIn(page);

  // Command Center communicates operational state from seeded, persisted data.
  await expect(page.getByTestId("org-name")).toHaveText("Canal Stays");
  await expect(page.getByRole("region", { name: "Today" })).toBeVisible();
  // Alerts are listed under "Needs attention" (below); the strip carries the operations metrics.
  for (const metric of [
    "metric-check-ins",
    "metric-check-outs",
    "metric-open-tasks",
    "metric-overdue-tasks",
    "metric-active-maintenance",
    "metric-blocked-units",
    "metric-occupied",
    "metric-revenue",
  ]) {
    await expect(page.getByTestId(metric)).toBeVisible();
  }
  for (const section of ["Today's operations", "Needs attention", "Recent activity", "Operational overview", "Check-ins", "Check-outs"]) {
    await expect(page.getByText(section, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByTestId("signal").first()).toBeVisible();

  const booking = uniqueBooking();
  await createReservationViaUi(page, booking);

  // The new reservation is persisted and shows up in the reservations list.
  await page.getByRole("link", { name: "Reservations" }).click();
  await page.getByLabel("Search reservations").fill(booking.lastName);
  const row = page.getByTestId("reservation-row").filter({ hasText: booking.lastName });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Canal View Apartments");
  await expect(row).toContainText("Confirmed");

  // Detail view shows the audit timeline for the creation.
  await row.click();
  await expect(page.getByRole("heading", { name: `E2E ${booking.lastName}` })).toBeVisible();
  await expect(page.getByTestId("timeline-entry").first()).toContainText("created reservation");
});
