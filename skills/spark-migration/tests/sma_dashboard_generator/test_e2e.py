"""
End-to-end tests for sma-dashboard-generator.

Tests the complete workflow:
1. Generate dashboard from Issues.csv
2. Verify generated file structure
3. Start HTTP server
4. Test API endpoints (health, EWI update, file update, shutdown)
5. Verify data persistence
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

import pytest


# Paths
SKILL_DIR = Path(__file__).parent.parent.parent / "sma-dashboard-generator"
SCRIPTS_DIR = SKILL_DIR / "scripts"
TEMPLATES_DIR = SKILL_DIR / "templates"


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def e2e_workspace():
    """Create a complete workspace simulating an SMA output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        # Create Reports directory with Issues.csv
        reports_dir = workspace / "Reports"
        reports_dir.mkdir()
        
        issues_csv = reports_dir / "Issues.csv"
        issues_csv.write_text("""Code,Description,Category,FileId,Line,Column
SPRKPY-1001,PySpark conversion warning,Conversion,src/main.py,10,5
SPRKPY-1001,PySpark conversion warning,Conversion,src/utils.py,20,10
SPRKPY-2002,Performance issue,Performance,src/etl/loader.py,50,1
SSC-EWI-0001,SQL conversion issue,SQL,queries/test.sql,5,1
""")
        
        # Create Output directory with sample files
        output_dir = workspace / "Output"
        (output_dir / "src" / "etl").mkdir(parents=True)
        (output_dir / "queries").mkdir(parents=True)
        
        (output_dir / "src" / "main.py").write_text("# main.py")
        (output_dir / "src" / "utils.py").write_text("# utils.py")
        (output_dir / "src" / "etl" / "loader.py").write_text("# loader.py")
        (output_dir / "queries" / "test.sql").write_text("-- test.sql")
        
        yield workspace


@pytest.fixture
def generated_dashboard(e2e_workspace):
    """Generate the dashboard and return its path."""
    # Run sma_manager.py
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "sma_manager.py"),
            str(e2e_workspace / "Reports" / "Issues.csv"),
            "--template-dir", str(TEMPLATES_DIR),
            "--no-open"
        ],
        cwd=str(e2e_workspace),
        capture_output=True,
        text=True
    )
    
    assert result.returncode == 0, f"sma_manager.py failed: {result.stderr}"
    
    dashboard_dir = e2e_workspace / "sma-dashboard"
    assert dashboard_dir.exists(), "Dashboard directory was not created"
    
    yield dashboard_dir


def find_free_port():
    """Find a free port for testing."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('localhost', 0))
        return s.getsockname()[1]


@pytest.fixture
def running_server(generated_dashboard):
    """Start the server and yield (dashboard_dir, port, process)."""
    port = find_free_port()
    server_script = generated_dashboard / "server" / "sma_server.py"
    
    # Start server process
    proc = subprocess.Popen(
        [sys.executable, str(server_script), str(generated_dashboard), "--port", str(port), "--no-open"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    # Wait for server to start
    base_url = f"http://localhost:{port}"
    max_attempts = 20
    for _ in range(max_attempts):
        try:
            urlopen(f"{base_url}/health", timeout=1)
            break
        except (URLError, ConnectionRefusedError):
            time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail("Server did not start in time")
    
    yield generated_dashboard, port, proc
    
    # Cleanup: terminate server
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ===========================================================================
# 1. Dashboard Generation Tests
# ===========================================================================

class TestDashboardGeneration:
    """E2E tests for dashboard generation."""

    def test_creates_dashboard_directory_structure(self, generated_dashboard):
        """Verify the complete directory structure is created."""
        assert (generated_dashboard / "index.html").exists()
        assert (generated_dashboard / "manifest.json").exists()
        assert (generated_dashboard / "assets" / "styles.css").exists()
        assert (generated_dashboard / "server" / "sma_server.py").exists()
        assert (generated_dashboard / "start_server.py").exists()
        assert (generated_dashboard / "ewi-tracker" / "ewi_tracker.html").exists()

    def test_ewi_data_available_via_api(self, running_server):
        """Verify EWI data is correctly stored in SQLite and accessible via API."""
        dashboard_dir, port, proc = running_server

        response = urlopen(f"http://localhost:{port}/api/ewi/data")
        data = json.loads(response.read().decode())

        assert data["total_ewis"] == 3  # SPRKPY-1001, SPRKPY-2002, SSC-EWI-0001
        assert "summary" in data
        assert data["summary"]["pending"] == 3

        codes = [ewi["code"] for ewi in data["ewis"]]
        assert "SPRKPY-1001" in codes
        assert "SPRKPY-2002" in codes
        assert "SSC-EWI-0001" in codes

    def test_ewi_occurrences_are_counted(self, running_server):
        """Verify occurrences are correctly counted."""
        dashboard_dir, port, proc = running_server

        response = urlopen(f"http://localhost:{port}/api/ewi/data")
        data = json.loads(response.read().decode())

        sprkpy_1001 = next(e for e in data["ewis"] if e["code"] == "SPRKPY-1001")
        assert sprkpy_1001["occurrences"] == 2  # main.py and utils.py

    def test_file_data_available_via_api(self, running_server):
        """Verify file data is correctly stored in SQLite and accessible via API."""
        dashboard_dir, port, proc = running_server

        response = urlopen(f"http://localhost:{port}/api/file/data")
        data = json.loads(response.read().decode())

        assert data["total_files"] >= 3

        file_paths = [f["file_path"] for f in data["files"]]
        assert "src/main.py" in file_paths
        assert "src/utils.py" in file_paths

    def test_content_pages_generated_for_each_ewi(self, generated_dashboard):
        """Verify detail pages are created for each EWI."""
        content_dir = generated_dashboard / "ewi-tracker" / "content"
        assert content_dir.exists()
        
        html_files = list(content_dir.glob("files_*.html"))
        assert len(html_files) == 3  # One for each unique EWI

    def test_manifest_contains_module_config(self, generated_dashboard):
        """Verify manifest.json has correct module configuration."""
        manifest = json.loads((generated_dashboard / "manifest.json").read_text())
        
        assert "modules" in manifest
        assert "ewi_tracker" in manifest["modules"]
        assert manifest["modules"]["ewi_tracker"]["enabled"] is True


# ===========================================================================
# 2. Server API Tests
# ===========================================================================

class TestServerAPI:
    """E2E tests for the HTTP server API."""

    def test_health_endpoint_returns_ok(self, running_server):
        """Test /health endpoint."""
        dashboard_dir, port, proc = running_server
        
        response = urlopen(f"http://localhost:{port}/health")
        data = json.loads(response.read().decode())
        
        assert data["status"] == "ok"

    def test_serves_static_files(self, running_server):
        """Test that static files are served correctly."""
        dashboard_dir, port, proc = running_server
        
        # Request index.html
        response = urlopen(f"http://localhost:{port}/index.html")
        content = response.read().decode()
        
        assert "<!DOCTYPE html>" in content or "<html" in content

    def test_ewi_update_endpoint_updates_status(self, running_server):
        """Test /api/ewi/update endpoint changes EWI status."""
        dashboard_dir, port, proc = running_server
        
        update_data = json.dumps({"code": "SPRKPY-1001", "status": "manual_resolved"}).encode()
        request = Request(
            f"http://localhost:{port}/api/ewi/update",
            data=update_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urlopen(request)
        result = json.loads(response.read().decode())
        
        assert result["success"] is True
        assert result["summary"]["manual_resolved"] >= 1
        
        response = urlopen(f"http://localhost:{port}/api/ewi/data")
        data = json.loads(response.read().decode())
        sprkpy = next(e for e in data["ewis"] if e["code"] == "SPRKPY-1001")
        assert sprkpy["status"] == "manual_resolved"

    def test_ewi_update_endpoint_updates_notes(self, running_server):
        """Test /api/ewi/update endpoint saves notes in the database."""
        dashboard_dir, port, proc = running_server

        update_data = json.dumps({
            "code": "SPRKPY-2002",
            "notes": "Fixed in PR #123"
        }).encode()
        request = Request(
            f"http://localhost:{port}/api/ewi/update",
            data=update_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urlopen(request)
        result = json.loads(response.read().decode())
        assert result["success"] is True

        import sqlite3
        sqlite_path = str(dashboard_dir.parent / "sma_storage.sqlite3")
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT notes FROM issues WHERE Code = ?", ("SPRKPY-2002",))
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) > 0
        assert rows[0]["notes"] == "Fixed in PR #123"

    def test_file_update_endpoint_updates_status(self, running_server):
        """Test /api/file/update endpoint changes file status."""
        dashboard_dir, port, proc = running_server
        
        update_data = json.dumps({
            "file_path": "src/main.py",
            "file_status": "in_progress"
        }).encode()
        request = Request(
            f"http://localhost:{port}/api/file/update",
            data=update_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urlopen(request)
        result = json.loads(response.read().decode())
        
        assert result["success"] is True

    def test_file_ewi_update_endpoint_updates_line_status(self, running_server):
        """Test /api/file/ewi/update endpoint changes line status within a file/EWI."""
        dashboard_dir, port, proc = running_server
        
        response = urlopen(f"http://localhost:{port}/api/file/data")
        data = json.loads(response.read().decode())
        main_py = next(f for f in data["files"] if f["file_path"] == "src/main.py")
        ewi = next(e for e in main_py["ewis"] if e["code"] == "SPRKPY-1001")
        line_num = ewi["lines"][0]["line"]
        
        update_data = json.dumps({
            "file_path": "src/main.py",
            "code": "SPRKPY-1001",
            "line": line_num,
            "status": "manual_resolved"
        }).encode()
        request = Request(
            f"http://localhost:{port}/api/file/ewi/update",
            data=update_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        response = urlopen(request)
        result = json.loads(response.read().decode())
        
        assert result["success"] is True


# ===========================================================================
# 3. Data Persistence Tests
# ===========================================================================

class TestDataPersistence:
    """E2E tests for data persistence across regeneration."""

    def test_regeneration_preserves_ewi_status(self, e2e_workspace):
        """Test that regenerating the dashboard preserves EWI status in SQLite."""
        csv_path = e2e_workspace / "Reports" / "Issues.csv"

        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sma_manager.py"), str(csv_path),
             "--template-dir", str(TEMPLATES_DIR), "--no-open"],
            cwd=str(e2e_workspace), check=True
        )

        sqlite_path = str(e2e_workspace / "sma_storage.sqlite3")
        assert os.path.exists(sqlite_path)

        sys.path.insert(0, str(SCRIPTS_DIR))
        sys.path.insert(0, str(SCRIPTS_DIR.parent.parent.parent / "scripts"))
        import sma_api
        sma_api.update_ewi_status(str(e2e_workspace), "SPRKPY-1001", "manual_resolved")

        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sma_manager.py"), str(csv_path),
             "--template-dir", str(TEMPLATES_DIR), "--no-open"],
            cwd=str(e2e_workspace), check=True
        )

        rows = sma_api.read_issues_raw(str(e2e_workspace))
        result = sma_api.extract_ewi_data(rows, "test")
        ewi_data = result['ewi_data']
        sprkpy = next(e for e in ewi_data["ewis"] if e["code"] == "SPRKPY-1001")
        assert sprkpy["status"] == "manual_resolved"

    def test_regeneration_preserves_file_status(self, e2e_workspace):
        """Test that regenerating the dashboard preserves file status in SQLite."""
        csv_path = e2e_workspace / "Reports" / "Issues.csv"

        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sma_manager.py"), str(csv_path),
             "--template-dir", str(TEMPLATES_DIR), "--no-open"],
            cwd=str(e2e_workspace), check=True
        )

        sys.path.insert(0, str(SCRIPTS_DIR))
        sys.path.insert(0, str(SCRIPTS_DIR.parent.parent.parent / "scripts"))
        import sma_api
        sma_api.update_file_status(str(e2e_workspace), "src/main.py", "in_progress")

        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sma_manager.py"), str(csv_path),
             "--template-dir", str(TEMPLATES_DIR), "--no-open"],
            cwd=str(e2e_workspace), check=True
        )

        rows = sma_api.read_issues_raw(str(e2e_workspace))
        result = sma_api.extract_ewi_data(rows, "test")
        file_data = result['file_data']
        main_py = next(f for f in file_data["files"] if f["file_path"] == "src/main.py")
        assert main_py["file_status"] == "in_progress"

    def test_regeneration_preserves_line_status_per_file(self, e2e_workspace):
        """Test that regenerating the dashboard preserves line-level status."""
        csv_path = e2e_workspace / "Reports" / "Issues.csv"

        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sma_manager.py"), str(csv_path),
             "--template-dir", str(TEMPLATES_DIR), "--no-open"],
            cwd=str(e2e_workspace), check=True
        )

        sys.path.insert(0, str(SCRIPTS_DIR))
        sys.path.insert(0, str(SCRIPTS_DIR.parent.parent.parent / "scripts"))
        import sma_api

        rows = sma_api.read_issues_raw(str(e2e_workspace))
        result = sma_api.extract_ewi_data(rows, "test")
        file_data = result['file_data']
        main_py = next(f for f in file_data["files"] if f["file_path"] == "src/main.py")
        first_ewi = main_py["ewis"][0]
        target_line = first_ewi["lines"][0]["line"]

        sma_api.update_line_status(str(e2e_workspace), "src/main.py", first_ewi["code"], target_line, "manual_resolved")

        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sma_manager.py"), str(csv_path),
             "--template-dir", str(TEMPLATES_DIR), "--no-open"],
            cwd=str(e2e_workspace), check=True
        )

        rows = sma_api.read_issues_raw(str(e2e_workspace))
        result = sma_api.extract_ewi_data(rows, "test")
        file_data = result['file_data']
        main_py = next(f for f in file_data["files"] if f["file_path"] == "src/main.py")
        ewi = next(e for e in main_py["ewis"] if e["code"] == first_ewi["code"])
        line_statuses = {ln["line"]: ln["status"] for ln in ewi["lines"]}
        assert line_statuses[target_line] == "manual_resolved"


# ===========================================================================
# 4. Start Server Script Tests
# ===========================================================================

class TestStartServerScript:
    """E2E tests for start_server.py launcher script."""

    def test_start_server_script_exists_in_dashboard(self, generated_dashboard):
        """Verify start_server.py is copied to dashboard."""
        start_script = generated_dashboard / "start_server.py"
        assert start_script.exists()

    def test_start_server_status_when_not_running(self, generated_dashboard):
        """Test --status flag when no server is running."""
        start_script = generated_dashboard / "start_server.py"
        
        result = subprocess.run(
            [sys.executable, str(start_script), "--status"],
            cwd=str(generated_dashboard),
            capture_output=True,
            text=True
        )
        
        assert "not running" in result.stdout.lower() or result.returncode == 0

    def test_start_server_list_option(self, generated_dashboard):
        """Test --list flag shows active servers."""
        start_script = generated_dashboard / "start_server.py"
        
        result = subprocess.run(
            [sys.executable, str(start_script), "--list"],
            cwd=str(generated_dashboard),
            capture_output=True,
            text=True
        )
        
        # Should complete without error
        assert result.returncode == 0
        