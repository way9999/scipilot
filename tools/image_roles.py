"""Image role classification for academic paper figure management.

Provides a shared taxonomy for classifying figures by their semantic role
in a paper, and a chapter-role compatibility matrix that determines which
roles belong in which chapters.  All other modules (figure_planner,
paper_writer, writing_enhancer, research_export) import from here.

Usage::

    from tools.image_roles import classify_image, is_role_compatible

    role = classify_image("household_demo_avoid_verify.png")
    # -> "process"

    ok = is_role_compatible("process", chapter=4, archetype="engineering")
    # -> True
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

IMAGE_ROLES = ("principle", "design", "scene", "process", "result", "comparison")

# Sort priority within a chapter (lower = earlier)
ROLE_ORDER: dict[str, int] = {
    "principle": 0,
    "design": 1,
    "scene": 2,
    "process": 3,
    "result": 4,
    "comparison": 5,
}

# Per-role matching heuristics
ROLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "principle": {
        "desc_zh": "算法原理/理论示意图",
        "keywords": (
            "principle", "grid_map", "potential", "cell_decomposition",
            "boustrophedon", "node_expansion", "voronoi", "visibility",
            "示意图", "原理", "模型图", "矢量场", "理论",
        ),
        "exclude": (
            "result", "comparison", "verify", "check", "animation",
            "coverage", "last_frame",
        ),
    },
    "design": {
        "desc_zh": "系统架构/模块框图",
        "keywords": (
            "architecture", "arch", "system", "module", "design",
            "data_flow", "data-flow", "flowchart", "sequence",
            "架构", "模块", "总体", "overview", "interaction",
        ),
        "exclude": ("result", "comparison", "bar", "radar", "animation"),
    },
    "scene": {
        "desc_zh": "实验场景/环境设置图",
        "keywords": (
            "floorplan", "roomsketcher", "setup", "scene",
            "户型", "场景", "environment", "benchmark",
        ),
        "exclude": ("animation", "last_frame", "comparison", "bar", "flow", "data-flow"),
    },
    "process": {
        "desc_zh": "中间过程/验证图",
        "keywords": (
            "avoid", "verify", "check", "obstacle", "intermediate",
            "detail", "cleaning", "检测", "避障", "验证",
        ),
        "exclude": ("last_frame", "final", "comparison", "bar", "radar"),
    },
    "result": {
        "desc_zh": "最终结果/输出图",
        "keywords": (
            "animation", "last_frame", "result", "coverage", "final",
            "output", "trajectory", "最终", "路径规划",
        ),
        "exclude": ("comparison", "bar", "radar", "heatmap"),
    },
    "comparison": {
        "desc_zh": "对比分析图(柱状图/雷达图等)",
        "keywords": (
            "comparison", "compare", "bar", "radar", "heatmap",
            "stacked", "curve", "对比", "性能", "convergence",
        ),
        "exclude": (),
    },
}

# ---------------------------------------------------------------------------
# Chapter-role compatibility matrix
# ---------------------------------------------------------------------------

# For each archetype and chapter: allowed roles and recommended order
CHAPTER_ROLE_MATRIX: dict[str, dict[int, dict[str, Any]]] = {
    "engineering": {
        1: {"allowed": {"design"}, "order": ["design"]},
        2: {"allowed": {"principle", "design"}, "order": ["principle", "design"]},
        3: {"allowed": {"design", "process"}, "order": ["design", "process"]},
        4: {"allowed": {"scene", "process", "result", "comparison"},
            "order": ["scene", "process", "result", "comparison"]},
        5: {"allowed": set(), "order": []},
    },
    "science": {
        1: {"allowed": {"design"}, "order": ["design"]},
        2: {"allowed": {"principle", "design"}, "order": ["principle", "design"]},
        3: {"allowed": {"design", "process"}, "order": ["design", "process"]},
        4: {"allowed": {"scene", "process", "result", "comparison"},
            "order": ["scene", "process", "result", "comparison"]},
        5: {"allowed": set(), "order": []},
    },
    "data_analytics": {
        1: {"allowed": {"design"}, "order": ["design"]},
        2: {"allowed": {"principle"}, "order": ["principle"]},
        3: {"allowed": {"design", "process"}, "order": ["design", "process"]},
        4: {"allowed": {"scene", "result", "comparison"},
            "order": ["scene", "result", "comparison"]},
        5: {"allowed": set(), "order": []},
    },
    "humanities": {
        1: {"allowed": set(), "order": []},
        2: {"allowed": set(), "order": []},
        3: {"allowed": {"process"}, "order": ["process"]},
        4: {"allowed": {"result", "comparison"}, "order": ["result", "comparison"]},
        5: {"allowed": set(), "order": []},
    },
    "arts": {
        1: {"allowed": set(), "order": []},
        2: {"allowed": {"result", "comparison"}, "order": ["result", "comparison"]},
        3: {"allowed": {"process"}, "order": ["process"]},
        4: {"allowed": {"result", "comparison"}, "order": ["result", "comparison"]},
        5: {"allowed": set(), "order": []},
    },
}


def _default_matrix() -> dict[str, Any]:
    """Fallback for unknown archetypes — same as engineering."""
    return CHAPTER_ROLE_MATRIX["engineering"]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_image(
    filename: str,
    alt_text: str = "",
    caption: str = "",
) -> str:
    """Classify an image into one of the 6 roles based on filename + context.

    Args:
        filename: Image filename or stem (e.g. "household_demo_avoid_verify").
        alt_text: Markdown alt text from ``![alt](path)``.
        caption: Figure caption text (e.g. "图4-2 避障验证结果").

    Returns:
        One of: principle, design, scene, process, result, comparison.
    """
    # Normalize: strip prefix, extension, path
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    # Also remove common prefixes like "household_demo_"
    clean = re.sub(r"^(?:household_demo_|gen_\d+_)", "", stem)

    combined = f"{clean} {alt_text} {caption}".lower()

    best_role = "result"  # default fallback
    best_score = -1

    for role, defn in ROLE_DEFINITIONS.items():
        score = 0
        for kw in defn["keywords"]:
            if kw.lower() in combined:
                score += 2
        for kw in defn["exclude"]:
            if kw.lower() in combined:
                score -= 3
        if score > best_score:
            best_score = score
            best_role = role

    return best_role


def classify_all_images(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``role`` key to each figure dict in *figures*."""
    for fig in figures:
        name = fig.get("name", fig.get("stem", str(fig.get("path", ""))))
        alt = fig.get("alt_text", "")
        cap = fig.get("caption", "")
        fig["role"] = classify_image(name, alt_text=alt, caption=cap)
    return figures


# ---------------------------------------------------------------------------
# Chapter-role compatibility
# ---------------------------------------------------------------------------

def is_role_compatible(
    role: str,
    chapter: int,
    archetype: str = "engineering",
) -> bool:
    """Check whether *role* is allowed in *chapter* for *archetype*."""
    matrix = CHAPTER_ROLE_MATRIX.get(archetype, _default_matrix())
    ch_info = matrix.get(chapter, {})
    allowed = ch_info.get("allowed", set())
    # If no rules defined (empty set), allow everything
    if not allowed:
        return True
    return role in allowed


def get_image_order_key(role: str) -> int:
    """Return sort priority for a role within a chapter."""
    return ROLE_ORDER.get(role, 99)


# ---------------------------------------------------------------------------
# Orphan table detection
# ---------------------------------------------------------------------------

def detect_orphan_tables(
    md_text: str,
    existing_figure_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detect comparison tables that lack a corresponding chart figure.

    Scans markdown for tables with 3+ data rows (likely comparison tables)
    and checks whether a ``comparison``-role figure exists nearby.

    Args:
        md_text: Full markdown text.
        existing_figure_roles: List of roles already assigned to figures.

    Returns:
        List of dicts with keys: line_number, table_ref, row_count, col_count.
    """
    has_comparison_fig = False
    if existing_figure_roles:
        has_comparison_fig = "comparison" in existing_figure_roles

    orphans: list[dict[str, Any]] = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and "---" not in line:
            # Collect table rows
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                stripped = lines[i].strip()
                if not all(
                    re.fullmatch(r":?-{2,}:?", c.strip())
                    for c in stripped.strip("|").split("|")
                    if c.strip()
                ):
                    cells = [c.strip() for c in stripped.strip("|").split("|")]
                    rows.append(cells)
                i += 1

            # A comparison table has 3+ data rows and 3+ columns
            if len(rows) >= 4 and len(rows[0]) >= 3:
                # Check if a comparison figure exists within 30 lines after
                table_end = i
                has_nearby_chart = False
                for j in range(table_end, min(len(lines), table_end + 30)):
                    if lines[j].strip().startswith("!["):
                        role = classify_image(lines[j].strip())
                        if role == "comparison":
                            has_nearby_chart = True
                            break

                if not has_nearby_chart and not has_comparison_fig:
                    # Try to extract table reference (表X-Y)
                    ref = ""
                    for k in range(max(0, i - len(rows) - 5), i - len(rows)):
                        m = re.search(r"表\s*(\d+[-．.]\s*\d+)", lines[k])
                        if m:
                            ref = m.group(1)
                            break
                    orphans.append({
                        "line": table_end,
                        "table_ref": ref or f"table_near_line_{table_end}",
                        "row_count": len(rows),
                        "col_count": len(rows[0]),
                    })
        else:
            i += 1

    return orphans


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_chapter_images(
    chapter: int,
    images: list[dict[str, Any]],
    archetype: str = "engineering",
) -> dict[str, Any]:
    """Validate image placement for a single chapter.

    Returns:
        Dict with keys: valid (bool), violations (list), missing_roles (list).
    """
    matrix = CHAPTER_ROLE_MATRIX.get(archetype, _default_matrix())
    ch_info = matrix.get(chapter, {})
    allowed = ch_info.get("allowed", set())
    order = ch_info.get("order", [])

    violations = []
    for img in images:
        role = img.get("role", "result")
        if allowed and role not in allowed:
            violations.append({
                "figure": img.get("name", img.get("path", "")),
                "role": role,
                "issue": f"Role '{role}' not allowed in chapter {chapter}",
            })

    # Check ordering
    if order and len(images) > 1:
        actual_order = [img.get("role", "result") for img in images]
        order_indices = [ROLE_ORDER.get(r, 99) for r in actual_order]
        if order_indices != sorted(order_indices):
            violations.append({
                "figure": "ordering",
                "role": ",".join(actual_order),
                "issue": "Images not in prescribed order",
            })

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "missing_roles": [],
    }
