#!/usr/bin/env python3
"""
apply_changes.py - Apply enrichment changes to semantic view

Creates Cortex Search Services and updates semantic view with:
- is_enum flags and all sample values for low-cardinality columns
- Search service references and sample values for high-cardinality columns

Usage:
    python apply_changes.py --analysis analysis.json --connection CONNECTION_NAME
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Any
import snowflake.connector
import yaml


def get_connection(connection_name: str):
    try:
        conn = snowflake.connector.connect(connection_name=connection_name)
        return conn
    except Exception as e:
        print(f"Failed to connect to Snowflake: {e}", file=sys.stderr)
        sys.exit(1)


def create_search_service(conn, column: Dict[str, Any], analysis: Dict[str, Any], target_lag: str = '1 day'):
    service_name = column['search_service_name']
    col_name = column['name']
    warehouse = analysis['warehouse_name']
    sv_database = analysis['sv_database']
    sv_schema = analysis['sv_schema']

    # Find the base table for this column
    logical_table = column['logical_table']
    base_table_info = None
    for table in analysis['base_tables']:
        if table['logical_name'] == logical_table:
            base_table_info = table
            break

    if not base_table_info:
        print(f"   Warning: Could not find base table for {logical_table}", file=sys.stderr)
        return

    table_name = base_table_info['full_name']
    full_service_name = f"{sv_database}.{sv_schema}.{service_name}"

    print(f"   Creating search service: {full_service_name}...")

    ddl = f"""
CREATE OR REPLACE CORTEX SEARCH SERVICE {full_service_name}
  ON {col_name}
  WAREHOUSE = {warehouse}
  TARGET_LAG = '{target_lag}'
  AS (
      SELECT DISTINCT {col_name} FROM {table_name}
  )
"""

    cursor = conn.cursor()
    try:
        cursor.execute(ddl)
        print(f"   Created: {service_name}")
    except Exception as e:
        print(f"   Failed to create {service_name}: {e}", file=sys.stderr)
        raise
    finally:
        cursor.close()


def get_current_semantic_view_yaml(conn, semantic_view_name: str) -> Dict[str, Any]:
    """Fetch current semantic view YAML using SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW"""
    print(f"   Reading current semantic view YAML...")

    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('{semantic_view_name}')")
        result = cursor.fetchone()
        if result:
            yaml_str = result[0]
            return yaml.safe_load(yaml_str)
        else:
            print(f"   Warning: No YAML returned for {semantic_view_name}", file=sys.stderr)
            return {}
    except Exception as e:
        print(f"   Warning: Failed to read semantic view YAML: {e}", file=sys.stderr)
        return {}
    finally:
        cursor.close()


def enhance_yaml_with_enrichments(current_yaml: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Add is_enum flags and cortex_search_service references to YAML"""

    if 'tables' not in current_yaml:
        print(f"   Warning: No tables found in semantic view YAML", file=sys.stderr)
        return current_yaml

    # Group columns by logical table
    columns_by_table = {}
    for col in analysis['columns']:
        logical_table = col['logical_table']
        if logical_table not in columns_by_table:
            columns_by_table[logical_table] = []
        columns_by_table[logical_table].append(col)

    # Process each table in the YAML
    for table in current_yaml['tables']:
        table_name = table.get('name')

        if table_name not in columns_by_table:
            continue

        if 'dimensions' not in table:
            print(f"   Warning: No dimensions found in table {table_name}", file=sys.stderr)
            continue

        # Process each dimension
        for dim in table['dimensions']:
            dim_name = dim.get('name')

            # Find matching column in analysis
            matching_col = None
            for col in columns_by_table[table_name]:
                if col['name'] == dim_name:
                    matching_col = col
                    break

            if not matching_col:
                continue

            # Update sample values
            dim['sample_values'] = matching_col['sample_values']

            # Add is_enum or cortex_search_service
            if matching_col['recommendation'] == 'enum':
                dim['is_enum'] = True
                # Remove cortex_search_service if present
                if 'cortex_search_service' in dim:
                    del dim['cortex_search_service']
                print(f"   Enhanced {table_name}.{dim_name}: is_enum=true, {len(matching_col['sample_values'])} sample values")

            elif matching_col['recommendation'] == 'search_service':
                dim['cortex_search_service'] = {
                    'service': matching_col['search_service_name'],
                    'literal_column': matching_col['name'],
                    'database': analysis['sv_database'],
                    'schema': analysis['sv_schema']
                }
                # Remove is_enum if present
                if 'is_enum' in dim:
                    del dim['is_enum']
                print(f"   Enhanced {table_name}.{dim_name}: cortex_search_service={matching_col['search_service_name']}, {len(matching_col['sample_values'])} sample values")

    return current_yaml


def update_semantic_view_with_yaml(conn, analysis: Dict[str, Any], enhanced_yaml: Dict[str, Any]):
    """Recreate semantic view using SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML"""
    semantic_view_name = analysis['semantic_view_name']
    sv_database = analysis['sv_database']
    sv_schema = analysis['sv_schema']

    print(f"   Recreating semantic view {semantic_view_name}...")

    yaml_str = yaml.dump(enhanced_yaml, default_flow_style=False, sort_keys=False)

    call_sql = f"""
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  '{sv_database}.{sv_schema}',
  $${yaml_str}$$
)
"""

    cursor = conn.cursor()
    try:
        cursor.execute(call_sql)
        result = cursor.fetchone()
        if result:
            print(f"   {result[0]}")
    except Exception as e:
        print(f"   Failed to update semantic view: {e}", file=sys.stderr)
        print(f"   Generated YAML:\n{yaml_str}", file=sys.stderr)
        raise
    finally:
        cursor.close()


def apply_changes(conn, analysis: Dict[str, Any]):
    print(f"Applying changes...")

    search_service_columns = [c for c in analysis['columns'] if c['recommendation'] == 'search_service']
    enum_columns = [c for c in analysis['columns'] if c['recommendation'] == 'enum']

    # Get update lag map from analysis (defaults to '1 day' if not present)
    update_lag_map = analysis.get('update_lag_map', {})

    # Step 1: Create search services
    if search_service_columns:
        print(f"\nCreating {len(search_service_columns)} Cortex Search Service(s)...")
        for col in search_service_columns:
            service_name = col['search_service_name']
            target_lag = update_lag_map.get(service_name, '1 day')
            create_search_service(conn, col, analysis, target_lag)

    # Step 2: Get current semantic view YAML
    print(f"\nReading current semantic view...")
    current_yaml = get_current_semantic_view_yaml(conn, analysis['semantic_view_name'])

    # Step 3: Enhance YAML with enrichments
    print(f"\nEnhancing semantic view YAML...")
    enhanced_yaml = enhance_yaml_with_enrichments(current_yaml, analysis)

    # Step 4: Recreate semantic view with enhanced YAML
    print(f"\nUpdating semantic view...")
    update_semantic_view_with_yaml(conn, analysis, enhanced_yaml)

    print(f"\nAll changes applied successfully!")

    print(f"\nSummary:")
    print(f"   Total base tables analyzed: {len(analysis['base_tables'])}")
    print(f"   Total dimensions enriched: {len(analysis['columns'])}")

    if enum_columns:
        print(f"\n   Enum columns updated: {len(enum_columns)}")
        for col in enum_columns:
            print(f"   - {col['logical_table']}.{col['name']}: {col['distinct_count']} distinct values, is_enum=true")

    if search_service_columns:
        print(f"\n   Search services created: {len(search_service_columns)}")
        for col in search_service_columns:
            print(f"   - {col['logical_table']}.{col['name']}: {col['search_service_name']}")


def main():
    parser = argparse.ArgumentParser(
        description="Apply enrichment changes to semantic view"
    )
    parser.add_argument(
        '--analysis',
        required=True,
        help='Path to analysis JSON file from analyze_table.py'
    )
    parser.add_argument(
        '--connection',
        default=os.environ.get('SNOWFLAKE_CONNECTION_NAME', 'demo'),
        help='Snowflake connection name (default: SNOWFLAKE_CONNECTION_NAME env var or "demo")'
    )
    parser.add_argument(
        '--update-lag',
        help='Default TARGET_LAG for search services (e.g., "1 day", "1 minute"). Can be overridden per service in analysis.json'
    )

    args = parser.parse_args()

    with open(args.analysis, 'r') as f:
        analysis = json.load(f)

    # Apply default update lag if specified via CLI and not already in analysis
    if args.update_lag and 'update_lag_map' not in analysis:
        search_service_columns = [c for c in analysis['columns'] if c['recommendation'] == 'search_service']
        analysis['update_lag_map'] = {c['search_service_name']: args.update_lag for c in search_service_columns}

    conn = get_connection(args.connection)

    try:
        apply_changes(conn, analysis)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
