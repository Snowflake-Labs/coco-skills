import { BvaDashboard } from "@/components/bva-dashboard"

// Data comes from Snowflake at request time.
export const dynamic = "force-dynamic"

export default function Home() {
  return <BvaDashboard />
}
