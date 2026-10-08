import type { CommandCenter } from "@stayops/shared";
import { ClipboardList, Inbox, MessageCircle } from "lucide-react";
import Link from "next/link";

import { Panel } from "@/components/ui/panel";
import { cn } from "@/lib/utils";

/** Guest communication at a glance: unread threads and requests not yet done. */
export function GuestPulse({ metrics }: { metrics: CommandCenter["metrics"] }) {
  const rows = [
    {
      href: "/inbox?view=unread",
      icon: MessageCircle,
      label: "Conversations awaiting a reply",
      value: metrics.unread_conversations,
      testId: "metric-unread-conversations",
    },
    {
      href: "/inbox?view=open",
      icon: ClipboardList,
      label: "Open guest requests",
      value: metrics.open_guest_requests,
      testId: "metric-open-guest-requests",
    },
  ];
  return (
    <Panel title="Guests" icon={<Inbox />} actions={<Link href="/inbox" className="text-xs text-accent hover:underline">Open inbox</Link>}>
      <ul className="divide-y">
        {rows.map(({ href, icon: Icon, label, value, testId }) => (
          <li key={href}>
            <Link href={href} className="flex items-center gap-3 px-4 py-2.5 text-[13px] hover:bg-muted/40" data-testid={testId} data-value={value}>
              <Icon className="size-4 text-muted-foreground" aria-hidden />
              <span className="flex-1">{label}</span>
              <span className={cn("font-semibold tabular", value ? "text-accent" : "text-subtle-foreground")}>{value}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
