"use client"

import Image from "next/image"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Settings, Sparkles } from "lucide-react"
import { APP_TITLE, LOGO_SRC } from "@/lib/constants"
import { ThemeToggle } from "@/components/theme-toggle"
import { getJson } from "@/lib/format"
import { useChatContext } from "@/lib/chat-context"

// Minimal shape of /api/filters this header actually needs — just enough
// to decide whether the "Expense Breakdown" nav link should be shown.
type FiltersShape = {
  capabilities?: {
    hasExpenseBreakdown: boolean
  }
}

/**
 * Top navigation bar shown on every page: logo/app title, page links, the
 * chat toggle, Settings link, and theme toggle. Rendered once at the app
 * shell level, so it stays mounted (and the chat panel state stays intact)
 * as the user navigates between pages.
 */
export function AppHeader() {
  const pathname = usePathname()
  const { data: filters } = useQuery({
    queryKey: ["filters"],
    queryFn: () => getJson<FiltersShape>("/api/filters"),
    staleTime: 5 * 60 * 1000,
  })
  const { isOpen, setIsOpen } = useChatContext()

  // Some data sources have no category/cost-center breakdown available (see
  // BvaCapabilities.hasExpenseBreakdown in lib/config.ts) — hide the nav link
  // to that page entirely rather than showing a page with nothing to show.
  // Default to showing it (!== false) while capabilities are still loading.
  const showBreakdown = filters?.capabilities?.hasExpenseBreakdown !== false

  const navLinks = [
    { href: "/", label: "BvA Working" },
    ...(showBreakdown ? [{ href: "/expense-breakdown", label: "Expense Breakdown" }] : []),
  ]

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background text-foreground">
      <div className="w-full px-4 h-14 flex items-center gap-3">
        {LOGO_SRC && (
          <Image src={LOGO_SRC} alt={`${APP_TITLE} logo`} width={28} height={28} className="shrink-0" />
        )}
        <span className="text-sm font-semibold tracking-tight">{APP_TITLE}</span>
        <nav className="flex items-center gap-1 ml-4">
          {navLinks.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                pathname === href
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              }`}
            >
              {label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {/* Persistent AI chat toggle — works on all pages */}
          <button
            onClick={() => setIsOpen((v) => !v)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors ${
              isOpen
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Cortex Chat
          </button>
          <Link
            href="/settings"
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors ${
              pathname === "/settings"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            <Settings className="h-3.5 w-3.5" />
            Settings
          </Link>
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
