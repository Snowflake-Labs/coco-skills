// Server Component — queries Snowflake at request time and renders session info.
// Change QUERY to fetch from your own tables.
//
// This is a template/demo scaffold left over from the app starter, not part
// of the real BvA dashboard flow — it just proves the Snowflake connection
// works by showing session metadata (account, role, warehouse, etc.). QUERY
// is hoisted to module scope since it's a fixed constant with no per-render
// inputs; there's no need to recreate the string on every request.

import { querySnowflake } from "@/lib/snowflake"
import { Table, TableBody, TableCell, TableHead, TableRow } from "@/components/ui/table"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"

const QUERY = `
  SELECT
    CURRENT_ACCOUNT()    AS ACCOUNT,
    CURRENT_REGION()     AS REGION,
    CURRENT_USER()       AS "USER",
    CURRENT_ROLE()       AS ROLE,
    CURRENT_WAREHOUSE()  AS WAREHOUSE,
    CURRENT_DATABASE()   AS "DATABASE",
    CURRENT_SCHEMA()     AS "SCHEMA",
    CURRENT_TIMESTAMP()  AS TIMESTAMP
`

// Loading placeholder shown (e.g. via React Suspense) while SessionCard's
// async server render is still in flight.
export function SessionCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Server Side Rendering</CardTitle>
        <CardDescription>
          Calls an API route that returns the current information about the Snowflake session.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex gap-4">
              <Skeleton className="h-4 w-[120px]" />
              <Skeleton className="h-4 w-[180px]" />
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export async function SessionCard() {
  let session: Record<string, any> | null = null
  let error: string | null = null

  // Runs on the server at request time (this is an async Server Component,
  // not a client-side fetch) — the query executes once per page render.
  try {
    const rows = await querySnowflake(QUERY)
    session = rows[0] ?? null
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error"
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Server Side Rendering</CardTitle>
        <CardDescription>
          Calls an API route that returns the current information about the Snowflake session.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <Alert variant="destructive">
            <AlertTitle>Query failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : session ? (
          <Table>
            <TableBody>
              {Object.entries(session).map(([key, value]) => (
                <TableRow key={key}>
                  <TableHead className="w-40">{key}</TableHead>
                  <TableCell>{String(value ?? "—")}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  )
}
