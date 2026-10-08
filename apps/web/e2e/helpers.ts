import { expect, type Page } from "@playwright/test";

export const DEMO_EMAIL = process.env.SEED_DEMO_EMAIL ?? "demo@stayops.dev";
export const DEMO_PASSWORD = process.env.SEED_DEMO_PASSWORD ?? "";

export function requireDemoCredentials() {
  if (!DEMO_PASSWORD) {
    throw new Error("SEED_DEMO_PASSWORD is not set. Fill .env and run `pnpm db:seed` first.");
  }
}

export async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DEMO_EMAIL);
  await page.getByLabel("Password").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Wait for the redirect away from /login (i.e. a real session) before asserting
  // on the Command Center, so the check can't race the sign-in request.
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByTestId("command-center")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening),/ })).toBeVisible();
}

export const isoDate = (d: Date) => d.toISOString().slice(0, 10);

export function shiftDate(date: string, days: number): string {
  return isoDate(new Date(Date.parse(`${date}T00:00:00Z`) + days * 86_400_000));
}

/**
 * A unique guest + a 2-night window far in the future so runs never collide
 * (earlier runs' bookings stay in the database). Stays start in the first part of
 * a month so a one-day move keeps them in the same calendar month.
 */
export function uniqueBooking() {
  const suffix = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
  const offsetDays = 400 + Math.floor(Math.random() * 25_000);
  const start = new Date(Date.UTC(2026, 0, 1) + offsetDays * 86_400_000);
  if (start.getUTCDate() > 20) start.setUTCDate(start.getUTCDate() - 12);
  const checkIn = isoDate(start);
  return { firstName: "E2E", lastName: `Guest${suffix}`, checkIn, checkOut: shiftDate(checkIn, 2) };
}

export type Booking = ReturnType<typeof uniqueBooking>;

export async function createReservationViaUi(page: Page, booking: Booking, unit = "102") {
  await page.getByRole("button", { name: "New reservation" }).first().click();
  const form = page.getByRole("form", { name: "Reservation form" });
  await expect(form).toBeVisible();
  await form.getByLabel("Property").selectOption({ label: "Canal View Apartments" });
  await expect(form.getByLabel("Unit", { exact: true })).toBeEnabled();
  await form.getByLabel("Check-in", { exact: true }).fill(booking.checkIn);
  await form.getByLabel("Check-out", { exact: true }).fill(booking.checkOut);
  await form.getByLabel("Unit", { exact: true }).selectOption({ label: unit });
  await form.getByRole("button", { name: "+ New guest" }).click();
  await form.getByLabel("First name").fill(booking.firstName);
  await form.getByLabel("Last name").fill(booking.lastName);
  await form.getByLabel("Total amount").fill("420.00");
  await form.getByRole("button", { name: "Create reservation" }).click();
  await expect(page.getByText(/Reservation SO-[A-Z0-9]+ created/)).toBeVisible();
}

/** Opens a reservation's drawer from the (filtered) reservations table. */
export async function openReservation(page: Page, lastName: string) {
  await page.getByLabel("Search reservations").fill(lastName);
  await page.getByTestId("reservation-row").filter({ hasText: lastName }).click();
  await expect(page.getByRole("heading", { level: 2, name: new RegExp(lastName) })).toBeVisible();
}

/** Edits check-in/check-out dates through the drawer's Edit form. */
export async function editDatesViaUi(page: Page, checkIn: string, checkOut: string) {
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const form = page.getByRole("form", { name: "Reservation form" });
  await expect(form).toBeVisible();
  await form.getByLabel("Check-out", { exact: true }).fill(checkOut);
  await form.getByLabel("Check-in", { exact: true }).fill(checkIn);
  await expect(form.getByTestId("unit-availability")).toBeVisible();
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText(/Saved SO-[A-Z0-9]+/)).toBeVisible();
}
