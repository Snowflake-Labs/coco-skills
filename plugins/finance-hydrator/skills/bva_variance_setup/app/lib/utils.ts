/** Small shared UI helper(s) — currently just the Tailwind classname merger. */

import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

// Combines conditional classnames (clsx) and then resolves any conflicting
// Tailwind utility classes (twMerge) so the last one wins — e.g. a component
// that hardcodes "p-2" but is passed "p-4" via props ends up with "p-4"
// instead of both classes applying and the browser picking one arbitrarily.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
