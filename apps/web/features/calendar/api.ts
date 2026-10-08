"use client";

import { calendarSchema } from "@stayops/shared";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export type CalendarParams = { start: string; end: string; property_id?: string; include_cancelled?: boolean };

/** One request per visible range: properties → units plus overlapping stays. */
export function useCalendar(params: CalendarParams) {
  return useQuery({
    queryKey: queryKeys.calendar.range(params),
    queryFn: ({ signal }) =>
      api("/api/v1/calendar", calendarSchema, {
        signal,
        query: { ...params, include_cancelled: params.include_cancelled ? "true" : undefined },
      }),
    placeholderData: keepPreviousData,
  });
}
