import { expect as baseExpect, test } from "@playwright/test";

import { createReservationViaUi, openReservation, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

// The API under test runs AI_PROVIDER=mock (playwright.config.ts): deterministic, no paid calls.
const ASYNC_TIMEOUT = 240_000; // an AI run commits each step (several remote round trips apiece)
const expect = baseExpect.configure({ timeout: ASYNC_TIMEOUT });

test("guest assistant answers from the guide and files a request; Copilot proposes, operator approves", async ({ browser }) => {
  test.setTimeout(2_700_000);
  const operatorCtx = await browser.newContext();
  const page = await operatorCtx.newPage();
  await signIn(page);

  // Setup: a fresh stay with a conversation and a guest portal link.
  const booking = uniqueBooking();
  await page.goto("/reservations");
  await createReservationViaUi(page, booking, "102");
  await openReservation(page, booking.lastName);
  await page.getByRole("button", { name: "Message guest" }).click();
  await expect(page).toHaveURL(/\/inbox\?c=/);
  const context = page.getByTestId("context-panel");
  await context.getByTestId("portal-link").getByRole("button", { name: "Create link" }).click();
  const portalUrl = await context.getByLabel("Guest portal link").inputValue();

  // 1. The guest opens the portal.
  const guestCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const guest = await guestCtx.newPage();
  await guest.goto(portalUrl);
  await expect(guest.getByTestId("guest-home")).toBeVisible();
  await guest.getByTestId("action-contact").click();
  const chat = guest.getByTestId("guest-messages");

  // 2–3. A check-in question, answered from the property's structured guide.
  await guest.getByLabel("Message", { exact: true }).fill("Hi! What time is check-in?");
  await guest.getByRole("button", { name: "Send message" }).click();
  const answer = chat.locator('[data-testid="guest-message"][data-sender="assistant"]').first();
  await expect(answer).toContainText("15:00");
  await expect(answer).toContainText("keypad at the green front door"); // the guide's CHECK_IN entry
  await expect(answer).toContainText("AI");
  await expect(answer).not.toContainText("4827"); // the door code stays locked until arrival day

  // 4–5. The guest reports a problem; the assistant files a maintenance request.
  await guest.getByLabel("Message", { exact: true }).fill("The AC is broken");
  await guest.getByRole("button", { name: "Send message" }).click();
  await expect(chat.locator('[data-testid="guest-message"][data-sender="assistant"]').nth(1)).toContainText("reported it to the team");
  await guest.getByRole("link", { name: "Requests" }).click();
  await expect(guest.getByTestId("guest-request-item").filter({ hasText: "Maintenance issue" })).toHaveAttribute("data-status", "OPEN");

  // 6. The operator sees it — request on the conversation, AI replies labelled as AI.
  const thread = page.getByTestId("conversation-thread");
  await expect(context.getByTestId("guest-request").filter({ hasText: "Maintenance issue" })).toHaveAttribute("data-status", "OPEN");
  await expect(thread.locator('[data-testid="message"][data-sender="AI"]').first()).toContainText("Assistant · AI");

  // Make sure there is unassigned urgent work for Copilot to act on.
  const taskTitle = `Check AC filter ${booking.lastName}`;
  await page.goto("/tasks?new=1");
  const taskForm = page.getByRole("form", { name: "Task form" });
  await taskForm.getByLabel("Title").fill(taskTitle);
  await taskForm.getByLabel("Priority").selectOption("URGENT");
  await taskForm.getByLabel("Property").selectOption({ label: "Canal View Apartments" });
  await taskForm.getByRole("button", { name: "Create task" }).click();
  await expect(page.getByTestId("task-detail")).toBeVisible();

  // A second operator watches the activity log (realtime check at the end).
  const watcherCtx = await browser.newContext();
  const watcher = await watcherCtx.newPage();
  await signIn(watcher);
  await watcher.goto("/activity");

  // 7–9. Copilot reads the actual operations. The task drawer is modal (the sidebar is
  // aria-hidden while it is open), so close it before navigating.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("task-detail")).toBeHidden();
  await page.getByRole("link", { name: "Copilot", exact: true }).click();
  await expect(page.getByTestId("copilot-page")).toBeVisible();
  await page.getByRole("button", { name: "What needs my attention today?" }).click();
  const run = page.getByTestId("copilot-run");
  await expect(run).not.toHaveAttribute("data-status", "RUNNING");
  await expect(page.locator('[data-testid="ai-step"][data-tool="get_overdue_tasks"]')).toHaveAttribute("data-status", "COMPLETED");
  await expect(page.locator('[data-testid="ai-step"][data-tool="get_active_maintenance"]')).toBeVisible();
  const answerCard = page.getByTestId("ai-answer");
  await expect(answerCard).toContainText("need attention");
  await expect(answerCard).toContainText("requested maintenance issue"); // the guest's AC report
  await expect(page.getByTestId("ai-evidence")).toBeVisible();

  // 10. The AI proposes an action — nothing has changed yet.
  const proposal = page.getByTestId("ai-proposal").first();
  await expect(proposal).toHaveAttribute("data-status", "PENDING");
  await expect(proposal).toContainText("Why");
  await expect(proposal).toContainText("Effect");

  // 11–12. The operator approves; the domain action executes.
  await proposal.getByRole("button", { name: "Approve" }).click();
  await expect(proposal).toHaveAttribute("data-status", "EXECUTED");
  await expect(proposal).toContainText("Decided by");
  const affected = await proposal.getByRole("link").first().getAttribute("href");
  expect(affected).toMatch(/^\/tasks\?t=/);
  await page.goto(affected as string);
  await expect(page.getByTestId("task-detail").getByTestId("detail-assignee")).not.toContainText("Unassigned");

  // 13–14. The approval is audited and appears live in the other operator's activity log.
  await expect(watcher.getByTestId("activity-item").filter({ hasText: "approved an AI proposal" }).first()).toBeVisible();

  await guestCtx.close();
  await watcherCtx.close();
  await operatorCtx.close();
});
