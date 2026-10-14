#!/usr/bin/env python3
"""
ui_window.py — Pop a native app window for the progress UI.

Strategy (in order):
  1. pywebview   — native OS window (WKWebView/macOS, Edge WebView2/Windows,
                   WebKit2GTK/Linux). Best UX: no browser chrome, stays on top.
  2. Chrome/Chromium --app mode — frameless "installed app" window.
  3. webbrowser.open — regular browser tab fallback.

Spawned as a detached subprocess by ui_launch.py; migration never waits on it.

Usage:
    python3 ui_window.py --url http://localhost:7860 [--title "Spark Migration"]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _try_pywebview(url: str, title: str) -> bool:
    try:
        import webview  # type: ignore
        window = webview.create_window(
            title, url,
            width=1280, height=860,
            resizable=True,
            min_size=(800, 600),
        )
        webview.start(gui=_detect_gui_backend())
        return True
    except ImportError:
        return False
    except Exception:
        return False


def _detect_gui_backend() -> str | None:
    """Pick the right pywebview backend for the current platform."""
    import platform
    s = platform.system()
    if s == "Darwin":
        return None  # default (cocoa)
    if s == "Windows":
        return None  # default (edgechromium / mshtml)
    # Linux — prefer gtk, fall back to qt
    for name in ("gtk", "qt"):
        try:
            import importlib
            importlib.import_module(f"webview.platforms.{name}")
            return name
        except Exception:
            continue
    return None


def _try_chrome_app(url: str, title: str) -> bool:
    """Open a frameless Chrome/Chromium app window."""
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        # Windows (common paths via shutil won't find these, so listed explicitly)
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    exe = None
    for c in candidates:
        found = shutil.which(c) or (c if shutil.os.path.isfile(c) else None)
        if found:
            exe = found
            break
    if not exe:
        return False
    try:
        subprocess.Popen(
            [exe,
             f"--app={url}",
             "--window-size=1280,860",
             "--window-position=80,80",
             f"--window-name={title}",
             "--disable-extensions",
             "--no-first-run",
             "--no-default-browser-check"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _try_browser(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url",   required=True)
    p.add_argument("--title", default="Spark Migration · Progress (Experimental)")
    args = p.parse_args()

    # Wait for the server to be ready before opening anything
    if not _wait_for_server(args.url, timeout=12):
        # Server didn't come up — silently exit; migration continues
        sys.exit(0)

    if _try_pywebview(args.url, args.title):
        return
    if _try_chrome_app(args.url, args.title):
        return
    _try_browser(args.url)


if __name__ == "__main__":
    main()
