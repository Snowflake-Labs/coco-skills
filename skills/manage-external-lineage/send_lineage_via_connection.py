#!/usr/bin/env python3
"""
Send OpenLineage events using existing Snowflake connection.
Uses the connection configured in Cortex Code (snow CLI connections).

Usage:
    SNOWFLAKE_CONNECTION_NAME=<connection> python send_lineage_via_connection.py -p payload.json
    
    # Or with inline payload:
    SNOWFLAKE_CONNECTION_NAME=<connection> python send_lineage_via_connection.py --inline '{...}'
"""

import argparse
import json
import os
import sys
import requests
import snowflake.connector


def get_session_token(conn):
    """Extract session token from active connection."""
    return conn.rest._token


def get_account_url(conn):
    """Get the account URL from connection."""
    host = conn.host
    if '_' in host:
        host = host.replace('_', '-')
    return f"https://{host}"


def send_lineage_event(conn, payload, token_file=None):
    """Send lineage event using connection's session token or PAT."""
    url = f"{get_account_url(conn)}/api/v2/lineage/external-lineage"
    
    if token_file:
        with open(token_file) as f:
            token = f.read().strip()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
            "User-Agent": "external-lineage-skill/2.0"
        }
    else:
        token = get_session_token(conn)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Snowflake Token=\"{token}\"",
            "User-Agent": "external-lineage-skill/2.0"
        }
    
    response = requests.post(url, headers=headers, json=payload)
    return response


def main():
    parser = argparse.ArgumentParser(description="Send OpenLineage events via Snowflake connection")
    parser.add_argument("-p", "--payload", help="Path to JSON payload file")
    parser.add_argument("--inline", help="Inline JSON payload")
    parser.add_argument("-c", "--connection", help="Connection name (or use SNOWFLAKE_CONNECTION_NAME env var)")
    args = parser.parse_args()
    
    if not args.payload and not args.inline:
        print("Error: Provide -p PAYLOAD_FILE or --inline JSON", file=sys.stderr)
        sys.exit(1)
    
    if args.payload:
        with open(args.payload) as f:
            payload = json.load(f)
    else:
        payload = json.loads(args.inline)
    
    connection_name = args.connection or os.getenv("SNOWFLAKE_CONNECTION_NAME")
    if not connection_name:
        print("Error: Set SNOWFLAKE_CONNECTION_NAME or use -c CONNECTION", file=sys.stderr)
        sys.exit(1)
    
    print(f"Connecting via: {connection_name}")
    conn = snowflake.connector.connect(connection_name=connection_name)
    
    cursor = conn.cursor()
    cursor.execute("SELECT CURRENT_ROLE(), CURRENT_ACCOUNT()")
    role, account = cursor.fetchone()
    print(f"Account: {account}, Role: {role}")
    
    cursor.execute("SHOW GRANTS ON ACCOUNT")
    grants = cursor.fetchall()
    has_ingest = any("INGEST LINEAGE" in str(g) for g in grants)
    if not has_ingest:
        print("Warning: INGEST LINEAGE privilege not found for current role", file=sys.stderr)
    
    token_file = None
    try:
        import subprocess
        result = subprocess.run(
            ['snow', 'connection', 'list', '--format', 'json'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            import json as json_mod
            connections = json_mod.loads(result.stdout)
            for c in connections:
                if c.get('connection_name') == connection_name:
                    token_file = c.get('parameters', {}).get('token_file_path')
                    break
    except Exception:
        pass
    
    print(f"Sending lineage event...")
    if token_file:
        print(f"Using PAT from connection config")
    response = send_lineage_event(conn, payload, token_file)
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("Success! Lineage event sent.")
        print("Verify in Snowsight: Catalog > Database Explorer > [Table] > Lineage tab")
    else:
        print(f"Error: {response.text}", file=sys.stderr)
        sys.exit(1)
    
    conn.close()


if __name__ == "__main__":
    main()
