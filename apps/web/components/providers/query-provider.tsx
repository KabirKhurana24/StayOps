"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api/client";

function shouldRetry(failureCount: number, error: unknown) {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Realtime pushes invalidations; this is only the safety net.
            staleTime: 30_000,
            retry: shouldRetry,
            refetchOnWindowFocus: true,
          },
        },
        queryCache: new QueryCache({
          onError: (error) => {
            if (error instanceof ApiError && error.status === 401) router.replace("/login");
          },
        }),
        mutationCache: new MutationCache({
          onError: (error, _vars, _ctx, mutation) => {
            if (mutation.meta?.inlineErrors) return; // the form renders the error itself
            const message = error instanceof ApiError ? error.message : "The change could not be saved.";
            toast.error(message, {
              description: error instanceof ApiError && error.requestId ? `Request ${error.requestId}` : undefined,
            });
          },
        }),
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
