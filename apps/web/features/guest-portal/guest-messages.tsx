"use client";

import { Send } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/states";
import { ApiError, newIdempotencyKey } from "@/lib/api/client";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useGuestMessages, useGuestStay, useMarkGuestRead, useSendGuestMessage } from "./api";

export function GuestMessages() {
  const messages = useGuestMessages();
  const stay = useGuestStay();
  const send = useSendGuestMessage();
  const markRead = useMarkGuestRead();
  const [text, setText] = useState("");
  const keyRef = useRef(newIdempotencyKey("guest"));
  const endRef = useRef<HTMLDivElement>(null);
  const unread = stay.data?.unread_messages ?? 0;
  const lastSeq = messages.data?.at(-1)?.seq;

  // Mark each newest message read once (no retry loop if the request fails).
  const { mutate: markReadNow } = markRead;
  const markedUpTo = useRef<number | null>(null);
  useEffect(() => {
    if (unread > 0 && lastSeq !== undefined && markedUpTo.current !== lastSeq) {
      markedUpTo.current = lastSeq;
      markReadNow();
    }
  }, [unread, lastSeq, markReadNow]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [lastSeq]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const content = text.trim();
    if (!content) return;
    send.mutate(
      { content, idempotencyKey: keyRef.current },
      {
        onSuccess: () => {
          setText("");
          keyRef.current = newIdempotencyKey("guest");
        },
      },
    );
  };

  return (
    <div className="flex min-h-[calc(100dvh-11rem)] flex-col" data-testid="guest-messages">
      <h1 className="mb-1 text-xl font-semibold">Messages</h1>
      <p className="mb-4 text-xs text-muted-foreground">Chat with {stay.data?.host_name ?? "your host"} about anything during your stay.</p>
      <div className="flex-1 space-y-3" aria-live="polite">
        {messages.isPending ? (
          <Skeleton className="h-16 w-3/4" />
        ) : messages.data?.length ? (
          messages.data.map((m) => {
            const mine = m.sender === "guest";
            if (m.sender === "system") {
              return (
                <p key={m.id} className="mx-auto max-w-[90%] rounded-lg bg-accent-soft/60 px-3 py-1.5 text-center text-xs" data-testid="guest-message" data-sender="system">
                  {m.content}
                </p>
              );
            }
            return (
              <div key={m.id} className={cn("flex flex-col", mine ? "items-end" : "items-start")} data-testid="guest-message" data-sender={m.sender}>
                <span className="mb-0.5 px-1 text-[11px] text-muted-foreground">
                  {mine ? "You" : m.sender_label} · {formatClock(m.created_at)}
                </span>
                <p
                  className={cn(
                    "max-w-[85%] rounded-2xl px-3.5 py-2 text-[14px] leading-relaxed whitespace-pre-wrap break-words",
                    mine ? "rounded-br-md bg-primary text-primary-foreground" : "rounded-bl-md border bg-surface shadow-xs",
                  )}
                >
                  {m.content}
                </p>
              </div>
            );
          })
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">No messages yet — say hello!</p>
        )}
        <div ref={endRef} />
      </div>
      <form onSubmit={submit} className="sticky bottom-20 mt-4 flex items-end gap-2 rounded-2xl border bg-surface p-2 shadow-sm">
        <Textarea
          aria-label="Message"
          rows={1}
          maxLength={4000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Write a message…"
          className="min-h-[40px] resize-none border-0 shadow-none focus-visible:ring-0"
        />
        <Button type="submit" variant="primary" size="icon" disabled={!text.trim() || send.isPending} aria-label="Send message">
          <Send />
        </Button>
      </form>
      {send.error ? <p className="mt-1 text-xs text-danger">{send.error instanceof ApiError ? send.error.message : "Not sent. Try again."}</p> : null}
    </div>
  );
}
