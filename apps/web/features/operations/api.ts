"use client";

import { staffMemberSchema, unitStateSchema } from "@stayops/shared";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

/** Assignable organization members with live workload (tasks + work orders). */
export function useStaff({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    enabled,
    queryKey: queryKeys.operations.staff,
    queryFn: ({ signal }) => api("/api/v1/operations/staff", z.array(staffMemberSchema), { signal }),
    staleTime: 30_000,
  });
}

/** Derived operational state per unit (READY / OCCUPIED / CLEANING / MAINTENANCE / OUT_OF_SERVICE). */
export function useUnitStates(propertyId?: string) {
  return useQuery({
    queryKey: queryKeys.operations.units(propertyId),
    queryFn: ({ signal }) =>
      api("/api/v1/operations/units", z.array(unitStateSchema), { signal, query: { property_id: propertyId } }),
  });
}
