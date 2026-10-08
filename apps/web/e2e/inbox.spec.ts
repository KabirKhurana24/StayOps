import { expect as baseExpect, type Page, test } from "@playwright/test";

import { createReservationViaUi, DEMO_EMAIL, openReservation, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

// Every step is a real API round trip (several remote DB calls) plus worker/realtime.
const ASYNC_TIMEOUT = 60_000;
const expect = baseExpect.configure({ timeout: ASYNC_TIMEOUT });

const PROPERTY = "Canal View Apartments";
const UNIT = "102";

/** Fresh stay → "Message guest" → its (new, empty) inbox thread. Returns the conversation id. */
async function openFreshConversation(page: Page, booking: ReturnType<typeof uniqueBooking>) {
  await page.goto("/reservations");
  await createReservationViaUi(page, booking, UNIT);
  await openReservation(page, booking.lastName);
  await page.getByRole("button", { name: "Message guest" }).click();
  await expect(page).toHaveURL(/\/inbox\?c=[0-9a-f-]{36}/);
  const conversationId = new URL(page.url()).searchParams.get("c");
  expect(conversationId).toBeTruthy();
  return conversationId as string;
}

test("operator works a guest conversation end to end; the guest sees only their own stay", async ({ browser }) => {
  test.setTimeout(1_200_000);
  const operator = await browser.newContext();
  const page = await operator.newPage();

  // 1–2. Operator signs in and opens the Inbox from the navigation.
  await signIn(page);
  await page.getByRole("link", { name: "Inbox", exact: true }).click();
  await expect(page.getByTestId("inbox-page")).toBeVisible();
  await expect(page.getByTestId("conversation-row").first()).toBeVisible(); // seeded threads

  // 3. A guest conversation for a fresh stay.
  const booking = uniqueBooking();
  const guestName = `${booking.firstName} ${booking.lastName}`;
  const conversationId = await openFreshConversation(page, booking);
  const thread = page.getByTestId("conversation-thread");
  await expect(thread.getByRole("heading", { name: guestName })).toBeVisible();

  // 4. Reservation context beside the thread.
  const context = page.getByTestId("context-panel");
  await expect(context).toContainText(PROPERTY);
  await expect(context).toContainText(`Unit ${UNIT}`);
  await expect(context).toContainText(/SO-[A-Z0-9]+/);
  await expect(context).toContainText("Upcoming");

  // 5. Send a message to the guest.
  const hello = `Welcome to Amsterdam, ${booking.lastName}!`;
  await thread.getByLabel("Reply").fill(hello);
  await thread.getByRole("button", { name: "Send" }).click();
  await expect(thread.getByTestId("message").filter({ hasText: hello })).toHaveAttribute("data-sender", "STAFF");
  await expect(thread.getByLabel("Reply")).toHaveValue("");

  // 6. Raise a guest request from the conversation.
  const ask = `Two extra towels for ${booking.lastName}`;
  await thread.getByRole("button", { name: "Request" }).click();
  const form = page.getByRole("form", { name: "Guest request form" });
  await form.getByLabel("Request").selectOption("EXTRA_TOWELS");
  await form.getByLabel("Details").fill(ask);
  await form.getByRole("button", { name: "Create request" }).click();
  const card = context.getByTestId("guest-request").filter({ hasText: ask });
  await expect(card).toHaveAttribute("data-status", "OPEN");

  // 7. Convert the request into an operations task.
  await card.getByRole("button", { name: "Create task" }).click();
  await page.getByRole("form", { name: "Convert to task form" }).getByRole("button", { name: "Create task" }).click();
  await expect(card).toHaveAttribute("data-status", "IN_PROGRESS");
  const taskHref = await card.getByTestId("request-task-link").getAttribute("href");
  expect(taskHref).toMatch(/^\/tasks\?t=/);

  // 8. The task is done.
  await page.goto(taskHref as string);
  const detail = page.getByTestId("task-detail");
  await expect(detail).toHaveAttribute("data-status", "TODO");
  await detail.getByRole("button", { name: "Complete", exact: true }).click();
  await expect(detail).toHaveAttribute("data-status", "COMPLETED");

  // 9. The request resolved itself and the guest was told, in the same thread.
  await page.goto(`/inbox?c=${conversationId}`);
  await expect(context.getByTestId("guest-request").filter({ hasText: ask })).toHaveAttribute("data-status", "RESOLVED");
  await expect(thread.getByTestId("message").filter({ hasText: "has been taken care of" })).toBeVisible();

  // 10. Share the guest portal link.
  await context.getByTestId("portal-link").getByRole("button", { name: "Create link" }).click();
  const portalUrl = await context.getByLabel("Guest portal link").inputValue();
  expect(portalUrl).toMatch(/\/guest#token=[A-Za-z0-9_-]{20,}$/);

  // 11. The guest (no StayOps account) sees their own stay — and nothing else.
  const guestBrowser = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const guest = await guestBrowser.newPage();
  await guest.goto(portalUrl);
  await expect(guest.getByTestId("guest-name")).toHaveText(booking.firstName);
  await expect(guest.getByTestId("guest-stay-card")).toContainText(PROPERTY);
  await expect(guest.getByTestId("guest-stay-card")).toContainText(UNIT);
  await expect(guest).toHaveURL(/\/guest$/); // the token left the address bar
  const body = guest.locator("body");
  for (const leak of ["Sarah Johnson", "Chloe Dubois", "Maria Santos", DEMO_EMAIL, "Demo Operator"]) {
    await expect(body).not.toContainText(leak);
  }
  await guest.getByTestId("action-contact").click();
  const guestThread = guest.getByTestId("guest-messages");
  await expect(guestThread.getByTestId("guest-message").filter({ hasText: hello })).toHaveAttribute("data-sender", "host");
  await expect(guestThread).toContainText("Canal Stays"); // the host, never the staff member
  await expect(guestThread).toContainText("taken care of");
  await expect(guestThread).not.toContainText("Task created"); // internal notes stay internal

  // The guest replies; the operator's open thread shows it without a refresh.
  const reply = `Thank you! — ${booking.lastName}`;
  await guest.getByLabel("Message", { exact: true }).fill(reply);
  await guest.getByRole("button", { name: "Send message" }).click();
  await expect(guestThread.getByTestId("guest-message").filter({ hasText: reply })).toBeVisible();
  await expect(thread.getByTestId("message").filter({ hasText: reply })).toHaveAttribute("data-sender", "GUEST");

  // A tampered link opens nothing.
  const intruder = await (await browser.newContext()).newPage();
  await intruder.goto(portalUrl.replace(/token=.{6}/, "token=AAAAAA"));
  await expect(intruder.getByTestId("guest-no-access")).toBeVisible();

  await guestBrowser.close();
  await operator.close();
});

test("guest portal works on a phone: report an issue and see it as a request", async ({ browser }) => {
  test.setTimeout(900_000);
  const operator = await browser.newContext();
  const page = await operator.newPage();
  await signIn(page);
  const booking = uniqueBooking();
  await openFreshConversation(page, booking);
  const context = page.getByTestId("context-panel");
  await context.getByTestId("portal-link").getByRole("button", { name: "Create link" }).click();
  const portalUrl = await context.getByLabel("Guest portal link").inputValue();

  const phone = await browser.newContext({ viewport: { width: 375, height: 740 }, isMobile: true, hasTouch: true });
  const guest = await phone.newPage();
  await guest.goto(portalUrl);
  await expect(guest.getByTestId("guest-home")).toBeVisible();
  // Mobile layout: bottom tab bar, no horizontal overflow.
  await expect(guest.getByRole("navigation", { name: "Portal" })).toBeVisible();
  const overflow = await guest.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);

  // Door codes stay locked until arrival day (the stay is months away).
  await guest.getByRole("link", { name: "Guide" }).click();
  await expect(guest.getByTestId("guest-guide")).toBeVisible();
  await expect(guest.getByTestId("stay-info-locked")).toBeVisible();
  await expect(guest.getByTestId("guest-guide")).not.toContainText("4827"); // building door code
  await expect(guest.getByTestId("guest-guide")).toContainText("Parking");

  await guest.getByRole("link", { name: "Stay" }).click();
  await guest.getByTestId("action-report-issue").click();
  const issue = `Shower drains slowly — ${booking.lastName}`;
  const form = guest.getByRole("form", { name: "New request" });
  await expect(form.getByLabel("What do you need?")).toHaveValue("MAINTENANCE");
  await form.getByLabel("Details").fill(issue);
  await form.getByRole("button", { name: "Send request" }).click();
  await expect(guest.getByTestId("guest-request-item").filter({ hasText: issue })).toHaveAttribute("data-status", "OPEN");

  // The operator sees the request on the conversation, raised by the guest.
  await expect(context.getByTestId("guest-request").filter({ hasText: issue })).toHaveAttribute("data-status", "OPEN");
  await phone.close();
  await operator.close();
});
