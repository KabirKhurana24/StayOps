"use client";

import { MessageSquare } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { can, useCurrentOrganization } from "@/features/organizations/api";

import { useStartConversation } from "../api";

/** Opens the stay's in-app conversation in the inbox (creating it on first use). */
export function MessageGuestButton({ reservationId, size = "sm" }: { reservationId: string; size?: "sm" | "md" }) {
  const org = useCurrentOrganization();
  const start = useStartConversation();
  const router = useRouter();
  if (!can(org.data, "inbox:write")) return null;
  return (
    <Button
      size={size}
      disabled={start.isPending}
      onClick={() => start.mutate({ reservation_id: reservationId }, { onSuccess: (c) => router.push(`/inbox?c=${c.id}`) })}
    >
      <MessageSquare /> Message guest
    </Button>
  );
}
