// Standard shadcn/ui presentational primitive; no custom business logic here.
import { cn } from "@/lib/utils"

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-primary/20 dark:bg-primary/15", className)}
      {...props}
    />
  )
}

export { Skeleton }
