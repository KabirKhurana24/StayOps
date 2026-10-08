import { expect, test } from "@playwright/test";

import { createReservationViaUi, openReservation, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

test("search and filter the reservation list", async ({ page }) => {
  test.setTimeout(150_000);
  await signIn(page);
  await page.goto("/reservations");
  const rows = page.getByTestId("reservation-row");
  await expect(rows.first()).toBeVisible();

  // Status filter (seed has cancelled stays).
  await page.getByRole("button", { name: "Filter by status" }).click();
  await page.getByRole("menuitemcheckbox", { name: "Cancelled" }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/status=CANCELLED/);
  // Poll: placeholder rows from the previous query stay visible until the refetch lands.
  await expect
    .poll(async () => {
      const statuses = await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
      return statuses.length > 0 && statuses.every((s) => s === "CANCELLED");
    })
    .toBe(true);

  // Property + source filters combine; clearing restores the full list.
  await page.getByRole("button", { name: /Clear/ }).click();
  const everyRow = (needle: string) =>
    expect.poll(async () => {
      const texts = await rows.allInnerTexts();
      return texts.length > 0 && texts.every((t) => t.includes(needle));
    });
  await page.getByLabel("Filter by property").selectOption({ label: "Harbour Lofts" });
  await everyRow("Harbour Lofts").toBe(true);
  await page.getByLabel("Filter by source").selectOption({ label: "Airbnb" });
  await everyRow("Airbnb").toBe(true);
  await everyRow("Harbour Lofts").toBe(true);
  await page.getByRole("button", { name: /Clear/ }).click();

  // Search by guest name.
  await page.getByLabel("Search reservations").fill("Isabella");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Isabella van Dijk");
});

test("create, edit, check in, check out and cancel through the UI", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  await page.goto("/reservations");
  const booking = uniqueBooking();
  await createReservationViaUi(page, booking);

  // Edit: amount and notes, validated and audited.
  await openReservation(page, booking.lastName);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const form = page.getByRole("form", { name: "Reservation form" });
  await form.getByLabel("Total amount").fill("480.00");
  await form.getByLabel("Internal notes").fill("Late arrival, keys in lockbox");
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText(/Saved SO-/)).toBeVisible();
  await expect(page.getByText("Late arrival, keys in lockbox")).toBeVisible();
  await expect(page.getByText("€480.00").first()).toBeVisible();

  // Check in → check out, each a recorded lifecycle event.
  await page.getByRole("button", { name: "Check in" }).click();
  await expect(page.getByText(/Check in: E2E/)).toBeVisible();
  await page.getByRole("button", { name: "Check out" }).click();
  await expect(page.getByText(/Check out: E2E/)).toBeVisible();
  const timeline = page.getByTestId("timeline-entry");
  await expect(timeline.filter({ hasText: "Guest checked out" })).toHaveCount(1);
  await expect(timeline.filter({ hasText: "Guest checked in" })).toHaveCount(1);
  await expect(timeline.filter({ hasText: "Amount" })).toHaveCount(1);
  await page.keyboard.press("Escape");

  // Cancel a second reservation from the row menu, with confirmation.
  const second = uniqueBooking();
  await createReservationViaUi(page, second);
  await page.getByLabel("Search reservations").fill(second.lastName);
  const row = page.getByTestId("reservation-row").filter({ hasText: second.lastName });
  await row.getByRole("button", { name: /Actions for/ }).click();
  await page.getByRole("menuitem", { name: "Cancel reservation" }).click();
  await expect(page.getByRole("alertdialog")).toContainText(second.lastName);
  await page.getByRole("button", { name: "Cancel reservation" }).click();
  await expect(page.getByText(/Cancel: E2E/)).toBeVisible();
  await expect(row).toHaveAttribute("data-status", "CANCELLED");
});

test("the form blocks a booking that overlaps an existing stay", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  await page.goto("/reservations");
  const booking = uniqueBooking();
  await createReservationViaUi(page, booking, "201");

  await page.getByRole("button", { name: "New reservation" }).first().click();
  const form = page.getByRole("form", { name: "Reservation form" });
  await form.getByLabel("Property").selectOption({ label: "Canal View Apartments" });
  await form.getByLabel("Check-in", { exact: true }).fill(booking.checkIn);
  await form.getByLabel("Check-out", { exact: true }).fill(booking.checkOut);
  await form.getByLabel("Unit", { exact: true }).selectOption({ label: "201" });

  const conflict = form.getByTestId("availability-conflict");
  await expect(conflict).toBeVisible();
  await expect(conflict).toContainText("Unit 201 at Canal View Apartments is unavailable for these dates.");
  await expect(conflict).toContainText(`E2E ${booking.lastName}`);
  await expect(form.getByRole("button", { name: "Create reservation" })).toBeDisabled();

  // Picking a free unit from the availability list clears the conflict.
  await form.getByTestId("unit-availability").getByRole("listitem").filter({ hasText: "Available" }).first().getByRole("button").click();
  await expect(conflict).toBeHidden();
});

test("calendar renders stays and switches month/week", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.goto("/calendar");
  const grid = page.getByTestId("calendar-grid");
  await expect(grid).toHaveAttribute("data-mode", "month");
  await expect(page.getByTestId("calendar-unit-row")).toHaveCount(9);
  await expect(page.getByTestId("calendar-bar").first()).toBeVisible();

  const title = page.getByTestId("calendar-title");
  const thisMonth = await title.innerText();
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(title).not.toHaveText(thisMonth);
  await page.getByRole("button", { name: "Today" }).click();
  await expect(title).toHaveText(thisMonth);

  await page.getByRole("radio", { name: "Week" }).click();
  await expect(grid).toHaveAttribute("data-mode", "week");
  await expect(page.locator("[data-testid=calendar-grid] [data-date]")).toHaveCount(7);

  // Clicking a stay opens its details.
  await page.getByTestId("calendar-bar").first().click();
  await expect(page.getByRole("dialog")).toContainText(/Reservation #/);
  await expect(page).toHaveURL(/r=/);
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("navigation drawer and card list on a phone", async ({ page }) => {
    test.setTimeout(120_000);
    await signIn(page);
    await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
    await page.getByRole("button", { name: "Open navigation" }).click();
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav).toBeVisible();
    await nav.getByRole("link", { name: "Reservations" }).click();
    await expect(page).toHaveURL(/\/reservations/);
    await expect(page.getByTestId("reservation-card").first()).toBeVisible();
    await expect(page.getByTestId("reservation-row").first()).toBeHidden();
  });
});
