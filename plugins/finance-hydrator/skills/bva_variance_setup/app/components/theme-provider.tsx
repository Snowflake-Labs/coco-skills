"use client"

/**
 * Dark/light/system theme context. Wraps the app near the root so any
 * component can read the current theme or switch it via useTheme() (see
 * components/theme-toggle.tsx for the actual toggle button). The resolved
 * theme is applied by toggling a "dark" class on <html>, which is what the
 * app's Tailwind dark-mode styles key off of.
 */

import { createContext, useContext, useEffect, useState } from "react"

type Theme = "light" | "dark" | "system"

interface ThemeContextValue {
  theme: Theme
  resolvedTheme: "light" | "dark"
  setTheme: (theme: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "system",
  resolvedTheme: "light",
  setTheme: () => {},
})

export function useTheme() {
  return useContext(ThemeContext)
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // `theme` is the user's chosen preference (may be "system"); `resolvedTheme`
  // is always the concrete "light" or "dark" actually applied — consumers
  // like ThemeToggle care about resolvedTheme, not the raw preference.
  const [theme, setThemeState] = useState<Theme>("system")
  const [resolvedTheme, setResolvedTheme] = useState<"light" | "dark">("light")

  // Load stored preference on mount
  useEffect(() => {
    const stored = localStorage.getItem("theme") as Theme | null
    if (stored === "light" || stored === "dark" || stored === "system") {
      setThemeState(stored)
    }
  }, [])

  // Apply theme class whenever theme changes. When theme is "system", resolve
  // it against the OS preference right now; otherwise use the explicit choice.
  useEffect(() => {
    const root = document.documentElement
    const resolved =
      theme === "system"
        ? window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light"
        : theme

    setResolvedTheme(resolved)
    root.classList.toggle("dark", resolved === "dark")
  }, [theme])

  // Keep in sync with OS preference changes when using system theme — e.g. the
  // user's OS switches to dark mode overnight while this tab stays open.
  // Only relevant while theme === "system"; an explicit light/dark choice
  // should never be overridden by an OS change.
  useEffect(() => {
    if (theme !== "system") return
    const mq = window.matchMedia("(prefers-color-scheme: dark)")
    const handler = (e: MediaQueryListEvent) => {
      const next = e.matches ? "dark" : "light"
      setResolvedTheme(next)
      document.documentElement.classList.toggle("dark", e.matches)
    }
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [theme])

  function setTheme(next: Theme) {
    setThemeState(next)
    localStorage.setItem("theme", next)
  }

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}
