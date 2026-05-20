#!/usr/bin/env python3
"""
analyze_table.py - Analyze string columns across all tables in a semantic view

Usage:
    python3 analyze_table.py \
        --semantic-view DATABASE.SCHEMA.VIEW \
        --warehouse WAREHOUSE_NAME \
        --output analysis.json
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Any
import snowflake.connector
import yaml


def parse_qualified_name(name: str) -> tuple:
    parts = name.split('.')
    if len(parts) != 3:
        raise ValueError(f"Expected fully qualified name (DB.SCHEMA.OBJECT), got: {name}")
    return tuple(parts)


def get_connection(connection_name: str):
    try:
        conn = snowflake.connector.connect(connection_name=connection_name)
        return conn
    except Exception as e:
        print(f"Failed to connect to Snowflake: {e}", file=sys.stderr)
        sys.exit(1)


def get_semantic_view_yaml(conn, semantic_view_name: str) -> Dict[str, Any]:
    """Fetch semantic view YAML and parse it"""
    cursor = conn.cursor()
    try:
        query = f"SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('{semantic_view_name}')"
        cursor.execute(query)
        result = cursor.fetchone()

        if result and result[0]:
            yaml_content = result[0]
            return yaml.safe_load(yaml_content)
        else:
            print(f"Could not read semantic view YAML", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Failed to read semantic view: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        cursor.close()


def extract_base_tables(semantic_view_yaml: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract all base tables from semantic view YAML"""
    tables = []

    if 'tables' not in semantic_view_yaml:
        print(f"Warning: No tables found in semantic view", file=sys.stderr)
        return tables

    for table in semantic_view_yaml['tables']:
        if 'base_table' in table:
            base_table = table['base_table']
            tables.append({
                'logical_name': table['name'],
                'database': base_table['database'],
                'schema': base_table['schema'],
                'table': base_table['table'],
                'full_name': f"{base_table['database']}.{base_table['schema']}.{base_table['table']}"
            })

    return tables


def get_string_dimensions(semantic_view_yaml: Dict[str, Any], logical_table_name: str) -> List[str]:
    """Get all string dimension column names for a logical table"""
    dimensions = []

    for table in semantic_view_yaml.get('tables', []):
        if table.get('name') == logical_table_name:
            for dim in table.get('dimensions', []):
                # Check if it's a string type
                data_type = dim.get('data_type', '').upper()
                if 'VARCHAR' in data_type or 'TEXT' in data_type or 'STRING' in data_type:
                    dimensions.append(dim['name'])
            break

    return dimensions


def get_column_stats(conn, table_name: str, column_name: str) -> Dict[str, Any]:
    cursor = conn.cursor()
    try:
        query = f"""
        SELECT
            COUNT(DISTINCT {column_name}) as distinct_count,
            COUNT({column_name}) as total_count
        FROM {table_name}
        WHERE {column_name} IS NOT NULL
        """
        cursor.execute(query)
        result = cursor.fetchone()

        distinct_count = result[0] if result[0] else 0
        total_count = result[1] if result[1] else 0

        return {
            'distinct_count': distinct_count,
            'total_count': total_count
        }
    finally:
        cursor.close()


def get_sample_values(conn, table_name: str, column_name: str, limit: int = 10) -> List[str]:
    cursor = conn.cursor()
    try:
        query = f"""
        SELECT DISTINCT {column_name}
        FROM {table_name}
        WHERE {column_name} IS NOT NULL
        ORDER BY {column_name}
        LIMIT {limit}
        """
        cursor.execute(query)
        results = cursor.fetchall()
        return [str(row[0]) for row in results]
    finally:
        cursor.close()


def analyze_table_dimensions(conn, table_info: Dict[str, str], dimension_names: List[str]) -> List[Dict[str, Any]]:
    """Analyze string dimensions for a single table"""
    table_name = table_info['full_name']
    table_short_name = table_info['table']

    print(f"   Analyzing {len(dimension_names)} dimension(s) in {table_info['logical_name']}...")

    column_analyses = []

    for dim_name in dimension_names:
        print(f"      - {dim_name}...", end=' ')

        try:
            stats = get_column_stats(conn, table_name, dim_name)
            distinct_count = stats['distinct_count']

            is_low_cardinality = distinct_count <= 10

            if is_low_cardinality:
                sample_values = get_sample_values(conn, table_name, dim_name, limit=distinct_count)
                recommendation = 'enum'
            else:
                sample_values = get_sample_values(conn, table_name, dim_name, limit=3)
                recommendation = 'search_service'

            search_service_name = f"{table_short_name}_{dim_name}_SS".upper()

            column_analysis = {
                'name': dim_name,
                'distinct_count': distinct_count,
                'total_count': stats['total_count'],
                'recommendation': recommendation,
                'sample_values': sample_values,
                'search_service_name': search_service_name if recommendation == 'search_service' else None,
                'logical_table': table_info['logical_name']
            }

            column_analyses.append(column_analysis)
            print(f"{distinct_count} distinct values")

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)

    return column_analyses


def analyze_semantic_view(conn, semantic_view_name: str, warehouse_name: str) -> Dict[str, Any]:
    sv_database, sv_schema, sv_name = parse_qualified_name(semantic_view_name)

    print(f"Analyzing semantic view {semantic_view_name}...")

    # Get semantic view YAML
    semantic_view_yaml = get_semantic_view_yaml(conn, semantic_view_name)

    # Extract all base tables
    base_tables = extract_base_tables(semantic_view_yaml)
    print(f"   Found {len(base_tables)} base table(s)")

    # Analyze string dimensions for each table
    all_columns = []
    for table_info in base_tables:
        dimension_names = get_string_dimensions(semantic_view_yaml, table_info['logical_name'])
        if dimension_names:
            column_analyses = analyze_table_dimensions(conn, table_info, dimension_names)
            all_columns.extend(column_analyses)

    analysis = {
        'semantic_view_name': semantic_view_name,
        'warehouse_name': warehouse_name,
        'sv_database': sv_database,
        'sv_schema': sv_schema,
        'sv_name': sv_name,
        'base_tables': base_tables,
        'columns': all_columns,
        'current_semantic_view': semantic_view_yaml
    }

    return analysis


def main():
    parser = argparse.ArgumentParser(
        description="Analyze string dimensions across all tables in a semantic view"
    )
    parser.add_argument(
        '--semantic-view',
        required=True,
        help='Fully qualified semantic view name (DATABASE.SCHEMA.VIEW)'
    )
    parser.add_argument(
        '--warehouse',
        required=True,
        help='Warehouse name for Cortex Search Services'
    )
    parser.add_argument(
        '--connection',
        default=os.environ.get('SNOWFLAKE_CONNECTION_NAME', 'demo'),
        help='Snowflake connection name (default: SNOWFLAKE_CONNECTION_NAME env var or "demo")'
    )
    parser.add_argument(
        '--output',
        required=True,
        help='Output JSON file path'
    )

    args = parser.parse_args()

    conn = get_connection(args.connection)

    try:
        analysis = analyze_semantic_view(
            conn,
            args.semantic_view,
            args.warehouse
        )

        with open(args.output, 'w') as f:
            json.dump(analysis, f, indent=2)

        print(f"\nAnalysis complete. Results written to {args.output}")

        print("\nSummary:")
        enum_cols = [c for c in analysis['columns'] if c['recommendation'] == 'enum']
        search_cols = [c for c in analysis['columns'] if c['recommendation'] == 'search_service']

        if enum_cols:
            print(f"\n   Low-Cardinality Dimensions (<=10 distinct values):")
            for col in enum_cols:
                print(f"   - {col['logical_table']}.{col['name']}: {col['distinct_count']} distinct values")

        if search_cols:
            print(f"\n   High-Cardinality Dimensions (>10 distinct values):")
            for col in search_cols:
                print(f"   - {col['logical_table']}.{col['name']}: {col['distinct_count']} distinct values -> {col['search_service_name']}")

    finally:
        conn.close()


if __name__ == '__main__':
    main()
