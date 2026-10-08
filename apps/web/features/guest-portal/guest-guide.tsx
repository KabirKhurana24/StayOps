"use client";

import { KNOWLEDGE_CATEGORY_LABEL } from "@stayops/shared";
import { BookOpen, KeyRound, MapPin } from "lucide-react";

import { EmptyState, Skeleton } from "@/components/ui/states";
import { formatInZone } from "@/lib/format";

import { useGuestKnowledge, useGuestStay } from "./api";
import { directionsUrl, shortTime } from "./token";

/** The house manual: structured, host-maintained information for this stay. */
export function GuestGuide() {
  const guide = useGuestKnowledge();
  const stay = useGuestStay();
  if (guide.isPending || stay.isPending) {
    return (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }
  if (guide.error) return <p className="text-sm text-danger">{guide.error.message}</p>;
  const s = stay.data;
  const g = guide.data;

  return (
    <div className="space-y-4" data-testid="guest-guide">
      <h1 className="text-xl font-semibold">House guide</h1>
      {s ? (
        <section className="rounded-2xl border bg-surface p-4 text-sm shadow-xs" id="times">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Check-in from</span>
            <span className="font-medium tabular">{shortTime(s.property.check_in_time)}</span>
          </div>
          <div className="mt-1 flex justify-between">
            <span className="text-muted-foreground">Check-out by</span>
            <span className="font-medium tabular">{shortTime(s.property.check_out_time)}</span>
          </div>
          <a
            href={directionsUrl(s.property.address)}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 flex items-center gap-1.5 text-[13px] font-medium text-accent"
          >
            <MapPin className="size-4" /> {s.property.address}
          </a>
        </section>
      ) : null}
      {!g.stay_info_available ? (
        <p className="flex items-start gap-2 rounded-xl bg-accent-soft/60 px-3.5 py-2.5 text-xs" data-testid="stay-info-locked">
          <KeyRound className="mt-0.5 size-3.5 shrink-0 text-accent" />
          Door codes and Wi-Fi details unlock on your arrival day ({s ? formatInZone(g.stay_info_available_from, s.property.timezone, { weekday: "short", day: "numeric", month: "short" }) : ""}).
        </p>
      ) : null}
      {g.sections.length === 0 ? (
        <EmptyState icon={BookOpen} title="Your host hasn't added a guide yet" description="Ask anything in Messages." />
      ) : (
        g.sections.map((section) => (
          <section key={section.category} id={section.category} className="scroll-mt-20 rounded-2xl border bg-surface p-4 shadow-xs" data-testid="guide-section">
            <h2 className="mb-2 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{KNOWLEDGE_CATEGORY_LABEL[section.category]}</h2>
            <div className="space-y-3">
              {section.entries.map((e) => (
                <article key={e.id}>
                  <h3 className="text-[14px] font-medium">{e.title}</h3>
                  <p className="mt-0.5 text-[13px] leading-relaxed whitespace-pre-line text-foreground/85">{e.content}</p>
                </article>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
