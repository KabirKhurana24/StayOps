import { expect as baseExpect, test } from "@playwright/test";

import { createReservationViaUi, openReservation, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

// Each step waits on API + worker + realtime round-trips (several remote DB calls each).
const ASYNC_TIMEOUT = 60_000;
const expect = baseExpect.configure({ timeout: ASYNC_TIMEOUT });

const PROPERTY = "Canal View Apartments";
const UNIT = "101";
const CLEANER = "Maria Santos"; // seeded staff member

test("browser A assigns, starts and blocks; browser B's task, work order and unit state follow without a refresh", async ({ browser }) => {
  test.setTimeout(480_000);
  // Two isolated browser sessions (separate cookies), same organization.
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const taskB = await contextB.newPage();
  await signIn(pageA);
  await signIn(taskB);

  // A: a checked-out stay → automation creates the turnover cleaning.
  const booking = uniqueBooking();
  await pageA.goto("/reservations");
  await createReservationViaUi(pageA, booking, UNIT);
  await openReservation(pageA, booking.lastName);
  await pageA.getByRole("button", { name: "Check in" }).click();
  await expect(pageA.getByText(/Check in: E2E/)).toBeVisible();
  await pageA.getByRole("button", { name: "Check out" }).click();
  await expect(pageA.getByText(/Check out: E2E/)).toBeVisible();
  const turnover = pageA.locator('[data-testid="reservation-task"][data-task-type="TURNOVER_CLEANING"]');
  await expect(turnover).toHaveCount(1, { timeout: ASYNC_TIMEOUT });
  await turnover.click();
  const cleaningA = pageA.getByTestId("cleaning-detail");
  await expect(cleaningA).toBeVisible();
  const taskHref = await cleaningA.getByRole("link", { name: "Open task" }).getAttribute("href");
  expect(taskHref).toMatch(/^\/tasks\?t=/);

  // B: watches the task drawer, the unit's operational state and the work-order list.
  await taskB.goto(taskHref!);
  const taskDetailB = taskB.getByTestId("task-detail");
  await expect(taskDetailB).toHaveAttribute("data-status", "TODO");
  await expect(taskDetailB.getByTestId("detail-assignee")).toHaveText(/Unassigned/);
  await expect(taskB.getByTestId("realtime-status")).toHaveAttribute("data-status", "live");

  const unitsB = await contextB.newPage();
  await unitsB.goto("/properties");
  await unitsB.getByTestId("property-row").filter({ hasText: PROPERTY }).first().click();
  await expect(unitsB).toHaveURL(/\/properties\/[0-9a-f-]+/);
  await unitsB.goto(`${unitsB.url().split("?")[0]}?tab=units`);
  const unitRowB = unitsB.locator(`[data-testid="unit-row"][data-unit-name="${UNIT}"]`);
  await expect(unitRowB).toBeVisible();
  await expect(unitRowB).not.toHaveAttribute("data-ops-status", "MAINTENANCE");
  await expect(unitsB.getByTestId("realtime-status")).toHaveAttribute("data-status", "live");

  const issue = `Broken lock ${booking.lastName}`;
  const ordersB = await contextB.newPage();
  await ordersB.goto(`/maintenance?q=${encodeURIComponent(issue)}`);
  await expect(ordersB.getByTestId("maintenance-page")).toBeVisible();
  await expect(ordersB.getByTestId("realtime-status")).toHaveAttribute("data-status", "live");
  const orderRowB = ordersB.getByTestId("work-order-row").filter({ hasText: issue });
  await expect(orderRowB).toHaveCount(0);

  let reloads = 0;
  for (const p of [taskB, unitsB, ordersB]) p.on("load", () => reloads++);

  // 1) A assigns the cleaner → B's drawer shows the assignee.
  await cleaningA.getByRole("button", { name: "Assign" }).click();
  await pageA.getByTestId("assignee-option").filter({ hasText: CLEANER }).click();
  await expect(pageA.getByText(`Assigned to ${CLEANER}`)).toBeVisible();
  await expect(taskDetailB.getByTestId("detail-assignee")).toHaveText(new RegExp(CLEANER), { timeout: ASYNC_TIMEOUT });

  // 2) A starts the cleaning → B's status follows.
  await cleaningA.getByRole("button", { name: "Start cleaning" }).click();
  await expect(cleaningA).toHaveAttribute("data-status", "IN_PROGRESS");
  await expect(taskDetailB).toHaveAttribute("data-status", "IN_PROGRESS", { timeout: ASYNC_TIMEOUT });

  // 3) A reports a unit-blocking issue → B sees the work order and the unit go to maintenance.
  await cleaningA.getByRole("button", { name: "Report issue" }).click();
  const woForm = pageA.getByRole("form", { name: "Work order form" });
  await woForm.getByLabel("Issue").fill(issue);
  await woForm.getByLabel("Category").selectOption("LOCK");
  await woForm.getByLabel("Block unit for new bookings").check();
  await woForm.getByRole("button", { name: "Report issue" }).click();
  await expect(pageA.getByText(`Work order created: ${issue}`)).toBeVisible();

  await expect(orderRowB).toHaveCount(1, { timeout: ASYNC_TIMEOUT });
  await expect(orderRowB).toHaveAttribute("data-unit-blocked", "true");
  await expect(unitRowB).toHaveAttribute("data-ops-status", "MAINTENANCE", { timeout: ASYNC_TIMEOUT });

  // 4) A resolves it → B's unit is no longer in maintenance.
  await cleaningA.getByTestId("related-work-orders").getByRole("link", { name: new RegExp(issue) }).click();
  const woA = pageA.getByTestId("work-order-detail");
  await expect(woA).toBeVisible();
  await woA.getByRole("button", { name: "Resolve" }).click();
  await woA.getByRole("button", { name: "Mark resolved" }).click();
  await expect(pageA.getByText(`Resolved: ${issue}`)).toBeVisible();

  await expect(orderRowB).toHaveAttribute("data-status", "RESOLVED", { timeout: ASYNC_TIMEOUT });
  await expect(unitRowB).not.toHaveAttribute("data-ops-status", "MAINTENANCE", { timeout: ASYNC_TIMEOUT });

  expect(reloads).toBe(0);
  await contextA.close();
  await contextB.close();
});
