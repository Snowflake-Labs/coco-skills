---
name: refs-security-review-triggers
description: "When and how to invoke security review during coding workflow. Trigger conditions, boundary mapping methodology, credential flow tracing, and output format."
---

# Security Review Triggers

Invoked by the orchestrator after the Build Critic (`xo-cold-smart`) approves changes that touch security-sensitive areas, before handing off to the Prove stage. Can also be invoked during plan review (pre-implementation).

## When to Invoke

The orchestrator MUST invoke a security review when ANY of these conditions are met:

### Trigger Conditions

| Condition | Description |
|-----------|-------------|
| **Credential handling** | Change reads, writes, transforms, or passes authentication credentials (passwords, tokens, private keys, API keys, session tokens) |
| **Cross-boundary writes** | Change modifies how data flows between process boundaries (IPC, network, inter-service) |
| **Sensitive file I/O** | Change writes to files that contain or could contain secrets (`~/.snowflake/`, credential caches, token files, `.env`, key files) |
| **New IPC/API surface** | Change adds new methods to an IPC interface, REST endpoint, or inter-process communication channel |
| **Sandbox/privilege context** | Change runs in a sandboxed context (Electron renderer, browser extension, WASM) and interacts with privileged resources |
| **Authentication flow changes** | Change modifies how users authenticate, how tokens are exchanged, or how sessions are managed |
| **Desktop process boundary** | Electron apps: any change touching both renderer (sandboxed) and main (privileged) processes |

### Skip Conditions

Do NOT invoke security review for:
- Pure UI/styling changes
- Documentation changes
- Test-only changes
- Read-only data display changes that don't touch credentials

---

## Review Procedure

### Step 1: Map Process/Trust Boundaries

Identify all processes and contexts involved in the change:

```
[Context A] ──boundary──► [Context B]
   (what trust level?)       (what trust level?)
```

For Electron apps:
```
Renderer (sandboxed) ──IPC──► Main (privileged) ──fs──► Disk
```

### Step 2: Trace Credential Flow

Follow credentials through the change path:

1. Where do credentials originate? (UI input, file read, env var, token exchange)
2. How do credentials move through the change? (function calls, IPC messages, network requests)
3. Where do credentials terminate? (disk write, API call, memory only)
4. Are credentials ever logged, serialised to an unexpected format, or exposed to a lower-privilege context?

### Step 3: Assess Change Impact

Compare before/after:

| Aspect | Before | After | Risk |
|--------|--------|-------|------|
| Boundary crossings | [list] | [list] | New crossing? |
| Credential exposure surface | [list] | [list] | Expanded? |
| IPC methods | [list] | [list] | New surface? |
| File I/O on sensitive paths | [list] | [list] | New writes? |

### Step 4: Produce Checklist

Evaluate each item — all must pass:

- [ ] No new IPC methods exposing credentials to renderer
- [ ] No credential values logged at any log level
- [ ] No credentials persisted in unexpected locations
- [ ] No credentials passed to lower-privilege contexts
- [ ] No new file writes to credential storage paths without proper permissions
- [ ] Token/secret lifetimes not extended beyond necessity
- [ ] Error paths do not leak credential content in error messages
- [ ] Existing security invariants preserved (no regression)
- [ ] Sandbox boundaries not weakened

### Step 5: Verdict

| Verdict | Meaning | Action |
|---------|---------|--------|
| **Safe to proceed** | All checklist items pass, no new risk | Continue to Prove stage |
| **Needs changes** | Fixable issues found | Return to Build generator with security findings |
| **Blocked** | Fundamental design concern | Escalate to operator |

---

## Integration Points

| Context | When | How |
|---------|------|-----|
| **Plan review** (pre-implementation) | When plan touches credential/boundary code | Invoked by `decide-plan.md`'s Security review step — review plan before Build |
| **Post-critic** (Build → Prove) | When Build Critic (`xo-cold-smart`) approved changes touching triggers | Review diff before Prove stage |
| **PR review** (Review) | When PR touches credential/boundary code | Review diff as part of PR assessment |

---

## Example: Electron Credential Flow

**Example — connection config CRUD:**

**Boundary map**:
```
Renderer ──IPC TOML──► Main ──IFileService──► Disk (~/.snowflake/connections.toml)
```

**Finding**: SDK uses Node.js `fs` directly — cannot be called from sandboxed renderer. Must replicate logic independently in main process.

**Checklist**: 9 items, all passing. No credential flow changes — credentials stay in main process, only non-secret config metadata crosses IPC.

**Verdict**: Safe to proceed.
