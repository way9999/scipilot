"""Universal figure planning module for academic papers.

Pure utility module — no LLM calls. The LLM-driven parts (analyzing paper
content to determine figure needs, and generating project visualization code)
are handled by the /paper-write slash command.

This module provides:
  - Content-based deduplication (pixel hash)
  - Auto-generation of structural diagrams (diagram_generator)
  - Auto-generation of data charts (figure_generator)
  - One-to-one figure assignment (each figure used exactly once)

Usage by LLM orchestrator:
  1. LLM analyzes paper → produces a list of figure needs
  2. Call scan_and_dedup() → get unique existing figures
  3. Call match_figures() → match existing figures to needs
  4. LLM generates project visualization code for remaining gaps
  5. Call generate_diagrams() / generate_charts() for automated gaps
  6. Call assign_figures() → final one-to-one mapping
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Content-based deduplication
# ---------------------------------------------------------------------------

def _pixel_hash(image_path: Path, size: int = 64) -> str:
    """Compute a perceptual hash of image content for dedup."""
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(image_path).resize((size, size)).convert("L")
        arr = np.array(img, dtype=float).flatten()
        return hashlib.md5(arr.tobytes()).hexdigest()
    except Exception:
        return hashlib.md5(image_path.read_bytes()).hexdigest()


def scan_and_dedup(
    fig_dir: str | Path,
) -> list[dict[str, Any]]:
    """Scan figure directory, deduplicate by pixel content.

    Returns list of {
        "path": Path,          # representative path (shortest name)
        "name": str,           # stem
        "content_hash": str,
        "duplicates": [Path],  # other paths with identical content
    }
    """
    fig_dir = Path(fig_dir)
    if not fig_dir.exists():
        return []

    hash_groups: dict[str, list[Path]] = {}
    for f in sorted(fig_dir.glob("*.png")):
        h = _pixel_hash(f)
        hash_groups.setdefault(h, []).append(f)

    result: list[dict[str, Any]] = []
    for h, paths in hash_groups.items():
        # Prefer shorter name (less likely a copy with prefix)
        paths.sort(key=lambda p: len(p.name))
        result.append({
            "path": paths[0],
            "name": paths[0].stem,
            "content_hash": h,
            "duplicates": paths[1:],
        })

    return result


def scan_and_classify(
    fig_dir: str | Path,
    archetype: str = "engineering",
) -> list[dict[str, Any]]:
    """Scan, deduplicate, and classify images by role.

    Returns list with all scan_and_dedup() fields plus ``role`` key.
    """
    from image_roles import classify_image
    figures = scan_and_dedup(fig_dir)
    for fig in figures:
        fig["role"] = classify_image(fig["name"])
    return figures


# ---------------------------------------------------------------------------
# Figure matching — match existing figures to needs
# ---------------------------------------------------------------------------

def match_figures(
    needs: list[dict[str, Any]],
    unique_figures: list[dict[str, Any]],
    archetype: str = "engineering",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match existing unique figures to needs by description keywords.

    Args:
        needs: [{id, desc, chapter, type, gen, ...}] — from LLM analysis
        unique_figures: from scan_and_dedup()
        archetype: domain archetype for role compatibility scoring

    Returns:
        (matched, unmatched_needs)
        matched: [{need_id, figure_path, desc, chapter}]
        unmatched_needs: needs without a matching figure
    """
    matched: list[dict[str, Any]] = []
    used_hashes: set[str] = set()
    matched_need_ids: set[str] = set()

    # Keywords for common figure types (used for filename→need matching)
    type_keywords: dict[str, list[str]] = {
        "system_architecture": ["architecture", "arch", "system", "架构"],
        "algorithm_flowchart": ["flowchart", "flow", "planning-flow", "流程"],
        "data_flow": ["data_flow", "dataflow", "数据流", "control-flow"],
        "scene_setup": ["plan", "setup", "scene", "地图", "户型", "grid", "map"],
        "intermediate_result": ["avoid", "verify", "check", "obstacle", "检测", "避障"],
        "final_result": ["animation", "last_frame", "result", "最终", "coverage"],
        "comparison_bar": ["comparison", "bar", "对比", "performance"],
        "sensitivity_curve": ["curve", "sensitivity", "detail", "曲线", "trend"],
        "module_diagram": ["module", "模块", "design"],
        "sequence_diagram": ["sequence", "timing", "时序"],
    }

    for need in needs:
        need_id = need.get("id", "")
        desc = need.get("desc", "").lower()
        need_type = need.get("type", "")
        chapter = need.get("chapter", 0)

        best_fig = None
        best_score = -1

        for fig in unique_figures:
            if fig["content_hash"] in used_hashes:
                continue

            name_lower = fig["name"].lower()
            score = 0

            # Match by need type keywords against filename
            for kw in type_keywords.get(need_type, []):
                if kw in name_lower:
                    score += 3
                    break

            # Match by description keywords against filename
            for word in re.split(r"[/\s,，、]", desc):
                if len(word) >= 2 and word in name_lower:
                    score += 2

            # Chapter match in filename
            if str(chapter) in name_lower:
                score += 1

            # Role-chapter compatibility bonus (image_roles)
            try:
                from image_roles import classify_image, is_role_compatible
                fig_role = classify_image(fig["name"])
                if is_role_compatible(fig_role, chapter, archetype):
                    score += 4  # strong bonus for correct role placement
                else:
                    score -= 5  # penalty for wrong role (e.g., result in theory)
            except ImportError:
                pass

            if score > best_score:
                best_score = score
                best_fig = fig

        if best_fig and best_score > 0:
            matched.append({
                "need_id": need_id,
                "figure_path": best_fig["path"],
                "desc": need["desc"],
                "chapter": chapter,
            })
            used_hashes.add(best_fig["content_hash"])
            matched_need_ids.add(need_id)

    unmatched = [n for n in needs if n.get("id") not in matched_need_ids]
    return matched, unmatched


# ---------------------------------------------------------------------------
# Auto-generation: structural diagrams
# ---------------------------------------------------------------------------

def generate_diagram(
    need: dict[str, Any],
    fig_dir: str | Path,
    paper_context: str = "",
    language: str = "zh",
) -> Path | None:
    """Generate a structural diagram for a single need.

    Supported diagram_type: "architecture", "flowchart", "data_flow",
    "sequence", "state".

    The need dict should contain:
      - diagram_type: str
      - diagram_config: dict (optional, with modules/steps/nodes/flows)
      - chapter: int
      - id: str

    If diagram_config is provided, uses it directly.
    Otherwise, tries to extract from paper_context.
    """
    try:
        from diagram_generator import (
            generate_architecture_diagram,
            generate_flowchart,
            generate_data_flow_diagram,
            generate_sequence_diagram,
            generate_state_diagram,
        )
    except ImportError:
        print("[FigurePlanner] diagram_generator not available")
        return None

    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)

    dtype = need.get("diagram_type", "")
    config = need.get("diagram_config")
    chapter = need.get("chapter", 1)
    desc = need.get("desc", "")
    output_name = f"gen_{chapter}_{need.get('id', 'diagram')}"

    try:
        if dtype == "architecture":
            if config:
                modules = config.get("modules", [])
                connections = [tuple(c) for c in config.get("connections", [])]
            else:
                modules, connections = _extract_architecture(paper_context)
            if not modules:
                return None
            path = generate_architecture_diagram(
                fig_dir / output_name,
                title=desc,
                modules=modules,
                connections=connections,
                width=11, height=7,
            )
            return _resolve_png(path, fig_dir, output_name)

        elif dtype == "flowchart":
            if config:
                steps = config.get("steps", [])
            else:
                steps = _extract_steps(paper_context)
            if not steps:
                return None
            path = generate_flowchart(
                fig_dir / output_name,
                title=desc,
                steps=steps,
                start_label="开始" if language == "zh" else "Start",
                end_label="结束" if language == "zh" else "End",
            )
            return _resolve_png(path, fig_dir, output_name)

        elif dtype == "data_flow":
            if config:
                nodes = config.get("nodes", [])
                flows = config.get("flows", [])
            else:
                nodes, flows = _extract_dataflow(paper_context)
            if not nodes:
                return None
            path = generate_data_flow_diagram(
                fig_dir / output_name,
                title=desc,
                nodes=nodes,
                flows=flows,
                width=11, height=5.5,
            )
            return _resolve_png(path, fig_dir, output_name)

        elif dtype == "sequence":
            if config:
                actors = config.get("actors", [])
                messages = config.get("messages", [])
            else:
                return None
            path = generate_sequence_diagram(
                fig_dir / output_name,
                title=desc,
                actors=actors,
                messages=messages,
            )
            return _resolve_png(path, fig_dir, output_name)

        elif dtype == "state":
            if config:
                states = config.get("states", [])
                transitions = config.get("transitions", [])
            else:
                return None
            path = generate_state_diagram(
                fig_dir / output_name,
                title=desc,
                states=states,
                transitions=transitions,
            )
            return _resolve_png(path, fig_dir, output_name)

    except Exception as exc:
        print(f"[FigurePlanner] Diagram generation failed: {exc}")

    return None


def _resolve_png(path: str, fig_dir: Path, name: str) -> Path:
    """Ensure the returned path points to an actual .png file."""
    p = Path(path)
    if p.exists():
        return p
    p = fig_dir / (name + ".png")
    if p.exists():
        return p
    # Last resort
    candidates = list(fig_dir.glob(f"{name}*"))
    return candidates[0] if candidates else Path(path)


# ---------------------------------------------------------------------------
# Auto-generation: data charts
# ---------------------------------------------------------------------------

def generate_chart(
    need: dict[str, Any],
    fig_dir: str | Path,
    paper_context: str = "",
    language: str = "zh",
) -> Path | None:
    """Generate a data chart for a single need.

    The need should contain:
      - chart_type: "bar" | "line" | "heatmap" | "radar" | etc.
      - chart_config: dict with data (optional, extracted from paper if absent)
    """
    try:
        from figure_generator import plot_comparison_bar
    except ImportError:
        print("[FigurePlanner] figure_generator not available")
        return None

    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)

    chart_type = need.get("chart_type", "")
    config = need.get("chart_config")
    chapter = need.get("chapter", 4)
    desc = need.get("desc", "")
    output_name = f"gen_{chapter}_{need.get('id', 'chart')}"

    try:
        if chart_type == "bar":
            if config:
                data = config.get("data", {})
            else:
                data = _extract_bar_data(paper_context)
            if not data:
                return None
            result = plot_comparison_bar(
                data=data,
                output_path=str(fig_dir / output_name),
                title=desc,
                ylabel="数值",
                language=language,
            )
            png_path = result.get("png", "")
            if png_path:
                return Path(png_path)
            return _resolve_png(str(fig_dir / output_name), fig_dir, output_name)

    except Exception as exc:
        print(f"[FigurePlanner] Chart generation failed: {exc}")

    return None


# ---------------------------------------------------------------------------
# Final assignment — each figure used exactly once
# ---------------------------------------------------------------------------

def assign_figures(
    matched: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    needs: list[dict[str, Any]],
    unique_figures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Final assignment: each unique figure to exactly one need slot.

    Combines matched existing figures and newly generated figures,
    ensures no content duplicates, assigns one-to-one.

    Args:
        matched: from match_figures()
        generated: [{need_id, path}] from generate_diagram/chart
        needs: original needs list
        unique_figures: from scan_and_dedup()

    Returns:
        [{need_id, figure_path, desc, chapter}]
    """
    assignments: list[dict[str, Any]] = []
    used_hashes: set[str] = set()

    # Start with matched existing figures
    for m in matched:
        fig_path = m["figure_path"]
        h = _pixel_hash(fig_path)
        if h not in used_hashes:
            assignments.append(m)
            used_hashes.add(h)

    # Add generated figures
    for g in generated:
        fig_path = Path(g.get("path", g.get("figure_path", "")))
        if not fig_path.exists():
            fig_path = fig_path.parent / (fig_path.name + ".png")
        if not fig_path.exists():
            continue
        h = _pixel_hash(fig_path)
        if h not in used_hashes:
            # Find the need for this generated figure
            need_id = g.get("need_id", "")
            need = next((n for n in needs if n.get("id") == need_id), None)
            if need:
                assignments.append({
                    "need_id": need_id,
                    "figure_path": fig_path,
                    "desc": need["desc"],
                    "chapter": need["chapter"],
                })
                used_hashes.add(h)

    return assignments


# ---------------------------------------------------------------------------
# Context extraction helpers (fallback when LLM doesn't provide config)
# ---------------------------------------------------------------------------

def _extract_architecture(text: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """Extract module names from paper text for architecture diagram."""
    pattern = re.compile(
        r"((?:环境感知|全局规划|局部避障|运动控制|数据采集|"
        r"特征提取|模型训练|结果输出|预处理|后处理|决策|通信|"
        r"路径规划|地图构建|传感器|控制)[^\s,，。.]*模块?)"
    )
    found: list[str] = []
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        if name and name not in found:
            found.append(name)
    if not found:
        return [], []
    modules = []
    for i, name in enumerate(found):
        modules.append({"name": name, "x": 5.0, "y": 1.0 + i * 1.2, "w": 2.5, "h": 0.9})
    connections = [(found[i], found[i + 1]) for i in range(len(found) - 1)]
    return modules, connections


def _extract_steps(text: str) -> list[str]:
    """Extract algorithm steps from paper text."""
    steps: list[str] = []
    for m in re.finditer(r"(?:步骤|Step)\s*\d+[：:]\s*(.+)", text):
        steps.append(m.group(1).strip().rstrip("。."))
    if not steps:
        for m in re.finditer(r"^\d+\.\s+(.{5,40})", text, re.MULTILINE):
            steps.append(m.group(1).strip().rstrip("。."))
    return steps[:8]


def _extract_dataflow(text: str) -> tuple[list[dict], list[dict]]:
    """Extract data flow from paper text."""
    modules, _ = _extract_architecture(text)
    if not modules:
        return [], []
    nodes = [{"name": m["name"], "x": m["x"], "y": m["y"]} for m in modules]
    flows = [{"src": modules[i]["name"], "dst": modules[i + 1]["name"], "label": ""}
             for i in range(len(modules) - 1)]
    return nodes, flows


def _extract_bar_data(text: str) -> dict[str, dict[str, float]]:
    """Extract comparison table data from markdown text."""
    table_pat = re.compile(r"^\|(.+)\|$\n^\|[-\s|:]+\|$\n((?:^\|.+\|$\n?)+)", re.MULTILINE)
    for match in table_pat.finditer(text):
        header = [h.strip() for h in match.group(1).split("|") if h.strip()]
        rows: list[list[str]] = []
        for line in match.group(2).strip().split("\n"):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells:
                rows.append(cells)
        if len(rows) < 2 or len(header) < 3:
            continue
        algo_col = -1
        for i, h in enumerate(header):
            if any(kw in h for kw in ("算法", "方法", "模型", "Algorithm", "Method")):
                algo_col = i
                break
        if algo_col < 0:
            continue
        data: dict[str, dict[str, float]] = {}
        for row in rows:
            if len(row) <= algo_col:
                continue
            label = row[algo_col]
            data[label] = {}
            for ci in range(len(header)):
                if ci == algo_col:
                    continue
                try:
                    val = float(row[ci].replace("%", "").strip())
                    data[label][header[ci]] = val
                except (ValueError, IndexError):
                    pass
        if data:
            return data
    return {}


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(
    needs: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    unmatched: list[dict[str, Any]],
) -> str:
    """Format planning result as a readable report."""
    lines = [f"=== Figure Plan: {len(needs)} needs, {len(assignments)} assigned, {len(unmatched)} gaps ==="]

    if assignments:
        lines.append("\n--- Assigned ---")
        for a in sorted(assignments, key=lambda x: x.get("chapter", 0)):
            lines.append(f"  Ch{a['chapter']}: {a['desc']}  ->  {Path(a['figure_path']).name}")

    if unmatched:
        lines.append("\n--- Gaps (need generation) ---")
        for g in unmatched:
            gen = g.get("gen", "?")
            lines.append(f"  Ch{g['chapter']}: {g['desc']}  [{gen}]  id={g.get('id', '')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------

def validate_figure_plan(
    assignments: list[dict[str, Any]],
    archetype: str = "engineering",
    md_text: str = "",
) -> dict[str, Any]:
    """Validate the complete figure plan against chapter-role rules.

    Args:
        assignments: from assign_figures() or match_figures()
        archetype: domain archetype for compatibility matrix
        md_text: optional markdown text for orphan table detection

    Returns:
        Dict with keys: valid, violations, missing, orphan_tables.
    """
    from image_roles import (
        classify_image, is_role_compatible, get_image_order_key,
        validate_chapter_images, detect_orphan_tables, CHAPTER_ROLE_MATRIX,
    )

    # Group assignments by chapter
    chapter_images: dict[int, list[dict[str, Any]]] = {}
    for a in assignments:
        ch = a.get("chapter", 0)
        fig_name = Path(a.get("figure_path", a.get("desc", ""))).stem
        role = classify_image(fig_name)
        chapter_images.setdefault(ch, []).append({
            "name": fig_name,
            "role": role,
            "order": get_image_order_key(role),
        })

    # Validate each chapter
    all_violations = []
    all_missing = []
    for ch, images in chapter_images.items():
        result = validate_chapter_images(ch, images, archetype)
        all_violations.extend(result["violations"])

        # Check for missing required roles
        matrix = CHAPTER_ROLE_MATRIX.get(archetype, {}).get(ch, {})
        order = matrix.get("order", [])
        present_roles = {img["role"] for img in images}
        for required_role in order:
            if required_role not in present_roles:
                all_missing.append({"chapter": ch, "role": required_role})

    # Detect orphan tables (tables without comparison charts)
    orphan_tables = []
    if md_text:
        existing_roles = [img["role"] for imgs in chapter_images.values() for img in imgs]
        orphan_tables = detect_orphan_tables(md_text, existing_roles)

    return {
        "valid": len(all_violations) == 0,
        "violations": all_violations,
        "missing": all_missing,
        "orphan_tables": orphan_tables,
    }
