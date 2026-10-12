"use client";

import { Bot, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/panel";
import { Tabs } from "@/components/ui/segmented";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useSearchState } from "@/lib/use-search-state";

import { useOverview } from "./api";
import { AlertsTab } from "./components/alerts";
import { ApprovalsTab } from "./components/approvals";
import { ExecutionSheet, ExecutionsTab } from "./components/executions";
import { NewWorkflowDialogs } from "./components/new-workflow";
import { OverviewTab } from "./components/overview";
import { WorkflowLibrary } from "./components/workflow-library";

const TABS = ["overview", "workflows", "runs", "approvals", "alerts"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = {
  overview: "Overview",
  workflows: "Workflows",
  runs: "Execution history",
  approvals: "Approvals",
  alerts: "Alerts",
};

export function AutomationsPage() {
  const { get, set } = useSearchState();
  const org = useCurrentOrganization();
  const canManage = can(org.data, "automations:manage");
  const raw = get("tab");
  const tab = ((TABS as readonly string[]).includes(raw) ? raw : "overview") as Tab;
  const overview = useOverview();

  return (
    <div data-testid="automations-page">
      <PageHeader
        eyebrow="StayOps Autopilot"
        title="Automations"
        description="Workflows that react to what happens — simulated before they run, versioned and audited."
        actions={
          canManage ? (
            <>
              <Button onClick={() => set({ dialog: "ai" }, { push: true })}>
                <Bot /> Draft with AI
              </Button>
              <Button onClick={() => set({ dialog: "templates" }, { push: true })}>From template</Button>
              <Button variant="primary" onClick={() => set({ dialog: "new" }, { push: true })}>
                <Plus /> New workflow
              </Button>
            </>
          ) : null
        }
      />
      <Tabs
        label="Autopilot sections"
        className="mb-4"
        value={tab}
        onChange={(v) => set({ tab: v === "overview" ? null : v, e: null })}
        tabs={TABS.map((t) => ({
          value: t,
          label: TAB_LABEL[t],
          count: t === "approvals" && overview.data?.waiting_approval ? overview.data.waiting_approval : undefined,
        }))}
      />
      {tab === "overview" ? <OverviewTab overview={overview} onOpenRun={(id) => set({ e: id }, { push: true })} /> : null}
      {tab === "workflows" ? <WorkflowLibrary /> : null}
      {tab === "runs" ? <ExecutionsTab onOpen={(id) => set({ e: id }, { push: true })} /> : null}
      {tab === "approvals" ? <ApprovalsTab onOpenRun={(id) => set({ e: id }, { push: true })} /> : null}
      {tab === "alerts" ? <AlertsTab /> : null}
      <ExecutionSheet id={get("e") || null} onClose={() => set({ e: null })} />
      {canManage ? <NewWorkflowDialogs dialog={get("dialog")} onClose={() => set({ dialog: null })} /> : null}
    </div>
  );
}
