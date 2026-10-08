"use client";

import {
  pageSchema,
  type PropertyStatus,
  propertySchema,
  type UnitStatus,
  type UnitType,
  unitSchema,
} from "@stayops/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { api, newIdempotencyKey } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

const propertyPage = pageSchema(propertySchema);

export function useProperties() {
  return useQuery({
    queryKey: queryKeys.properties.list,
    queryFn: ({ signal }) => api("/api/v1/properties", propertyPage, { signal, query: { limit: 200 } }),
  });
}

export function useProperty(id: string) {
  return useQuery({
    queryKey: queryKeys.properties.detail(id),
    queryFn: ({ signal }) => api(`/api/v1/properties/${id}`, propertySchema, { signal }),
  });
}

export function useUnits(propertyId?: string) {
  return useQuery({
    queryKey: queryKeys.units.list(propertyId),
    queryFn: ({ signal }) =>
      propertyId
        ? api(`/api/v1/properties/${propertyId}/units`, z.array(unitSchema), { signal })
        : api("/api/v1/units", z.array(unitSchema), { signal }),
  });
}

export type PropertyCreateInput = {
  name: string;
  address: string;
  timezone: string;
  default_check_in_time: string;
  default_check_out_time: string;
};

export function useCreateProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PropertyCreateInput) =>
      api("/api/v1/properties", propertySchema, {
        method: "POST",
        body,
        idempotencyKey: newIdempotencyKey("property"),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.properties.all }),
  });
}

export function useUpdateProperty(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<PropertyCreateInput> & { status?: PropertyStatus }) =>
      api(`/api/v1/properties/${id}`, propertySchema, { method: "PATCH", body }),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.properties.detail(id), updated);
      void qc.invalidateQueries({ queryKey: queryKeys.properties.all });
    },
  });
}

export function useCreateUnit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { property_id: string; name: string; type: UnitType; status: UnitStatus }) =>
      api("/api/v1/units", unitSchema, {
        method: "POST",
        body,
        idempotencyKey: newIdempotencyKey("unit"),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.units.all });
      void qc.invalidateQueries({ queryKey: queryKeys.properties.all });
    },
  });
}
