"use client"

/**
 * App-wide React Query setup. Wraps the app once near the root (see the
 * root layout) so any component can call useQuery/useQueryClient — that's
 * how components across this codebase (e.g. AdminPanel, SettingsWizard)
 * fetch and cache server data (config, admin status, etc.) without each
 * one wiring up its own client.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState } from "react"

export function QueryProvider({ children }: { children: React.ReactNode }) {
  // useState (not a plain `new QueryClient()`) so the client is created once
  // per mount and reused across re-renders, instead of being thrown away and
  // recreated on every render (which would drop all cached query data).
  const [client] = useState(() => new QueryClient())
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
