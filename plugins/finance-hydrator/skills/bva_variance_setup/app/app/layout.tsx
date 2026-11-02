import type { Metadata } from "next"
import type React from "react"
import { AppHeader } from "@/components/app-header"
import { ThemeProvider } from "@/components/theme-provider"
import { QueryProvider } from "@/components/query-provider"
import { ChatProvider } from "@/lib/chat-context"
import { CortexChatPanel } from "@/components/cortex-chat"
import { APP_TITLE, LOGO_SRC } from "@/lib/constants"
import "./globals.css"

export const metadata: Metadata = {
  title: APP_TITLE,
  description: "Budget vs. Actuals variance dashboard on Snowflake",
  icons: { icon: LOGO_SRC },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased" suppressHydrationWarning>
        <ThemeProvider>
          <QueryProvider>
            <ChatProvider>
              <AppHeader />
              {/* Push layout: main content + persistent chat panel side-by-side */}
              <div className="flex h-[calc(100svh-56px)] overflow-hidden">
                <main className="flex-1 min-w-0 overflow-y-auto">
                  {children}
                </main>
                <CortexChatPanel />
              </div>
            </ChatProvider>
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
