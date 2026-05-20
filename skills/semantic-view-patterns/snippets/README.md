# Semantic Snippets

Atomic, executable patterns for Snowflake Semantic Views. Each snippet covers one modeling concept end-to-end: the use case, example schema with seed data, the SV DDL, and example queries showing what works and what doesn't.

## How to Use

Each snippet is self-contained and deployable against any Snowflake account. Files in each directory:

| File | Contents |
|------|----------|
| `README.md` | Use case, how to express the need, equivalents in other tools, gotchas |
| `schema.sql` | `CREATE TABLE` DDL for the example tables |
| `seed_data.sql` | `INSERT` statements — small enough to run in any account |
| `semantic_view.sql` | The `CREATE OR REPLACE SEMANTIC VIEW` DDL |
| `queries.sql` | `SEMANTIC_VIEW()` queries — what works, what doesn't, and why |

All SQL targets `SNIPPETS.PUBLIC` by default. Swap in your own database/schema.

## Snippets

### Relationship Patterns
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`range_join/`](range_join/) | Range join (BETWEEN EXCLUSIVE) | Join to the dimension record valid within an explicit start/end window (SCD2 with both dates) |
| [`asof_join/`](asof_join/) | ASOF join | Join to the most recent dimension record active *as of* the event date — no end date required |
| [`multi_path_metrics/`](multi_path_metrics/) | USING clause | Disambiguate when a fact has two range relationships to the same dimension table |
| [`shared_degenerate_dimension/`](shared_degenerate_dimension/) | Shared degenerate dimension | Two facts share a low-cardinality column (`region`, `status`) — union distinct values into a helper, create one shared dimension entity |

### Metric Patterns
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`semi_additive_metric/`](semi_additive_metric/) | Semi-additive / NON ADDITIVE BY | Snapshot data where summing across time double-counts (balances, headcount, inventory) |
| [`window_metrics/`](window_metrics/) | Window functions (LAG, rolling AVG, YTD) | Period-over-period comparisons, smoothed trends, year-to-date cumulative totals |
| [`derived_metrics/`](derived_metrics/) | Cross-table derived metrics | Totals, ratios, and % of total across multiple fact tables |
| [`time_intelligence/`](time_intelligence/) | Role-playing aliases + computed-FK FACTS | SPLY, SPLM, YoY%, MoM% — no window functions; date shift lives in the join key |

### Entity & Dimension Patterns
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`entity_facts/`](entity_facts/) | Entity-level aggregated facts + calculated dims | Customer LTV aggregated from orders; derived value segments; calculated age from birth year |
| [`variables/`](variables/) | VARIABLES clause | Parameterized SVs with runtime-adjustable weights, thresholds, and date windows |

### Multi-Fact Patterns
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`multi_fact_table/`](multi_fact_table/) | Multiple fact tables | Store, web, and returns as independent facts sharing a product and date dimension |

### AI & Governance
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`ai_metadata/`](ai_metadata/) | AI_SQL_GENERATION, AI_QUESTION_CATEGORIZATION, AI_VERIFIED_QUERIES | Steer Cortex Analyst query style, scope, and pre-approved SQL |
| [`tags/`](tags/) | `WITH TAG` on metrics | Tag metrics with owner/status metadata; discover via `tag_references()` |

### Ops & Tooling
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`introspection/`](introspection/) | SHOW METRICS, SHOW DIMENSIONS, DESCRIBE, get_lineage() | Discover what's in a SV, check metric-dimension compatibility, trace data lineage |
| [`standard_sql/`](standard_sql/) | Standard SQL on SVs | Query a SV like a view with plain SELECT; `ANY_VALUE()`, metric-less dim queries |

### Inline SV ⚠️ *Private Preview*
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`inline_sv/`](inline_sv/) | Inline SV + SQL subquery as table | Ad-hoc SV CTEs for testing; SQL subquery as inline table definition — inline SV syntax requires account enablement |

### Data Scoping ⚠️ *Private Preview*
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`scoped_dataset/`](scoped_dataset/) | SQL query as logical table (LOB scoping) | Embed `WHERE lob='Enterprise'` directly in the TABLES clause to create one SV per LOB/segment from a single source table |

### Performance ⚠️ *Private Preview*
| Directory | Concept | Use Case |
|-----------|---------|----------|
| [`materialization/`](materialization/) | Semantic view materialization | Pre-aggregate selected dimension/metric combinations to speed up repeated rollup queries; use `IMMUTABLE WHERE` for incremental refresh of historical data |
