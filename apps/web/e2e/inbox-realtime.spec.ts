import { expect as baseExpect, test } from "@playwright/test";

import { createReservationViaUi, openReservation, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

const ASYNC_TIMEOUT = 60_000;
const expect = baseExpect.configure({ timeout: ASYNC_TIMEOUT });

test("two operator browsers: messages, unread state and ordering update without a refresh", async ({ browser }) => {
  test.setTimeout(1_200_000);
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  await signIn(pageA);
  await signIn(pageB);

  // A: a fresh stay and its conversation, plus a portal link for the guest.
  const booking = uniqueBooking();
  await pageA.goto("/reservations");
  await createReservationViaUi(pageA, booking, "201");
  await openReservation(pageA, booking.lastName);
  await pageA.getByRole("button", { name: "Message guest" }).click();
  await expect(pageA).toHaveURL(/\/inbox\?c=/);
  const threadA = pageA.getByTestId("conversation-thread");
  await pageA.getByTestId("context-panel").getByTestId("portal-link").getByRole("button", { name: "Create link" }).click();
  const portalUrl = await pageA.getByTestId("context-panel").getByLabel("Guest portal link").inputValue();

  // B: watching the inbox list (no conversation open).
  await pageB.goto("/inbox");
  await expect(pageB.getByTestId("inbox-page")).toBeVisible();
  const rowB = pageB.getByTestId("conversation-row").filter({ hasText: booking.lastName });

  // The guest writes from the portal…
  const guest = await (await browser.newContext()).newPage();
  await guest.goto(portalUrl);
  await guest.getByTestId("action-contact").click();
  const fromGuest = `Is early check-in possible? (${booking.lastName})`;
  await guest.getByLabel("Message", { exact: true }).fill(fromGuest);
  await guest.getByRole("button", { name: "Send message" }).click();

  // …B's list shows the conversation, unread, at the top — no reload.
  await expect(rowB).toBeVisible();
  await expect(rowB.getByTestId("unread-badge")).toHaveText("1");
  await expect(pageB.getByTestId("conversation-row").first()).toContainText(booking.lastName);

  // B opens it: the message is there and the unread badge clears for the team.
  await rowB.click();
  const threadB = pageB.getByTestId("conversation-thread");
  await expect(threadB.getByTestId("message").filter({ hasText: fromGuest })).toBeVisible();
  await expect(rowB.getByTestId("unread-badge")).toHaveCount(0);

  // A (already in the thread) sees the guest message arrive, then replies; B sees the reply.
  await expect(threadA.getByTestId("message").filter({ hasText: fromGuest })).toBeVisible();
  const reply = `We'll confirm by 11:00 (${booking.lastName})`;
  await threadA.getByLabel("Reply").fill(reply);
  await threadA.getByRole("button", { name: "Send" }).click();
  await expect(threadB.getByTestId("message").filter({ hasText: reply })).toHaveAttribute("data-sender", "STAFF");
  await expect(rowB).toContainText(`You: ${reply}`);

  // And the guest's portal picks the reply up too.
  await expect(guest.getByTestId("guest-message").filter({ hasText: reply })).toBeVisible();

  await contextA.close();
  await contextB.close();
});
