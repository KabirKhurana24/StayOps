"use client";

import {
  availabilitySchema,
  type CalendarData,
  pageSchema,
  type Reservation,
  type ReservationSource,
  type ReservationStatus,
  reservationSchema,
} from "@stayops/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const reservationPage = pageSchema(reservationSchema);

export type ReservationFilters = {
  status?: ReservationStatus[];
  source?: ReservationSource[];
  property_id?: string;
  unit_id?: string;
  guest_id?: string;
  q?: string;
  start?: string;
  end?: string;
  sort?: string;
  limit?: number;
  offset?: number;
};

export function useReservations(filters: ReservationFilters, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.reservations.list(filters),
    queryFn: ({ signal }) =>
      api("/api/v1/reservations", reservationPage, {
        signal,
        query: { ...filters, limit: filters.limit ?? 50, offset: filters.offset ?? 0 },
      }),
    placeholderData: keepPreviousData,
  });
}

export function useReservation(id: string) {
  return useQuery({
    queryKey: queryKeys.reservations.detail(id),
    queryFn: ({ signal }) => api(`/api/v1/reservations/${id}`, reservationSchema, { signal }),
  });
}

export type ReservationCreateInput = {
  unit_id: string;
  guest_id?: string;
  guest?: { first_name: string; last_name: string; email?: string; phone?: string };
  check_in: string;
  check_out: string;
  status: "PENDING" | "CONFIRMED";
  adults: number;
  children: number;
  source: ReservationSource;
  total_amount: string;
  currency?: string;
  notes?: string;
};

export function useCreateReservation() {
  const qc = useQueryClient();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ body, idempotencyKey }: { body: ReservationCreateInput; idempotencyKey: string }) =>
      api("/api/v1/reservations", reservationSchema, { method: "POST", body, idempotencyKey }),
    onSuccess: (created) => {
      qc.setQueryData(queryKeys.reservations.detail(created.id), created);
      void qc.invalidateQueries({ queryKey: queryKeys.reservations.all });
      void qc.invalidateQueries({ queryKey: queryKeys.commandCenter });
    },
  });
}

export type ReservationUpdateInput = Partial<
  Pick<
    Reservation,
    "status" | "check_in" | "check_out" | "adults" | "children" | "source" | "total_amount" | "currency"
  >
> & { unit_id?: string; notes?: string | null; expected_version?: number };

export function useUpdateReservation(id: string, { inlineErrors = false } = {}) {
  const qc = useQueryClient();
  return useMutation({
    meta: { inlineErrors },
    mutationFn: (body: ReservationUpdateInput) =>
      api(`/api/v1/reservations/${id}`, reservationSchema, { method: "PATCH", body }),
    onSuccess: (updated) => {
      // Server response is authoritative; no optimistic writes for bookings.
      qc.setQueryData(queryKeys.reservations.detail(id), updated);
      void qc.invalidateQueries({ queryKey: queryKeys.reservations.all });
      void qc.invalidateQueries({ queryKey: queryKeys.commandCenter });
      void qc.invalidateQueries({ queryKey: queryKeys.audit.all });
    },
  });
}

const ACTION_PATH: Partial<Record<ReservationStatus, string>> = {
  CONFIRMED: "confirm",
  CHECKED_IN: "check-in",
  CHECKED_OUT: "check-out",
  CANCELLED: "cancel",
};

/** Lifecycle transition via the named action endpoints; the server state machine decides. */
export function useReservationAction(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ status, expectedVersion, reason }: { status: ReservationStatus; expectedVersion: number; reason?: string }) => {
      const action = ACTION_PATH[status];
      return action
        ? api(`/api/v1/reservations/${id}/${action}`, reservationSchema, {
            method: "POST",
            body: { expected_version: expectedVersion, ...(reason ? { reason } : {}) },
          })
        : api(`/api/v1/reservations/${id}`, reservationSchema, { method: "PATCH", body: { status, expected_version: expectedVersion } });
    },
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.reservations.detail(id), updated);
      void qc.invalidateQueries({ queryKey: queryKeys.reservations.all });
      void qc.invalidateQueries({ queryKey: queryKeys.calendar.all });
      void qc.invalidateQueries({ queryKey: queryKeys.commandCenter });
      void qc.invalidateQueries({ queryKey: queryKeys.audit.all });
    },
  });
}

export type AvailabilityParams = { property_id: string; check_in: string; check_out: string; exclude_reservation_id?: string };

/** Live unit availability for a property and period (same rule as the DB constraint). */
export function useAvailability(params: AvailabilityParams | null) {
  return useQuery({
    queryKey: queryKeys.reservations.availability(params ?? {}),
    queryFn: ({ signal }) => api("/api/v1/reservations/availability", availabilitySchema, { signal, query: params ?? {} }),
    enabled: params !== null,
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  });
}

/**
 * Move a stay (dates and/or unit). Not optimistic: the calendar shows the bar as
 * "saving" until the server confirms, then swaps in the server's version.
 */
export function useMoveReservation() {
  const qc = useQueryClient();
  return useMutation({
    meta: { inlineErrors: true },
    mutationFn: ({ id, ...body }: { id: string; unit_id: string; check_in: string; check_out: string; expected_version: number }) =>
      api(`/api/v1/reservations/${id}`, reservationSchema, { method: "PATCH", body }),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.reservations.detail(updated.id), updated);
      qc.setQueriesData<CalendarData>({ queryKey: queryKeys.calendar.all }, (data) =>
        data ? { ...data, reservations: data.reservations.map((r) => (r.id === updated.id ? updated : r)) } : data,
      );
      void qc.invalidateQueries({ queryKey: queryKeys.calendar.all });
      void qc.invalidateQueries({ queryKey: queryKeys.reservations.all });
      void qc.invalidateQueries({ queryKey: queryKeys.commandCenter });
    },
  });
}
