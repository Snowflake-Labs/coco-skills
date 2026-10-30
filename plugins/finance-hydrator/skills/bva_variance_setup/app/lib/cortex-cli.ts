import { existsSync } from "fs"
import { join } from "path"

// This module has no exported functions to call — it just runs the setup
// logic below once, the moment it's imported (see the bare `import
// "./cortex-cli"` at the top of lib/agent-backend.ts). Its whole job is to
// figure out, at startup, which "cortex" binary the CLI-fallback backend
// should spawn later, and stash that answer in an environment variable so
// the rest of the app doesn't need to repeat this lookup.
//
// Vendored CoCo CLI location — populated by app.yml's `install:` step, which
// reassembles the chunked tarball already committed under vendor/ (produced
// by scripts/vendor-cortex-cli.sh on the deploying machine, BEFORE `snow app
// deploy`) and extracts it here with --strip-components=1. No network call
// happens at build time at all. This works around two App Runtime build-container
// limitations: there is no curl/wget or package manager, and outbound network
// access is restricted during the build phase. See scripts/vendor-cortex-cli.sh
// for the download/chunking step this depends on.
//
// Resolved dynamically via process.cwd() (not a static app.yml env var)
// because the run-phase working directory isn't a documented guarantee —
// this way the code doesn't need to hardcode or guess it.
const VENDORED_CLI_PATH = join(process.cwd(), "vendor", "cortex-cli", "cortex")

const vendoredExists = existsSync(VENDORED_CLI_PATH)
console.log(`[cortex-cli] vendored binary check: path=${VENDORED_CLI_PATH} exists=${vendoredExists} envOverride=${process.env.CORTEX_CODE_CLI_PATH ?? "(none)"}`)

// Only fill in the env var if nothing already set it (e.g. a developer
// pointing at a local build for testing) and the vendored binary is
// actually present on disk.
if (!process.env.CORTEX_CODE_CLI_PATH && vendoredExists) {
  process.env.CORTEX_CODE_CLI_PATH = VENDORED_CLI_PATH
}

// Final resolution order: explicit env override > vendored binary > bare
// "cortex" (relies on it being on $PATH, which is only true in local dev
// where the CLI was installed normally rather than vendored).
export const CORTEX_CLI_PATH = process.env.CORTEX_CODE_CLI_PATH ?? "cortex"
console.log(`[cortex-cli] resolved CLI path: ${CORTEX_CLI_PATH}`)
