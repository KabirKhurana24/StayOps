"use client";

import { INBOX_VIEWS, type InboxView } from "@stayops/shared";
import { Inbox, MessageSquareDashed } from "lucide-react";
import { useDeferredValue, useEffect, useState } from "react";

import { useBreadcrumb } from "@/components/shell/shell-context";
import { Select, SearchInput } from "@/components/ui/form";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { useNow } from "@/lib/use-now";
import { useSearchState } from "@/lib/use-search-state";
import { cn } from "@/lib/utils";

import { useConversation, useConversations, useInboxCounts } from "./api";
import { ContextPanel } from "./components/context-panel";
import { ConversationList } from "./components/conversation-list";
import { ConversationThread } from "./components/conversation-thread";
import { CreateRequestDialog } from "./components/request-dialogs";
import { INBOX_VIEW_LABEL, visiblePanes } from "./model";

/** Tailwind classes for a pane from the (unit-tested) per-breakpoint visibility rules. */
function paneClass(pane: "list" | "thread" | "context", selected: boolean): string {
  const show = (w: "narrow" | "medium" | "wide") => visiblePanes(selected, w)[pane];
  return cn(show("narrow") ? "flex" : "hidden", show("medium") ? "md:flex" : "md:hidden", show("wide") ? "xl:flex" : "xl:hidden");
}

export function InboxPage() {
  const { get, set } = useSearchState();
  const org = useCurrentOrganization();
  const canWrite = can(org.data, "inbox:write");
  const canManageRequests = can(org.data, "guest_requests:manage");
  const canManageWork = can(org.data, "tasks:manage") && can(org.data, "maintenance:manage");
  const view = ((INBOX_VIEWS as readonly string[]).includes(get("view")) ? get("view") : "all") as InboxView;
  const [search, setSearch] = useState(get("q"));
  const q = useDeferredValue(search.trim());
  const selectedId = get("c") || null;
  const [contextOpen, setContextOpen] = useState(false);
  const [newRequest, setNewRequest] = useState(false);
  const now = new Date(useNow(30_000));

  const conversations = useConversations({ view, q: q || undefined });
  const counts = useInboxCounts({ q: q || undefined });
  const items = conversations.data?.items ?? [];
  // Keep the open thread even when it isn't in the current filter (e.g. just resolved).
  const listed = items.find((c) => c.id === selectedId) ?? null;
  const detail = useConversation(selectedId, { enabled: !listed && !conversations.isPending });
  const selected = listed ?? detail.data ?? null;
  useBreadcrumb(selected ? `${selected.guest.first_name} ${selected.guest.last_name}` : null);

  useEffect(() => {
    set({ q: q || null });
  }, [q, set]);

  const select = (id: string | null) => set({ c: id }, { push: Boolean(id) });
  const hasSelection = Boolean(selectedId);

  return (
    <div className="-mx-4 -my-5 sm:-mx-6 lg:-mx-8 lg:-my-6" data-testid="inbox-page">
      <div className="flex h-[calc(100dvh-3rem)] min-h-[520px] bg-surface">
        {/* LEFT — conversations */}
        <aside className={cn("w-full min-w-0 flex-col border-r md:w-[320px] md:shrink-0 xl:w-[340px]", paneClass("list", hasSelection))} aria-label="Inbox">
          <div className="space-y-2.5 border-b px-3.5 pt-3.5 pb-3">
            <div className="flex items-center justify-between">
              <h1 className="flex items-center gap-2 text-[15px] font-semibold">
                <Inbox className="size-4" /> Inbox
              </h1>
              <span className="text-xs text-muted-foreground tabular">{counts.data ? `${counts.data.unread} unread` : ""}</span>
            </div>
            <SearchInput value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search guests, bookings, messages" aria-label="Search conversations" />
            <Select
              aria-label="Inbox filter"
              value={view}
              onChange={(e) => set({ view: e.target.value === "all" ? null : e.target.value })}
              className="h-7 text-xs"
            >
              {INBOX_VIEWS.map((v) => (
                <option key={v} value={v}>
                  {INBOX_VIEW_LABEL[v]}
                  {counts.data ? ` (${counts.data[v]})` : ""}
                </option>
              ))}
            </Select>
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Quick filters">
              {(["all", "unread", "mine", "high"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  role="tab"
                  aria-selected={view === v}
                  onClick={() => set({ view: v === "all" ? null : v })}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors",
                    view === v ? "border-accent/30 bg-accent-soft text-accent" : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  {INBOX_VIEW_LABEL[v]}
                  {counts.data && v !== "all" ? <span className="ml-1 tabular opacity-70">{counts.data[v]}</span> : null}
                </button>
              ))}
            </div>
          </div>
          <div className={cn("min-h-0 flex-1 overflow-y-auto", conversations.isPlaceholderData && "opacity-60")}>
            {conversations.isPending ? (
              <SkeletonRows rows={7} columns={1} />
            ) : conversations.error ? (
              <ErrorState error={conversations.error} onRetry={() => conversations.refetch()} />
            ) : items.length === 0 ? (
              <EmptyState
                compact
                icon={MessageSquareDashed}
                title={q ? "No conversations match" : view === "all" ? "No conversations yet" : `Nothing in “${INBOX_VIEW_LABEL[view]}”`}
                description={q || view !== "all" ? "Try another filter." : "Guests write to you from their portal; you can start a thread from any reservation."}
              />
            ) : (
              <ConversationList items={items} selectedId={selectedId} onSelect={select} now={now} />
            )}
          </div>
        </aside>

        {/* CENTER — thread */}
        <main className={cn("min-w-0 flex-1 flex-col", paneClass("thread", hasSelection))}>
          {selected ? (
            <ConversationThread
              key={selected.id}
              conversation={selected}
              canWrite={canWrite}
              now={now}
              onBack={() => select(null)}
              onShowContext={() => setContextOpen(true)}
              onNewRequest={() => setNewRequest(true)}
            />
          ) : selectedId && (conversations.isPending || detail.isPending) ? null : (
            <div className="grid h-full place-items-center">
              <EmptyState
                icon={Inbox}
                title={selectedId ? "Conversation not found" : "Select a conversation"}
                description="Guest messages, requests and the stay's operational context in one place."
              />
            </div>
          )}
        </main>

        {/* RIGHT — guest / reservation context */}
        {selected ? (
          <aside className={cn("w-[340px] shrink-0 flex-col overflow-y-auto border-l bg-surface", paneClass("context", true))} aria-label="Guest and stay details">
            <ContextPanel conversationId={selected.id} canWrite={canWrite} canManageRequests={canManageRequests} canManageWork={canManageWork} />
          </aside>
        ) : null}
      </div>

      {selected ? (
        <>
          <Sheet open={contextOpen} onOpenChange={setContextOpen} title="Guest and stay" size="sm" bodyClassName="p-0">
            <ContextPanel conversationId={selected.id} canWrite={canWrite} canManageRequests={canManageRequests} canManageWork={canManageWork} />
          </Sheet>
          {newRequest ? (
            <CreateRequestDialog open onOpenChange={setNewRequest} conversationId={selected.id} timeZone={selected.property?.timezone ?? "UTC"} />
          ) : null}
        </>
      ) : null}
    </div>
  );
}
