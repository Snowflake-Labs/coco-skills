"use client"

/**
 * Controls for restricting the setup wizard's account discovery scan to one
 * database/schema instead of the full account-wide scan. See
 * lib/discovery-scope-mode.ts for the reasoning (mainly: a live scoped scan
 * finishes in seconds vs. several minutes for the unscoped account-wide scan
 * — useful when walking someone through setup live rather than actually
 * onboarding a new account).
 *
 * No isAdmin gating or icon/collapse behavior here — this is meant to be
 * rendered inside AdminPanel (components/admin-panel.tsx), which already
 * gates the whole panel on isAdmin and owns the "hidden until you click the
 * gear" behavior.
 */

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { getJson } from "@/lib/format"

type ScopeSetting = { enabled: boolean; database: string; schema: string; isAdmin: boolean }

export function DiscoveryScopeControls() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ["discovery-scope"],
    queryFn: () => getJson<ScopeSetting>("/api/discovery-scope"),
  })
  // `database`/`schema` are null until the admin types into the inputs —
  // that lets dbValue/schemaValue below fall back to the saved server value
  // (data?.database / data?.schema) until the admin actually edits a field,
  // instead of the inputs starting out blank and stomping the saved value.
  const [database, setDatabase] = useState<string | null>(null)
  const [schema, setSchema] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const dbValue = database ?? data?.database ?? ""
  const schemaValue = schema ?? data?.schema ?? ""

  // True when the user has cleared the fields but the DB still has stored values
  const hasStoredValues = !!(data?.database || data?.schema)
  const isClearing = database === "" && !dbValue && !schemaValue && hasStoredValues && !data?.enabled

  // Saves (or clears) the override. `enabled` toggles whether the scoped
  // scan is actually applied — the database/schema values can be saved
  // without turning scoping on, but the button below only ever asks for
  // one or the other in a single click (see buttonLabel/onClick).
  const save = async (enabled: boolean) => {
    setSaving(true)
    try {
      const res = await fetch("/api/discovery-scope", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled, database: dbValue, schema: schemaValue }),
      })
      if (res.ok) {
        setDatabase(null)
        setSchema(null)
        queryClient.invalidateQueries({ queryKey: ["discovery-scope"] })
      }
    } finally {
      setSaving(false)
    }
  }

  // Button behaves as a single toggle: turn scoping on (needs a database
  // typed in first), turn it off, or — if the admin blanked out the fields
  // while scoping was already off — just clear the stored values.
  const buttonLabel = data?.enabled
    ? "Turn off scoping"
    : isClearing
      ? "Clear saved scope"
      : "Turn on scoping"

  const buttonDisabled = saving || (!data?.enabled && !dbValue && !isClearing)

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">Discovery scan scope</span>
        <Badge variant={data?.enabled ? "default" : "secondary"} className="font-normal">
          {data?.enabled ? "Scoped" : "Full account"}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">
        Limits the setup wizard&apos;s account scan to one database/schema instead of the whole account.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1">
          <span className="block text-[11px] text-muted-foreground">Database</span>
          <input
            type="text"
            placeholder="e.g. MY_DATABASE"
            value={dbValue}
            onChange={(e) => setDatabase(e.target.value)}
            className="w-full px-2.5 py-1.5 text-xs border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </label>
        <label className="space-y-1">
          <span className="block text-[11px] text-muted-foreground">Schema (optional)</span>
          <input
            type="text"
            placeholder="e.g. MY_SCHEMA"
            value={schemaValue}
            onChange={(e) => setSchema(e.target.value)}
            className="w-full px-2.5 py-1.5 text-xs border rounded-md bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </label>
      </div>
      <Button
        variant={data?.enabled ? "outline" : "default"}
        size="sm"
        disabled={buttonDisabled}
        onClick={() => isClearing ? save(false) : save(!data?.enabled)}
        className="w-full"
      >
        {buttonLabel}
      </Button>
    </div>
  )
}
