"use client";

import {
  CONVERSATION_PRIORITIES,
  CONVERSATION_STATUSES,
  type Conversation,
  type ConversationPriority,
  type ConversationStatus,
  GUEST_REQUEST_LABEL,
  type GuestRequestType,
  type Message,
} from "@stayops/shared";
import { ArrowLeft, ClipboardList, Info, Lock, MessageSquare, PanelRight, Send, UserPlus } from "lucide-react";
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Select, Textarea } from "@/components/ui/form";
import { Avatar } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { AssigneeMenu } from "@/features/operations/components/assignee-menu";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";
import { formatClock, formatStayRange } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useAssignConversation, useChannels, useMarkRead, useMessages, useSendMessage, useUpdateConversation } from "../api";
import { groupByDay, messageSide } from "../model";

const STATUS_LABEL: Record<ConversationStatus, string> = { OPEN: "Open", PENDING: "Waiting on guest", RESOLVED: "Resolved", CLOSED: "Closed" };
const PRIORITY_LABEL: Record<ConversationPriority, string> = { LOW: "Low", NORMAL: "Normal", HIGH: "High", URGENT: "Urgent" };

function MessageBubble({ m, guestName }: { m: Message; guestName: string }) {
  const side = messageSide(m);
  const requestType = typeof m.metadata.request_type === "string" ? (m.metadata.request_type as GuestRequestType) : null;

  if (side === "center") {
    return (
      <li className="flex justify-center px-6" data-testid="message" data-sender="SYSTEM" data-internal={m.internal || undefined}>
        <div
          className={cn(
            "flex max-w-[85%] items-start gap-1.5 rounded-md px-2.5 py-1 text-xs",
            m.message_type === "STATUS" || m.message_type === "REQUEST"
              ? "border border-accent/20 bg-accent-soft/60 text-foreground"
              : "text-muted-foreground",
          )}
        >
          {m.internal ? <Lock className="mt-0.5 size-3 shrink-0" aria-label="Team only" /> : <Info className="mt-0.5 size-3 shrink-0" aria-hidden />}
          <span className="min-w-0">
            {m.message_type === "STATUS" ? <span className="font-medium">Sent to guest · </span> : null}
            {m.content}
            <span className="ml-1.5 text-[10px] text-subtle-foreground tabular">{formatClock(m.created_at)}</span>
          </span>
        </div>
      </li>
    );
  }

  const fromGuest = side === "left";
  const who = fromGuest ? guestName : m.sender_type === "AI" ? "Assistant" : (m.sender_label ?? "Team");
  return (
    <li
      className={cn("flex gap-2 px-4", fromGuest ? "justify-start" : "justify-end")}
      data-testid="message"
      data-sender={m.sender_type}
      data-internal={m.internal || undefined}
    >
      {fromGuest ? <Avatar name={guestName} size="sm" className="mt-5" /> : null}
      <div className={cn("flex max-w-[78%] flex-col", fromGuest ? "items-start" : "items-end")}>
        <span className="mb-1 px-1 text-[11px] text-subtle-foreground">
          {who}
          {m.internal ? " · internal note" : ""} · <span className="tabular">{formatClock(m.created_at)}</span>
        </span>
        <div
          className={cn(
            "rounded-xl px-3 py-2 text-[13px] leading-relaxed whitespace-pre-wrap break-words",
            m.internal
              ? "rounded-tr-sm border border-dashed border-warning/40 bg-warning/8 text-foreground"
              : fromGuest
                ? "rounded-tl-sm border bg-surface text-foreground shadow-xs"
                : "rounded-tr-sm bg-primary text-primary-foreground",
          )}
        >
          {m.message_type === "REQUEST" && requestType ? (
            <span className="mb-1 flex items-center gap-1 text-[11px] font-semibold tracking-wide uppercase opacity-80">
              <ClipboardList className="size-3" aria-hidden /> {GUEST_REQUEST_LABEL[requestType]} request
            </span>
          ) : null}
          {m.content}
        </div>
        {!fromGuest && !m.internal && m.read_at ? <span className="mt-0.5 px-1 text-[10px] text-subtle-foreground">Seen</span> : null}
      </div>
    </li>
  );
}

function Composer({ conversation, canWrite }: { conversation: Conversation; canWrite: boolean }) {
  const send = useSendMessage(conversation.id);
  const channels = useChannels();
  const [mode, setMode] = useState<"reply" | "note">("reply");
  const [text, setText] = useState("");
  const keyRef = useRef(newIdempotencyKey("msg"));
  const channel = channels.data?.find((c) => c.channel === conversation.channel);
  const connected = channel ? channel.status === "CONNECTED" : conversation.channel === "IN_APP";
  const closed = conversation.status === "CLOSED";
  const blocked = mode === "reply" && (!connected || closed);

  if (!canWrite) {
    return <p className="border-t px-4 py-3 text-xs text-muted-foreground">You have read-only access to the inbox.</p>;
  }

  const submit = () => {
    const content = text.trim();
    if (!content || blocked || send.isPending) return;
    send.mutate(
      { content, internal: mode === "note", idempotencyKey: keyRef.current },
      {
        onSuccess: () => {
          setText("");
          keyRef.current = newIdempotencyKey("msg");
        },
      },
    );
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className={cn("border-t px-3 py-2.5", mode === "note" && "bg-warning/5")}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <Segmented
          size="sm"
          label="Message type"
          value={mode}
          onChange={setMode}
          options={[
            { value: "reply", label: "Reply to guest", icon: <MessageSquare /> },
            { value: "note", label: "Internal note", icon: <Lock /> },
          ]}
        />
        <span className="text-[11px] text-muted-foreground" data-testid="channel-status">
          {mode === "note"
            ? "Only your team sees notes"
            : connected
              ? `Delivered via ${channel?.label ?? "guest portal"}`
              : `${channel?.label ?? conversation.channel} is not connected`}
        </span>
      </div>
      <div className="flex items-end gap-2">
        <Textarea
          aria-label={mode === "note" ? "Internal note" : "Reply"}
          rows={2}
          maxLength={4000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={blocked && mode === "reply"}
          placeholder={
            closed && mode === "reply"
              ? "This conversation is closed — reopen it to reply."
              : mode === "note"
                ? "Add a note for the team…"
                : `Message ${conversation.guest.first_name}…`
          }
          className="min-h-[60px] resize-none"
        />
        <Button variant="primary" onClick={submit} disabled={!text.trim() || blocked || send.isPending} aria-label={mode === "note" ? "Add note" : "Send"}>
          <Send /> {mode === "note" ? "Add note" : "Send"}
        </Button>
      </div>
      {send.error ? (
        <p role="alert" className="mt-1.5 text-xs text-danger">
          {send.error instanceof ApiError ? send.error.message : "The message could not be sent."}
        </p>
      ) : null}
    </div>
  );
}

export function ConversationThread({
  conversation,
  canWrite,
  onBack,
  onShowContext,
  onNewRequest,
  now,
}: {
  conversation: Conversation;
  canWrite: boolean;
  onBack?: () => void;
  onShowContext?: () => void;
  onNewRequest: () => void;
  now: Date;
}) {
  const messages = useMessages(conversation.id);
  const markRead = useMarkRead(conversation.id);
  const update = useUpdateConversation(conversation.id);
  const assign = useAssignConversation(conversation.id);
  const scrollRef = useRef<HTMLDivElement>(null);
  const guestName = `${conversation.guest.first_name} ${conversation.guest.last_name}`;
  const tz = conversation.property?.timezone;
  const days = useMemo(() => groupByDay(messages.data ?? [], now, tz), [messages.data, now, tz]);
  const lastSeq = messages.data?.at(-1)?.seq;

  // Opening a thread with unread guest messages marks them read for the whole team.
  // Once per (thread, newest message): no retry loop if the request fails.
  const { mutate: markReadNow } = markRead;
  const markedKey = useRef<string | null>(null);
  useEffect(() => {
    const key = `${conversation.id}:${lastSeq ?? 0}`;
    if (canWrite && conversation.unread_count > 0 && markedKey.current !== key) {
      markedKey.current = key;
      markReadNow();
    }
  }, [canWrite, conversation.id, conversation.unread_count, lastSeq, markReadNow]);

  // Keep the newest message in view as messages arrive.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastSeq, conversation.id]);

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={`Conversation with ${guestName}`} data-testid="conversation-thread">
      <header className="border-b px-4 py-2.5">
        <div className="flex items-center gap-3">
          {onBack ? (
            <Button variant="ghost" size="icon-sm" onClick={onBack} aria-label="Back to conversations" className="md:hidden">
              <ArrowLeft />
            </Button>
          ) : null}
          <Avatar name={guestName} size="md" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[14px] font-semibold">{guestName}</h2>
            <p className="truncate text-xs text-muted-foreground">
              {conversation.property ? conversation.property.name : "No stay linked"}
              {conversation.unit ? ` · Unit ${conversation.unit.name}` : ""}
              {conversation.reservation && tz ? ` · ${formatStayRange(conversation.reservation.check_in, conversation.reservation.check_out, tz)}` : ""}
              {conversation.reservation ? ` · ${conversation.reservation.confirmation_code}` : ""}
            </p>
          </div>
          {canWrite ? (
            <Button size="sm" onClick={onNewRequest}>
              <ClipboardList /> Request
            </Button>
          ) : null}
          {onShowContext ? (
            <Button variant="ghost" size="icon-sm" onClick={onShowContext} aria-label="Guest and stay details" className="xl:hidden">
              <PanelRight />
            </Button>
          ) : null}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5" aria-label="Conversation settings">
          <Select
            aria-label="Conversation status"
            value={conversation.status}
            disabled={!canWrite || update.isPending}
            onChange={(e) => update.mutate({ status: e.target.value as ConversationStatus })}
            wrapperClassName="w-[150px]"
            className="h-7 text-xs"
          >
            {CONVERSATION_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Conversation priority"
            value={conversation.priority}
            disabled={!canWrite || update.isPending}
            onChange={(e) => update.mutate({ priority: e.target.value as ConversationPriority })}
            wrapperClassName="w-[104px]"
            className="h-7 text-xs"
          >
            {CONVERSATION_PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABEL[p]}
              </option>
            ))}
          </Select>
          {canWrite ? (
            <AssigneeMenu
              value={conversation.assignee?.id ?? null}
              onChange={(id) => assign.mutate(id)}
              pending={assign.isPending}
              roles={["OWNER", "ADMIN", "OPERATOR"]}
              trigger={
                <Button size="sm" aria-label="Assign conversation">
                  {conversation.assignee ? <Avatar name={conversation.assignee.name} size="xs" /> : <UserPlus />}
                  <span className="max-w-[120px] truncate">{conversation.assignee ? conversation.assignee.name : "Assign"}</span>
                </Button>
              }
            />
          ) : conversation.assignee ? (
            <span className="text-xs text-muted-foreground">Assigned to {conversation.assignee.name}</span>
          ) : null}
        </div>
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto bg-background/60 py-3" data-testid="message-list">
        {messages.isPending ? (
          <div className="space-y-3 px-4">
            <Skeleton className="h-12 w-2/3" />
            <Skeleton className="ml-auto h-12 w-1/2" />
          </div>
        ) : messages.error ? (
          <ErrorState error={messages.error} onRetry={() => messages.refetch()} />
        ) : days.length === 0 ? (
          <EmptyState compact icon={MessageSquare} title="No messages yet" description={`Say hello to ${conversation.guest.first_name}.`} />
        ) : (
          days.map((day) => (
            <div key={day.key}>
              <div className="sticky top-0 z-[1] flex justify-center py-1.5">
                <span className="rounded-full border bg-surface px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground shadow-xs">{day.label}</span>
              </div>
              <ol className="space-y-3 py-1">
                {day.messages.map((m) => (
                  <MessageBubble key={m.id} m={m} guestName={guestName} />
                ))}
              </ol>
            </div>
          ))
        )}
      </div>

      <Composer conversation={conversation} canWrite={canWrite} />
    </section>
  );
}
