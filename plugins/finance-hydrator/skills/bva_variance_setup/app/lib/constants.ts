/**
 * App-wide branding constants — kept separate from lib/config.ts's
 * per-deployment BrandColors so these can be referenced from static/server
 * contexts (e.g. metadata, favicon) without needing a config load.
 */

/** App title — displayed in the nav header and browser tab */
export const APP_TITLE = "BvA Variance"

/** Path to the logo in /public (used in the header and as favicon) */
export const LOGO_SRC = "/icon.svg"
