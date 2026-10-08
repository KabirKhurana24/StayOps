/** Central query-key factory: realtime invalidation targets these prefixes. */
export const queryKeys = {
  me: ["me"] as const,
  currentOrg: ["organization", "current"] as const,
  commandCenter: ["command-center"] as const,
  reservations: {
    all: ["reservations"] as const,
    list: (filters: Record<string, unknown>) => ["reservations", "list", filters] as const,
    detail: (id: string) => ["reservations", "detail", id] as const,
    availability: (params: Record<string, unknown>) => ["reservations", "availability", params] as const,
  },
  calendar: {
    all: ["calendar"] as const,
    range: (params: Record<string, unknown>) => ["calendar", params] as const,
  },
  properties: {
    all: ["properties"] as const,
    list: ["properties", "list"] as const,
    detail: (id: string) => ["properties", "detail", id] as const,
  },
  units: {
    all: ["units"] as const,
    list: (propertyId?: string) => ["units", "list", propertyId ?? "all"] as const,
  },
  guests: {
    all: ["guests"] as const,
    list: (q: string, limit: number, stats = true) => ["guests", "list", q, limit, stats] as const,
    detail: (id: string) => ["guests", "detail", id] as const,
  },
  events: {
    all: ["events"] as const,
    list: (filters: Record<string, unknown>) => ["events", "list", filters] as const,
  },
  audit: {
    all: ["audit"] as const,
    list: (filters: Record<string, unknown>) => ["audit", "list", filters] as const,
  },
  tasks: {
    all: ["tasks"] as const,
    list: (filters: Record<string, unknown>) => ["tasks", "list", filters] as const,
    counts: (filters: Record<string, unknown>) => ["tasks", "counts", filters] as const,
    detail: (id: string) => ["tasks", "detail", id] as const,
  },
  cleanings: {
    all: ["cleanings"] as const,
    list: (filters: Record<string, unknown>) => ["cleanings", "list", filters] as const,
    counts: (filters: Record<string, unknown>) => ["cleanings", "counts", filters] as const,
    detail: (id: string) => ["cleanings", "detail", id] as const,
  },
  maintenance: {
    all: ["maintenance"] as const,
    list: (filters: Record<string, unknown>) => ["maintenance", "list", filters] as const,
    counts: (filters: Record<string, unknown>) => ["maintenance", "counts", filters] as const,
    detail: (id: string) => ["maintenance", "detail", id] as const,
  },
  operations: {
    all: ["operations"] as const,
    staff: ["operations", "staff"] as const,
    units: (propertyId?: string) => ["operations", "units", propertyId ?? "all"] as const,
  },
};
