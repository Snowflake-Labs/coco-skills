#!/usr/bin/env python3
"""
SMA Dashboard Server Launcher

Usage:
    python start_server.py [--port PORT] [--no-open]

This script starts the SMA Dashboard server and opens the browser.
Run this from the sma-dashboard directory.
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import webbrowser


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    if platform.system() == 'Windows':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        except Exception:
            try:
                output = subprocess.check_output(
                    ['tasklist', '/FI', f'PID eq {pid}'],
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                return str(pid) in output.decode()
            except Exception:
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def check_existing_server(dashboard_dir: str) -> int | None:
    """Check if a server is already running. Returns port if running, None otherwise."""
    pid_file = os.path.join(dashboard_dir, 'server', '.server.pid')
    
    if not os.path.exists(pid_file):
        return None
    
    try:
        with open(pid_file, 'r') as f:
            content = f.read().strip()
            if ':' in content:
                pid_str, port_str = content.split(':')
                pid = int(pid_str)
                port = int(port_str)
            else:
                return None
        
        if is_process_running(pid):
            return port
        else:
            os.remove(pid_file)
            return None
    except (ValueError, FileNotFoundError):
        return None


def find_available_port(start_port: int = 8080, max_attempts: int = 20) -> int | None:
    """Find an available port starting from start_port (tries 8080-8099 by default)."""
    for attempt in range(max_attempts):
        port = start_port + attempt
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    return None


def stop_server(dashboard_dir: str) -> bool:
    """Stop the running server if any."""
    pid_file = os.path.join(dashboard_dir, 'server', '.server.pid')
    
    if not os.path.exists(pid_file):
        print("No server is currently running.")
        return False
    
    try:
        with open(pid_file, 'r') as f:
            content = f.read().strip()
            if ':' in content:
                pid = int(content.split(':')[0])
            else:
                print("Invalid PID file format.")
                return False
        
        if platform.system() == 'Windows':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                         capture_output=True)
        else:
            os.kill(pid, 15)  # SIGTERM
        
        os.remove(pid_file)
        print(f"Server (PID: {pid}) stopped.")
        return True
    except Exception as e:
        print(f"Error stopping server: {e}")
        return False


def list_active_servers():
    """List all active sma_server.py processes."""
    import subprocess
    
    try:
        # Get PIDs listening on common ports
        if platform.system() == 'Windows':
            # Windows: use netstat
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'tcp'],
                capture_output=True, text=True
            )
            # Parse Windows netstat output (more complex)
            print("Active servers (Windows):")
            print("Use Task Manager to view python processes")
            return
        else:
            # Unix/macOS: use lsof to find processes on ports 8080-8099
            result = subprocess.run(
                ['lsof', '-i', ':8080-8099', '-sTCP:LISTEN'],
                capture_output=True, text=True
            )
            
            if not result.stdout.strip():
                print("No servers found on ports 8080-8099")
                return
            
            # Get PIDs from lsof output
            lines = result.stdout.strip().split('\n')
            if len(lines) <= 1:
                print("No servers found on ports 8080-8099")
                return
            
            # Port name to number mapping
            port_map = {
                'http-alt': '8080', 
                'sunproxyadmin': '8081', 
                'us-cli': '8082',
                'oa-system': '8083',
                'websnp': '8084'
            }
            
            # Parse lsof output to get PID -> port mapping
            pid_to_port = {}
            pids = set()
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 9:
                    pid = parts[1]
                    pids.add(pid)
                    # NAME is second to last, format: "localhost:port-name"
                    # Last column is "(LISTEN)"
                    name = parts[-2]  # e.g., "localhost:http-alt"
                    if ':' in name:
                        port_name = name.split(':')[-1]
                        port = port_map.get(port_name, port_name)
                        pid_to_port[pid] = port
            
            if not pids:
                print("No servers found")
                return
            
            # Get full command for each PID
            pid_list = ','.join(pids)
            ps_result = subprocess.run(
                ['ps', '-p', pid_list, '-o', 'pid,lstart,command'],
                capture_output=True, text=True
            )
            
            print("Active servers on ports 8080-8099:\n")
            print("-" * 80)
            
            servers = []
            for line in ps_result.stdout.strip().split('\n')[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 6:
                    pid = parts[0]
                    # lstart format: Day Mon DD HH:MM:SS YYYY
                    started = ' '.join(parts[1:6])
                    command = ' '.join(parts[6:])
                    
                    # Check if it's sma_server.py
                    is_sma = 'sma_server.py' in command
                    
                    # Get port from our mapping
                    port = pid_to_port.get(pid, "?")
                    
                    servers.append({
                        'pid': pid,
                        'port': port,
                        'started': started,
                        'is_sma': is_sma,
                        'command': command
                    })
            
            # Print formatted output
            for s in servers:
                marker = "[SMA]" if s['is_sma'] else "[OTHER]"
                print(f"{marker} PID: {s['pid']}  Port: {s['port']}")
                print(f"    Started: {s['started']}")
                # Show clickable URL
                if s['port'] != "?":
                    print(f"    URL: http://localhost:{s['port']}/index.html")
                # Truncate long commands
                cmd = s['command']
                if len(cmd) > 70:
                    cmd = cmd[:67] + "..."
                print(f"    Command: {cmd}")
                print()
            
            print("-" * 80)
            print(f"Total: {len(servers)} server(s)")
            
            # Show quick links for SMA servers
            sma_servers = [s for s in servers if s['is_sma'] and s['port'] != "?"]
            if sma_servers:
                print("\nQuick links:")
                for s in sma_servers:
                    print(f"  http://localhost:{s['port']}/index.html")
            
            sma_pids = [s['pid'] for s in servers if s['is_sma']]
            if sma_pids:
                print(f"\nTo stop all SMA servers: kill {' '.join(sma_pids)}")
                
    except FileNotFoundError:
        print("Error: lsof/ps commands not found")
    except Exception as e:
        print(f"Error listing servers: {e}")


def main():
    parser = argparse.ArgumentParser(description='SMA Dashboard Server Launcher')
    parser.add_argument('--port', type=int, default=8080, help='Port to run server on (default: 8080)')
    parser.add_argument('--no-open', action='store_true', help='Do not open browser automatically')
    parser.add_argument('--stop', action='store_true', help='Stop the running server')
    parser.add_argument('--restart', action='store_true', help='Restart the server (stop + start)')
    parser.add_argument('--status', action='store_true', help='Check if server is running')
    parser.add_argument('--list', action='store_true', help='List all active servers on ports 8080-8099')
    args = parser.parse_args()
    
    # Handle --list (doesn't require being in dashboard directory)
    if args.list:
        list_active_servers()
        return 0
    
    # Get the dashboard directory (where this script is located)
    dashboard_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(dashboard_dir, 'server', 'sma_server.py')
    
    # Verify we're in the right directory
    if not os.path.exists(server_script):
        print("Error: server/sma_server.py not found.")
        print("Make sure you're running this from the sma-dashboard directory.")
        return 1
    
    # Handle --status
    if args.status:
        existing_port = check_existing_server(dashboard_dir)
        if existing_port:
            print(f"Server is running at http://localhost:{existing_port}")
        else:
            print("Server is not running.")
        return 0
    
    # Handle --stop
    if args.stop:
        stop_server(dashboard_dir)
        return 0
    
    # Handle --restart
    if args.restart:
        stop_server(dashboard_dir)
        import time
        time.sleep(0.5)  # Give time for port to be released
    
    # Check if server is already running (skip if restarting)
    if not args.restart:
        existing_port = check_existing_server(dashboard_dir)
        if existing_port:
            url = f"http://localhost:{existing_port}/index.html"
            print(f"""
+--------------------------------------------------------------+
|  Server already running at: http://localhost:{existing_port:<5}            |
+--------------------------------------------------------------+
|  Dashboard: {url:<47} |
|                                                              |
|  To stop the server:  python start_server.py --stop          |
|  To restart:          python start_server.py --restart       |
+--------------------------------------------------------------+
""")
            if not args.no_open:
                webbrowser.open(url)
                print(f"\n  Tip: To view inside your editor, press Cmd+Shift+P → 'Simple Browser: Show'")
                print(f"       then paste: {url}\n")
            return 0
    
    # Find available port
    port = find_available_port(args.port)
    if port is None:
        print(f"Error: Could not find an available port (tried {args.port}-{args.port + 9})")
        return 1
    
    # Launch server as background process
    # Server will read sqlite_path and workload from manifest.json automatically
    cmd = [
        sys.executable, server_script, dashboard_dir,
        '--port', str(port),
        '--no-open'
    ]
    
    if platform.system() == 'Windows':
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )
    
    # Save PID file
    pid_file = os.path.join(dashboard_dir, 'server', '.server.pid')
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    with open(pid_file, 'w') as f:
        f.write(f"{proc.pid}:{port}")
    
    # Wait and verify
    import time
    time.sleep(0.5)
    
    if proc.poll() is not None:
        print("Error: Server failed to start.")
        print(f"Try running manually: python server/sma_server.py . --port {port}")
        return 1
    
    url = f"http://localhost:{port}/index.html"
    
    # Read manifest for display info
    db_name = "sma_storage.sqlite3"
    manifest_path = os.path.join(dashboard_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                sqlite_path = manifest.get('sqlite_path', '')
                if sqlite_path:
                    db_name = os.path.basename(sqlite_path)
        except:
            pass
    
    print(f"""
+--------------------------------------------------------------+
|  SMA Dashboard Server v2.0.0 (SQLite)                        |
+--------------------------------------------------------------+
|  Server running at: http://localhost:{port:<5}                  |
|  Dashboard: {url:<47} |
|  Database: {db_name:<48} |
|                                                              |
|  Server is running in background (PID: {proc.pid:<6})              |
|                                                              |
|  Commands:                                                   |
|    python start_server.py --status   Check server status     |
|    python start_server.py --stop     Stop the server         |
+--------------------------------------------------------------+
""")
    
    if not args.no_open:
        webbrowser.open(url)
        print(f"\n  Tip: To view inside your editor, press Cmd+Shift+P → 'Simple Browser: Show'")
        print(f"       then paste: {url}\n")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
