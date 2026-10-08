"use client";

import {
  type CurrentOrganization,
  currentOrganizationSchema,
  meSchema,
  organizationSchema,
} from "@stayops/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, setActiveOrganizationId } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export function useMe() {
  return useQuery({ queryKey: queryKeys.me, queryFn: () => api("/api/v1/me", meSchema) });
}

export function useCurrentOrganization() {
  return useQuery({
    queryKey: queryKeys.currentOrg,
    queryFn: () => api("/api/v1/organizations/me", currentOrganizationSchema),
    staleTime: 5 * 60_000,
  });
}

export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; default_currency: string }) =>
      api("/api/v1/organizations", organizationSchema, { method: "POST", body }),
    onSuccess: async (org) => {
      setActiveOrganizationId(org.id);
      await qc.invalidateQueries();
    },
  });
}

export const can = (org: CurrentOrganization | undefined, permission: CurrentOrganization["permissions"][number]) =>
  Boolean(org?.permissions.includes(permission));
