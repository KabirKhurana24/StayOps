"use client";

import { History, Radio } from "lucide-react";
import { useDeferredValue, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, SearchInput, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/segmented";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Table, TableScroll, Td, Th, THead, Tr } from "@/components/ui/table";
import { useAuditInfinite, useEvents } from "@/features/activity/api";
import { ActivityTimeline } from "@/features/activity/components/activity-timeline";
import { ACTIVITY_CATEGORIES, type ActivityCategory } from "@/lib/format";

type Range = "any" | "today" | "7d" | "30d" | "custom";

function rangeBounds(range: Range, from: string, to: string): { since?: string; until?: string } {
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const now = new Date();
  switch (range) {
    case "today":
      return { since: startOfDay(now).toISOString() };
    case "7d":
      return { since: new Date(startOfDay(now).getTime() - 6 * 86_400_000).toISOString() };
    case "30d":
      return { since: new Date(startOfDay(now).getTime() - 29 * 86_400_000).toISOString() };
    case "custom": {
      // Custom dates are the operator's local calendar days.
      const since = from ? new Date(`${from}T00:00:00`).toISOString() : undefined;
      const until = to ? new Date(new Date(`${to}T00:00:00`).getTime() + 86_400_000).toISOString() : undefined;
      return { since, until };
    }
    default:
      return {};
  }
}

function AuditView() {
  const [category, setCategory] = useState<ActivityCategory>("all");
  const [q, setQ] = useState("");
  const deferredQ = useDeferredValue(q.trim());
  const [range, setRange] = useState<Range>("any");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const audit = useAuditInfinite({
    entity_type: ACTIVITY_CATEGORIES[category].entityTypes,
    q: deferredQ || undefined,
    ...rangeBounds(range, from, to),
  });
  const entries = audit.data?.pages.flatMap((p) => p.items) ?? [];
  const total = audit.data?.pages[0]?.total ?? 0;
  const filtered = category !== "all" || deferredQ || range !== "any";

  return (
    <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
      <div className="flex flex-col gap-2 border-b px-4 py-3 lg:flex-row lg:items-center">
        <Segmented
          label="Category"
          value={category}
          onChange={setCategory}
          options={(Object.keys(ACTIVITY_CATEGORIES) as ActivityCategory[]).map((k) => ({ value: k, label: ACTIVITY_CATEGORIES[k].label }))}
          className="scrollbar-thin max-w-full overflow-x-auto"
        />
        <div className="flex flex-1 flex-wrap items-center gap-2 lg:justify-end">
          <SearchInput
            wrapperClassName="w-full sm:w-64"
            placeholder="Search people, codes, units…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onClear={() => setQ("")}
            aria-label="Search activity"
          />
          <Select value={range} onChange={(e) => setRange(e.target.value as Range)} aria-label="Date range" wrapperClassName="w-36">
            <option value="any">Any time</option>
            <option value="today">Today</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
            <option value="custom">Custom range…</option>
          </Select>
          {range === "custom" ? (
            <div className="flex items-center gap-1.5">
              <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From date" className="w-36" />
              <span className="text-xs text-subtle-foreground">to</span>
              <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="To date" className="w-36" />
            </div>
          ) : null}
        </div>
      </div>

      {audit.isPending ? (
        <SkeletonRows rows={8} columns={2} />
      ) : audit.error ? (
        <ErrorState error={audit.error} onRetry={() => audit.refetch()} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={History}
          title={filtered ? "No activity matches these filters" : "No activity yet"}
          description={filtered ? "Try another category, a broader date range, or clear the search." : "Every change your team makes — bookings, check-ins, edits — is recorded here."}
          action={
            filtered ? (
              <Button
                size="sm"
                onClick={() => {
                  setCategory("all");
                  setQ("");
                  setRange("any");
                }}
              >
                Clear filters
              </Button>
            ) : null
          }
        />
      ) : (
        <div className={audit.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
          <ActivityTimeline entries={entries} />
          <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
            <span className="tabular">
              Showing {entries.length} of {total}
            </span>
            {audit.hasNextPage ? (
              <Button size="sm" onClick={() => void audit.fetchNextPage()} disabled={audit.isFetchingNextPage}>
                {audit.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function EventStreamView() {
  const [offset, setOffset] = useState(0);
  const events = useEvents({ limit: 50, offset });
  return (
    <div className="overflow-hidden rounded-lg border bg-surface shadow-xs">
      {events.isPending ? (
        <SkeletonRows rows={8} columns={5} />
      ) : events.error ? (
        <ErrorState error={events.error} onRetry={() => events.refetch()} />
      ) : events.data.items.length === 0 ? (
        <EmptyState icon={Radio} title="No domain events yet" />
      ) : (
        <>
          <TableScroll>
            <Table className="min-w-[760px]">
              <THead>
                <tr>
                  <Th>Event</Th>
                  <Th>Aggregate</Th>
                  <Th>Source</Th>
                  <Th>Processing</Th>
                  <Th>Occurred (UTC)</Th>
                </tr>
              </THead>
              <tbody>
                {events.data.items.map((e) => (
                  <Tr key={e.id}>
                    <Td className="font-mono text-xs">{e.event_type}</Td>
                    <Td className="font-mono text-[11px] text-muted-foreground">
                      {e.aggregate_type}/{e.aggregate_id.slice(0, 8)}
                    </Td>
                    <Td className="text-xs">{e.source}</Td>
                    <Td>
                      <Badge dot tone={e.status === "COMPLETED" ? "success" : e.status === "FAILED" ? "danger" : "warning"}>
                        {e.status.toLowerCase()}
                        {e.attempts > 1 ? ` ×${e.attempts}` : ""}
                      </Badge>
                    </Td>
                    <Td className="font-mono text-[11px] text-muted-foreground">{e.occurred_at.replace("T", " ").slice(0, 19)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableScroll>
          {events.data.total > 50 ? (
            <div className="flex items-center justify-between border-t px-4 py-2.5 text-xs text-muted-foreground">
              <span className="tabular">
                {offset + 1}–{Math.min(offset + 50, events.data.total)} of {events.data.total}
              </span>
              <div className="flex gap-1.5">
                <Button size="sm" disabled={offset === 0} onClick={() => setOffset(offset - 50)}>
                  Previous
                </Button>
                <Button size="sm" disabled={offset + 50 >= events.data.total} onClick={() => setOffset(offset + 50)}>
                  Next
                </Button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

export function ActivityPage() {
  const [view, setView] = useState<"timeline" | "events">("timeline");
  return (
    <div>
      <PageHeader
        title="Activity"
        description="Everything happening across your operation."
        actions={
          <Segmented
            label="View"
            value={view}
            onChange={setView}
            options={[
              { value: "timeline", label: "Timeline", icon: <History /> },
              { value: "events", label: "Event stream", icon: <Radio /> },
            ]}
          />
        }
      />
      {view === "timeline" ? <AuditView /> : <EventStreamView />}
    </div>
  );
}
