# Generated Customer Plugin Layout

The builder writes a normal CoCo plugin directory.

```text
customer-finance/
├── .cortex-plugin/
│   └── plugin.json
├── README.md
├── skills/
│   ├── selected_skill_one/
│   └── selected_skill_two/
└── evals/
    └── selected_skill_one/
```

Generated plugins should be customer-owned. Avoid writing into GitHub- or Catalog-sourced plugin installs in place because sync may overwrite local edits.

Activation:

```bash
cortex plugin add /path/to/customer-finance
/plugin reload
```