# Stack to Business Ontology mapping contract

This file defines how an ontology-stack-builder deployment maps onto Snowflake native Business Ontology (BON). `scripts/export_to_bon.py` implements it. If you change one, change the other and the tests in `tests/test_export_to_bon.py`.

## Principles

1. **Meaning moves, execution stays.** Classes, relations, descriptions and table bindings move into BON. Physical data (`KG_NODE`/`KG_EDGE`, source tables), the base semantic view, inference procedures, graph UDFs and SPCS stay where they are.
2. **Nothing is silently dropped.** Every source row that has no BON home is listed in `migration_loss_report.md`, and its full original row is kept in `ONT_BON_MIGRATION_MAP.SOURCE_ROW`.
3. **The domain is never invented.** The user names it (`--domain`); every candidate carries `domainSource: USER_DECLARED`.
4. **No edge without both endpoints.** A relationship whose domain or range class is not migrated is reported, not written.

## Schema variants

The stack's metadata tables differ by how they were created. The exporter detects the variant from column names.

| Variant | Created by | Detection | Class binding columns |
|---|---|---|---|
| `GENERATOR` | `scripts/generate_ontology_sql.py` (normal skill run) | `ONT_CLASS_MAP.SOURCE_TABLE` and `ID_EXPR` | `SOURCE_DATABASE`, `SOURCE_SCHEMA`, `SOURCE_TABLE` (`KG_NODE` on the KG path) |
| `TEMPLATE` | `references/metadata_tables_template.sql` | `ONT_CLASS_MAP.SOURCE_TABLE` plus `PROPS_EXPR`, or `ONT_PROPERTY.PROPERTY_NAME` | same as `GENERATOR` |
| `REFERENCE` | the `ontology-on-snowflake` reference repo | `ONT_CLASS_MAP.CONCRETE_VIEW` | `CONCRETE_VIEW` (a view in the stack schema) |
| `ID_KEYED` | hand-built stacks keyed by numeric IDs | `ONT_CLASS.CLASS_ID` | `ONT_CLASS.SOURCE_TABLE` (3-part FQN); relations use `SOURCE_CLASS_ID` / `TARGET_CLASS_ID`; properties carry `SOURCE_COLUMN` |

`ONT_PROPERTY` uses `PROP_NAME` (generator, reference) or `PROPERTY_NAME` (template, id-keyed). Both are accepted. Columns are resolved by name, so extra or missing optional columns (`STATUS`, `RENDER_HINT`, `CLASS_LABEL`, ...) do not break the export. Rows whose `STATUS` is set and not `ACTIVE` are skipped.

## Object mapping

| Source | BON object | Rule |
|---|---|---|
| `ONT_ONTOLOGY` row | Domain | Name = `--domain` (user-confirmed). `ONT_ONTOLOGY.DESCRIPTION` becomes the domain description when the domain is new. |
| `ONT_CLASS` row | Node, `itemKind: ENTITY` | Name = `CLASS_LABEL` when present, else the humanized `CLASS_NAME` (`PurchaseOrder`, `PURCHASE_ORDER` -> `Purchase Order`); the original `CLASS_NAME` is added as a synonym when it differs. `Thing` is skipped. Parentless abstract roots with generic names (`Entity`, `Object`, ...) are migrated but flagged `generic_root` for the review gate. |
| `ONT_CLASS.DESCRIPTION` | Node description | Capped at 270 characters. Generated defaults (empty, `Entity from table ...`, `Auto-generated ...`, equal to the class name) are flagged `needs_description` for the review gate. |
| `ONT_CLASS.PARENT_CLASS_NAME` | Relationship `parent HAS_VARIANT child` | BON has no `IS_A`; `HAS_VARIANT` is the closest standard type for a narrower kind of the same concept. Skipped when the parent is `Thing` or not migrated. Override at the review gate if the steward prefers `RELATED_TO`. |
| `ONT_RELATION_DEF` row | Relationship `DOMAIN_CLASS -> RANGE_CLASS` | Type chosen by the table below. A relation that only restates another one in reverse (its `INVERSE_REL_NAME` points at a row with swapped endpoints) is folded into the forward relation: the row marked `RENDER_HINT = inverse` or described as "Inverse..." is dropped, else the later row. Self-referential relations (`Company related_to Company`) are not migrated: BON requires distinct endpoints. |
| `ONT_RELATION_DEF.CARDINALITY`, `INVERSE_REL_NAME`, `IS_TRANSITIVE` | Not representable | Kept in `SOURCE_ROW`, listed in the loss report. |
| `ONT_CLASS_MAP` / `ONT_OBJECT_SOURCE` / `ONT_CLASS.SOURCE_TABLE` | Representation, role `DESCRIBES` | `GENERATOR`/`TEMPLATE` direct path: `SOURCE_DATABASE.SOURCE_SCHEMA.SOURCE_TABLE`. KG path (`SOURCE_TABLE = KG_NODE`): the typed view `V_{CLASS}` in the stack schema. `REFERENCE`: `CONCRETE_VIEW`, only when it is the class's own view (abstract classes mapped to their subclasses' views are not bound; the subclasses are). `ID_KEYED`: `ONT_CLASS.SOURCE_TABLE`. `refType` is `VIEW` or `TABLE` from `INFORMATION_SCHEMA.TABLES`; a binding to an object that no longer exists is reported, not written. When the declared source is gone, the class is **not** bound to `V_{CLASS}` either: that view selects from the missing source and cannot compile. |
| `ONT_PROPERTY` row | COLUMN representation on the class node, role `DESCRIBES` | Parent = the class binding above; `dimensionName` = `SOURCE_COLUMN` when present, else the upper-cased property name. Properties are **not** nodes (a node per column is the column-dictionary anti-pattern the BON gate blocks). Skipped when the column is not in the parent object, or is KG plumbing (`NODE_ID`, `NODE_TYPE`, `PROPS`, `TS_INGESTED`) on a `V_*` view. `NAME` is kept: it is the entity's display name. |
| `ONT_SHARED_PROPERTY` row | Optional node, `itemKind: DIMENSION_CONCEPT`, plus `CLASSIFIES` edges to each implementing class | Only with `--include-shared-properties`. Off by default because generated shared properties are usually `name`/`created_at` noise. `realisations` = number of classes that carry it. |
| `ONT_DERIVED_PROPERTY` with `SQL_EXPR` | Node named `{Class} {Property}` (class-qualified, so `Player Age` and `Coach Age` do not collide), with the unqualified name (`Age`, `Career Duration`) as a synonym so it stays findable. `METRIC` with `formula` when the expression is a plain aggregate (`SUM`, `COUNT`, ... and no `SELECT`); otherwise `TERM` with the expression in the description | `metric MEASURES class` for metrics, `class RELATED_TO term` otherwise. Rows without `SQL_EXPR` (function-backed) are reported. |
| Data semantic view metrics (`{ONT}_BASE`, or `{ONT}_KG_MODEL` on the KG path) | `METRIC` node with `formula` (the SV expression), synonyms and description, plus a `RELATED_SEMANTIC_VIEW` representation (`subObjectType: METRIC`) | `metric MEASURES class`: the class bound to the metric's base table, or the source class of the relation view `V_{REL}`. Produced by `bon_import_metrics.py`. Not imported: metrics on tables bound to no migrated class, and plain aggregates whose description only restates the name (B-09; `--include-trivial` overrides). Both are recorded `NOT_MIGRATED` in the map table. |

## Relationship type selection

Checked in order; first match wins. Names are compared lower-cased with `_`, `-` and spaces removed.

| Condition | BON type | Direction |
|---|---|---|
| `REL_NAME` is `subclassof` / `hassubclass` | skipped (hierarchy comes from `PARENT_CLASS_NAME`) | - |
| name in `partof`, `belongsto`, `memberof`, `componentof` | `HAS_PART` | swapped: `RANGE_CLASS -> DOMAIN_CLASS` (whole -> part) |
| name in `haspart`, `contains`, `hascomponent`, `hasmember`, `consistsof` | `HAS_PART` | `DOMAIN_CLASS -> RANGE_CLASS` |
| name in `identifiedby`, `hasidentifier` | `IDENTIFIED_BY` | as declared |
| name in `equivalentto`, `sameas` | `EQUIVALENT_TO` | as declared |
| name in `relatedto`, `associatedwith`, `relatesto` | `RELATED_TO` | as declared |
| anything else | `CUSTOM`, `label = RELATION_LABEL` or the humanized `REL_NAME` (`plays_for` -> `plays for`) | as declared |

`IS_HIERARCHICAL` alone does not select `HAS_PART`: real stacks set it on peer relations such as `Company related_to Company`. Such rows are noted for review instead.

BON guidance says not to use `CUSTOM` as a fallback for ambiguity. Business verbs like `plays for` or `listed on` have no standard type, so they are exported as `CUSTOM` with the verb as label and every one is listed at the review gate, where the user confirms or overrides it.

The 11 valid BON types are `HAS_VARIANT`, `HAS_PART`, `DERIVES`, `MEASURES`, `IDENTIFIED_BY`, `CLASSIFIES`, `APPLIES_TO`, `SCOPES`, `EQUIVALENT_TO`, `RELATED_TO`, `CUSTOM`. A label is sent only with `CUSTOM`. The review gate shows this table with the actual relations so the user can override any choice (`--rel-type-override REL_NAME=TYPE`).

## Not migrated (loss report)

| Source | Why |
|---|---|
| `KG_NODE` / `KG_EDGE` rows, `REL_EDGE_INFERRED` | BON stores concepts, not instances. The data stays queryable. |
| `ONT_RULE`, inference procedures, `ONT_CONSTRAINT_VIOLATION` | BON has no rule or inference engine. |
| `ACT_TYPE`, `ACT_DEF`, `ACT_INVOCATION` | Operational actions are explicitly not planned for BON. |
| `ONT_FUNCTION`, `ONT_FUNCTION_BINDING`, graph UDFs, SPCS service | Execution assets; they stay as agent tools. |
| `ONT_INTERFACE*` | No interface concept in BON. |
| `OBJ_VIEW_DEF`, `OBJ_VIEW_FIELD` | UI rendering hints. |
| `ONT_ROLE`, `ONT_ROLE_BINDING`, `ONT_PERMISSION` | BON access is governed by Snowflake privileges, not ontology roles. |
| Cardinality, inverse, transitivity, symmetric flags, join keys | BON edges carry only a type and (for `CUSTOM`) a label. |
| Self-referential relations | BON requires distinct source and target nodes. |
