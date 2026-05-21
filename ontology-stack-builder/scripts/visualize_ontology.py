# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "streamlit>=1.30.0",
#     "streamlit-agraph>=0.0.45",
#     "pyyaml>=6.0",
# ]
# ///
"""
Ontology Visualization App - Interactive exploration of parsed OWL ontology
and generated semantic model.

Usage:
    uv run --script visualize_ontology.py -- \
      --classes-json /tmp/parsed/classes.json \
      --relations-json /tmp/parsed/relations.json \
      --semantic-model /tmp/generated/03_ontology_semantic_model.yaml
"""

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

import streamlit as st
import yaml
from streamlit_agraph import agraph, Node, Edge, Config


# ---------------------------------------------------------------------------
# Session-state helpers for mutable ontology editing
# ---------------------------------------------------------------------------

def _init_session_state(classes: list[dict], relations: list[dict],
                        classes_path: str, relations_path: str) -> None:
    """Deep-copy loaded data into st.session_state on first run."""
    if "classes" not in st.session_state:
        st.session_state["classes"] = copy.deepcopy(classes)
    if "relations" not in st.session_state:
        st.session_state["relations"] = copy.deepcopy(relations)
    if "classes_path" not in st.session_state:
        st.session_state["classes_path"] = classes_path
    if "relations_path" not in st.session_state:
        st.session_state["relations_path"] = relations_path
    # Track which names were added or modified this session
    if "added_classes" not in st.session_state:
        st.session_state["added_classes"] = set()
    if "added_relations" not in st.session_state:
        st.session_state["added_relations"] = set()
    if "modified_classes" not in st.session_state:
        st.session_state["modified_classes"] = set()
    if "modified_relations" not in st.session_state:
        st.session_state["modified_relations"] = set()
    if "has_unsaved_changes" not in st.session_state:
        st.session_state["has_unsaved_changes"] = False


def _mark_dirty() -> None:
    st.session_state["has_unsaved_changes"] = True


def _save_to_disk() -> None:
    """Write current session-state classes/relations back to JSON files."""
    with open(st.session_state["classes_path"], "w") as f:
        json.dump(st.session_state["classes"], f, indent=2)
    with open(st.session_state["relations_path"], "w") as f:
        json.dump(st.session_state["relations"], f, indent=2)
    st.session_state["has_unsaved_changes"] = False


def _undo_all(original_classes: list[dict], original_relations: list[dict]) -> None:
    """Reset session state to the original loaded data."""
    st.session_state["classes"] = copy.deepcopy(original_classes)
    st.session_state["relations"] = copy.deepcopy(original_relations)
    st.session_state["added_classes"] = set()
    st.session_state["added_relations"] = set()
    st.session_state["modified_classes"] = set()
    st.session_state["modified_relations"] = set()
    st.session_state["has_unsaved_changes"] = False


# ---------------------------------------------------------------------------
# Sidebar editor widgets
# ---------------------------------------------------------------------------

def _render_editor_sidebar(original_classes: list[dict],
                           original_relations: list[dict]) -> None:
    """Render the ontology editor section in the sidebar."""
    classes = st.session_state["classes"]
    relations = st.session_state["relations"]
    class_names = sorted(c["name"] for c in classes)

    st.sidebar.divider()
    st.sidebar.header("Ontology Editor")

    # --- Unsaved-changes indicator ---
    if st.session_state["has_unsaved_changes"]:
        st.sidebar.warning("You have unsaved changes.")

    # --- Save / Undo ---
    col_save, col_undo = st.sidebar.columns(2)
    with col_save:
        if st.button("Save Changes", use_container_width=True,
                      disabled=not st.session_state["has_unsaved_changes"]):
            _save_to_disk()
            st.rerun()
    with col_undo:
        if st.button("Undo All", use_container_width=True,
                      disabled=not st.session_state["has_unsaved_changes"]):
            _undo_all(original_classes, original_relations)
            st.rerun()

    # --- Add Class ---
    with st.sidebar.expander("Add Class"):
        with st.form("add_class_form", clear_on_submit=True):
            new_name = st.text_input("Class name (PascalCase)")
            new_label = st.text_input("Label (display name)")
            new_desc = st.text_input("Description")
            parent_opts = ["(none)"] + class_names
            new_parent = st.selectbox("Parent class", parent_opts)
            new_abstract = st.checkbox("Abstract?")
            submitted = st.form_submit_button("Add Class")
            if submitted and new_name.strip():
                name = new_name.strip()
                if any(c["name"] == name for c in classes):
                    st.error(f"Class '{name}' already exists.")
                else:
                    cls_dict = {
                        "name": name,
                        "label": new_label.strip() or name,
                        "description": new_desc.strip(),
                        "parent_name": None if new_parent == "(none)" else new_parent,
                        "is_abstract": new_abstract,
                        "is_deprecated": False,
                        "namespace": "",
                        "uri": f"urn:user:{name}",
                    }
                    st.session_state["classes"].append(cls_dict)
                    st.session_state["added_classes"].add(name)
                    _mark_dirty()
                    st.rerun()

    # --- Add Relation ---
    with st.sidebar.expander("Add Relation"):
        with st.form("add_relation_form", clear_on_submit=True):
            rel_name = st.text_input("Relation name")
            src_class = st.selectbox("Source (domain) class", class_names, key="rel_src")
            dst_class = st.selectbox("Target (range) class", class_names, key="rel_dst")
            card_opts = ["many_to_many", "many_to_one", "one_to_many", "one_to_one"]
            cardinality = st.selectbox("Cardinality", card_opts)
            rel_desc = st.text_input("Description", key="rel_desc")
            rel_submit = st.form_submit_button("Add Relation")
            if rel_submit and rel_name.strip():
                rn = rel_name.strip()
                if any(r["name"] == rn for r in relations):
                    st.error(f"Relation '{rn}' already exists.")
                else:
                    rel_dict = {
                        "name": rn,
                        "domain_class": src_class,
                        "range_class": dst_class,
                        "cardinality": cardinality,
                        "description": rel_desc.strip(),
                        "is_hierarchical": False,
                        "is_transitive": False,
                    }
                    st.session_state["relations"].append(rel_dict)
                    st.session_state["added_relations"].add(rn)
                    _mark_dirty()
                    st.rerun()

    # --- Delete Class ---
    with st.sidebar.expander("Delete Class"):
        del_class = st.selectbox("Select class to delete", ["(select)"] + class_names,
                                 key="del_class_sel")
        if del_class != "(select)":
            children = [c["name"] for c in classes if c.get("parent_name") == del_class]
            involved = [r["name"] for r in relations
                        if r.get("domain_class") == del_class or r.get("range_class") == del_class]
            if children:
                st.caption(f"Children that will be re-parented to root: {', '.join(children)}")
            if involved:
                st.caption(f"Relations that will be removed: {', '.join(involved)}")
            if st.button("Delete Class", key="del_class_btn"):
                # Re-parent children
                for c in st.session_state["classes"]:
                    if c.get("parent_name") == del_class:
                        c["parent_name"] = None
                        st.session_state["modified_classes"].add(c["name"])
                # Remove involved relations
                st.session_state["relations"] = [
                    r for r in st.session_state["relations"]
                    if r.get("domain_class") != del_class and r.get("range_class") != del_class
                ]
                # Remove the class
                st.session_state["classes"] = [
                    c for c in st.session_state["classes"] if c["name"] != del_class
                ]
                st.session_state["added_classes"].discard(del_class)
                _mark_dirty()
                st.rerun()

    # --- Delete Relation ---
    with st.sidebar.expander("Delete Relation"):
        rel_names = sorted(r["name"] for r in relations)
        del_rel = st.selectbox("Select relation to delete", ["(select)"] + rel_names,
                               key="del_rel_sel")
        if del_rel != "(select)":
            if st.button("Delete Relation", key="del_rel_btn"):
                st.session_state["relations"] = [
                    r for r in st.session_state["relations"] if r["name"] != del_rel
                ]
                st.session_state["added_relations"].discard(del_rel)
                _mark_dirty()
                st.rerun()


# ---------------------------------------------------------------------------
# Inline edit form for the detail panel (Tab 2 right column)
# ---------------------------------------------------------------------------

def _render_edit_class_form(cls_name: str) -> None:
    """Render an inline edit form for a class in the detail panel."""
    classes = st.session_state["classes"]
    cls = next((c for c in classes if c["name"] == cls_name), None)
    if not cls:
        return

    class_names = sorted(c["name"] for c in classes if c["name"] != cls_name)

    st.markdown("---")
    st.markdown("#### Edit Class")
    with st.form(f"edit_class_{cls_name}", clear_on_submit=False):
        new_label = st.text_input("Label", value=cls.get("label", cls_name))
        new_desc = st.text_input("Description", value=cls.get("description", ""))
        parent_opts = ["(none)"] + class_names
        current_parent = cls.get("parent_name")
        parent_idx = parent_opts.index(current_parent) if current_parent in parent_opts else 0
        new_parent = st.selectbox("Parent", parent_opts, index=parent_idx)
        new_abstract = st.checkbox("Abstract?", value=cls.get("is_abstract", False))

        col1, col2 = st.columns(2)
        with col1:
            save = st.form_submit_button("Save")
        with col2:
            cancel = st.form_submit_button("Cancel")

        if save:
            cls["label"] = new_label.strip() or cls_name
            cls["description"] = new_desc.strip()
            cls["parent_name"] = None if new_parent == "(none)" else new_parent
            cls["is_abstract"] = new_abstract
            st.session_state["modified_classes"].add(cls_name)
            _mark_dirty()
            st.rerun()
        if cancel:
            st.rerun()


def load_json(path: str) -> list | dict:
    with open(path) as f:
        return json.load(f)


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_tree(classes: list[dict]) -> dict:
    """Build a parent->children tree structure."""
    children_map = defaultdict(list)
    roots = []
    name_to_cls = {}
    for cls in classes:
        name_to_cls[cls["name"]] = cls
        parent = cls.get("parent_name")
        if parent:
            children_map[parent].append(cls["name"])
        else:
            roots.append(cls["name"])
    return children_map, roots, name_to_cls


def build_relation_tree(relations: list[dict]) -> tuple[dict, list, list, dict]:
    """Build a parent->children tree structure for relations.

    Returns (rel_children_map, rel_roots, flat_concrete, rel_by_name).
    - rel_roots: abstract relations that are tree roots (or concrete parents)
    - flat_concrete: concrete relations with no parent and no children
    """
    rel_by_name = {r["name"]: r for r in relations if r["name"] != "subClassOf"}
    rel_children_map: dict[str, list[str]] = defaultdict(list)
    has_parent = set()

    for rel in relations:
        if rel["name"] == "subClassOf":
            continue
        parent_rel = rel.get("parent_name")
        if parent_rel and parent_rel in rel_by_name:
            rel_children_map[parent_rel].append(rel["name"])
            has_parent.add(rel["name"])

    # Roots: abstract relations without a parent, or concrete relations that are parents
    rel_roots = []
    for name, r in rel_by_name.items():
        if name in has_parent:
            continue
        if r.get("is_abstract") or name in rel_children_map:
            rel_roots.append(name)

    # Flat concrete: no parent, not abstract, not a parent of anything
    flat_concrete = [
        name for name, r in rel_by_name.items()
        if name not in has_parent
        and not r.get("is_abstract")
        and name not in rel_children_map
    ]

    return rel_children_map, rel_roots, flat_concrete, rel_by_name


def render_tree_text(name: str, children_map: dict, name_to_cls: dict, depth: int = 0, max_depth: int = 6) -> str:
    """Render a text-based tree."""
    if depth > max_depth:
        return ""
    cls = name_to_cls.get(name, {})
    prefix = "  " * depth + ("|- " if depth > 0 else "")
    label = cls.get("label", name)
    abstract_tag = " [abstract]" if cls.get("is_abstract") else ""
    line = f"{prefix}{label}{abstract_tag}\n"
    for child in sorted(children_map.get(name, [])):
        line += render_tree_text(child, children_map, name_to_cls, depth + 1, max_depth)
    return line


def render_interactive_tree(name: str, children_map: dict, name_to_cls: dict,
                            depth: int = 0, max_depth: int = 6, search: str = "") -> None:
    """Render an interactive Streamlit tree with colored nodes and descriptions."""
    if depth > max_depth:
        return
    cls = name_to_cls.get(name, {})
    label = cls.get("label", name)
    is_abstract = cls.get("is_abstract", False)
    description = cls.get("description", "")
    kids = sorted(children_map.get(name, []))
    has_children = len(kids) > 0

    # Build the display label with icon and styling
    if is_abstract:
        icon = "🔹"
        display = f"{icon} **{label}**  `abstract`"
    else:
        icon = "🟢"
        display = f"{icon} {label}"

    # Highlight search matches
    if search and search.lower() in label.lower():
        display += "  🔍"

    indent_px = depth * 24

    if has_children:
        # Use indented markdown (no expanders — parent context already uses one)
        st.markdown(
            f'<div style="margin-left:{indent_px}px; padding:4px 0; font-weight:bold;">{display}</div>',
            unsafe_allow_html=True
        )
        if description:
            st.markdown(
                f'<div style="margin-left:{indent_px + 16}px; color:gray; font-size:0.85em;">{description}</div>',
                unsafe_allow_html=True
            )
        for child in kids:
            render_interactive_tree(child, children_map, name_to_cls,
                                    depth + 1, max_depth, search)
    else:
        # Leaf node: render inline with indentation via markdown
        st.markdown(
            f'<div style="margin-left:{indent_px}px; padding:2px 0;">{display}</div>',
            unsafe_allow_html=True
        )
        if description and search and search.lower() in label.lower():
            st.markdown(
                f'<div style="margin-left:{indent_px + 16}px; color:gray; font-size:0.85em;">{description}</div>',
                unsafe_allow_html=True
            )


def render_rel_tree_text(name: str, rel_children_map: dict, rel_by_name: dict,
                         depth: int = 0, max_depth: int = 6) -> str:
    """Render a text-based tree for relations."""
    if depth > max_depth:
        return ""
    r = rel_by_name.get(name, {})
    prefix = "  " * depth + ("|- " if depth > 0 else "")
    label = r.get("label", name)
    is_abs = r.get("is_abstract", False)
    abstract_tag = " [abstract]" if is_abs else ""
    domain = r.get("domain_class", "?")
    rng = r.get("range_class", "?")
    line = f"{prefix}{label}{abstract_tag}  ({domain} -> {rng})\n"
    for child in sorted(rel_children_map.get(name, [])):
        line += render_rel_tree_text(child, rel_children_map, rel_by_name, depth + 1, max_depth)
    return line


def render_interactive_rel_tree(name: str, rel_children_map: dict, rel_by_name: dict,
                                depth: int = 0, max_depth: int = 6) -> None:
    """Render an interactive Streamlit tree for relations with expanders."""
    if depth > max_depth:
        return
    r = rel_by_name.get(name, {})
    label = r.get("label", name)
    is_abstract = r.get("is_abstract", False)
    domain = r.get("domain_class", "?")
    rng = r.get("range_class", "?")
    description = r.get("description", "")
    kids = sorted(rel_children_map.get(name, []))
    has_children = len(kids) > 0

    if is_abstract:
        icon = "🔷"
        display = f"{icon} **{label}**  `abstract`  ({domain} -> {rng})"
    else:
        icon = "🟣"
        display = f"{icon} {label}  ({domain} -> {rng})"

    indent_px = depth * 24

    if has_children:
        # Use indented markdown (no expanders — parent context already uses one)
        st.markdown(
            f'<div style="margin-left:{indent_px}px; padding:4px 0; font-weight:bold;">{display}</div>',
            unsafe_allow_html=True
        )
        if description:
            st.markdown(
                f'<div style="margin-left:{indent_px + 16}px; color:gray; font-size:0.85em;">{description}</div>',
                unsafe_allow_html=True
            )
        cardinality = r.get("cardinality", "")
        if cardinality:
            st.markdown(
                f'<div style="margin-left:{indent_px + 16}px; color:gray; font-size:0.85em;">Cardinality: {cardinality}</div>',
                unsafe_allow_html=True
            )
        for child in kids:
            render_interactive_rel_tree(child, rel_children_map, rel_by_name,
                                       depth + 1, max_depth)
    else:
        st.markdown(
            f'<div style="margin-left:{indent_px}px; padding:2px 0;">{display}</div>',
            unsafe_allow_html=True
        )
        if description:
            st.markdown(
                f'<div style="margin-left:{indent_px + 16}px; color:gray; font-size:0.85em;">{description}</div>',
                unsafe_allow_html=True
            )


def build_coverage_map(classes: list[dict], sem_model: dict | None,
                       deployed_objects: dict | None = None) -> dict[str, dict]:
    """Classify each OWL class by its coverage status.

    Coverage can be determined from either a semantic model YAML (Phase 5+)
    or a deployed-objects manifest (Phase 4 post-deployment).

    Returns a dict keyed by class name with values:
        status: 'mapped' | 'covered' | 'unmapped' | 'abstract'
        view_name: the view name (only for 'mapped')
        covering_ancestor: label of the ancestor whose view covers this class (only for 'covered')
        sf_objects: list of Snowflake object names for this class (when deployed_objects provided)
    """
    result: dict[str, dict] = {}

    # --- Path 1: Deployed-objects manifest (Phase 4 post-deployment) ---
    if deployed_objects and not sem_model:
        class_to_objects = deployed_objects.get("class_to_objects", {})
        name_to_cls = {c["name"]: c for c in classes}

        def _has_view(cls_name: str) -> list[str]:
            """Return list of view names for this class from the manifest."""
            entry = class_to_objects.get(cls_name, {})
            return entry.get("views", [])

        def _find_deployed_ancestor(cls: dict) -> tuple[str, list[str]] | None:
            """Walk up the hierarchy to find an ancestor with deployed views."""
            visited = set()
            current = cls.get("parent_name")
            while current and current not in visited:
                visited.add(current)
                parent_cls = name_to_cls.get(current)
                if parent_cls:
                    views = _has_view(current)
                    if views:
                        return (parent_cls.get("label", current), views)
                    current = parent_cls.get("parent_name")
                else:
                    break
            return None

        for cls in classes:
            name = cls["name"]
            views = _has_view(name)
            entry = class_to_objects.get(name, {})
            sf_objs = views + ([entry["metadata_row"]] if entry.get("metadata_row") else [])

            if views:
                result[name] = {
                    "status": "mapped",
                    "view_name": views[0],
                    "sf_objects": sf_objs,
                }
            elif cls.get("is_abstract"):
                result[name] = {"status": "abstract", "sf_objects": sf_objs}
            else:
                ancestor = _find_deployed_ancestor(cls)
                if ancestor:
                    result[name] = {
                        "status": "covered",
                        "covering_ancestor": ancestor[0],
                        "covering_view": ancestor[1][0],
                        "sf_objects": sf_objs,
                    }
                else:
                    result[name] = {"status": "unmapped", "sf_objects": sf_objs}

        return result

    # --- Path 2: No coverage source available (Phase 3 pre-deployment) ---
    if not sem_model:
        for cls in classes:
            result[cls["name"]] = {"status": "abstract" if cls.get("is_abstract") else "unmapped"}
        return result

    # --- Path 3: Semantic model YAML (Phase 5+) ---
    tables_in_model = sem_model.get("tables", [])
    # Build suffix -> full view name mapping
    suffix_to_view: dict[str, str] = {}
    for tbl in tables_in_model:
        tname = tbl["name"]
        upper = tname.upper()
        if "VW_ONT_" in upper:
            suffix = upper.split("VW_ONT_", 1)[1]
            suffix_to_view[suffix] = tname

    name_to_cls = {c["name"]: c for c in classes}

    def get_matched_view(cls: dict) -> str | None:
        name_upper = cls["name"].upper()
        for suffix, view_name in suffix_to_view.items():
            if suffix == name_upper or suffix in name_upper or name_upper in suffix:
                return view_name
        return None

    def find_covering_ancestor(cls: dict) -> tuple[str, str] | None:
        """Returns (ancestor_label, ancestor_view_name) or None."""
        visited = set()
        current = cls.get("parent_name")
        while current and current not in visited:
            visited.add(current)
            parent_cls = name_to_cls.get(current)
            if parent_cls:
                view = get_matched_view(parent_cls)
                if view:
                    return (parent_cls.get("label", current), view)
                current = parent_cls.get("parent_name")
            else:
                break
        return None

    for cls in classes:
        view = get_matched_view(cls)
        if view:
            result[cls["name"]] = {"status": "mapped", "view_name": view}
        elif cls.get("is_abstract"):
            result[cls["name"]] = {"status": "abstract"}
        else:
            ancestor = find_covering_ancestor(cls)
            if ancestor:
                result[cls["name"]] = {
                    "status": "covered",
                    "covering_ancestor": ancestor[0],
                    "covering_view": ancestor[1],
                }
            else:
                result[cls["name"]] = {"status": "unmapped"}

    return result


def build_agraph_nodes_edges(classes: list[dict], relations: list[dict],
                              coverage_map: dict[str, dict],
                              max_nodes: int = 100,
                              show_relations: bool = True,
                              added_classes: set | None = None,
                              modified_classes: set | None = None,
                              added_relations: set | None = None,
                              layer_filter: str = "all") -> tuple[list, list]:
    """Build streamlit-agraph Node and Edge lists with coverage coloring.

    Nodes that were added this session get a gold border; modified nodes get
    an orange border.  Similarly, added relation edges are drawn in gold.

    layer_filter controls visibility:
      - "all": show everything (abstract items rendered distinctly)
      - "concrete": hide abstract classes and abstract relations
      - "abstract": show only abstract classes and abstract relations
    """
    added_classes = added_classes or set()
    modified_classes = modified_classes or set()
    added_relations = added_relations or set()

    # Filter classes based on layer
    if layer_filter == "concrete":
        classes = [c for c in classes if not c.get("is_abstract")]
    elif layer_filter == "abstract":
        classes = [c for c in classes if c.get("is_abstract")]

    # Color scheme matching coverage status
    colors = {
        "mapped":   {"background": "#2ecc71", "border": "#27ae60", "font": "#ffffff"},
        "covered":  {"background": "#3498db", "border": "#2980b9", "font": "#ffffff"},
        "unmapped": {"background": "#e74c3c", "border": "#c0392b", "font": "#ffffff"},
        "abstract": {"background": "#ecf0f1", "border": "#95a5a6", "font": "#2c3e50"},
    }

    name_to_cls = {c["name"]: c for c in classes}
    nodes = []
    edges = []
    displayed = set()

    for cls in classes[:max_nodes]:
        name = cls["name"]
        label = cls.get("label", name)
        cov = coverage_map.get(name, {"status": "unmapped"})
        status = cov["status"]
        c = colors[status]

        # Override border color for added / modified nodes
        if name in added_classes:
            border_color = "#f1c40f"  # gold
            border_width = 4
        elif name in modified_classes:
            border_color = "#e67e22"  # orange
            border_width = 4
        else:
            border_color = c["border"]
            border_width = 2

        # Build hover tooltip
        tooltip_parts = [f"<b>{label}</b>"]
        if name in added_classes:
            tooltip_parts.append("<b style='color:#f1c40f;'>NEW</b>")
        elif name in modified_classes:
            tooltip_parts.append("<b style='color:#e67e22;'>MODIFIED</b>")
        if cls.get("is_abstract"):
            tooltip_parts.append("<i>Abstract class</i>")
        if cls.get("description"):
            tooltip_parts.append(cls["description"][:150])
        tooltip_parts.append(f"<br/>Coverage: <b>{status.upper()}</b>")
        if status == "mapped":
            vn = cov.get("view_name", "")
            short = vn
            if "VW_ONT_" in vn.upper():
                short = "VW_ONT_" + vn.upper().split("VW_ONT_", 1)[1]
            tooltip_parts.append(f"View: <code>{short}</code>")
        elif status == "covered":
            tooltip_parts.append(f"Covered via: {cov.get('covering_ancestor', '?')}")
        tooltip = "<br/>".join(tooltip_parts)

        # Node shape: diamond for abstract, box for concrete
        is_abs = cls.get("is_abstract", False)
        # Diamond labels render BELOW the shape on the white page background,
        # so always use dark font for diamonds regardless of coverage status
        font_color = "#2c3e50" if is_abs else c["font"]

        nodes.append(Node(
            id=name,
            label=label,
            title=tooltip,
            color={"background": c["background"], "border": border_color},
            shape="diamond" if is_abs else "box",
            size=25,
            font={"color": font_color, "size": 12},
            borderWidth=border_width,
            borderWidthSelected=3,
        ))
        displayed.add(name)

    # subClassOf edges (hierarchy)
    for cls in classes[:max_nodes]:
        parent = cls.get("parent_name")
        if parent and cls["name"] in displayed:
            if parent not in displayed:
                # Add missing parent as a ghost node
                parent_cls = name_to_cls.get(parent, {})
                nodes.append(Node(
                    id=parent,
                    label=parent_cls.get("label", parent),
                    title=f"<b>{parent_cls.get('label', parent)}</b><br/><i>Not in visible set</i>",
                    color={"background": "#ecf0f1", "border": "#bdc3c7"},
                    shape="diamond",
                    size=25,
                    font={"color": "#95a5a6", "size": 12},
                    borderWidth=2,
                ))
                displayed.add(parent)
            edges.append(Edge(
                source=cls["name"],
                target=parent,
                color="#5dade2",
                label="subClassOf",
                width=1.5,
                arrows="to",
                dashes=False,
                font={"size": 8, "color": "#5dade2", "strokeWidth": 0},
            ))

    # Other relation edges
    if show_relations:
        rel_name_to_rel = {r["name"]: r for r in relations}
        for rel in relations:
            if rel["name"] == "subClassOf":
                continue
            is_abstract_rel = rel.get("is_abstract", False)

            # Layer filtering for relations
            if layer_filter == "concrete" and is_abstract_rel:
                continue
            if layer_filter == "abstract" and not is_abstract_rel:
                continue

            domain = rel.get("domain_class", "")
            rng = rel.get("range_class", "")
            if domain in displayed and rng in displayed:
                if rel["name"] in added_relations:
                    edge_color = "#f1c40f"  # gold for newly added
                    edge_width = 2.0
                elif is_abstract_rel:
                    edge_color = "#8e44ad"  # deep purple for abstract relations
                    edge_width = 2.5
                elif rel.get("is_hierarchical"):
                    edge_color = "#e67e22"
                    edge_width = 1.0
                else:
                    edge_color = "#9b59b6"
                    edge_width = 1.0
                edges.append(Edge(
                    source=domain,
                    target=rng,
                    color=edge_color,
                    label=rel["name"],
                    width=edge_width,
                    arrows="to",
                    dashes=True if not is_abstract_rel else False,
                    font={"size": 8, "color": edge_color, "strokeWidth": 0},
                ))

        # Relation hierarchy edges: child relation → parent relation
        # Rendered as thin dotted lines between the midpoints of related edges
        if layer_filter in ("all", "abstract"):
            for rel in relations:
                parent_rel_name = rel.get("parent_name")
                if not parent_rel_name:
                    continue
                parent_rel = rel_name_to_rel.get(parent_rel_name)
                if not parent_rel:
                    continue
                # Show relation hierarchy by connecting child domain → parent domain
                # only if both endpoints are visible
                child_domain = rel.get("domain_class", "")
                parent_domain = parent_rel.get("domain_class", "")
                if child_domain in displayed and parent_domain in displayed:
                    edges.append(Edge(
                        source=child_domain,
                        target=parent_domain,
                        color="#d2b4de",  # light purple
                        label=f"specializes ({rel['name']} → {parent_rel_name})",
                        width=1.0,
                        arrows="to",
                        dashes=[2, 4],  # dotted
                        font={"size": 7, "color": "#d2b4de", "strokeWidth": 0},
                    ))

    return nodes, edges


def render_node_detail(cls_name: str, classes: list[dict], relations: list[dict],
                       coverage_map: dict[str, dict], sem_model: dict | None,
                       deployed_objects: dict | None = None) -> None:
    """Render the detail inspector panel for a clicked node."""
    name_to_cls = {c["name"]: c for c in classes}
    cls = name_to_cls.get(cls_name)
    if not cls:
        st.warning(f"Class '{cls_name}' not found.")
        return

    label = cls.get("label", cls_name)
    cov = coverage_map.get(cls_name, {"status": "unmapped"})
    status = cov["status"]

    # --- Header with coverage badge ---
    badge_colors = {
        "mapped": "#2ecc71", "covered": "#3498db",
        "unmapped": "#e74c3c", "abstract": "#95a5a6",
    }
    badge_labels = {
        "mapped": "MAPPED TO VIEW", "covered": "COVERED AS ROWS",
        "unmapped": "UNMAPPED", "abstract": "ABSTRACT",
    }
    bc = badge_colors.get(status, "#95a5a6")
    bl = badge_labels.get(status, status.upper())
    st.markdown(
        f'<h3 style="margin-bottom:0;">{label}</h3>'
        f'<span style="background:{bc};color:white;padding:2px 10px;border-radius:10px;'
        f'font-size:0.75em;font-weight:bold;">{bl}</span>',
        unsafe_allow_html=True,
    )

    # Description
    if cls.get("description"):
        st.caption(cls["description"])

    st.divider()

    # --- Class info ---
    type_label = "Abstract" if cls.get("is_abstract") else "Concrete"
    st.markdown(f"**Type:** {type_label}")
    if cls.get("parent_name"):
        parent_cls = name_to_cls.get(cls["parent_name"], {})
        st.markdown(f"**Parent:** {parent_cls.get('label', cls['parent_name'])}")

    # Children
    children = [c for c in classes if c.get("parent_name") == cls_name]
    if children:
        child_labels = ", ".join(sorted(c.get("label", c["name"]) for c in children))
        st.markdown(f"**Children ({len(children)}):** {child_labels}")

    st.divider()

    # --- Snowflake Implementation ---
    st.markdown("#### Snowflake Implementation")

    # Show all deployed Snowflake objects for this class (from manifest)
    sf_objs = cov.get("sf_objects", [])
    if sf_objs:
        st.markdown("**Deployed objects:**")
        for obj in sf_objs:
            st.write(f"- `{obj}`")

    # Find the subClassOf edge table in semantic model (used by multiple sections)
    subclass_table_info = None
    if sem_model:
        for tbl in sem_model.get("tables", []):
            if "SUBCLASS" in tbl["name"].upper():
                bt = tbl.get("base_table", {})
                subclass_table_info = {
                    "name": tbl["name"],
                    "fqn": f"{bt.get('database', '')}.{bt.get('schema', '')}.{bt.get('table', '')}",
                    "dims": tbl.get("dimensions", []),
                }
                break

    if status == "mapped":
        view_name = cov.get("view_name", "")
        short = view_name
        if "VW_ONT_" in view_name.upper():
            short = "VW_ONT_" + view_name.upper().split("VW_ONT_", 1)[1]
        st.markdown(f"**View:** `{short}`")

        # Find the table definition in the semantic model
        if sem_model:
            for tbl in sem_model.get("tables", []):
                if tbl["name"].upper() == view_name.upper():
                    bt = tbl.get("base_table", {})
                    fqn = f"{bt.get('database', '')}.{bt.get('schema', '')}.{bt.get('table', '')}"
                    st.markdown(f"**Base table:** `{fqn}`")

                    dims = tbl.get("dimensions", [])
                    if dims:
                        st.markdown("**Dimensions:**")
                        for d in dims:
                            desc = f" — {d['description']}" if d.get("description") else ""
                            st.markdown(f"- `{d['name']}` ({d.get('data_type', 'VARCHAR')}){desc}")

                    facts = tbl.get("facts", [])
                    if facts:
                        st.markdown("**Facts:**")
                        for f_item in facts:
                            st.markdown(f"- `{f_item['name']}` ({f_item.get('data_type', 'FLOAT')})")
                    break

    elif status == "covered":
        ancestor = cov.get("covering_ancestor", "?")
        covering_view = cov.get("covering_view", "")
        short_cv = covering_view
        if "VW_ONT_" in covering_view.upper():
            short_cv = "VW_ONT_" + covering_view.upper().split("VW_ONT_", 1)[1]
        st.markdown(f"**Covered by ancestor:** {ancestor}")
        st.markdown(f"**Ancestor's view:** `{short_cv}`")
        st.info(f"Rows for *{label}* exist as typed rows (ENTITY_TYPE column) within the ancestor's view.")

    elif status == "unmapped":
        st.error("No view or ancestor view covers this class.")
        st.caption("To add coverage, create a mapping in the ontology mappings file.")

    elif status == "abstract":
        st.markdown("**No dedicated view** — abstract classes are structural groupings.")
        if subclass_table_info:
            st.markdown(f"Appears as parent/child references in the hierarchy edge table:")
            st.markdown(f"- `{subclass_table_info['fqn']}`")
        if children:
            st.info(f"This class organizes {len(children)} child classes. "
                     "It exists in the hierarchy edge table as PARENT_NAME values.")
        else:
            st.info("Structural node with no children — leaf abstract class.")

    # --- Hierarchy edge (subClassOf) implementation ---
    if cls.get("parent_name") and subclass_table_info:
        st.divider()
        st.markdown("#### Hierarchy Edge (subClassOf)")
        st.markdown(f"**Edge table:** `{subclass_table_info['fqn']}`")
        parent_label = name_to_cls.get(cls["parent_name"], {}).get("label", cls["parent_name"])
        st.code(
            f"-- Row in edge table:\n"
            f"CHILD_NAME = '{label}'\n"
            f"PARENT_NAME = '{parent_label}'\n"
            f"REL_TYPE = 'subClassOf'",
            language="sql",
        )
        dim_names = [d["name"] for d in subclass_table_info.get("dims", [])]
        if dim_names:
            st.caption(f"Columns: {', '.join(dim_names)}")

    # --- Relations involving this class ---
    involved_rels = [r for r in relations
                     if r.get("domain_class") == cls_name or r.get("range_class") == cls_name]
    if involved_rels:
        st.divider()
        st.markdown("#### Relations")
        for r in involved_rels:
            direction = "domain" if r.get("domain_class") == cls_name else "range"
            other = r.get("range_class") if direction == "domain" else r.get("domain_class")
            other_label = name_to_cls.get(other, {}).get("label", other)
            arrow = f"{label} —[{r['name']}]→ {other_label}" if direction == "domain" else f"{other_label} —[{r['name']}]→ {label}"
            st.markdown(f"- {arrow}")
            if r.get("description"):
                st.caption(f"  {r['description'][:120]}")

            # Show edge table if in semantic model
            if sem_model:
                if r["name"] == "subClassOf" and subclass_table_info:
                    st.markdown(f"  Edge table: `{subclass_table_info['fqn']}`")
                elif r["name"] != "subClassOf":
                    for tbl in sem_model.get("tables", []):
                        tname_upper = tbl["name"].upper()
                        rel_upper = r["name"].upper().replace(" ", "_")
                        if rel_upper in tname_upper and "VW_ONT_" in tname_upper:
                            bt = tbl.get("base_table", {})
                            fqn = f"{bt.get('database','')}.{bt.get('schema','')}.{bt.get('table','')}"
                            st.markdown(f"  Edge table: `{fqn}`")
                            break


def render_default_detail(classes: list[dict], relations: list[dict],
                          coverage_map: dict[str, dict], sem_model: dict | None,
                          deployed_objects: dict | None = None) -> None:
    """Render the default detail panel when no node is selected."""
    st.markdown("#### Click a node to inspect")
    st.caption("Click any node in the graph to see its Snowflake implementation details.")

    st.divider()

    # Quick stats
    status_counts = defaultdict(int)
    for cov in coverage_map.values():
        status_counts[cov["status"]] += 1

    cols = st.columns(4)
    cols[0].metric("Mapped", status_counts.get("mapped", 0))
    cols[1].metric("Covered", status_counts.get("covered", 0))
    cols[2].metric("Unmapped", status_counts.get("unmapped", 0))
    cols[3].metric("Abstract", status_counts.get("abstract", 0))

    # Deployed objects summary (when manifest available)
    if deployed_objects:
        st.divider()
        st.markdown("#### Snowflake Artifacts")
        db = deployed_objects.get("database", "")
        schema = deployed_objects.get("schema", "")
        if db and schema:
            st.caption(f"Target: **{db}.{schema}**")
        n_views = len(deployed_objects.get("views", []))
        n_tables = len(deployed_objects.get("tables", []))
        n_procs = len(deployed_objects.get("procedures", []))
        n_udfs = len(deployed_objects.get("udfs", []))
        st.write(f"- **{n_views}** views, **{n_tables}** tables")
        st.write(f"- **{n_procs}** procedures, **{n_udfs}** UDFs")

    # Relations summary
    if relations:
        st.divider()
        st.markdown("#### Relations")
        for r in relations:
            props = []
            if r.get("is_hierarchical"):
                props.append("hierarchical")
            if r.get("is_transitive"):
                props.append("transitive")
            prop_str = f" ({', '.join(props)})" if props else ""
            domain = r.get("domain_class", "?")
            rng = r.get("range_class", "?")
            st.markdown(f"- **{r['name']}**: {domain} → {rng}{prop_str}")

    # Semantic model summary
    if sem_model:
        st.divider()
        st.markdown("#### Semantic Model")
        st.markdown(f"**{sem_model.get('name', 'Unknown')}**")
        for tbl in sem_model.get("tables", []):
            short = tbl["name"]
            if "VW_ONT_" in short.upper():
                short = "VW_ONT_" + short.upper().split("VW_ONT_", 1)[1]
            ndims = len(tbl.get("dimensions", []))
            nfacts = len(tbl.get("facts", []))
            st.markdown(f"- `{short}` — {ndims} dims, {nfacts} facts")

        vqs = sem_model.get("verified_queries", [])
        if vqs:
            st.divider()
            st.markdown(f"#### Verified Queries ({len(vqs)})")
            for vq in vqs:
                with st.expander(f"Q: {vq.get('question', vq.get('name', ''))}"):
                    st.code(vq.get("sql", ""), language="sql")


def main():
    # Parse args before Streamlit takes over
    # Use sys.argv to find our custom args (after --)
    custom_args = []
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        custom_args = sys.argv[idx + 1:]
    else:
        # Try parsing directly
        custom_args = sys.argv[1:]

    parser = argparse.ArgumentParser()
    parser.add_argument("--classes-json", required=True)
    parser.add_argument("--relations-json", required=True)
    parser.add_argument("--semantic-model", default=None)
    parser.add_argument("--deployed-objects", default=None,
                        help="JSON manifest mapping ontology concepts to deployed Snowflake objects")
    parser.add_argument("--stats-json", default=None)
    parser.add_argument("--port", default="8501")
    args, _ = parser.parse_known_args(custom_args)

    # Load data from disk (the "original" baseline for undo)
    original_classes = load_json(args.classes_json)
    original_relations = load_json(args.relations_json)
    stats = load_json(args.stats_json) if args.stats_json and Path(args.stats_json).exists() else None
    sem_model = load_yaml(args.semantic_model) if args.semantic_model and Path(args.semantic_model).exists() else None
    deployed_objects = load_json(args.deployed_objects) if args.deployed_objects and Path(args.deployed_objects).exists() else None

    # --- Streamlit App ---
    st.set_page_config(page_title="Ontology Editor", layout="wide")
    st.title("Ontology Semantic Modeler - Visualize & Edit")

    # Initialize mutable session state
    _init_session_state(original_classes, original_relations,
                        args.classes_json, args.relations_json)

    # Live working copies from session state
    classes = st.session_state["classes"]
    relations = st.session_state["relations"]

    # Sidebar: Summary stats
    st.sidebar.header("Ontology Summary")
    if stats:
        st.sidebar.metric("Total Classes", stats["total_classes"])
        st.sidebar.metric("Abstract", stats["abstract_classes"])
        st.sidebar.metric("Concrete", stats["concrete_classes"])
        st.sidebar.metric("Relations", stats["total_relations"])
        st.sidebar.metric("Max Depth", stats["max_hierarchy_depth"])
        if stats.get("total_individuals"):
            st.sidebar.metric("Individuals", stats["total_individuals"])
    else:
        st.sidebar.metric("Classes", len(classes))
        st.sidebar.metric("Relations", len(relations))

    # Sidebar: Ontology Editor (add/delete/save/undo)
    _render_editor_sidebar(original_classes, original_relations)

    # Tab layout (3 tabs: Hierarchy, Graph, Coverage)
    tab_tree, tab_graph, tab_coverage = st.tabs([
        "Hierarchy", "Ontology Graph", "Coverage"
    ])

    # Build coverage map once (shared across tabs)
    coverage_map = build_coverage_map(classes, sem_model, deployed_objects)

    # --- Tab 1: Hierarchy (Classes + Relations) ---
    with tab_tree:
        st.header("Hierarchy")

        # ── Class Hierarchy section ──
        with st.expander("Class Hierarchy", expanded=True):
            children_map, roots, name_to_cls = build_tree(classes)

            col_ctrl1, col_ctrl2 = st.columns([1, 1])
            with col_ctrl1:
                max_depth = st.slider("Max display depth", 1, 15, 6, key="cls_depth")
            with col_ctrl2:
                cls_view_mode = st.radio("View", ["Interactive", "Text"],
                                         horizontal=True, key="cls_view")

            # Search
            search = st.text_input("Search classes", "", placeholder="Type to filter...")

            # Legend
            st.markdown(
                "🔹 = abstract (no data rows) &nbsp;&nbsp; 🟢 = concrete (has data) &nbsp;&nbsp; 🔍 = search match",
                unsafe_allow_html=True
            )
            st.divider()

            if search:
                matches = [c for c in classes if search.lower() in c.get("label", "").lower() or search.lower() in c["name"].lower()]
                st.write(f"**{len(matches)} matches** for \"{search}\":")
                for m in matches[:50]:
                    path_parts = []
                    current = m.get("parent_name")
                    while current:
                        path_parts.insert(0, name_to_cls.get(current, {}).get("label", current))
                        current = name_to_cls.get(current, {}).get("parent_name")
                    path_str = " → ".join(path_parts) if path_parts else "root"

                    icon = "🔹" if m.get("is_abstract") else "🟢"
                    st.markdown(f"{icon} **{m.get('label', m['name'])}**")
                    st.caption(f"Path: {path_str}")
                    if m.get("description"):
                        st.caption(m["description"][:200])
            elif cls_view_mode == "Interactive":
                for root in sorted(roots):
                    render_interactive_tree(root, children_map, name_to_cls,
                                            max_depth=max_depth, search=search)
            else:
                tree_text = ""
                for root in sorted(roots):
                    tree_text += render_tree_text(root, children_map, name_to_cls, max_depth=max_depth)
                if tree_text:
                    st.code(tree_text, language=None)
                else:
                    st.info("No root classes found. The ontology may use a flat structure.")

        # ── Relation Hierarchy section ──
        with st.expander("Relation Hierarchy", expanded=True):
            rel_children_map, rel_roots, flat_concrete, rel_by_name = build_relation_tree(relations)

            rel_view_mode = st.radio("View", ["Interactive", "Text"],
                                     horizontal=True, key="rel_view")

            # Legend
            st.markdown(
                "🔷 = abstract relation &nbsp;&nbsp; 🟣 = concrete relation",
                unsafe_allow_html=True
            )
            st.divider()

            if not rel_roots and not flat_concrete:
                st.info("No relations defined yet. Add relations in the sidebar editor.")
            elif rel_view_mode == "Interactive":
                for root_rel in sorted(rel_roots):
                    render_interactive_rel_tree(root_rel, rel_children_map, rel_by_name)
                if flat_concrete:
                    st.markdown("**Ungrouped concrete relations**")
                    for name in sorted(flat_concrete):
                        r = rel_by_name[name]
                        domain = r.get("domain_class", "?")
                        rng = r.get("range_class", "?")
                        desc = r.get("description", "")
                        st.markdown(f'<div style="padding:2px 0;">🟣 {r.get("label", name)}  ({domain} -> {rng})</div>',
                                    unsafe_allow_html=True)
                        if desc:
                            st.markdown(f'<div style="margin-left:16px; color:gray; font-size:0.85em;">{desc}</div>',
                                        unsafe_allow_html=True)
            else:
                tree_text = ""
                for root_rel in sorted(rel_roots):
                    tree_text += render_rel_tree_text(root_rel, rel_children_map, rel_by_name)
                if flat_concrete:
                    tree_text += "\n(ungrouped concrete relations)\n"
                    for name in sorted(flat_concrete):
                        r = rel_by_name[name]
                        tree_text += f"  {r.get('label', name)}  ({r.get('domain_class', '?')} -> {r.get('range_class', '?')})\n"
                if tree_text:
                    st.code(tree_text, language=None)
                else:
                    st.info("No relation hierarchy defined. Relations are flat.")

    # --- Tab 2: Ontology Graph (merged Graph + Relations + Semantic Model) ---
    with tab_graph:
        st.header("Ontology Graph")

        # Controls row
        col_g1, col_g2, col_g3, col_g4 = st.columns([1, 1, 1, 1])
        with col_g1:
            max_nodes = st.slider("Max nodes", 10, 500, min(len(classes), 50), key="graph_max")
        with col_g2:
            show_rels = st.checkbox("Show relation edges", value=True, key="graph_rels")
        with col_g3:
            physics_on = st.checkbox("Physics simulation", value=True, key="graph_physics")
        with col_g4:
            layer_mode = st.radio("Layer", ["All", "Concrete", "Abstract"],
                                  horizontal=True, key="graph_layer")
            layer_filter = layer_mode.lower()

        # Legend bar (extended with diff markers and abstract relations)
        st.markdown(
            '<div style="display:flex;gap:16px;flex-wrap:wrap;margin:4px 0 8px 0;font-size:0.85em;">'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:14px;height:14px;background:#2ecc71;border:2px solid #27ae60;border-radius:3px;display:inline-block;"></span>'
            ' Mapped to View</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:14px;height:14px;background:#3498db;border:2px solid #2980b9;border-radius:3px;display:inline-block;"></span>'
            ' Covered as Rows</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:14px;height:14px;background:#e74c3c;border:2px solid #c0392b;border-radius:3px;display:inline-block;"></span>'
            ' Unmapped</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:14px;height:14px;background:#ecf0f1;border:2px solid #95a5a6;'
            'transform:rotate(45deg);display:inline-block;"></span>'
            ' Abstract Class</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:20px;border-top:2px solid #5dade2;display:inline-block;"></span>'
            ' subClassOf</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:20px;border-top:2px dashed #9b59b6;display:inline-block;"></span>'
            ' Concrete Rel</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:20px;border-top:3px solid #8e44ad;display:inline-block;"></span>'
            ' Abstract Rel</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:20px;border-top:2px dotted #d2b4de;display:inline-block;"></span>'
            ' Specializes</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:14px;height:14px;border:3px solid #f1c40f;border-radius:3px;display:inline-block;"></span>'
            ' New</span>'
            '<span style="display:inline-flex;align-items:center;gap:4px;">'
            '<span style="width:14px;height:14px;border:3px solid #e67e22;border-radius:3px;display:inline-block;"></span>'
            ' Modified</span>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Two-panel layout: graph (left) + detail (right)
        col_graph, col_detail = st.columns([7, 3])

        with col_graph:
            # Build nodes and edges with diff markers
            ag_nodes, ag_edges = build_agraph_nodes_edges(
                classes, relations, coverage_map,
                max_nodes=max_nodes, show_relations=show_rels,
                added_classes=st.session_state["added_classes"],
                modified_classes=st.session_state["modified_classes"],
                added_relations=st.session_state["added_relations"],
                layer_filter=layer_filter,
            )

            config = Config(
                width=900,
                height=600,
                physics=physics_on,
                layout={"hierarchical": False},
            )

            # agraph returns the clicked node ID
            selected_node = agraph(nodes=ag_nodes, edges=ag_edges, config=config)

        with col_detail:
            if selected_node:
                render_node_detail(selected_node, classes, relations, coverage_map, sem_model, deployed_objects)
                # Inline edit form below the detail panel
                _render_edit_class_form(selected_node)
            else:
                render_default_detail(classes, relations, coverage_map, sem_model, deployed_objects)

    # --- Tab 3: Coverage Matrix ---
    with tab_coverage:
        cov_map = coverage_map  # reuse
        has_coverage_source = bool(sem_model or deployed_objects)

        if has_coverage_source:
            # --- Header: Original Design → Snowflake Implementation ---
            if deployed_objects and not sem_model:
                source_label = deployed_objects.get("source", "Ontology Design")
                db = deployed_objects.get("database", "")
                schema = deployed_objects.get("schema", "")
                target_label = f"{db}.{schema}" if db and schema else "Snowflake"
                st.header("Original Design → Snowflake Implementation")
                st.caption(
                    f"Mapping from **{source_label}** to deployed objects in **{target_label}**"
                )
            else:
                st.header("Ontology-to-Table Coverage")

            directly_mapped = [c for c in classes if cov_map[c["name"]]["status"] == "mapped"]
            covered_by_parent = [
                (c, cov_map[c["name"]]["covering_ancestor"])
                for c in classes if cov_map[c["name"]]["status"] == "covered"
            ]
            truly_unmapped = [c for c in classes if cov_map[c["name"]]["status"] == "unmapped"]

            # Summary metrics
            total_concrete = sum(1 for c in classes if not c.get("is_abstract"))
            total_abstract = sum(1 for c in classes if c.get("is_abstract"))
            concrete_mapped = [c for c in directly_mapped if not c.get("is_abstract")]
            concrete_covered_count = len(concrete_mapped) + len(covered_by_parent)

            mcol1, mcol2, mcol3, mcol4 = st.columns(4)
            mcol1.metric("Total Classes", len(classes))
            mcol2.metric("Abstract (no view needed)", total_abstract)
            mcol3.metric("Concrete Implemented", concrete_covered_count)
            mcol4.metric("Not Yet Mapped", len(truly_unmapped))

            if total_concrete > 0:
                ratio = min(concrete_covered_count / total_concrete, 1.0)
                st.progress(ratio,
                            text=f"{concrete_covered_count}/{total_concrete} concrete classes "
                                 f"mapped to Snowflake ({100 * ratio:.0f}%)")

            # Three-column detail view
            col1, col2, col3 = st.columns(3)
            with col1:
                st.subheader(f"Mapped to Snowflake ({len(directly_mapped)})")
                if deployed_objects and not sem_model:
                    st.caption("Ontology class has dedicated Snowflake view(s)")
                else:
                    st.caption("Has a dedicated view in the semantic model")
                for c in sorted(directly_mapped, key=lambda x: x.get("label", x["name"])):
                    view_name = cov_map[c["name"]].get("view_name", "")
                    short = view_name
                    if "VW_ONT_" in view_name.upper():
                        short = "VW_ONT_" + view_name.upper().split("VW_ONT_", 1)[1]
                    elif "V_" in view_name.upper():
                        short = view_name.upper()
                    # Show all Snowflake objects for this class if available
                    sf_objs = cov_map[c["name"]].get("sf_objects", [])
                    if sf_objs and len(sf_objs) > 1:
                        st.write(f"- **{c.get('label', c['name'])}** →")
                        for obj in sf_objs:
                            st.write(f"  - `{obj}`")
                    else:
                        st.write(f"- **{c.get('label', c['name'])}** → `{short}`")
            with col2:
                st.subheader(f"Covered by Ancestor ({len(covered_by_parent)})")
                st.caption("No own view, but included as rows in a parent's view")
                by_ancestor: dict[str, list] = defaultdict(list)
                for cls, ancestor in covered_by_parent:
                    by_ancestor[ancestor].append(cls)
                for ancestor in sorted(by_ancestor.keys()):
                    children = by_ancestor[ancestor]
                    with st.expander(f"via **{ancestor}** ({len(children)} classes)"):
                        for c in sorted(children, key=lambda x: x.get("label", x["name"])):
                            st.write(f"- {c.get('label', c['name'])}")
            with col3:
                st.subheader(f"Unmapped ({len(truly_unmapped)})")
                st.caption("No Snowflake view or ancestor view covers this class")
                for c in sorted(truly_unmapped, key=lambda x: x.get("label", x["name"])):
                    st.write(f"- {c.get('label', c['name'])}")
                if not truly_unmapped:
                    st.success("All concrete classes are implemented in Snowflake!")

            # --- Full Artifact Inventory (deployed-objects mode) ---
            if deployed_objects and not sem_model:
                st.divider()
                with st.expander("Full Artifact Inventory — All Snowflake Objects Generated"):
                    inv_col1, inv_col2 = st.columns(2)
                    with inv_col1:
                        views = deployed_objects.get("views", [])
                        if views:
                            st.markdown(f"**Views ({len(views)})**")
                            for v in sorted(views):
                                st.write(f"- `{v}`")

                        tables = deployed_objects.get("tables", [])
                        if tables:
                            st.markdown(f"**Tables ({len(tables)})**")
                            for t in sorted(tables):
                                st.write(f"- `{t}`")
                    with inv_col2:
                        procs = deployed_objects.get("procedures", [])
                        if procs:
                            st.markdown(f"**Stored Procedures ({len(procs)})**")
                            for p in sorted(procs):
                                st.write(f"- `{p}`")

                        udfs = deployed_objects.get("udfs", [])
                        if udfs:
                            st.markdown(f"**UDFs / Graph Tools ({len(udfs)})**")
                            for u in sorted(udfs):
                                st.write(f"- `{u}`")

                # --- Relation mapping ---
                relation_to_objects = deployed_objects.get("relation_to_objects", {})
                if relation_to_objects:
                    st.divider()
                    st.subheader("Relation → Snowflake Mapping")
                    for rel_name, rel_info in sorted(relation_to_objects.items()):
                        view = rel_info.get("view", "")
                        meta = rel_info.get("metadata_row", "")
                        parts = [f"`{view}`" if view else None, f"`{meta}`" if meta else None]
                        mapped_to = ", ".join(p for p in parts if p)
                        st.write(f"- **{rel_name}** → {mapped_to}")

        else:
            # Phase 3 pre-deployment: show design only
            st.header("Ontology Design — Coverage")
            st.info("Coverage mapping will be available after Phase 4 deployment. "
                    "Currently showing the ontology design structure.")
            abstract_count = sum(1 for c in classes if c.get("is_abstract"))
            concrete_count = len(classes) - abstract_count
            st.metric("Abstract Classes (structural groupings)", abstract_count)
            st.metric("Concrete Classes (will become Snowflake views)", concrete_count)


if __name__ == "__main__":
    main()
