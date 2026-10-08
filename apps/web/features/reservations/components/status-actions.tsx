"use client";

import { RESERVATION_TRANSITIONS, type Reservation, type ReservationStatus } from "@stayops/shared";
import { LogIn, LogOut } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useReservationAction } from "@/features/reservations/api";
import { formatStayRange, guestName } from "@/lib/format";

export const ACTION_LABEL: Partial<Record<ReservationStatus, string>> = {
  CONFIRMED: "Confirm",
  CHECKED_IN: "Check in",
  CHECKED_OUT: "Check out",
  CANCELLED: "Cancel",
  PENDING: "Mark pending",
};

/** Hook shared by buttons and menus: runs a lifecycle transition through the API. */
export function useStatusTransition(reservation: Reservation) {
  const action = useReservationAction(reservation.id);
  const [confirmingCancel, setConfirmingCancel] = useState(false);

  const run = (status: ReservationStatus) => {
    if (status === "CANCELLED") {
      setConfirmingCancel(true);
      return;
    }
    execute(status);
  };

  const execute = (status: ReservationStatus) =>
    action.mutate(
      { status, expectedVersion: reservation.version },
      {
        onSuccess: () => {
          setConfirmingCancel(false);
          toast.success(`${ACTION_LABEL[status]}: ${guestName(reservation.guest)}`, {
            description: `${reservation.confirmation_code} · ${reservation.unit.name}`,
          });
        },
      },
    );

  const dialog = (
    <ConfirmDialog
      open={confirmingCancel}
      onOpenChange={setConfirmingCancel}
      title={`Cancel ${reservation.confirmation_code}?`}
      description={
        <>
          {guestName(reservation.guest)} · {reservation.property.name}, unit {reservation.unit.name} ·{" "}
          {formatStayRange(reservation.check_in, reservation.check_out, reservation.property.timezone)}. The unit becomes available
          again and the change is recorded in the audit trail.
        </>
      }
      confirmLabel="Cancel reservation"
      cancelLabel="Keep reservation"
      pending={action.isPending}
      onConfirm={() => execute("CANCELLED")}
    />
  );

  return { run, pending: action.isPending, dialog, targets: RESERVATION_TRANSITIONS[reservation.status] };
}

/** The next-step actions a reservation allows, following the server's state machine. */
export function StatusActions({
  reservation,
  only,
  size = "sm",
  includeCancel = true,
}: {
  reservation: Reservation;
  only?: ReservationStatus[];
  size?: "xs" | "sm" | "md";
  includeCancel?: boolean;
}) {
  const { run, pending, dialog, targets } = useStatusTransition(reservation);
  const visible = targets.filter((s) => (!only || only.includes(s)) && (includeCancel || s !== "CANCELLED") && s !== "PENDING");
  if (visible.length === 0) return dialog;

  return (
    <div className="flex items-center gap-1.5">
      {visible.map((status) => (
        <Button
          key={status}
          size={size}
          variant={status === "CANCELLED" ? "danger" : status === "CHECKED_IN" || status === "CONFIRMED" ? "primary" : "secondary"}
          disabled={pending}
          onClick={(e) => {
            e.stopPropagation();
            run(status);
          }}
        >
          {status === "CHECKED_IN" ? <LogIn /> : status === "CHECKED_OUT" ? <LogOut /> : null}
          {ACTION_LABEL[status]}
        </Button>
      ))}
      {dialog}
    </div>
  );
}
