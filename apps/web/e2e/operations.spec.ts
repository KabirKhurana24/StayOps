import { expect as baseExpect, type Page, test } from "@playwright/test";

import { type Booking, createReservationViaUi, openReservation, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

// Each step waits on API + worker + realtime round-trips (several remote DB calls each).
const ASYNC_TIMEOUT = 60_000;
const expect = baseExpect.configure({ timeout: ASYNC_TIMEOUT });

const PROPERTY = "Canal View Apartments";
const UNIT = "201";
const CLEANER = "Maria Santos"; // seeded staff member

async function metricValue(page: Page, testId: string): Promise<number> {
  const metric = page.getByTestId(testId);
  await expect(metric).toHaveAttribute("data-value", /\d+/);
  return Number(await metric.getAttribute("data-value"));
}

/** Opens the property's Units tab and returns the row for `unit`. */
async function unitRow(page: Page, unit: string) {
  await page.goto("/properties");
  await page.getByTestId("property-row").filter({ hasText: PROPERTY }).first().click();
  await expect(page).toHaveURL(/\/properties\/[0-9a-f-]+/);
  await page.goto(`${page.url().split("?")[0]}?tab=units`);
  const row = page.locator(`[data-testid="unit-row"][data-unit-name="${unit}"]`);
  await expect(row).toBeVisible();
  return row;
}

/** Opens the new-reservation form for `booking`'s dates and returns `unit`'s availability entry. */
async function availabilityFor(page: Page, booking: Booking, unit: string) {
  await page.goto("/reservations");
  await page.getByRole("button", { name: "New reservation" }).first().click();
  const form = page.getByRole("form", { name: "Reservation form" });
  await form.getByLabel("Property").selectOption({ label: PROPERTY });
  await form.getByLabel("Check-in", { exact: true }).fill(booking.checkIn);
  await form.getByLabel("Check-out", { exact: true }).fill(booking.checkOut);
  const entry = form.getByTestId("unit-availability").getByRole("button", { name: new RegExp(`^${unit} `) });
  await expect(entry).toBeVisible();
  return { form, entry };
}

test("checkout → automatic turnover cleaning → issue blocks the unit → resolved → bookable again", async ({ page }) => {
  test.setTimeout(480_000);
  await signIn(page);
  const blockedBefore = await metricValue(page, "metric-blocked-units");

  // 1) Book, check in and check out a stay in the unit.
  const booking = uniqueBooking();
  await page.goto("/reservations");
  await createReservationViaUi(page, booking, UNIT);
  await openReservation(page, booking.lastName);
  await page.getByRole("button", { name: "Check in" }).click();
  await expect(page.getByText(/Check in: E2E/)).toBeVisible();
  await page.getByRole("button", { name: "Check out" }).click();
  await expect(page.getByText(/Check out: E2E/)).toBeVisible();

  // 2) The worker creates the turnover cleaning — nobody creates it by hand.
  const turnover = page.locator('[data-testid="reservation-task"][data-task-type="TURNOVER_CLEANING"]');
  await expect(turnover).toHaveCount(1, { timeout: ASYNC_TIMEOUT });
  await turnover.click();
  const detail = page.getByTestId("cleaning-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toHaveAttribute("data-status", "CREATED");

  // 3) Assign a cleaner, start, tick the persisted checklist, complete.
  await detail.getByRole("button", { name: "Assign" }).click();
  await page.getByTestId("assignee-option").filter({ hasText: CLEANER }).click();
  await expect(page.getByText(`Assigned to ${CLEANER}`)).toBeVisible();
  await expect(detail).toHaveAttribute("data-status", "ASSIGNED", { timeout: ASYNC_TIMEOUT });

  await detail.getByRole("button", { name: "Start cleaning" }).click();
  await expect(detail).toHaveAttribute("data-status", "IN_PROGRESS", { timeout: ASYNC_TIMEOUT });

  const items = detail.getByTestId("checklist-item");
  const total = await items.count();
  expect(total).toBe(7);
  for (let i = 0; i < total; i++) {
    const saved = page.waitForResponse((r) => /\/api\/v1\/cleanings\/[0-9a-f-]+$/.test(r.url()) && r.request().method() === "PATCH");
    await items.nth(i).click();
    expect((await saved).status()).toBe(200);
    await expect(items.nth(i)).toBeChecked();
    await expect(detail.getByTestId("checklist-progress")).toHaveText(`${i + 1}/${total}`);
  }
  // Persisted server-side, not just component state.
  await page.reload();
  await expect(detail.getByTestId("checklist-progress")).toHaveText(`${total}/${total}`);
  await detail.getByRole("button", { name: "Complete cleaning" }).click();
  // COMPLETED, then the worker requests inspection.
  await expect(detail).toHaveAttribute("data-status", "INSPECTION_REQUIRED", { timeout: ASYNC_TIMEOUT });

  // 4) Report a problem found during the cleaning; it takes the unit out of availability.
  const issue = `AC not working ${booking.lastName}`;
  await detail.getByRole("button", { name: "Report issue" }).click();
  const woForm = page.getByRole("form", { name: "Work order form" });
  await woForm.getByLabel("Issue").fill(issue);
  await woForm.getByLabel("Category").selectOption("HVAC");
  await woForm.getByLabel("Priority").selectOption("HIGH");
  await woForm.getByLabel("Block unit for new bookings").check();
  await woForm.getByRole("button", { name: "Report issue" }).click();
  await expect(page.getByText(`Work order created: ${issue}`)).toBeVisible();

  // 5) The work order exists, traced back to the cleaning, and blocks the unit.
  const related = detail.getByTestId("related-work-orders").getByRole("link", { name: new RegExp(issue) });
  await expect(related).toBeVisible();
  await related.click();
  const wo = page.getByTestId("work-order-detail");
  await expect(wo).toBeVisible();
  await expect(wo).toHaveAttribute("data-unit-blocked", "true");
  await expect(wo.getByTestId("source-task-link")).toBeVisible();
  const workOrderUrl = page.url();

  // 6) Unit operational state: maintenance.
  await expect(await unitRow(page, UNIT)).toHaveAttribute("data-ops-status", "MAINTENANCE");

  // 7) A new reservation can't use the blocked unit.
  const next = uniqueBooking();
  const blocked = await availabilityFor(page, next, UNIT);
  await expect(blocked.entry).toHaveAttribute("data-available", "false");
  await expect(blocked.entry).toContainText(/blocked by maintenance/i);
  await blocked.form.getByLabel("Unit", { exact: true }).selectOption({ label: UNIT });
  await expect(blocked.form.getByRole("button", { name: "Create reservation" })).toBeDisabled();
  await page.keyboard.press("Escape");

  // 8) Command Center: one more blocked unit, flagged in Needs attention.
  await page.goto("/");
  await expect(page.getByTestId("metric-blocked-units")).toHaveAttribute("data-value", String(blockedBefore + 1));
  const signal = page.locator('[data-testid="signal"][data-code="blocked_unit"]').filter({ hasText: `${PROPERTY} · ${UNIT}` });
  await expect(signal).toBeVisible();

  // 9) Resolve the work order → the unit is bookable again.
  await page.goto(workOrderUrl);
  await expect(wo).toBeVisible();
  await wo.getByRole("button", { name: "Resolve" }).click();
  await wo.getByLabel("Resolution notes (optional)").fill("Replaced the thermostat.");
  await wo.getByRole("button", { name: "Mark resolved" }).click();
  await expect(page.getByText(`Resolved: ${issue}`)).toBeVisible();
  await expect(wo).toHaveAttribute("data-unit-blocked", "false");

  await expect(await unitRow(page, UNIT)).not.toHaveAttribute("data-ops-status", "MAINTENANCE");
  const free = await availabilityFor(page, next, UNIT);
  await expect(free.entry).toHaveAttribute("data-available", "true");
  await page.keyboard.press("Escape");

  // 10) Command Center reflects the resolution.
  await page.goto("/");
  await expect(page.getByTestId("metric-blocked-units")).toHaveAttribute("data-value", String(blockedBefore));
  await expect(signal).toHaveCount(0);
});
