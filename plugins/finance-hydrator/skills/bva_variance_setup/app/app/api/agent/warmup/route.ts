import { querySnowflake } from "@/lib/snowflake"
import { primeAvailabilityProbe } from "@/lib/agent-availability"

export const dynamic = "force-dynamic"

// Fires SELECT 1 to resume the warehouse before the user sends a chat message,
// and kicks off the one-time code_toolset_all availability probe (cached —
// see lib/agent-availability.ts) so backend selection is already known by the
// time the user's first message arrives, instead of adding latency to it.
export async function POST() {
  primeAvailabilityProbe()
  await querySnowflake("SELECT 1").catch(() => {})
  return new Response(null, { status: 204 })
}
