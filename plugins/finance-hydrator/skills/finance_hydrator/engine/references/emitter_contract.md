# Hydrator Emitter Contract

The hydrator engine separates recommendation from artifact emission. The front half answers:

- which assets match the user's workflow (`business_fit`), and
- which assets can bind to the customer's data (`readiness`).

An emitter turns the selected recommendation rows into a customer-owned artifact.

## Current emitter: `skill_plugin`

`skill_plugin` writes a CoCo plugin containing selected hydrated skills.

Input contract:

```json
{
  "emitter": "skill_plugin",
  "assessment": "path/to/recommendations.json",
  "catalogs": ["path/to/catalog.json"],
  "templates_root": "path/to/templates",
  "output": "path/to/customer-finance",
  "include_partial": false
}
```

Selection rules:

- `recommendation: include` is copied by default.
- `recommendation: ask` is copied only when the caller explicitly opts in.
- `recommendation: exclude` is never copied.
- Legacy readiness-only rows still work: `READY` maps to include, `PARTIAL` maps to ask.

Output contract:

```text
customer-finance/
├── .cortex-plugin/plugin.json
├── README.md
├── skills/<skill_name>/
│   └── hydration_hints.yaml
└── evals/<skill_name>/
    ├── golden_cases.md   # if available
    └── eval_plan.md      # if available
```

Only customer-relevant eval planning files are emitted. Prior source-account eval results or output
artifacts are not copied.

## Future emitters

Future domains can add emitters without changing the profile / fit / readiness flow:

- `semantic_view_agent` — create semantic views plus a Cortex Agent.
- `cortex_search_agent` — create Cortex Search services plus an agent.
- `app_bundle` — create a packaged app or dashboard artifact.

Each new emitter should provide a small wrapper CLI and a test fixture proving it can build from a
self-contained plugin zip without reaching back to the monorepo.