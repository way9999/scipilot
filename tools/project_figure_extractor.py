"""Project source code analyzer for automatic figure extraction.

Scans a project's source code (MATLAB, Python) to identify intermediate
visualisation data that is computed but never exported as figures.  Then
generates standalone extraction scripts (Python + matplotlib) that load
cached data and produce publication-ready PNGs.

The pipeline is:

  1. ``scan_project()``  — discover source files, cache files, existing figures
  2. ``analyze_visualization_gaps()`` — compare what exists vs what is needed
  3. ``generate_extraction_scripts()`` — write Python scripts that produce PNGs
  4. ``run_extraction_scripts()`` — execute them (optional, requires runtime)

Usage::

    from tools.project_figure_extractor import scan_project, generate_extraction_scripts

    report = scan_project("G:/matlab")
    gaps = analyze_visualization_gaps(report, archetype="engineering")
    scripts = generate_extraction_scripts(gaps, output_dir="G:/matlab/output/figures")

This module is **project-agnostic** — it works with any MATLAB or Python project
that has source code and optionally cached data files (.mat / .pkl / .npz / .h5).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 1. Code pattern definitions — what to look for in source files
# ---------------------------------------------------------------------------

# MATLAB patterns that indicate visualisable intermediate data
_MATLAB_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "matlab_grid_map",
        "role": "principle",
        "name_zh": "栅格地图与A*搜索过程",
        "name_en": "Occupancy grid with A* search",
        "trigger": {
            "function": r"function\s+.*plan_route_on_map|astar_on_grid|build_grid_map",
            "variables": r"(?:grid_map|closed|g_score|came_from|open_list)",
        },
        "extract_hint": "grid_map, closed set, path overlay",
        "priority": 8,
    },
    {
        "id": "matlab_cell_decomposition",
        "role": "principle",
        "name_zh": "房间单元分解",
        "name_en": "Room cell decomposition",
        "trigger": {
            "function": r"decompose_room_into_cells|plan_partitioned_room_cleaning",
            "variables": r"cells\(\w+\)\.(?:vertices|area|centroid)",
        },
        "extract_hint": "cell vertices, room polygon, obstacle rects",
        "priority": 8,
    },
    {
        "id": "matlab_boustrophedon_scan",
        "role": "principle",
        "name_zh": "牛耕法扫描线路径",
        "name_en": "Boustrophedon scan line pattern",
        "trigger": {
            "function": r"generate_boustrophedon_path",
            "variables": r"all_line_segments|scan_line_room|x_positions",
        },
        "extract_hint": "scan line segments, room polygon, obstacles",
        "priority": 7,
    },
    {
        "id": "matlab_coverage_heatmap",
        "role": "result",
        "name_zh": "房间覆盖率热力图",
        "name_en": "Per-room coverage heatmap",
        "trigger": {
            "function": r"calculate_coverage",
            "variables": r"(?:room_grid|covered_grid|coverage)",
        },
        "extract_hint": "room_grid, covered_grid",
        "priority": 6,
    },
    {
        "id": "matlab_room_sequence",
        "role": "process",
        "name_zh": "房间访问顺序与导航图",
        "name_en": "Room visit sequence and navigation graph",
        "trigger": {
            "function": r"plan_room_sequence_by_travel_entropy",
            "variables": r"(?:room_sequence|travel_log|nav_graph)",
        },
        "extract_hint": "room polygons, sequence, transfer routes",
        "priority": 7,
    },
    {
        "id": "matlab_connection_route",
        "role": "process",
        "name_zh": "单元间连接路径",
        "name_en": "Inter-cell connection routes",
        "trigger": {
            "function": r"connect_to_candidate|plan_partitioned_room_cleaning",
            "variables": r"connection_route|visit_log",
        },
        "extract_hint": "cell vertices + connection_route overlay",
        "priority": 5,
    },
]

# Python patterns
_PYTHON_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "python_training_curve",
        "role": "result",
        "name_zh": "训练曲线",
        "name_en": "Training curve",
        "trigger": {
            "import": r"(?:torch|tensorflow|keras|pytorch_lightning)",
            "variables": r"(?:loss_history|train_loss|val_loss|epoch|rewards?)",
        },
        "extract_hint": "loss/reward arrays",
        "priority": 7,
    },
    {
        "id": "python_confusion_matrix",
        "role": "comparison",
        "name_zh": "混淆矩阵",
        "name_en": "Confusion matrix",
        "trigger": {
            "import": r"sklearn\.metrics|confusion_matrix",
            "variables": r"(?:y_true|y_pred|confusion_matrix|cm)",
        },
        "extract_hint": "confusion matrix array, class names",
        "priority": 6,
    },
    {
        "id": "python_embedding_plot",
        "role": "result",
        "name_zh": "特征嵌入可视化",
        "name_en": "Feature embedding visualization",
        "trigger": {
            "import": r"sklearn\.manifold|umap|openTSNE",
            "variables": r"(?:embedding|tsne|umap_result|latent)",
        },
        "extract_hint": "2D embedding coordinates + labels",
        "priority": 6,
    },
]

# Cache file formats and their Python loaders
_CACHE_LOADERS: dict[str, str] = {
    ".mat": "scipy.io.loadmat",
    ".pkl": "pickle.load",
    ".npz": "numpy.load",
    ".h5": "h5py.File",
    ".hdf5": "h5py.File",
    ".npy": "numpy.load",
}

# ---------------------------------------------------------------------------
# 2. Scanner — discover project structure
# ---------------------------------------------------------------------------


def scan_project(project_root: str | Path) -> dict[str, Any]:
    """Scan a project directory and return a structure report.

    Returns dict with keys:
        root: Path
        source_files: list of {path, language, size, content_preview}
        cache_files: list of {path, format, size}
        existing_figures: list of {path, stem, suffix}
        detected_patterns: list of matched pattern IDs
    """
    root = Path(project_root)
    if not root.exists():
        return {"root": root, "error": f"Project root does not exist: {root}"}

    report: dict[str, Any] = {
        "root": root,
        "source_files": [],
        "cache_files": [],
        "existing_figures": [],
        "detected_patterns": [],
    }

    # Source files
    src_exts = {".m": "matlab", ".py": "python"}
    for ext, lang in src_exts.items():
        for f in root.rglob(f"*{ext}"):
            # Skip common non-project dirs
            rel = f.relative_to(root)
            if any(p.startswith(".") for p in rel.parts):
                continue
            if any(skip in str(f) for skip in ("node_modules", "__pycache__", "venv", ".git")):
                continue
            content = ""
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            report["source_files"].append({
                "path": str(f),
                "rel": str(rel),
                "language": lang,
                "size": f.stat().st_size,
                "content": content,
            })

    # Cache files
    for ext in _CACHE_LOADERS:
        for f in root.rglob(f"*{ext}"):
            rel = f.relative_to(root)
            report["cache_files"].append({
                "path": str(f),
                "rel": str(rel),
                "format": ext,
                "size": f.stat().st_size,
            })

    # Existing figures
    fig_dir = root / "output" / "figures"
    if fig_dir.exists():
        for f in fig_dir.iterdir():
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf"):
                report["existing_figures"].append({
                    "path": str(f),
                    "stem": f.stem,
                    "suffix": f.suffix.lower(),
                })

    # Pattern detection
    all_patterns = _MATLAB_PATTERNS + _PYTHON_PATTERNS
    for src in report["source_files"]:
        content = src["content"]
        lang = src["language"]
        for pattern in all_patterns:
            if _pattern_matches(pattern, content, lang):
                pid = pattern["id"]
                if pid not in [d["id"] for d in report["detected_patterns"]]:
                    report["detected_patterns"].append({
                        "id": pid,
                        "source": src["rel"],
                        **{k: pattern[k] for k in ("role", "name_zh", "name_en", "extract_hint", "priority")},
                    })

    return report


def _pattern_matches(pattern: dict[str, Any], content: str, language: str) -> bool:
    """Check if a pattern's triggers match the source content."""
    trigger = pattern.get("trigger", {})

    # Check language relevance
    pid = pattern["id"]
    if pid.startswith("matlab_") and language != "matlab":
        return False
    if pid.startswith("python_") and language != "python":
        return False

    # Must match at least one trigger condition
    for key, regex in trigger.items():
        if re.search(regex, content, re.IGNORECASE | re.MULTILINE):
            return True
    return False


# ---------------------------------------------------------------------------
# 3. Gap analysis — what figures are missing
# ---------------------------------------------------------------------------


def analyze_visualization_gaps(
    scan_report: dict[str, Any],
    archetype: str = "engineering",
    chapter: int | None = None,
    required_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Compare detected patterns with existing figures to find gaps.

    Args:
        scan_report: Output of scan_project().
        archetype: Paper archetype for role compatibility.
        chapter: If set, only suggest figures compatible with this chapter.
        required_roles: If set, only suggest figures with these roles.

    Returns:
        List of gap dicts, each with keys:
            pattern_id, role, name_zh, name_en, extract_hint,
            source_file, cache_available, priority, script_type
    """
    from tools.image_roles import classify_image, is_role_compatible

    existing_roles: dict[str, int] = {}
    for fig in scan_report.get("existing_figures", []):
        role = classify_image(fig["stem"])
        existing_roles[role] = existing_roles.get(role, 0) + 1

    detected = scan_report.get("detected_patterns", [])
    gaps: list[dict[str, Any]] = []

    for det in detected:
        role = det["role"]

        # Filter by chapter compatibility
        if chapter is not None and not is_role_compatible(role, chapter, archetype):
            continue

        # Filter by required roles
        if required_roles and role not in required_roles:
            continue

        # Skip if we already have enough of this role
        # (allow multiple process/result figures but limit principle to 2)
        role_count = existing_roles.get(role, 0)
        if role in ("principle", "design") and role_count >= 2:
            continue

        # Check cache availability
        cache_available = len(scan_report.get("cache_files", [])) > 0
        has_mat_cache = any(
            cf["format"] == ".mat" for cf in scan_report.get("cache_files", [])
        )

        script_type = "matlab_loader"
        if has_mat_cache:
            script_type = "mat_loader"
        elif any(cf["format"] in (".pkl", ".npz", ".h5", ".npy")
                 for cf in scan_report.get("cache_files", [])):
            script_type = "python_loader"

        gaps.append({
            "pattern_id": det["id"],
            "role": role,
            "name_zh": det["name_zh"],
            "name_en": det["name_en"],
            "extract_hint": det["extract_hint"],
            "source_file": det["source"],
            "cache_available": cache_available,
            "priority": det["priority"],
            "script_type": script_type,
            "role_count": role_count,
        })

    # Sort by priority (higher = more important)
    gaps.sort(key=lambda g: g["priority"], reverse=True)
    return gaps


# ---------------------------------------------------------------------------
# 4. Script generation — produce extraction scripts
# ---------------------------------------------------------------------------

# Template registry: maps pattern IDs to matplotlib drawing functions
_EXTRACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    # ── MATLAB patterns ──
    "matlab_grid_map": {
        "func_name": "draw_astar_grid",
        "data_fields": ["grid_map", "origin", "resolution", "path", "closed"],
        "imports": ["numpy as np", "matplotlib.pyplot as plt", "matplotlib.patches as mpatches",
                     "scipy.io as sio"],
        "load_code": (
            "data = sio.loadmat(cache_path, squeeze_me=True)\n"
            "# Extract from benchmark struct if nested\n"
            "if 'room_plans' in data:\n"
            "    plans = data['room_plans']\n"
            "    # Each plan has full_path, cells, coverage\n"
            "    for i in range(len(plans)):\n"
            "        plan = plans[i] if hasattr(plans, '__len__') else plans\n"
        ),
        "draw_func": "astar_grid",
    },
    "matlab_cell_decomposition": {
        "func_name": "draw_cell_decomposition",
        "data_fields": ["room_vertices", "cells.vertices", "cells.centroid", "obstacles"],
        "imports": ["numpy as np", "matplotlib.pyplot as plt", "matplotlib.patches as mpatches",
                     "scipy.io as sio"],
        "load_code": (
            "data = sio.loadmat(cache_path, squeeze_me=True)\n"
            "rooms = data['rooms']\n"
            "room_plans = data['room_plans']\n"
        ),
        "draw_func": "cell_decomp",
    },
    "matlab_boustrophedon_scan": {
        "func_name": "draw_boustrophedon_scan",
        "data_fields": ["room_vertices", "scan_path", "obstacles"],
        "imports": ["numpy as np", "matplotlib.pyplot as plt", "matplotlib.patches as mpatches",
                     "scipy.io as sio"],
        "load_code": (
            "data = sio.loadmat(cache_path, squeeze_me=True)\n"
            "rooms = data['rooms']\n"
            "room_plans = data['room_plans']\n"
        ),
        "draw_func": "boustrophedon",
    },
    "matlab_coverage_heatmap": {
        "func_name": "draw_coverage_heatmap",
        "data_fields": ["room_grid", "covered_grid", "coverage"],
        "imports": ["numpy as np", "matplotlib.pyplot as plt", "scipy.io as sio"],
        "load_code": (
            "data = sio.loadmat(cache_path, squeeze_me=True)\n"
            "rooms = data['rooms']\n"
            "room_plans = data['room_plans']\n"
        ),
        "draw_func": "coverage_heatmap",
    },
    "matlab_room_sequence": {
        "func_name": "draw_room_sequence",
        "data_fields": ["rooms", "room_sequence", "nav_points", "travel_log"],
        "imports": ["numpy as np", "matplotlib.pyplot as plt", "scipy.io as sio"],
        "load_code": (
            "data = sio.loadmat(cache_path, squeeze_me=True)\n"
            "rooms = data['rooms']\n"
            "room_sequence = data['room_sequence'].flatten()\n"
            "nav_points = data['nav_points']\n"
        ),
        "draw_func": "room_sequence",
    },
    "matlab_connection_route": {
        "func_name": "draw_connection_routes",
        "data_fields": ["cells.vertices", "visit_log.connection_route"],
        "imports": ["numpy as np", "matplotlib.pyplot as plt", "scipy.io as sio"],
        "load_code": (
            "data = sio.loadmat(cache_path, squeeze_me=True)\n"
            "rooms = data['rooms']\n"
            "room_plans = data['room_plans']\n"
        ),
        "draw_func": "connection_route",
    },
}

# Drawing code templates for each figure type
_DRAW_CODE: dict[str, str] = {
    "astar_grid": '''
    if len(rooms) == 0:
        print("No rooms found, skipping A* grid visualization.")
        return
    # A* grid visualization from room plan connection routes
    # The actual occupancy grid is computed locally in plan_route_on_map.m,
    # so we visualize the A* routing results (connection routes within a room).
    fig, ax = plt.subplots(1, 1, figsize=(10, 8))
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    # Pick the first room that has connection routes for visualization
    for ri, room in enumerate(rooms):
        verts = _get_field(room, 'vertices', 'Vertices')
        if verts is None or len(verts) < 3:
            continue
        plan = room_plans[ri] if ri < len(room_plans) else None
        if plan is None:
            continue

        # Draw room
        ax.fill(verts[:, 0], verts[:, 1], alpha=0.06, color='gray')
        ax.plot(np.append(verts[:, 0], verts[0, 0]),
                np.append(verts[:, 1], verts[0, 1]),
                '-', color='#333', linewidth=1.8)

        # Draw obstacles
        obs = _get_field(room, 'obstacles', 'Obstacles')
        if obs is not None and len(obs) > 0:
            for oi in range(len(obs)):
                o = obs[oi] if obs.ndim == 2 else obs
                if hasattr(o, 'flatten') and len(o.flatten()) >= 4:
                    v = o.flatten()[:4]
                    rect = mpatches.Rectangle((v[0], v[1]), v[2], v[3],
                                             facecolor='#E57373', alpha=0.5, edgecolor='#C62828')
                    ax.add_patch(rect)

        # Draw cells with boundaries
        cells = _get_field(plan, 'cells', 'Cells')
        cell_colors = plt.cm.Pastel1(np.linspace(0, 1, max(len(cells) if cells is not None else 1, 1)))
        if cells is not None:
            for ci in range(len(cells)):
                cell = cells[ci]
                cv = _get_field(cell, 'vertices', 'Vertices')
                if cv is not None and len(cv) >= 3:
                    ax.fill(cv[:, 0], cv[:, 1], alpha=0.15, color=cell_colors[ci % len(cell_colors)])
                    ax.plot(np.append(cv[:, 0], cv[0, 0]),
                            np.append(cv[:, 1], cv[0, 1]),
                            '--', color='#7E57C2', linewidth=0.8)

        # Draw A* connection routes (these are the actual A* planned routes)
        visit_log = _get_field(plan, 'visit_log', 'VisitLog')
        if visit_log is not None:
            for vi in range(len(visit_log)):
                vl = visit_log[vi]
                conn = _get_field(vl, 'connection_route', 'ConnectionRoute')
                if conn is not None and len(conn) >= 2:
                    conn = np.atleast_2d(conn)
                    if conn.shape[1] == 2:
                        ax.plot(conn[:, 0], conn[:, 1], 'o-', color=colors[vi % 10],
                                markersize=3, linewidth=1.5, alpha=0.8,
                                label=f"Route {vi+1}" if vi < 5 else None)
                        ax.plot(conn[0, 0], conn[0, 1], 's', color=colors[vi % 10], markersize=5)
                        ax.plot(conn[-1, 0], conn[-1, 1], 'v', color=colors[vi % 10], markersize=5)

        # Draw sweep path as thin background
        full_path = _get_field(plan, 'full_path', 'FullPath')
        if full_path is not None and len(full_path) > 1:
            ax.plot(full_path[:, 0], full_path[:, 1], '-', color='#BDBDBD', linewidth=0.4, alpha=0.4)

        name = _get_field(room, 'name', 'Name')
        ax.set_title(f"{name or '房间'} — A*路由规划与单元分解" , fontsize=12)
        break  # Just show one room as example

    legend_items = [
        mpatches.Patch(facecolor='#E57373', alpha=0.5, label='障碍物'),
        plt.Line2D([0], [0], color='#7E57C2', linestyle='--', linewidth=1, label='单元边界'),
        plt.Line2D([0], [0], marker='o', color='gray', markersize=4, linewidth=1.5, label='A*连接路径'),
    ]
    ax.legend(handles=legend_items, loc='upper left', fontsize=8)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"  -> {output_path}")
''',

    "cell_decomp": '''
    if len(rooms) == 0:
        print("No rooms found, skipping cell decomposition visualization.")
        return
    fig, ax = plt.subplots(1, 1, figsize=(10, 8))
    colors = plt.cm.Set2(np.linspace(0, 1, max(len(rooms), 1)))

    for ri, room in enumerate(rooms):
        verts = _get_field(room, 'vertices', 'Vertices')
        if verts is None or len(verts) < 3:
            continue
        ax.fill(verts[:, 0], verts[:, 1], alpha=0.08, color=colors[ri % len(colors)])
        ax.plot(np.append(verts[:, 0], verts[0, 0]),
                np.append(verts[:, 1], verts[0, 1]),
                '-', color=colors[ri % len(colors)], linewidth=1.8)

        # Draw obstacles
        obs = _get_field(room, 'obstacles', 'Obstacles')
        if obs is not None and len(obs) > 0:
            for oi in range(len(obs)):
                o = obs[oi] if obs.ndim == 2 else obs
                if hasattr(o, 'flatten') and len(o.flatten()) >= 4:
                    v = o.flatten()[:4]
                    rect = mpatches.Rectangle((v[0], v[1]), v[2], v[3],
                                             facecolor='#E57373', alpha=0.6, edgecolor='#C62828')
                    ax.add_patch(rect)

        # Draw cell decomposition boundaries
        plan = room_plans[ri] if ri < len(room_plans) else None
        if plan is not None:
            cells = _get_field(plan, 'cells', 'Cells')
            if cells is not None:
                for ci in range(len(cells)):
                    cell = cells[ci] if hasattr(cells, '__getitem__') else cells
                    cell_verts = _get_field(cell, 'vertices', 'Vertices')
                    if cell_verts is not None and len(cell_verts) >= 3:
                        ax.plot(np.append(cell_verts[:, 0], cell_verts[0, 0]),
                                np.append(cell_verts[:, 1], cell_verts[0, 1]),
                                '--', color=colors[ri % len(colors)], linewidth=0.9, alpha=0.7)
                        cx, cy = cell_verts[:, 0].mean(), cell_verts[:, 1].mean()
                        ax.text(cx, cy, f"C{ci+1}", ha='center', va='center',
                                fontsize=7, color=colors[ri % len(colors)], alpha=0.8)

        name = _get_field(room, 'name', 'Name')
        if name:
            anchor = _get_field(room, 'anchor', 'Anchor')
            if anchor is not None:
                ax.text(anchor[0], anchor[1], str(name),
                        ha='center', va='center', fontweight='bold',
                        color=colors[ri % len(colors)], fontsize=9)

    ax.set_title("房间单元分解示意图", fontsize=13)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"  -> {output_path}")
''',

    "boustrophedon": '''
    if len(rooms) == 0:
        print("No rooms found, skipping boustrophedon scan visualization.")
        return
    n_show = min(len(rooms), 3)
    fig, axes = plt.subplots(1, n_show, figsize=(6 * n_show, 6))
    if n_show == 1:
        axes = [axes]
    colors = plt.cm.Set2(np.linspace(0, 1, max(len(rooms), 1)))

    for ri, room in enumerate(rooms[:3]):
        ax = axes[ri]
        verts = _get_field(room, 'vertices', 'Vertices')
        if verts is None or len(verts) < 3:
            continue

        # Room outline
        ax.fill(verts[:, 0], verts[:, 1], alpha=0.06, color=colors[ri])
        ax.plot(np.append(verts[:, 0], verts[0, 0]),
                np.append(verts[:, 1], verts[0, 1]),
                '-', color=colors[ri], linewidth=1.5)

        # Draw scan path (sweep path)
        plan = room_plans[ri] if ri < len(room_plans) else None
        if plan is not None:
            full_path = _get_field(plan, 'full_path', 'FullPath')
            if full_path is not None and len(full_path) > 1:
                # Draw sweep lines in blue, connections in light gray
                seg_type = _get_field(plan, 'segment_type', 'SegmentType')
                if seg_type is not None and len(seg_type) > 0:
                    for si in range(1, len(full_path)):
                        st_idx = min(si, len(seg_type) - 1)
                        color = '#1565C0' if seg_type[st_idx] == 2 else '#BDBDBD'
                        lw = 1.2 if color == '#1565C0' else 0.5
                        ax.plot([full_path[si-1, 0], full_path[si, 0]],
                                [full_path[si-1, 1], full_path[si, 1]],
                                color=color, linewidth=lw, alpha=0.7)
                else:
                    ax.plot(full_path[:, 0], full_path[:, 1], '-', color='#1565C0', linewidth=0.8)

        name = _get_field(room, 'name', 'Name')
        ax.set_title(f"{name}" if name else f"房间{ri+1}", fontsize=10)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.3)

    fig.suptitle("牛耕法扫描线路径示意图", fontsize=13, y=1.02)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"  -> {output_path}")
''',

    "coverage_heatmap": '''
    if len(rooms) == 0:
        print("No rooms found, skipping coverage heatmap visualization.")
        return
    n_show = min(len(rooms), 4)
    fig, axes = plt.subplots(1, n_show, figsize=(5 * n_show, 5))
    if n_show == 1:
        axes = [axes]

    for ri, room in enumerate(rooms[:4]):
        ax = axes[ri]
        verts = _get_field(room, 'vertices', 'Vertices')
        if verts is None:
            continue

        plan = room_plans[ri] if ri < len(room_plans) else None
        coverage = _get_field(plan, 'coverage', 'Coverage') if plan else 0
        full_path = _get_field(plan, 'full_path', 'FullPath') if plan else None

        # Simple heatmap: room polygon + path density
        if verts is not None and len(verts) >= 3:
            ax.fill(verts[:, 0], verts[:, 1], alpha=0.1, color='gray')
            ax.plot(np.append(verts[:, 0], verts[0, 0]),
                    np.append(verts[:, 1], verts[0, 1]),
                    '-', color='#333', linewidth=1.2)

        if full_path is not None and len(full_path) > 1:
            ax.plot(full_path[:, 0], full_path[:, 1], '-', color='#1565C0', linewidth=0.6, alpha=0.5)

        name = _get_field(room, 'name', 'Name')
        cov_val = float(coverage) if coverage is not None else 0
        ax.set_title(f"{name}\\n覆盖率 {cov_val:.1f}%", fontsize=9)
        ax.set_aspect('equal')

    fig.suptitle("各房间覆盖率可视化", fontsize=13, y=1.02)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"  -> {output_path}")
''',

    "room_sequence": '''
    if len(rooms) == 0:
        print("No rooms found, skipping room sequence visualization.")
        return
    fig, ax = plt.subplots(1, 1, figsize=(12, 8))
    colors = plt.cm.Set2(np.linspace(0, 1, max(len(rooms), 1)))

    dock_point = None
    nav_pts = data.get('nav_points', None)
    dock_name_str = data.get('dock_name', 'dock')
    if nav_pts is not None:
        try:
            if hasattr(nav_pts, 'keys'):
                dk = dock_name_str if isinstance(dock_name_str, str) else 'dock'
                dp = nav_pts.get(dk, nav_pts.get('dock', None))
                if dp is not None:
                    dock_point = np.array(dp).flatten()[:2]
        except Exception:
            pass

    # Draw rooms
    for ri, room in enumerate(rooms):
        verts = _get_field(room, 'vertices', 'Vertices')
        if verts is None:
            continue
        ax.fill(verts[:, 0], verts[:, 1], alpha=0.1, color=colors[ri])
        ax.plot(np.append(verts[:, 0], verts[0, 0]),
                np.append(verts[:, 1], verts[0, 1]),
                '-', color=colors[ri], linewidth=1.8)
        anchor = _get_field(room, 'anchor', 'Anchor')
        name = _get_field(room, 'name', 'Name')
        if anchor is not None and name:
            ax.text(anchor[0], anchor[1], str(name), ha='center', va='center',
                    fontweight='bold', color=colors[ri], fontsize=9)

    # Draw visit sequence with arrows
    seq = data.get('room_sequence', np.arange(1, len(rooms)+1))
    seq = np.array(seq).flatten().astype(int) - 1  # 0-indexed

    anchors = []
    for idx in seq:
        if 0 <= idx < len(rooms):
            a = _get_field(rooms[idx], 'anchor', 'Anchor')
            if a is not None:
                anchors.append(a)

    if dock_point is not None:
        anchors = [dock_point] + anchors

    for i in range(1, len(anchors)):
        p1, p2 = anchors[i-1], anchors[i]
        ax.annotate("", xy=(p2[0], p2[1]), xytext=(p1[0], p1[1]),
                    arrowprops=dict(arrowstyle="->", color="#333", lw=2.0))
        ax.text((p1[0]+p2[0])/2, (p1[1]+p2[1])/2 + 0.15,
                str(i), ha='center', fontsize=8, color="#333", fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.8))

    if dock_point is not None:
        ax.plot(dock_point[0], dock_point[1], 'k*', markersize=14,
                markerfacecolor='#FFD54F', zorder=5)
        ax.text(dock_point[0]+0.2, dock_point[1]+0.2, "充电座", fontsize=9)

    ax.set_title("房间访问顺序与导航路径", fontsize=13)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"  -> {output_path}")
''',

    "connection_route": '''
    if len(rooms) == 0:
        print("No rooms found, skipping connection route visualization.")
        return
    n_show = min(len(rooms), 3)
    fig, axes = plt.subplots(1, n_show, figsize=(6 * n_show, 6))
    if n_show == 1:
        axes = [axes]
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    for ri, room in enumerate(rooms[:n_show]):
        ax = axes[ri]
        verts = _get_field(room, 'vertices', 'Vertices')
        if verts is None or len(verts) < 3:
            continue

        ax.fill(verts[:, 0], verts[:, 1], alpha=0.06, color='gray')
        ax.plot(np.append(verts[:, 0], verts[0, 0]),
                np.append(verts[:, 1], verts[0, 1]),
                '-', color='#333', linewidth=1.2)

        plan = room_plans[ri] if ri < len(room_plans) else None
        if plan is not None:
            # Draw cells
            cells = _get_field(plan, 'cells', 'Cells')
            if cells is not None:
                for ci in range(len(cells)):
                    cell = cells[ci]
                    cv = _get_field(cell, 'vertices', 'Vertices')
                    if cv is not None and len(cv) >= 3:
                        ax.plot(np.append(cv[:, 0], cv[0, 0]),
                                np.append(cv[:, 1], cv[0, 1]),
                                '--', color='#7E57C2', linewidth=0.8, alpha=0.5)

            # Draw connection routes between cells
            visit_log = _get_field(plan, 'visit_log', 'VisitLog')
            if visit_log is not None:
                for vi in range(len(visit_log)):
                    vl = visit_log[vi]
                    conn = _get_field(vl, 'connection_route', 'ConnectionRoute')
                    if conn is not None and len(conn) >= 2:
                        conn = np.atleast_2d(conn)
                        if conn.shape[1] == 2:
                            ax.plot(conn[:, 0], conn[:, 1], '-', color=colors[vi % 10],
                                    linewidth=1.5, alpha=0.7)
                        elif conn.ndim == 1 and len(conn) >= 4:
                            conn = conn.reshape(-1, 2)
                            ax.plot(conn[:, 0], conn[:, 1], '-', color=colors[vi % 10],
                                    linewidth=1.5, alpha=0.7)

        name = _get_field(room, 'name', 'Name')
        ax.set_title(f"{name}" if name else f"房间{ri+1}", fontsize=10)
        ax.set_aspect('equal')
        ax.grid(True, alpha=0.3)

    fig.suptitle("单元间连接路径示意图", fontsize=13, y=1.02)
    fig.savefig(output_path, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print(f"  -> {output_path}")
''',
}


def generate_extraction_scripts(
    gaps: list[dict[str, Any]],
    output_dir: str | Path,
    cache_files: list[dict[str, Any]] | None = None,
    language: str = "zh",
) -> list[dict[str, str]]:
    """Generate Python extraction scripts for the identified gaps.

    Args:
        gaps: Output of analyze_visualization_gaps().
        output_dir: Directory to write generated figures into.
        cache_files: List of cache file dicts from scan_report.
        language: "zh" or "en" for labels.

    Returns:
        List of {script_path, figure_path, gap_id, role}.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find MATLAB .mat cache if present — prefer root-level, not subdirectories
    mat_cache_path = ""
    if cache_files:
        # Sort by depth (fewer path separators = closer to root = preferred)
        sorted_caches = sorted(cache_files, key=lambda cf: len(Path(cf["path"]).parts))
        for cf in sorted_caches:
            if cf["format"] == ".mat":
                mat_cache_path = cf["path"]
                break

    scripts: list[dict[str, str]] = []

    # Group gaps by project to generate one script per project
    for gap in gaps:
        pid = gap["pattern_id"]
        template = _EXTRACTION_TEMPLATES.get(pid)
        if not template:
            continue

        draw_type = template.get("draw_func", "")
        draw_code = _DRAW_CODE.get(draw_type, "")
        if not draw_code:
            continue

        fig_filename = f"gen_extract_{pid.replace('matlab_', '')}.png"
        fig_path = output_dir / fig_filename
        script_path = output_dir / f"extract_{pid.replace('matlab_', '')}.py"

        script_content = _build_script(
            template=template,
            draw_code=draw_code,
            cache_path=mat_cache_path,
            output_path=str(fig_path),
            language=language,
        )

        script_path.write_text(script_content, encoding="utf-8")
        scripts.append({
            "script_path": str(script_path),
            "figure_path": str(fig_path),
            "gap_id": pid,
            "role": gap["role"],
            "name_zh": gap["name_zh"],
        })

    return scripts


def _build_script(
    template: dict[str, Any],
    draw_code: str,
    cache_path: str,
    output_path: str,
    language: str,
) -> str:
    """Build a complete Python extraction script."""
    imports_list = template.get("imports", [])
    # Build import block: matplotlib first (for Agg backend), then the rest
    import_lines = [
        "import matplotlib",
        "matplotlib.use('Agg')",
    ]
    for imp in imports_list:
        if imp.startswith("matplotlib"):
            import_lines.append(f"import {imp}")
        else:
            import_lines.append(f"import {imp}")
    # Deduplicate
    seen_imports = set()
    unique_lines = []
    for line in import_lines:
        if line not in seen_imports:
            seen_imports.add(line)
            unique_lines.append(line)
    imports_block = "\n".join(unique_lines)

    # Chinese font support
    font_setup = ""
    if language == "zh":
        font_setup = (
            'matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]\n'
            'matplotlib.rcParams["axes.unicode_minus"] = False'
        )

    return f'''#!/usr/bin/env python3
"""Auto-generated figure extraction script.
Source: {template.get("func_name", "unknown")}
Run: python {template.get("func_name", "extract")}.py
"""
from __future__ import annotations
import sys
import os

{imports_block}

{font_setup}

CACHE_PATH = r"{cache_path}"


def _get_field(obj, *names):
    """Get a field from a MATLAB struct (numpy void) or dict."""
    for name in names:
        try:
            if isinstance(obj, dict):
                if name in obj:
                    return obj[name]
            elif hasattr(obj, name):
                val = getattr(obj, name)
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    return val
            elif hasattr(obj, 'dtype') and hasattr(obj.dtype, 'names') and obj.dtype.names:
                if name in obj.dtype.names:
                    val = obj[name]
                    if val is not None and not (isinstance(val, float) and np.isnan(val)):
                        return val
                    if hasattr(val, 'item'):
                        v = val.item()
                        if v is not None:
                            return v
        except (ValueError, KeyError, AttributeError, IndexError):
            continue
    return None


def _load_mat_structs(path):
    """Load .mat file with struct handling."""
    import scipy.io as sio
    raw = sio.loadmat(path, squeeze_me=True, struct_as_record=False)

    # Look for a nested benchmark struct first
    bench = raw.get('benchmark', None)
    if bench is not None and hasattr(bench, 'rooms'):
        raw = {{attr_name: getattr(bench, attr_name) for attr_name in bench._fieldnames}}

    # Extract key arrays
    rooms = raw.get('rooms', None)
    room_plans = raw.get('room_plans', None)

    # Convert to lists if single elements
    if rooms is not None:
        if not hasattr(rooms, '__len__'):
            rooms = [rooms]
        else:
            rooms = list(rooms)
    else:
        rooms = []

    if room_plans is not None:
        if not hasattr(room_plans, '__len__'):
            room_plans = [room_plans]
        else:
            room_plans = list(room_plans)
    else:
        room_plans = []

    return raw, rooms, room_plans


def main():
    if not os.path.exists(CACHE_PATH):
        print(f"Cache not found: {{CACHE_PATH}}")
        print("Run the project's main script first to generate the cache.")
        sys.exit(1)

    print(f"Loading {{CACHE_PATH}}...")
    data, rooms, room_plans = _load_mat_structs(CACHE_PATH)
    print(f"Found {{len(rooms)}} rooms, {{len(room_plans)}} plans")

    output_path = r"{output_path}"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

{draw_code}


if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# 5. Script execution
# ---------------------------------------------------------------------------


def run_extraction_scripts(
    scripts: list[dict[str, str]],
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Execute generated extraction scripts.

    Args:
        scripts: Output of generate_extraction_scripts().
        timeout: Max seconds per script.

    Returns:
        List of {script_path, success, figure_path, error}.
    """
    import subprocess
    import sys as _sys

    results: list[dict[str, Any]] = []
    for s in scripts:
        sp = s["script_path"]
        fp = s["figure_path"]
        try:
            proc = subprocess.run(
                [_sys.executable, sp],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            success = proc.returncode == 0 and Path(fp).exists()
            results.append({
                "script_path": sp,
                "success": success,
                "figure_path": fp if success else None,
                "error": proc.stderr if not success else None,
            })
        except subprocess.TimeoutExpired:
            results.append({
                "script_path": sp,
                "success": False,
                "figure_path": None,
                "error": f"Timeout after {timeout}s",
            })
        except Exception as e:
            results.append({
                "script_path": sp,
                "success": False,
                "figure_path": None,
                "error": str(e),
            })
    return results


# ---------------------------------------------------------------------------
# 6. High-level API — one call to do everything
# ---------------------------------------------------------------------------


def auto_extract_project_figures(
    project_root: str | Path,
    output_dir: str | Path | None = None,
    archetype: str = "engineering",
    execute: bool = True,
    language: str = "zh",
) -> dict[str, Any]:
    """Full pipeline: scan → analyze → generate → (optionally) execute.

    Args:
        project_root: Path to the project directory.
        output_dir: Where to write figures (default: project_root/output/figures).
        archetype: Paper archetype for role filtering.
        execute: Whether to run the generated scripts.
        language: "zh" or "en".

    Returns:
        Dict with: scan_report, gaps, scripts, results (if executed).
    """
    project_root = Path(project_root)
    if output_dir is None:
        output_dir = project_root / "output" / "figures"
    output_dir = Path(output_dir)

    # Step 1: Scan
    scan_report = scan_project(project_root)

    # Step 2: Analyze gaps
    gaps = analyze_visualization_gaps(scan_report, archetype=archetype)

    if not gaps:
        return {
            "scan_report": scan_report,
            "gaps": [],
            "scripts": [],
            "results": [],
            "message": "No visualization gaps detected.",
        }

    # Step 3: Generate scripts
    scripts = generate_extraction_scripts(
        gaps,
        output_dir=output_dir,
        cache_files=scan_report.get("cache_files", []),
        language=language,
    )

    # Step 4: Execute (optional)
    results = []
    if execute and scripts:
        results = run_extraction_scripts(scripts)

    return {
        "scan_report": scan_report,
        "gaps": gaps,
        "scripts": scripts,
        "results": results,
    }
