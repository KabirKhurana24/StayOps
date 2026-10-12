import { expect as baseExpect, type Page, test } from "@playwright/test";

import { createReservationViaUi, REMOTE_WRITE_TIMEOUT, requireDemoCredentials, signIn, uniqueBooking } from "./helpers";

test.beforeAll(requireDemoCredentials);

// Workflows run on the API's background worker after each commit; against the remote
// database every step is several round trips, so allow generous asynchronous waits.
const ASYNC_TIMEOUT = 180_000;
const expect = baseExpect.configure({ timeout: ASYNC_TIMEOUT });

async function book(page: Page) {
  const booking = uniqueBooking();
  await page.goto("/reservations");
  await createReservationViaUi(page, booking, "102");
  return booking;
}

const runRows = (page: Page) => page.getByTestId("execution-history").getByTestId("execution-row");

test("template → edit → simulate → publish → real run → pause → approval, live in a second browser", async ({ browser }) => {
  test.setTimeout(2_400_000);
  const marker = `E2E${Date.now().toString(36)}`;
  const operatorCtx = await browser.newContext();
  const page = await operatorCtx.newPage();

  // 1–3. Sign in, open Automations, create a draft from a template.
  await signIn(page);
  await page.getByRole("link", { name: "Automations", exact: true }).click();
  await expect(page.getByTestId("automations-page")).toBeVisible();
  await page.getByRole("button", { name: "From template" }).click();
  await page.getByRole("button", { name: "Use template New Reservation Preparation" }).click();
  const editor = page.getByTestId("workflow-editor");
  await expect(editor).toBeVisible();
  await expect(editor).toHaveAttribute("data-status", "DRAFT");
  const workflowUrl = page.url();
  const name = `Prep ${marker}`;
  await page.getByLabel("Workflow name").fill(name);

  // 4. Edit a condition (24 h → 1 h ahead) and an action (task title).
  await page.locator('[data-testid="node-card"][data-node-id="ahead"]').click();
  const hoursRule = page.getByTestId("node-config").getByTestId("rule").nth(1);
  await hoursRule.getByLabel("Value").fill("1");
  await page.locator('[data-testid="node-card"][data-node-id="checklist"]').click();
  await page.getByTestId("node-config").getByLabel("Title", { exact: true }).fill(`Pre-arrival ${marker} · {{reservation.confirmation_code}}`);
  await expect(page.getByTestId("unsaved")).toBeVisible();

  // 5. Save the draft.
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByTestId("editor-notice")).toContainText("Draft saved");
  await expect(editor).toHaveAttribute("data-dirty", "false");

  // A real event while the workflow is only a draft: it must not run.
  await book(page);
  await page.goto(workflowUrl);
  await expect(editor).toBeVisible();
  await expect(page.getByTestId("execution-history")).toContainText("No runs yet");

  // 6–7. Simulate against that real event; nothing is created.
  await page.getByRole("button", { name: "Simulate" }).click();
  const sim = page.getByTestId("simulation-panel");
  await expect(sim.getByLabel("Sample")).toBeEnabled();
  await sim.getByRole("button", { name: "Run simulation" }).click();
  const result = sim.getByTestId("simulation-result");
  await expect(result).toHaveAttribute("data-matches", "true");
  await expect(result).toContainText("Simulation");
  await expect(result.locator('[data-testid="simulation-step"][data-node-id="ahead"]')).toHaveAttribute("data-outcome", "passed");
  await expect(result.locator('[data-testid="simulation-step"][data-node-id="checklist"]')).toHaveAttribute("data-outcome", "would_execute");
  await expect(result).toContainText("nothing was changed");
  await page.keyboard.press("Escape");
  await page.goto(`/tasks?q=${marker}`);
  await expect(page.getByText("No tasks match these filters")).toBeVisible();
  await page.goto(workflowUrl);
  await expect(page.getByTestId("execution-history")).toContainText("No runs yet");

  // 8. Publish (version 1).
  await page.getByRole("button", { name: "Publish" }).click();
  await expect(editor).toHaveAttribute("data-status", "ACTIVE");
  await expect(page.getByTestId("version-history")).toContainText("v1");

  // 9–10. A real eligible event → the run appears (live, no reload).
  await book(page);
  await page.goto(workflowUrl);
  await expect(runRows(page).first()).toHaveAttribute("data-status", "COMPLETED");

  // 11–12. The task exists — created through the task service, attributed to Autopilot.
  await page.goto(`/tasks?q=${marker}`);
  const taskRow = page.getByText(new RegExp(`Pre-arrival ${marker}`)).first();
  await expect(taskRow).toBeVisible();
  await taskRow.click();
  await expect(page.getByTestId("task-detail")).toContainText(`Autopilot · ${name}`);
  await page.keyboard.press("Escape");

  // 13. Audited.
  await page.goto("/activity");
  await page.getByPlaceholder(/Search people/).fill(marker);
  await expect(page.getByTestId("activity-item").filter({ hasText: "Autopilot" }).first()).toBeVisible();

  // 14. The execution timeline (persisted steps).
  await page.goto(workflowUrl);
  await runRows(page).first().click();
  const timeline = page.getByTestId("execution-timeline");
  await expect(timeline).toContainText("Event received");
  await expect(timeline).toContainText("Workflow matched");
  await expect(timeline).toContainText("Condition passed");
  await expect(timeline).toContainText(`Pre-arrival ${marker}`);
  await expect(timeline).toContainText("Workflow completed");
  await page.keyboard.press("Escape");

  // 15–16. Pause → another event doesn't run it.
  await page.getByRole("button", { name: "Pause" }).click();
  await expect(editor).toHaveAttribute("data-status", "PAUSED");
  await book(page);
  await page.waitForTimeout(20_000); // give the worker time it would need to (wrongly) run it
  await page.goto(workflowUrl);
  await expect(runRows(page).first()).toBeVisible();
  await expect(runRows(page)).toHaveCount(1);

  // 18 (part 1). A second operator watches the library: status changes arrive live.
  const watcherCtx = await browser.newContext();
  const watcher = await watcherCtx.newPage();
  await signIn(watcher);
  await watcher.goto(`/automations?tab=workflows&q=${marker}`);
  const watchedRow = watcher.getByTestId("workflow-row").filter({ hasText: name });
  await expect(watchedRow.getByTestId("workflow-status")).toHaveAttribute("data-status", "PAUSED");

  // 17. The approval path: the task step now always asks; publish v2 (re-activates).
  await page.locator('[data-testid="node-card"][data-node-id="checklist"]').click();
  await page.getByTestId("node-config").getByLabel("Approval").selectOption("required");
  await page.getByRole("button", { name: "Publish" }).click();
  await expect(editor).toHaveAttribute("data-status", "ACTIVE");
  await expect(page.getByTestId("version-history")).toContainText("v2");
  await expect(watchedRow.getByTestId("workflow-status")).toHaveAttribute("data-status", "ACTIVE"); // no reload

  // The watcher opens the workflow (client-side navigation) and follows its runs.
  await watchedRow.click();
  await expect(watcher.getByTestId("workflow-editor")).toBeVisible();

  await book(page);
  await expect(runRows(watcher).first()).toHaveAttribute("data-status", "WAITING_APPROVAL"); // live

  await page.goto("/automations?tab=approvals");
  const approval = page.getByTestId("approval-queue").getByTestId("automation-approval").filter({ hasText: marker }).first();
  await expect(approval).toHaveAttribute("data-status", "PENDING");
  await expect(approval).toContainText("Why");
  await approval.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("automation-approval").filter({ hasText: marker }).first()).toHaveAttribute("data-status", "EXECUTED", {
    timeout: REMOTE_WRITE_TIMEOUT,
  });

  // 18 (part 2). The watcher sees the run complete without reloading.
  await expect(runRows(watcher).first()).toHaveAttribute("data-status", "COMPLETED");
  await page.goto(`/tasks?q=${marker}`);
  await expect(page.getByText(new RegExp(`Pre-arrival ${marker}`))).toHaveCount(2);

  // Clean up: archive, so later runs against the demo organization aren't affected.
  await page.goto(workflowUrl);
  await page.getByRole("button", { name: "Archive" }).click();
  await page.getByRole("button", { name: "Archive workflow" }).click();
  await expect(page).toHaveURL(/tab=workflows/);

  await watcherCtx.close();
  await operatorCtx.close();
});
