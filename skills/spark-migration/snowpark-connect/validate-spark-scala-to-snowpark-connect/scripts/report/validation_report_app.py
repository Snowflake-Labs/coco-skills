from __future__ import annotations

import html
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validation_report_data import (
    entrypoint_short_name,
    load_parquet_artifact,
    load_validation_run,
    now_utc,
)


st.set_page_config(
    page_title="Snowpark Connect Validation Report",
    page_icon="❄️",
    layout="wide",
)


def cli_run_root() -> str:
    """Path from --run-root / VALIDATION_RUN_ROOT when launching the app."""
    env_path = os.environ.get("VALIDATION_RUN_ROOT", "").strip()
    if env_path:
        return env_path
    for index, token in enumerate(sys.argv):
        if token in ("--run-root", "--validation-path") and index + 1 < len(sys.argv):
            return sys.argv[index + 1].strip()
    return ""


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _pick_directory_macos(initialdir: str = "") -> str | None:
    import subprocess

    prompt = _escape_applescript("Select validation run directory")
    if initialdir:
        location = _escape_applescript(str(Path(initialdir).expanduser().resolve()))
        script = (
            f'POSIX path of (choose folder with prompt "{prompt}" '
            f'default location (POSIX file "{location}"))'
        )
    else:
        script = f'POSIX path of (choose folder with prompt "{prompt}")'

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    picked = result.stdout.strip()
    return picked or None


def _pick_directory_zenity(initialdir: str = "") -> str | None:
    import shutil
    import subprocess

    zenity = shutil.which("zenity")
    if not zenity:
        return None
    cmd = [zenity, "--file-selection", "--directory", "--title=Select validation run directory"]
    if initialdir:
        cmd.append(f"--filename={initialdir}/")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    picked = result.stdout.strip()
    return picked or None


def _pick_directory_subprocess_tk(initialdir: str = "") -> str | None:
    """Run tkinter in a child process so AppKit gets a real main thread."""
    import subprocess

    helper = """
import sys
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
root.update_idletasks()
kwargs = {"title": "Select validation run directory"}
if len(sys.argv) > 1 and sys.argv[1]:
    kwargs["initialdir"] = sys.argv[1]
picked = filedialog.askdirectory(**kwargs)
if picked:
    print(picked, end="")
root.destroy()
"""
    cmd = [sys.executable, "-c", helper]
    if initialdir:
        cmd.append(initialdir)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    picked = result.stdout.strip()
    return picked or None


def pick_directory(initial: str = "") -> str | None:
    """Open a native folder picker without blocking Streamlit's worker thread."""
    initial_path = Path(initial).expanduser()
    initialdir = ""
    if initial_path.is_dir():
        initialdir = str(initial_path)
    elif initial_path.parent.is_dir():
        initialdir = str(initial_path.parent)

    if sys.platform == "darwin":
        return _pick_directory_macos(initialdir)
    if sys.platform == "win32":
        return _pick_directory_subprocess_tk(initialdir)

    picked = _pick_directory_zenity(initialdir)
    if picked is not None:
        return picked
    return _pick_directory_subprocess_tk(initialdir)


def resolve_run_root() -> str:
    if "run_root_input" not in st.session_state:
        st.session_state.run_root_input = cli_run_root()

    pending = st.session_state.pop("_run_root_pending", None)
    if pending:
        st.session_state.run_root_input = pending

    if st.sidebar.button("Browse directory", width="stretch", key="browse_run_root"):
        try:
            picked = pick_directory(st.session_state.run_root_input)
        except OSError as exc:
            st.sidebar.error(f"Could not open folder picker: {exc}")
        else:
            if picked:
                st.session_state._run_root_pending = picked.rstrip("/")
                st.rerun()

    st.sidebar.text_input(
        "Validation directory",
        key="run_root_input",
        help="Path to a completed validation run containing run_index.json",
    )

    return str(st.session_state.run_root_input or "").strip()


SOURCE_LABEL = "Source"
SCOS_LABEL = "Snowpark Connect"


# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------

GREEN = "#15803d"
AMBER = "#b45309"
RED = "#b91c1c"
GRAY = "#64748b"
BLUE = "#0369a1"
SF_BLUE = "#29b5e8"

# verdict -> (color, short label, plain-English meaning)
VERDICT_META: dict[str, tuple[str, str, str]] = {
    "passed": (GREEN, "Passed", "Snowpark Connect output matched the source baseline. Safe to ship."),
    "match": (GREEN, "Match", "Snowpark Connect output matched the source baseline."),
    "match_with_skips": (GREEN, "Match (skipped cols)", "Matched after ignoring documented cosmetic columns."),
    "baseline_produced": (GREEN, "Baseline produced", "Source run produced a trustworthy local baseline."),
    "done": (GREEN, "Done", "Completed."),
    "pass": (GREEN, "Pass", "Comparison check passed."),
    "fail": (RED, "Fail", "Comparison check failed."),
    "cosmetic_divergence": (AMBER, "Cosmetic divergence", "Differences are cosmetic (ordering, formatting) and non-blocking."),
    "passed_no_baseline": (
        AMBER,
        "Passed · no baseline",
        "Snowpark Connect ran cleanly but there was no source baseline to compare against. A human must review the output by hand.",
    ),
    "no_baseline": (AMBER, "No baseline", "No source baseline was available for comparison."),
    "phase_a_skipped": (GRAY, "Source skipped", "Source run was skipped (e.g. Databricks-only SQL). Snowpark Connect still ran."),
    "partial": (AMBER, "Partial", "Some entrypoints matched; others need manual review or are stuck."),
    "review": (AMBER, "Review", "Operator review recommended before shipping."),
    "unknown": (GRAY, "Unknown", "Verdict not recorded."),
    "real_divergence": (RED, "Real divergence", "Snowpark Connect output diverged from the source baseline in a meaningful way."),
    "diverge": (AMBER, "Documented diff", "Known cosmetic difference documented as non-blocking."),
    "hard_stuck": (RED, "Hard stuck", "Could not get this entrypoint to a clean Snowpark Connect run."),
    "blocked": (RED, "Blocked", "Blocking issues remain — do not ship."),
    "ship": (GREEN, "Ship", "Recommended to ship."),
    "green": (GREEN, "Ship", "Recommended to ship."),
    "block": (RED, "Block", "Do not ship — blocking issues remain."),
    "success": (GREEN, "Success", "Fixer resolved the error."),
    "no_change": (GRAY, "No change", "Fixer did not change the outcome."),
}


def vmeta(verdict: str | None) -> tuple[str, str, str]:
    return VERDICT_META.get((verdict or "unknown").lower(), (GRAY, str(verdict or "unknown"), ""))


def vcolor(verdict: str | None) -> str:
    return vmeta(verdict)[0]


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; max-width: 1320px; }
        :root {
          --sf-blue: #29b5e8;
          --sf-navy: #0f2b46;
          --sf-light: #f4f9fc;
          --navy: #0f2b46;
          --ink: #16324f;
          --muted: #64748b;
          --line: #e2e8f0;
          --panel: #ffffff;
        }
        .stApp { background: var(--sf-light); }

        /* Force readable text in the main panel regardless of OS color scheme */
        [data-testid="stMain"] { color: #2b3e50; }
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] h3,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] h4,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] h5,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] h6 { color: var(--navy) !important; }
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] li { color: #2b3e50; }
        [data-testid="stMain"] label,
        [data-testid="stMain"] [data-testid="stWidgetLabel"] p { color: #33485c !important; }
        [data-testid="stMain"] [data-baseweb="tab"] { color: #45596b; }
        [data-testid="stMain"] [data-testid="stMetricValue"] { color: var(--navy); }
        [data-testid="stMain"] [data-testid="stMetricLabel"] p { color: var(--muted) !important; }
        [data-testid="stMain"] small { color: var(--muted) !important; }
        /* Hero text stays white over the gradient (high specificity to beat heading overrides) */
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] .hero,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] .hero h1,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] .hero .sub,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] .hero .eyebrow,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] .hero .chip,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] .hero .hero-status { color: #fff !important; }

        /* Hero banner — aligned with assessment report header */
        .hero {
          position: relative; overflow: hidden;
          border-radius: 12px;
          padding: 1.35rem 1.6rem;
          color: #fff;
          margin-bottom: 1.1rem;
          background: var(--sf-navy);
          box-shadow: 0 2px 8px rgba(15,43,70,0.12);
          border-bottom: 3px solid var(--sf-blue);
        }
        .hero-accent {
          position: absolute; top: 0; left: 0; bottom: 0; width: 6px;
        }
        .hero .eyebrow {
          text-transform: uppercase; letter-spacing: .14em;
          font-size: .72rem; font-weight: 700; opacity: .82;
        }
        .hero-headrow { display: flex; align-items: center; gap: .9rem; flex-wrap: wrap; }
        .hero h1 { margin: .25rem 0 .15rem; font-size: 1.65rem; line-height: 1.15; font-weight: 600; }
        .hero-status {
          display: inline-flex; align-items: center; gap: .4rem;
          padding: .3rem .8rem; border-radius: 999px;
          font-size: .82rem; font-weight: 700; color: #fff;
          box-shadow: 0 2px 8px rgba(0,0,0,.18);
        }
        .hero .sub { font-size: .95rem; opacity: .88; margin: 0; color: #d7e8f4; }
        .hero .meta { margin-top: .75rem; display: flex; flex-wrap: wrap; gap: .45rem; }
        .hero .chip {
          background: rgba(255,255,255,.12);
          border: 1px solid rgba(255,255,255,.22);
          padding: .22rem .62rem; border-radius: 999px;
          font-size: .78rem; font-weight: 600;
        }

        /* Prototype-style sections */
        .section {
          background: var(--panel); border-radius: 8px; padding: 1.25rem 1.35rem;
          margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
          border: 1px solid var(--line);
        }
        .section h2 {
          font-size: 1.05rem; color: var(--sf-navy); margin: 0 0 .85rem;
          padding-bottom: .45rem; border-bottom: 1px solid var(--line);
        }
        .report-table { width: 100%; border-collapse: collapse; font-size: .88rem; table-layout: fixed; }
        .report-table th {
          background: var(--sf-light); text-align: left; padding: .65rem .75rem;
          font-weight: 600; color: var(--sf-navy); border-bottom: 2px solid var(--line);
        }
        .report-table td {
          padding: .65rem .75rem; border-bottom: 1px solid var(--line); vertical-align: top;
          overflow-wrap: anywhere; word-break: break-word;
        }
        .report-table tr:hover { background: #f8fafc; }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; color: var(--muted); }
        .ep-title { font-weight: 700; color: var(--navy); font-size: .95rem; }
        .ep-sub { font-size: .78rem; color: var(--muted); margin-top: .15rem; overflow-wrap: anywhere; }
        .ep-id-hint {
          font-size: .72rem; color: #94a3b8; overflow: hidden;
          text-overflow: ellipsis; white-space: nowrap; max-width: 100%;
        }

        /* Streamlit selectbox / long labels */
        [data-testid="stSelectbox"] [data-baseweb="select"] > div {
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }

        /* KPI cards */
        .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .75rem; margin-bottom: .3rem; }
        .kpi {
          background: var(--panel); border: 1px solid var(--line);
          border-radius: 8px; padding: .85rem .95rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }
        .kpi .label { text-transform: uppercase; letter-spacing: .06em; font-size: .68rem;
          font-weight: 700; color: var(--muted); }
        .kpi .value { font-size: 1.65rem; font-weight: 700; color: var(--navy); line-height: 1.15; margin-top: .15rem; }
        .kpi .sub { font-size: .8rem; color: var(--muted); margin-top: .2rem; }

        /* Generic pill / badge */
        .badge {
          display: inline-flex; align-items: center; gap: .35rem;
          padding: .22rem .62rem; border-radius: 999px;
          font-size: .78rem; font-weight: 700; white-space: nowrap;
        }
        .dot { width: .55rem; height: .55rem; border-radius: 50%; display: inline-block; }

        /* Phase flow */
        .flow { display: flex; align-items: stretch; gap: .4rem; margin-top: .7rem; flex-wrap: wrap; }
        .flow-step {
          flex: 1; min-width: 150px;
          border: 1px solid var(--line); border-radius: 12px;
          padding: .6rem .75rem; background: #fbfdff;
        }
        .flow-step .ph { font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
          color: var(--muted); font-weight: 700; }
        .flow-step .vd { font-weight: 700; margin-top: .2rem; font-size: .92rem; }
        .flow-step .det { font-size: .78rem; color: var(--muted); margin-top: .15rem; }
        .flow-arrow { align-self: center; color: #b6c4d2; font-size: 1.2rem; }

        /* Workload errors */
        .workload-error {
          border: 1px solid #fecaca; border-left: 4px solid #dc2626;
          background: #fef2f2; border-radius: 10px; padding: .85rem 1rem;
          margin: .75rem 0 1rem;
        }
        .we-code { font-weight: 700; color: #991b1b; font-size: .9rem; margin-bottom: .35rem; }
        .we-headline { font-weight: 600; color: #7f1d1d; font-size: .95rem; }
        .we-detail { color: #991b1b; margin-top: .35rem; font-size: .88rem; white-space: pre-wrap; }
        .we-state { font-weight: 500; color: #b91c1c; }
        .we-meta { font-size: .78rem; color: #64748b; margin-top: .5rem; }

        /* Timeline */
        .tl { position: relative; margin: .4rem 0 0 .3rem; padding-left: 1.4rem;
          border-left: 2px solid var(--line); }
        .tl-item { position: relative; padding: 0 0 1.05rem .4rem; }
        .tl-item:last-child { padding-bottom: .2rem; }
        .tl-dot { position: absolute; left: -1.72rem; top: .15rem;
          width: .8rem; height: .8rem; border-radius: 50%; border: 2px solid #fff;
          box-shadow: 0 0 0 1px var(--line); }
        .tl-time { font-size: .74rem; color: var(--muted); font-variant-numeric: tabular-nums; }
        .tl-head { font-weight: 600; color: var(--ink); font-size: .92rem; }
        .tl-reason { font-size: .82rem; color: var(--muted); margin-top: .1rem; }

        section[data-testid="stSidebar"] { background: #0f2b46; }
        section[data-testid="stSidebar"] * { color: #e7f1f8; }

        /* Pipeline stepper */
        .pipeline {
          display: flex; align-items: flex-start; gap: 0; overflow-x: auto;
          padding: .5rem 0 1rem;
        }
        .pipe-step {
          flex: 1; min-width: 118px; position: relative; text-align: center;
        }
        .pipe-step:not(:last-child)::after {
          content: ''; position: absolute; top: 1.05rem; left: 58%; width: 84%;
          height: 2px; background: var(--line); z-index: 0;
        }
        .pipe-step.done:not(:last-child)::after { background: #86efac; }
        .pipe-node {
          width: 2.1rem; height: 2.1rem; border-radius: 50%; margin: 0 auto .45rem;
          display: flex; align-items: center; justify-content: center;
          font-size: .78rem; font-weight: 800; border: 2px solid var(--line);
          background: #fff; color: var(--muted); position: relative; z-index: 1;
        }
        .pipe-step.done .pipe-node { background: #dcfce7; border-color: #86efac; color: #166534; }
        .pipe-step.pending .pipe-node { background: #f8fafc; }
        .pipe-label { font-size: .72rem; font-weight: 700; color: var(--navy); line-height: 1.25; }
        .pipe-desc { font-size: .68rem; color: var(--muted); margin-top: .2rem; line-height: 1.35; }

        /* Patch / commit cards */
        .change-card {
          border: 1px solid var(--line); border-radius: 10px; padding: .75rem .9rem;
          margin-bottom: .55rem; background: #fbfdff;
        }
        .change-card .tag {
          display: inline-block; font-size: .68rem; font-weight: 700; text-transform: uppercase;
          letter-spacing: .05em; color: var(--muted); margin-bottom: .25rem;
        }
        .change-card .title { font-weight: 600; color: var(--navy); font-size: .88rem; }
        .change-card .body { font-size: .82rem; color: #475569; margin-top: .25rem; }

        .qual-note {
          font-size: .84rem; color: #475569; margin: 0 0 .75rem;
          padding: .65rem .8rem; background: #fffbeb; border-left: 3px solid #f59e0b;
          border-radius: 0 6px 6px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Small HTML component helpers (each returns a self-contained string)
# ---------------------------------------------------------------------------

def badge_html(verdict: str | None) -> str:
    color, label, _ = vmeta(verdict)
    return (
        f'<span class="badge" style="background:{color}14;color:{color};border:1px solid {color}40;">'
        f'<span class="dot" style="background:{color};"></span>{esc(label)}</span>'
    )


def kpi_html(label: str, value: str, sub: str = "", color: str | None = None) -> str:
    value_style = f"color:{color};" if color else ""
    return (
        '<div class="kpi">'
        f'<div class="label">{esc(label)}</div>'
        f'<div class="value" style="{value_style}">{esc(value)}</div>'
        f'<div class="sub">{esc(sub)}</div>'
        "</div>"
    )


def flow_step_html(phase: str, verdict: str | None, detail: str) -> str:
    color = vcolor(verdict)
    _, label, _ = vmeta(verdict)
    return (
        '<div class="flow-step">'
        f'<div class="ph">{esc(phase)}</div>'
        f'<div class="vd" style="color:{color};">{esc(label)}</div>'
        f'<div class="det">{esc(detail)}</div>'
        "</div>"
    )


def ship_label(raw: str | None) -> str:
    _, label, _ = vmeta(raw)
    return label


def _trial_labels(data) -> dict[str, str]:
    return {ep["id"]: ep.get("short_name") or entrypoint_short_name(ep["id"], ep.get("source_path"))
            for ep in data.entrypoints}


def _shorten_trial_text(text: str, labels: dict[str, str]) -> str:
    out = text
    for trial_id, short in sorted(labels.items(), key=lambda item: len(item[0]), reverse=True):
        out = out.replace(trial_id, short)
    return out


def format_blocker(blocker, labels: dict[str, str] | None = None) -> str:
    if isinstance(blocker, str):
        return _shorten_trial_text(blocker, labels or {})
    if isinstance(blocker, dict):
        trial = blocker.get("trial") or "unknown trial"
        label = (labels or {}).get(trial, entrypoint_short_name(trial))
        kind = (blocker.get("kind") or "blocker").replace("_", " ")
        reason = blocker.get("reason") or ""
        return f"{label}: {kind}" + (f" — {reason}" if reason else "")
    return str(blocker)


def _fmt_dt(dt) -> str:
    if not dt:
        return "n/a"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def entrypoint_table_html(data) -> str:
    rows = []
    for ep in data.entrypoints:
        comp = ep.get("comparison", {}).get("verdict")
        divs = ep.get("divergence_count", 0)
        note = f"{divs} documented div." if divs else "—"
        short = ep.get("short_name") or entrypoint_short_name(ep["id"], ep.get("source_path"))
        rows.append(
            "<tr>"
            f"<td><div class='ep-title' title='{esc(ep['id'])}'>{esc(short)}</div>"
            f"<div class='ep-sub'>{esc(ep.get('source_path') or '')}</div></td>"
            f"<td>{badge_html(ep.get('phase_a_verdict'))}</td>"
            f"<td>{badge_html(ep.get('phase_b_verdict'))}</td>"
            f"<td>{badge_html(comp)}</td>"
            f"<td>{badge_html(ep.get('overall_verdict'))}<br><span class='mono'>{esc(note)}</span></td>"
            "</tr>"
        )
    return (
        '<div class="section"><h2>Entrypoint Results</h2>'
        '<table class="report-table"><thead><tr>'
        "<th>Entrypoint</th><th>Source</th><th>Snowpark Connect</th><th>Comparison</th><th>Overall</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def fixer_table_html(dispatches: list[dict]) -> str:
    if not dispatches:
        return ""
    rows = []
    for item in dispatches:
        trials = ", ".join(
            entrypoint_short_name(t) for t in (item.get("trials_affected") or [])
        )
        rows.append(
            "<tr>"
            f"<td>{esc(item.get('iter'))}</td>"
            f"<td>{esc((item.get('error_class') or '').replace('_', ' '))}</td>"
            f"<td><span class='ep-sub'>{esc(trials)}</span></td>"
            f"<td>{badge_html(item.get('outcome'))}</td>"
            f"<td>{esc(item.get('error_hash') or '')}</td>"
            "</tr>"
        )
    return (
        '<div class="section"><h2>Fixer Dispatches</h2>'
        '<table class="report-table"><thead><tr>'
        "<th>Iter</th><th>Class</th><th>Trials</th><th>Outcome</th><th>Error</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


@st.cache_data(show_spinner=False)
def cached_load_validation_run(root_path: str, _schema_version: int = 6):
    return load_validation_run(root_path)


@st.cache_data(show_spinner=False)
def cached_load_parquet(root_path: str, relative_path: str):
    return load_parquet_artifact(root_path, relative_path)


# ---------------------------------------------------------------------------
# Hero + KPIs
# ---------------------------------------------------------------------------

def entrypoint_pass_count(data) -> int:
    metrics = data.run_metrics
    passes = metrics.get("phase_b_passes")
    if passes is not None:
        return int(passes)
    decision = (data.summary or {}).get("decision", {})
    if decision.get("phase_b_passes") is not None:
        return int(decision["phase_b_passes"])
    return sum(1 for ep in data.entrypoints if (ep.get("overall_verdict") or "").lower() == "passed")


def run_duration_label(metrics: dict) -> str:
    duration = metrics.get("duration_seconds")
    if not duration:
        return "n/a"
    if duration >= 3600:
        return f"{duration // 3600}h {(duration % 3600) // 60}m wall span"
    return f"{duration // 60}m {duration % 60}s wall span"


def render_hero(data) -> None:
    metrics = data.run_metrics
    overall = metrics.get("overall_decision") or metrics.get("status") or "unknown"
    color, label, meaning = vmeta(overall)
    passes = entrypoint_pass_count(data)
    total = metrics.get("entrypoint_count") or 0
    if overall.lower() == "passed" and passes == total and total:
        meaning = "All entrypoints passed — Snowpark Connect output matched the source baseline."
    elif overall.lower() == "blocked":
        meaning = "One or more entrypoints are hard stuck or blocked. Review blockers before shipping."
    elif overall.lower() == "partial":
        meaning = "Some entrypoints passed; others need attention before shipping."
    duration_str = run_duration_label(metrics)
    started = _fmt_dt(metrics.get("started_at"))
    completed = _fmt_dt(metrics.get("completed_at"))

    chips = [
        f"Run {esc(metrics.get('run_id') or '?')}",
        f"{esc(metrics.get('connection') or 'no connection')}",
        f"{esc(metrics.get('database') or 'no database')}",
        f"{esc(total)} entrypoints",
        f"{duration_str}",
    ]
    chip_html = "".join(f'<span class="chip">{c}</span>' for c in chips)

    status_pill = (
        f'<span class="hero-status" style="background:{color};">'
        f'<span class="dot" style="background:#fff;"></span>{esc(label)}</span>'
    )

    st.markdown(
        f"""
        <div class="hero">
          <div class="hero-accent" style="background:{SF_BLUE};"></div>
          <div class="eyebrow">Snowpark Connect Validation Report</div>
          <div class="hero-headrow">
            <h1>{esc(passes)}/{esc(total)} entrypoints passed</h1>
            {status_pill}
          </div>
          <p class="sub">{esc(meaning)}</p>
          <p class="sub" style="margin-top:.35rem;font-size:.82rem;">Started {esc(started)} · Completed {esc(completed)}</p>
          <div class="meta">{chip_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(data) -> None:
    metrics = data.run_metrics
    total = metrics.get("entrypoint_count") or 0
    passes = entrypoint_pass_count(data)
    review = metrics.get("manual_review_required", 0)
    stuck = sum(1 for ep in data.entrypoints if (ep.get("overall_verdict") or "").lower() == "hard_stuck")
    ship = metrics.get("ship_recommendation") or "—"
    divergences = metrics.get("non_blocking_divergences")
    if divergences is None:
        divergences = len(getattr(data, "documented_divergences", []) or data.run_index.get("documented_divergences", []))

    cards = [
        kpi_html("Outcome", ship_label(metrics.get("overall_decision") or metrics.get("status")),
                 "overall decision", vcolor(metrics.get("overall_decision") or metrics.get("status"))),
        kpi_html("Entrypoints passed", f"{passes}/{total}", "matched source baseline", GREEN),
        kpi_html("Hard stuck", str(stuck), "could not complete", RED if stuck else GRAY),
        kpi_html("Ship rec.", ship_label(ship), "from summary.json", vcolor(ship)),
        kpi_html("Documented divs", str(divergences), "non-blocking", AMBER if divergences else GRAY),
        kpi_html("Manual review", str(review), "no baseline to compare", AMBER if review else GRAY),
    ]
    st.markdown(f'<div class="kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------

def qualifications_table_html(quals: list[dict], labels: dict[str, str] | None = None) -> str:
    if not quals:
        return ""
    rows = []
    for q in quals:
        trial = q.get("trial") or ""
        trial_label = (labels or {}).get(trial, entrypoint_short_name(trial))
        rows.append(
            "<tr>"
            f"<td><span class='ep-title'>{esc(trial_label)}</span></td>"
            f"<td><span class='mono'>{esc(q.get('sink_id'))}</span></td>"
            f"<td><strong>{esc(q.get('column'))}</strong></td>"
            f"<td>{esc(q.get('reason'))}</td>"
            "</tr>"
        )
    return (
        '<div class="section"><h2>Non-blocking qualifications</h2>'
        '<p class="qual-note">These are known cosmetic or serialization differences between the '
        "source baseline and Snowpark Connect output. They were reviewed and marked safe to ship.</p>"
        '<table class="report-table"><thead><tr>'
        "<th>Entrypoint</th><th>Output sink</th><th>Column</th><th>Why it is acceptable</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def pipeline_html(steps: list[dict]) -> str:
    if not steps:
        return ""
    rendered = []
    for idx, milestone in enumerate(steps, start=1):
        status = (milestone.get("status") or "unknown").lower()
        css = "done" if status == "done" else "pending"
        rendered.append(
            f'<div class="pipe-step {css}">'
            f'<div class="pipe-node">{idx}</div>'
            f'<div class="pipe-label">{esc(milestone.get("label"))}</div>'
            f'<div class="pipe-desc">{esc(milestone.get("description") or "")}</div>'
            "</div>"
        )
    return (
        '<div class="section"><h2>Validation pipeline</h2>'
        '<p style="font-size:.84rem;color:var(--muted);margin:0 0 .65rem;">'
        "Setup, execution, and comparison steps for this validation run.</p>"
        f'<div class="pipeline">{"".join(rendered)}</div></div>'
    )


def render_overview(data) -> None:
    labels = _trial_labels(data)
    render_kpis(data)
    st.write("")
    st.markdown(entrypoint_table_html(data), unsafe_allow_html=True)
    st.markdown(pipeline_html(data.pipeline_steps), unsafe_allow_html=True)

    quals = getattr(data, "qualifications", None) or []
    if quals:
        st.markdown(qualifications_table_html(quals, labels), unsafe_allow_html=True)

    if getattr(data, "fixer_dispatches", None) or data.run_index.get("fixer_dispatches"):
        dispatches = getattr(data, "fixer_dispatches", None) or data.run_index.get("fixer_dispatches", [])
        st.markdown(fixer_table_html(dispatches), unsafe_allow_html=True)

    decision = (data.summary or {}).get("decision", {})
    with st.container(border=True):
        st.markdown("#### Decision")
        if decision:
            st.markdown(badge_html(decision.get("overall")), unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            c1.metric("Ship recommendation", ship_label(decision.get("ship_recommendation")))
            c2.metric("Entrypoints passed", f"{decision.get('phase_b_passes', metrics_fallback(data))}/{data.run_metrics.get('entrypoint_count', 0)}")
            c3.metric("Non-blocking divergences", decision.get("non_blocking_divergences", 0))
            blockers = decision.get("blocking_reasons") or metrics_fallback_blockers(data)
            if blockers:
                st.markdown("**Blocking reasons**")
                for b in blockers:
                    st.error(format_blocker(b, labels))
        else:
            st.info("No summary.json decision payload found.")

    if data.warnings:
        with st.container(border=True):
            st.markdown("#### Warnings")
            for w in data.warnings:
                st.warning(_shorten_trial_text(str(w), labels))

    if data.report_markdown:
        with st.expander("REPORT.md (generated human summary)", expanded=False):
            st.markdown(data.report_markdown)


def metrics_fallback(data) -> int:
    return data.run_metrics.get("phase_b_passes") or 0


def metrics_fallback_blockers(data) -> list:
    return data.run_metrics.get("blocking_reasons") or []


# ---------------------------------------------------------------------------
# Entrypoints tab
# ---------------------------------------------------------------------------

def _styled_preview(frame, highlight_cols: set[str]):
    import pandas as pd

    if not highlight_cols:
        return frame
    styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
    for col in highlight_cols:
        if col in styles.columns:
            styles[col] = "background-color: #fef3c7; color: #92400e"
    return frame.style.apply(lambda _: styles, axis=None)


def _sort_for_comparison(left, right):
    common = [col for col in left.columns if col in right.columns]
    if not common:
        return left, right

    def _sortable(frame):
        out = frame.copy()
        for col in common:
            if out[col].dtype == "object":
                out[col] = out[col].astype(str)
        return out.sort_values(by=common, kind="mergesort", na_position="last").reset_index(drop=True)

    return _sortable(left), _sortable(right)


def _render_comparison_table(frame, highlight_cols: set[str]):
    if highlight_cols:
        st.caption("Documented divergence columns: " + ", ".join(sorted(highlight_cols)))
    st.dataframe(frame, width="stretch", hide_index=True)


def render_parquet_preview(data, relative_path: str | None, label: str) -> None:
    if not relative_path:
        st.caption(f"No {label} parquet captured.")
        return
    preview = cached_load_parquet(str(data.root), relative_path)
    if preview.get("error"):
        st.warning(preview["error"])
        return
    frame = preview.get("preview")
    if frame is None:
        st.warning(f"No preview available for `{relative_path}`.")
        return
    st.caption(f"`{relative_path}` · {preview.get('rows', 0)} rows")
    st.dataframe(frame, width="stretch", hide_index=True)


def _diff_index_summary(diff: dict) -> list[tuple[str, str]]:
    fields = [
        ("verdict", "Verdict"),
        ("schema_match", "Schema match"),
        ("row_count_a", "Source rows"),
        ("row_count_b", "Snowpark Connect rows"),
        ("tier", "Comparison tier"),
    ]
    rows: list[tuple[str, str]] = []
    for key, label in fields:
        value = diff.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            value = "yes" if value else "no"
        rows.append((label, str(value)))
    return rows


def _format_artifact_path(relative_path: str) -> str:
    path = Path(relative_path)
    if len(relative_path) <= 72:
        return relative_path
    return f"{path.parent.name}/{path.name}"


def _render_diff_metadata(diff: dict, root: Path) -> None:
    rel = diff.get("relative_path") or diff.get("diff_path")
    if rel:
        st.markdown(
            f'<p class="mono" style="font-size:.82rem;color:var(--muted);margin:0;" '
            f'title="{esc(rel)}"><code>{esc(_format_artifact_path(rel))}</code></p>',
            unsafe_allow_html=True,
        )
        if diff.get("missing_on_disk") or not (root / rel).is_file():
            st.warning("Comparison result indexed in run_index.json but the file is not on disk.")
    summary = _diff_index_summary(diff)
    if summary:
        cols = st.columns(min(len(summary), 4))
        for idx, (label, value) in enumerate(summary):
            cols[idx % len(cols)].metric(label, value)


def render_sink_outputs(data, ep: dict) -> None:
    catalog = ep.get("sink_catalog") or []
    diff_files = ep.get("diff_files") or []
    if not catalog and diff_files:
        catalog = [
            {
                "name": diff.get("table") or Path(diff.get("relative_path", "diff")).stem,
                "phase_a": None,
                "phase_b": None,
                "diff": diff,
                "diff_result": (diff.get("payload") or {}).get("result") or diff.get("verdict"),
                "diff_summary": (diff.get("payload") or {}).get("summary"),
                "row_diffs": (diff.get("payload") or {}).get("row_diffs", []),
                "documented_divergences": [],
                "divergent_columns": [],
            }
            for diff in diff_files
        ]
    if not catalog:
        trial = ep.get("short_name") or ep["id"]
        st.info(
            f"No captured sink outputs for **{trial}**. "
            "Expected files under `results/phase_a/<trial_id>/tables/*.parquet`, "
            "`results/phase_b/<trial_id>/tables/*.parquet`, and "
            "`results/phase_b/<trial_id>/diffs/*.json`."
        )
        return

    sink_names = [s["name"] for s in catalog]
    selected_sink = st.selectbox("Output sink", sink_names, key=f"sink-{ep['id']}")
    sink = next(s for s in catalog if s["name"] == selected_sink)

    st.markdown(
        f"**{esc(selected_sink)}** {badge_html(sink.get('diff_result'))} "
        f"<span class='mono'>{esc(sink.get('diff_summary') or '')}</span>",
        unsafe_allow_html=True,
    )

    documented_cols = {d.get("column") for d in sink.get("documented_divergences", []) if d.get("column")}
    for div in sink.get("documented_divergences", []):
        st.markdown(
            f'<div class="qual-note"><strong>{esc(div.get("column"))}</strong> — '
            f'{esc(div.get("reason") or "")}</div>',
            unsafe_allow_html=True,
        )

    row_diffs = sink.get("row_diffs") or []
    undocumented_diffs = []
    for row in row_diffs:
        for field in row.get("field_diffs", []):
            col_name = field.get("col")
            if col_name not in documented_cols:
                undocumented_diffs.append(
                    {
                        "row": row.get("row_index"),
                        "column": col_name,
                        "source": field.get("baseline_value"),
                        "snowpark_connect": field.get("shadow_value"),
                    }
                )
    if undocumented_diffs:
        with st.expander(f"Undocumented cell differences ({len(undocumented_diffs)})", expanded=True):
            st.dataframe(undocumented_diffs, width="stretch", hide_index=True)

    diff_entry = sink.get("diff") or {}
    diff_payload = diff_entry.get("payload") or {}
    if diff_entry:
        with st.expander("Comparison result", expanded=not diff_payload):
            _render_diff_metadata(diff_entry, data.root)
            if diff_payload:
                st.json(diff_payload, expanded=bool(diff_payload.get("row_diffs")))

    col_a, col_b = st.columns(2)
    phase_a = sink.get("phase_a") or {}
    phase_b = sink.get("phase_b") or {}
    source_frame = None
    scos_frame = None
    source_error = None
    scos_error = None

    if phase_a.get("relative_path"):
        preview = cached_load_parquet(str(data.root), phase_a["relative_path"])
        if preview.get("error"):
            source_error = preview["error"]
        else:
            source_frame = preview["preview"]
    if phase_b.get("relative_path"):
        preview = cached_load_parquet(str(data.root), phase_b["relative_path"])
        if preview.get("error"):
            scos_error = preview["error"]
        else:
            scos_frame = preview["preview"]

    if source_frame is not None and scos_frame is not None:
        source_frame, scos_frame = _sort_for_comparison(source_frame, scos_frame)

    with col_a:
        st.markdown(f"##### {SOURCE_LABEL}")
        if source_error:
            st.error(source_error)
        elif source_frame is not None:
            _render_comparison_table(source_frame, documented_cols)
        else:
            st.caption("No source capture.")

    with col_b:
        st.markdown(f"##### {SCOS_LABEL}")
        if scos_error:
            st.error(scos_error)
        elif scos_frame is not None:
            _render_comparison_table(scos_frame, documented_cols)
        else:
            st.caption("No Snowpark Connect capture.")


def render_migration_fixes(ep: dict) -> None:
    commits = ep.get("migration_fix_commits") or []
    if not commits:
        st.caption("No migration fixes recorded for this entrypoint.")
        return
    cards = []
    for commit in commits:
        cards.append(
            '<div class="change-card">'
            f'<div class="tag">migration fix</div>'
            f'<div class="title"><span class="mono">{esc(commit.get("sha"))}</span></div>'
            f'<div class="body">{esc(commit.get("subject") or "")}</div>'
            "</div>"
        )
    st.markdown("".join(cards), unsafe_allow_html=True)


def render_workload_error(ep: dict) -> None:
    raw = ep.get("workload_error")
    if not raw:
        return

    overall = (ep.get("overall_verdict") or "").lower()
    phase_b = (ep.get("phase_b_verdict") or "").lower()
    if overall not in {"hard_stuck", "failed", "blocked"} and phase_b != "hard_stuck":
        return

    parsed = ep.get("workload_error_parsed") or {}
    code = parsed.get("error_code")
    headline = parsed.get("headline")
    detail = parsed.get("detail")
    sql_state = parsed.get("sql_state")
    query_id = parsed.get("query_id")
    traceback = parsed.get("traceback") or raw

    parts = ['<div class="workload-error">']
    if code:
        parts.append(f'<div class="we-code">Snowpark Connect error <span class="mono">{esc(code)}</span></div>')
    if headline:
        state = f' <span class="we-state">({esc(sql_state)})</span>' if sql_state else ""
        parts.append(f'<div class="we-headline">{esc(headline)}{state}</div>')
    if detail:
        parts.append(f'<div class="we-detail">{esc(detail)}</div>')
    if query_id:
        parts.append(f'<div class="we-meta">Query ID: <span class="mono">{esc(query_id)}</span></div>')
    if not code and not headline:
        parts.append(f'<div class="we-detail">{esc(ep.get("workload_error_excerpt") or raw[:500])}</div>')
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)

    with st.expander("Full traceback", expanded=False):
        st.code(traceback, language="python")


def render_entrypoint_detail(data, ep: dict) -> None:
    short = ep.get("short_name") or entrypoint_short_name(ep["id"], ep.get("source_path"))
    st.markdown(
        f"### {esc(short)} {badge_html(ep.get('overall_verdict'))}",
        unsafe_allow_html=True,
    )
    trial_id = ep["id"]
    trial_hint = trial_id if len(trial_id) <= 56 else f"{trial_id[:24]}…{trial_id[-24:]}"
    st.markdown(
        f'<div class="ep-sub">{esc(ep.get("source_path") or "")}</div>'
        f'<div class="ep-id-hint" title="{esc(trial_id)}">Trial id: {esc(trial_hint)}</div>',
        unsafe_allow_html=True,
    )
    if ep.get("reason"):
        st.caption(ep["reason"])

    comp = ep.get("comparison", {}).get("verdict")
    st.markdown(
        f"""
        <div class="flow">
          {flow_step_html(SOURCE_LABEL, ep.get('phase_a_verdict'), f"{ep.get('phase_a_iters', 0)} iter(s)")}
          <div class="flow-arrow">→</div>
          {flow_step_html(SCOS_LABEL, ep.get('phase_b_verdict'), f"{ep.get('phase_b_iters', 0)} iter(s)")}
          <div class="flow-arrow">→</div>
          {flow_step_html('Comparison', comp, f"{ep.get('diff_count', 0)} sink(s)")}
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_workload_error(ep)

    mr = ep.get("manual_review")
    if mr:
        st.warning(f"Manual review: {str(mr.get('reason') or '').replace('_', ' ')}")

    with st.container(border=True):
        st.markdown(f"#### Output sinks · {SOURCE_LABEL} vs {SCOS_LABEL}")
        render_sink_outputs(data, ep)

    with st.container(border=True):
        st.markdown("#### Migration fixes")
        render_migration_fixes(ep)


def _ep_option_label(ep: dict) -> str:
    verdict = vmeta(ep.get("overall_verdict"))[1]
    short = ep.get("short_name") or entrypoint_short_name(ep["id"], ep.get("source_path"))
    return f"{short} · {verdict}"


def render_entrypoints(data) -> None:
    if not data.entrypoints:
        st.info("No entrypoints found.")
        return

    selected_id = st.selectbox(
        "Entrypoint",
        [ep["id"] for ep in data.entrypoints],
        format_func=lambda ep_id: _ep_option_label(next(ep for ep in data.entrypoints if ep["id"] == ep_id)),
        key="entrypoint_select",
    )
    ep = next(item for item in data.entrypoints if item["id"] == selected_id)
    render_entrypoint_detail(data, ep)


# ---------------------------------------------------------------------------
# Timeline tab
# ---------------------------------------------------------------------------

KIND_COLOR = {
    "milestone_completed": BLUE,
    "iter_recorded": "#7c3aed",
    "trial_marked": GREEN,
    "patch_applied": AMBER,
    "diff_written": "#0891b2",
    "capture_completed": GRAY,
}


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return ""
    return ts.replace("T", " ").replace("Z", "").split(".")[0]


def render_timeline(data) -> None:
    kinds = sorted({row.get("kind", "unknown") for row in data.timeline_rows})
    selected_kinds = st.multiselect("Event kinds", kinds, default=kinds)
    labels = _trial_labels(data)
    trial_ids = sorted({r.get("trial") for r in data.timeline_rows if r.get("trial")})
    selected_trial = st.selectbox(
        "Trial",
        ["all", *trial_ids],
        format_func=lambda tid: "all" if tid == "all" else labels.get(tid, entrypoint_short_name(tid)),
    )

    filtered = [
        r for r in data.timeline_rows
        if r.get("kind", "unknown") in selected_kinds
        and (selected_trial == "all" or r.get("trial") == selected_trial)
    ]

    with st.container(border=True):
        st.markdown(f"#### Timeline · {len(filtered)} events")
        items = []
        for r in filtered:
            color = KIND_COLOR.get(r.get("kind"), GRAY)
            kind_label = (r.get("kind") or "").replace("_", " ")
            reason = r.get("reason") or ""
            items.append(
                '<div class="tl-item">'
                f'<span class="tl-dot" style="background:{color};"></span>'
                f'<div class="tl-time">{esc(_fmt_ts(r.get("ts")))} · {esc(kind_label)}</div>'
                f'<div class="tl-head">{esc(_shorten_trial_text(r.get("headline") or "", labels))}</div>'
                + (f'<div class="tl-reason">{esc(reason)}</div>' if reason else "")
                + "</div>"
            )
        if items:
            st.markdown(f'<div class="tl">{"".join(items)}</div>', unsafe_allow_html=True)
        else:
            st.info("No events match the current filters.")


# ---------------------------------------------------------------------------
# Artifacts tab
# ---------------------------------------------------------------------------

def render_artifacts(data) -> None:
    st.markdown(
        '<p style="font-size:.88rem;color:var(--muted);margin:0 0 1rem;">'
        "Explore mock inputs, captured sink outputs, and harness artifacts from this validation run.</p>",
        unsafe_allow_html=True,
    )

    category = st.radio(
        "Browse by",
        ["Mock inputs (sources)", "Captured sinks", "Comparison diffs", "Harness & analysis"],
        horizontal=True,
        key="artifact-category",
    )

    artifacts_index = data.run_index.get("artifacts_index", {})

    if category == "Mock inputs (sources)":
        mock_groups = artifacts_index.get("mock_data") or []
        if not mock_groups:
            st.info("No mock input files indexed.")
            return
        labels = _trial_labels(data)
        trial_labels = [g.get("trial_id") or "unknown" for g in mock_groups]
        trial = st.selectbox(
            "Entrypoint",
            trial_labels,
            format_func=lambda tid: labels.get(tid, entrypoint_short_name(tid)),
            key="artifact-mock-trial",
        )
        group = next(g for g in mock_groups if (g.get("trial_id") or "unknown") == trial)
        files = [f for f in (group.get("files") or []) if (data.root / f).is_file()]
        if not files:
            indexed = group.get("files") or []
            if indexed:
                st.warning(
                    f"{len(indexed)} mock file(s) indexed for this entrypoint, but none exist on disk under `{data.root}`."
                )
            else:
                st.info("No mock files for this entrypoint.")
            return
        chosen = st.selectbox(
            "Mock parquet",
            files,
            format_func=lambda p: Path(p).name,
            key="artifact-mock-file",
        )
        render_parquet_preview(data, chosen, "mock input")

    elif category == "Comparison diffs":
        labels = _trial_labels(data)
        eps_with_diffs = [ep for ep in data.entrypoints if ep.get("diff_files")]
        if not eps_with_diffs:
            st.info("No comparison diff JSON files found under `results/phase_b/<trial_id>/diffs/`.")
            return
        ep_id = st.selectbox(
            "Entrypoint",
            [ep["id"] for ep in eps_with_diffs],
            format_func=lambda tid: labels.get(tid, entrypoint_short_name(tid)),
            key="artifact-diff-ep",
        )
        ep = next(e for e in eps_with_diffs if e["id"] == ep_id)
        diff_names = [
            (diff.get("table") or Path(diff.get("relative_path", "diff")).stem)
            for diff in ep.get("diff_files", [])
        ]
        chosen = st.selectbox("Diff file", diff_names, key="artifact-diff-name")
        diff = next(
            d for d in ep.get("diff_files", [])
            if (d.get("table") or Path(d.get("relative_path", "diff")).stem) == chosen
        )
        _render_diff_metadata(diff, data.root)
        payload = diff.get("payload")
        if payload:
            st.json(payload, expanded=True)
        elif not diff.get("missing_on_disk"):
            st.warning("Diff file exists but could not be parsed.")

    elif category == "Captured sinks":
        labels = _trial_labels(data)
        ep_labels = [ep["id"] for ep in data.entrypoints]
        ep_id = st.selectbox(
            "Entrypoint",
            ep_labels,
            format_func=lambda tid: labels.get(tid, entrypoint_short_name(tid)),
            key="artifact-sink-ep",
        )
        ep = next(e for e in data.entrypoints if e["id"] == ep_id)
        phase = st.radio("Output side", [SOURCE_LABEL, SCOS_LABEL], horizontal=True, key="artifact-sink-phase")
        tables = ep.get("phase_a_tables") if phase == SOURCE_LABEL else ep.get("phase_b_tables")
        if not tables:
            side = "source baseline" if phase == SOURCE_LABEL else "Snowpark Connect"
            st.info(f"No captured sinks for this entrypoint on the {side} side.")
            other = ep.get("phase_b_tables") if phase == SOURCE_LABEL else ep.get("phase_a_tables")
            if other:
                st.caption("Other side has captures — comparison may be partial.")
            return
        table_names = [t["name"] for t in tables]
        chosen_name = st.selectbox(
            "Sink output",
            table_names,
            format_func=lambda name: name.split(" (stage)")[0] if " (stage)" in name else name,
            key="artifact-sink-name",
        )
        table = next(t for t in tables if t["name"] == chosen_name)
        render_parquet_preview(data, table.get("relative_path"), chosen_name)

    else:
        tab_analysis, tab_tests, tab_index = st.tabs(["Analysis", "Tests", "Run index"])
        with tab_analysis:
            if data.analysis:
                st.json(data.analysis, expanded=False)
            else:
                st.info("No shared/schemas/manifest.json found.")
            blueprint = artifacts_index.get("patch_blueprint")
            if blueprint:
                st.caption(f"`{blueprint}`")
                item = next((i for i in data.artifact_inventory if i.get("relative_path") == blueprint), None)
                if item and item.get("payload"):
                    st.json(item["payload"], expanded=False)
        with tab_tests:
            tests = artifacts_index.get("rendered_tests") or []
            if not tests:
                st.info("No rendered tests indexed.")
            else:
                chosen = st.selectbox("Test file", tests, key="artifact-test-file")
                test_path = data.root / chosen
                content = test_path.read_text() if test_path.is_file() else None
                if content is None:
                    st.error(f"Could not read {chosen}")
                else:
                    st.code(content, language="python")
        with tab_index:
            st.json(data.run_index, expanded=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    inject_styles()

    with st.sidebar:
        st.header("Run source")
        root_path = resolve_run_root()
        st.caption("Use **Browse directory**, type a path, or launch with `-- --run-root /path/to/Validation`.")
        if st.button("Reload run", width="stretch"):
            cached_load_validation_run.clear()
            cached_load_parquet.clear()
            st.session_state.pop("_loaded_run_root", None)
        st.caption(f"Viewed {now_utc().strftime('%Y-%m-%d %H:%M:%S UTC')}")

    if not root_path:
        st.info("Enter a validation directory in the sidebar, or relaunch with `-- --run-root /path/to/Validation`.")
        return

    if st.session_state.get("_loaded_run_root") != root_path:
        cached_load_validation_run.clear()
        cached_load_parquet.clear()
        st.session_state._loaded_run_root = root_path

    try:
        data = cached_load_validation_run(root_path, _schema_version=7)
    except Exception as exc:
        st.error(f"Failed to load validation run: {exc}")
        return

    render_hero(data)

    overview_tab, entrypoints_tab, timeline_tab, artifacts_tab = st.tabs(
        ["Overview", "Entrypoints", "Timeline", "Artifacts"]
    )

    with overview_tab:
        render_overview(data)
    with entrypoints_tab:
        render_entrypoints(data)
    with timeline_tab:
        render_timeline(data)
    with artifacts_tab:
        render_artifacts(data)


if __name__ == "__main__":
    main()
