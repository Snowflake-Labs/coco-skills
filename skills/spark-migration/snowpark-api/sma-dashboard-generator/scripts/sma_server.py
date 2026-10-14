"""
SMA Dashboard Server - HTTP server with sma_api database access
"""

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import sma_api


def create_handler(base_dir: str, sqlite_path: str, workload_name: str = ""):
    """Create a request handler with the specified base directory and SQLite path."""

    workload_path = os.path.dirname(sqlite_path)

    class SMARequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            self.base_dir = base_dir
            self.sqlite_path = sqlite_path
            self.workload_name = workload_name
            self.workload_path = workload_path
            super().__init__(*args, directory=base_dir, **kwargs)

        def end_headers(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            super().end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.end_headers()

        def _get_data_from_sqlite(self):
            """Read all issues and return aggregated data via sma_api."""
            rows = sma_api.read_issues_raw(db_path=self.sqlite_path)
            result = sma_api.extract_ewi_data(rows, self.workload_name)
            return result['ewi_data'], result['file_data']

        def do_GET(self):
            parsed_path = urlparse(self.path)

            if parsed_path.path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
                return

            if parsed_path.path == '/api/metadata':
                try:
                    metadata = sma_api.get_all_metadata(self.workload_path, db_path=self.sqlite_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(metadata, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/ewi/data':
                try:
                    ewi_data, _ = self._get_data_from_sqlite()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(ewi_data, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/file/data':
                try:
                    _, file_data = self._get_data_from_sqlite()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(file_data, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/ewi/descriptions':
                try:
                    descriptions = sma_api.get_ewi_descriptions(self.workload_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(descriptions, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/dependency/summary':
                try:
                    data = sma_api.get_dependency_summary_by_file(self.workload_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'data': data}, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/dependency/inventory':
                try:
                    data = sma_api.get_dependency_inventory(self.workload_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'data': data}, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/test/data':
                try:
                    data = sma_api.get_tests(self.workload_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/test/runs':
                try:
                    from urllib.parse import parse_qs
                    params = parse_qs(parsed_path.query)
                    test_id = int(params['test_id'][0]) if 'test_id' in params else None
                    data = sma_api.get_test_runs(self.workload_path, test_id=test_id)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/test/export':
                try:
                    data = sma_api.export_test_results(self.workload_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path.startswith('/api/test/export/download/'):
                try:
                    filename = parsed_path.path.split('/api/test/export/download/')[-1]
                    filepath = os.path.join(
                        self.workload_path, "dvp", "04-results", "testing-results", filename
                    )
                    if not os.path.isfile(filepath):
                        self.send_response(404)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({'error': 'File not found'}).encode())
                        return
                    with open(filepath, 'rb') as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/csv')
                    self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            if parsed_path.path == '/api/dependency/graph':
                try:
                    data = sma_api.get_dependency_graph(self.workload_path)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            super().do_GET()

        def do_POST(self):
            parsed_path = urlparse(self.path)

            if parsed_path.path == '/api/shutdown':
                self._handle_shutdown()
            elif parsed_path.path == '/api/ewi/update':
                self._handle_ewi_update()
            elif parsed_path.path == '/api/file/update':
                self._handle_file_update()
            elif parsed_path.path == '/api/file/ewi/update':
                self._handle_file_ewi_update()
            elif parsed_path.path == '/api/file/ewi/update-all':
                self._handle_file_ewi_update_all()
            elif parsed_path.path == '/api/dependency/update':
                self._handle_dependency_update()
            elif parsed_path.path == '/api/file/validation':
                self._handle_file_validation()
            elif parsed_path.path == '/api/file/recommended-actions':
                self._handle_recommended_actions()
            elif parsed_path.path == '/api/test/update':
                self._handle_test_update()
            elif parsed_path.path == '/api/test/run':
                self._handle_test_run()
            else:
                self.send_response(404)
                self.end_headers()

        def _handle_shutdown(self):
            """Handle server shutdown request."""
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'shutting_down'}).encode())

            pid_file = os.path.join(self.base_dir, 'server', '.server.pid')
            try:
                os.remove(pid_file)
            except FileNotFoundError:
                pass

            import threading
            threading.Thread(target=self.server.shutdown).start()

        def _handle_ewi_update(self):
            """Handle EWI status/notes update - updates all occurrences via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                code = update.get('code')
                new_status = update.get('status')
                new_notes = update.get('notes')

                if new_status:
                    sma_api.update_ewi_status(self.workload_path, code, new_status)
                if new_notes is not None:
                    sma_api.update_ewi_notes(self.workload_path, code, new_notes)

                ewi_data, _ = self._get_data_from_sqlite()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'summary': ewi_data.get('summary', {})
                }).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_file_update(self):
            """Handle file status update - updates all lines in the file via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                file_path = update.get('file_path')
                new_status = update.get('file_status')
                update_all_ewis = update.get('update_all_ewis', False)

                if new_status and update_all_ewis:
                    sma_api.update_file_status(self.workload_path, file_path, new_status)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_file_ewi_update(self):
            """Handle line-level status update within a specific file/EWI via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                file_path = update.get('file_path')
                ewi_code = update.get('code')
                line_num = update.get('line')
                new_status = update.get('status')

                sma_api.update_line_status(self.workload_path, file_path, ewi_code, line_num, new_status)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_file_ewi_update_all(self):
            """Handle bulk status update for all lines of an EWI in a file via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                file_path = update.get('file_path')
                ewi_code = update.get('code')
                new_status = update.get('status')

                sma_api.update_file_ewi_status(self.workload_path, file_path, ewi_code, new_status)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_dependency_update(self):
            """Handle dependency status update via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                file_id = update.get('file_id')
                dependency = update.get('dependency')
                new_status = update.get('status')

                if not file_id or not dependency or not new_status:
                    raise ValueError("file_id, dependency, and status are required")

                result = sma_api.update_dependency_status(self.workload_path, file_id, dependency, new_status)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': True,
                    'file_validated': result.get('file_validated')
                }).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_file_validation(self):
            """Handle file validation status update via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                file_id = update.get('file_id')
                validated = update.get('validated', 0)

                if not file_id:
                    raise ValueError("file_id is required")

                if isinstance(validated, bool):
                    validated = 2 if validated else 0
                else:
                    validated = int(validated)

                sma_api.update_file_validation(self.workload_path, file_id, validated)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_recommended_actions(self):
            """Handle recommended actions update via sma_api."""
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                file_id = update.get('file_id')
                recommended_actions = update.get('recommended_actions', '')

                if not file_id:
                    raise ValueError("file_id is required")

                sma_api.update_recommended_actions(self.workload_path, file_id, recommended_actions)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': True}).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_test_update(self):
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                update = json.loads(post_data.decode('utf-8'))

                test_id = update.get('test_id')
                new_status = update.get('status')

                if not test_id or not new_status:
                    raise ValueError("test_id and status are required")

                result = sma_api.update_test_status(self.workload_path, int(test_id), new_status)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def _handle_test_run(self):
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))

                test_id = data.get('test_id')
                status = data.get('status')
                error_message = data.get('error_message')
                duration_seconds = data.get('duration_seconds')
                test_method = data.get('test_method')

                if not test_id or not status:
                    raise ValueError("test_id and status are required")

                result = sma_api.insert_test_run(
                    self.workload_path, int(test_id), status,
                    error_message=error_message,
                    duration_seconds=float(duration_seconds) if duration_seconds else None,
                    test_method=test_method
                )

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        def log_message(self, format, *args):
            if 'POST' in str(args):
                print(f"  {args[0]}")

    return SMARequestHandler


def find_available_port(start_port=8080, max_attempts=100):
    """Find an available port starting from start_port."""
    import socket
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    return None


def run_server(base_dir: str, sqlite_path: str, workload_name: str = "", port: int = 8080, open_browser: bool = True):
    """Run the SMA Dashboard server."""
    import webbrowser

    handler = create_handler(base_dir, sqlite_path, workload_name)

    try:
        server = HTTPServer(('localhost', port), handler)
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Port {port} is already in use                                 ║
╠══════════════════════════════════════════════════════════════╣
║  Try a different port:                                       ║
║    python3 sma_server.py <dir> --port {port + 1}                    ║
╚══════════════════════════════════════════════════════════════╝
""")
            return None
        raise

    url = f"http://localhost:{port}/index.html"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SMA Dashboard Server v2.0.0 (SQLite)                        ║
╠══════════════════════════════════════════════════════════════╣
║  Server running at: http://localhost:{port:<5}                  ║
║  Dashboard: {url:<47} ║
║  Database: {os.path.basename(sqlite_path):<48} ║
║                                                              ║
║  Changes are saved directly to SQLite database.              ║
║  Press Ctrl+C to stop the server.                            ║
╚══════════════════════════════════════════════════════════════╝
""")

    if open_browser:
        webbrowser.open(url)

    return server


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='SMA Dashboard Server')
    parser.add_argument('directory', help='Directory to serve')
    parser.add_argument('--sqlite', default=None, help='Path to SQLite database (reads from manifest.json if not provided)')
    parser.add_argument('--workload', default='', help='Workload name')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on')
    parser.add_argument('--auto-port', action='store_true', help='Automatically find available port if default is in use')
    parser.add_argument('--no-open', action='store_true', help="Don't open browser automatically")

    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: Directory not found: {args.directory}")
        exit(1)

    sqlite_path = args.sqlite
    workload_name = args.workload

    manifest_path = os.path.join(args.directory, 'manifest.json')
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
                if not sqlite_path:
                    sqlite_path = manifest.get('sqlite_path')
                if not workload_name:
                    workload_name = manifest.get('workload_name', '')
        except (json.JSONDecodeError, KeyError):
            pass

    if not sqlite_path:
        print("Error: SQLite path not found. Provide --sqlite or ensure manifest.json contains sqlite_path")
        exit(1)

    if not os.path.exists(sqlite_path):
        print(f"Error: SQLite database not found: {sqlite_path}")
        exit(1)

    port = args.port
    if args.auto_port:
        port = find_available_port(args.port)
        if port is None:
            print(f"Error: Could not find available port starting from {args.port}")
            exit(1)
        if port != args.port:
            print(f"Port {args.port} in use, using port {port}")

    server = run_server(args.directory, sqlite_path, workload_name, port, not args.no_open)
    if server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
            server.shutdown()
