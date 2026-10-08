"use client";

import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/panel";
import { can, useCurrentOrganization } from "@/features/organizations/api";
import { ReservationDrawer } from "@/features/reservations/components/reservation-drawer";
import { ReservationFormSheet } from "@/features/reservations/components/reservation-form";
import { useSearchState } from "@/lib/use-search-state";

import { CalendarView } from "./calendar-view";

export function CalendarPage() {
  const { get, set } = useSearchState();
  const org = useCurrentOrganization();
  return (
    <div>
      <PageHeader
        title="Calendar"
        description="Availability and stays across every unit, live."
        actions={
          can(org.data, "reservations:write") ? (
            <Button variant="primary" onClick={() => set({ new: "1" }, { push: true })}>
              <Plus /> New reservation
            </Button>
          ) : null
        }
      />
      <CalendarView initialDate={get("date") || undefined} onOpenReservation={(id) => set({ r: id }, { push: true })} />
      <ReservationDrawer id={get("r") || null} onClose={() => set({ r: null })} />
      <ReservationFormSheet open={get("new") === "1"} onOpenChange={(open) => set({ new: open ? "1" : null })} />
    </div>
  );
}
