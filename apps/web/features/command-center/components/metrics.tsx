import type { CommandCenter } from "@stayops/shared";
import { AlarmClock, BedDouble, DoorClosed, DoorOpen, ListChecks, Lock, Wallet, Wrench } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Metric, MetricStrip } from "@/components/ui/misc";
import { Tooltip } from "@/components/ui/menu";
import { formatMoney } from "@/lib/format";

function Linked({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="block transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none">
      {children}
    </Link>
  );
}

/** Operational metrics, all derived from persisted reservations, tasks and work orders. */
export function CommandCenterMetrics({ data }: { data: CommandCenter }) {
  const m = data.metrics;
  const [primary, ...others] = m.revenue_this_month;
  const pct = Math.round(m.occupancy_rate * 100);

  return (
    <MetricStrip label="Today" className="mb-5" columns={8}>
      <Metric
        testId="metric-check-ins"
        icon={<DoorOpen />}
        label="Check-ins today"
        value={m.check_ins_today}
        sub={m.check_ins_today ? `${m.check_ins_completed} of ${m.check_ins_today} arrived` : "No arrivals"}
      />
      <Metric
        testId="metric-check-outs"
        icon={<DoorClosed />}
        label="Check-outs today"
        value={m.check_outs_today}
        sub={m.check_outs_today ? `${m.check_outs_completed} of ${m.check_outs_today} departed` : "No departures"}
      />
      <Linked href="/tasks">
        <Metric
          testId="metric-open-tasks"
          icon={<ListChecks />}
          label="Open tasks"
          value={m.open_tasks}
          sub={m.unassigned_tasks ? `${m.unassigned_tasks} unassigned` : m.cleanings_today ? `${m.cleanings_today} cleanings today` : "All assigned"}
        />
      </Linked>
      <Linked href="/tasks?view=overdue">
        <Metric
          testId="metric-overdue-tasks"
          icon={<AlarmClock />}
          label="Overdue tasks"
          value={m.overdue_tasks}
          tone={m.overdue_tasks ? "danger" : undefined}
          sub={m.overdue_tasks ? "Past their deadline" : "On schedule"}
        />
      </Linked>
      <Linked href="/maintenance">
        <Metric
          testId="metric-active-maintenance"
          icon={<Wrench />}
          label="Active maintenance"
          value={m.active_maintenance}
          tone={m.urgent_maintenance ? "warning" : undefined}
          sub={m.urgent_maintenance ? `${m.urgent_maintenance} high priority` : "Work orders open"}
        />
      </Linked>
      <Linked href="/maintenance">
        <Metric
          testId="metric-blocked-units"
          icon={<Lock />}
          label="Blocked units"
          value={m.blocked_units}
          tone={m.blocked_units ? "danger" : undefined}
          sub={m.blocked_units ? "Not bookable" : "All bookable"}
        />
      </Linked>
      <Metric
        testId="metric-occupied"
        icon={<BedDouble />}
        label="Occupied"
        value={
          <>
            {m.occupied_units}
            <span className="text-sm font-medium text-subtle-foreground">/{m.active_units}</span>
          </>
        }
        sub={`${pct}% occupancy · ${m.in_house} in house`}
      />
      <Metric
        testId="metric-revenue"
        icon={<Wallet />}
        label="Revenue · this month"
        value={primary ? formatMoney(primary.amount, primary.currency, { compact: true }) : "—"}
        sub={
          others.length ? (
            <Tooltip side="bottom" content={others.map((o) => formatMoney(o.amount, o.currency)).join(" · ")}>
              <span className="cursor-help underline decoration-dotted underline-offset-2">
                +{others.map((o) => formatMoney(o.amount, o.currency, { compact: true })).join(", ")}
              </span>
            </Tooltip>
          ) : (
            "Stays starting this month"
          )
        }
      />
    </MetricStrip>
  );
}
