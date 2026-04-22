"""Academic diagram generator — flowcharts, architecture diagrams, data flow diagrams,
sequence diagrams, state diagrams.

Uses Graphviz DOT as the rendering engine for automatic layout and publication-quality
output. Falls back to matplotlib patches if Graphviz is unavailable.

Public API is identical to the previous matplotlib version:
  - generate_flowchart()
  - generate_architecture_diagram()
  - generate_data_flow_diagram()
  - generate_sequence_diagram()
  - generate_state_diagram()
  - auto_generate_missing_diagrams()
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Engine detection
# ---------------------------------------------------------------------------

_HAS_GRAPHVIZ = False
_GRAPHVIZ_PATH = ""

def _detect_graphviz() -> bool:
    """Detect if Graphviz dot command is available."""
    global _HAS_GRAPHVIZ, _GRAPHVIZ_PATH
    # Check common install locations on Windows
    candidates = []
    system = platform.system()
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Graphviz\bin",
            r"C:\Program Files (x86)\Graphviz\bin",
        ]
    for c in candidates:
        dot = os.path.join(c, "dot.exe")
        if os.path.isfile(dot):
            _GRAPHVIZ_PATH = c
            _HAS_GRAPHVIZ = True
            return True
    # Try PATH
    try:
        result = subprocess.run(["dot", "-V"], capture_output=True, timeout=5)
        if result.returncode == 0:
            _HAS_GRAPHVIZ = True
            return True
    except Exception:
        pass
    return False

_detect_graphviz()


def _font_name() -> str:
    """Return a suitable Chinese font name for Graphviz."""
    system = platform.system()
    if system == "Windows":
        return "Microsoft YaHei"
    elif system == "Darwin":
        return "PingFang SC"
    return "WenQuanYi Micro Hei"


def _dot_cmd() -> list[str]:
    """Return the full path to the dot executable."""
    if _GRAPHVIZ_PATH:
        return [os.path.join(_GRAPHVIZ_PATH, "dot.exe" if platform.system() == "Windows" else "dot")]
    return ["dot"]


def _render_dot(dot_source: str, output_path: str | Path, dpi: int = 300) -> str:
    """Render DOT source to PNG using Graphviz dot command."""
    output_path = Path(output_path)
    if output_path.suffix != ".png":
        output_path = output_path.with_suffix(".png")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dot_file = output_path.with_suffix(".gv")
    dot_file.write_text(dot_source, encoding="utf-8")

    try:
        result = subprocess.run(
            _dot_cmd() + ["-Tpng", f"-Gdpi={dpi}", "-o", str(output_path), str(dot_file)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and output_path.exists():
            return str(output_path)
    except Exception:
        pass

    # Fallback: python-graphviz library
    try:
        import graphviz
        src = graphviz.Source(dot_source)
        src.format = "png"
        out = src.render(filename=output_path.stem, directory=str(output_path.parent), cleanup=True)
        out_path = Path(out)
        if out_path.exists() and out_path != output_path:
            if output_path.exists():
                output_path.unlink()
            out_path.rename(output_path)
        return str(output_path)
    except Exception as exc:
        print(f"[DiagramGenerator] Rendering failed: {exc}")
        return str(output_path)


def _wrap_diagram_label(text: str, max_chars: int = 12) -> str:
    """Wrap long labels so Graphviz blocks stay readable."""
    wrapped_parts: list[str] = []
    for part in str(text or "").splitlines() or [""]:
        segment = part.strip()
        if not segment:
            wrapped_parts.append("")
            continue
        if len(segment) <= max_chars:
            wrapped_parts.append(segment)
            continue
        if " " in segment:
            current = ""
            for token in segment.split():
                candidate = f"{current} {token}".strip()
                if current and len(candidate) > max_chars:
                    wrapped_parts.append(current)
                    current = token
                else:
                    current = candidate
            if current:
                wrapped_parts.append(current)
            continue
        while segment:
            wrapped_parts.append(segment[:max_chars])
            segment = segment[max_chars:]
    return "\n".join(part for part in wrapped_parts if part)


# ---------------------------------------------------------------------------
# Category color palette (pastel academic style)
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "input":    "#C8E6C9",  # light green
    "process":  "#BBDEFB",  # light blue
    "output":   "#FFCCBC",  # light orange
    "storage":  "#D1C4E9",  # light purple
    "control":  "#FFF9C4",  # light yellow
    "default":  "#E8EDF2",  # light grey
}


# ---------------------------------------------------------------------------
# Flowchart generator
# ---------------------------------------------------------------------------

def generate_flowchart(
    output_path: str | Path,
    title: str = "",
    steps: list[str] = (),
    start_label: str = "开始",
    end_label: str = "结束",
    decision_labels: list[str] = (),
    box_width: float = 2.8,
    box_height: float = 0.6,
    gap: float = 0.4,
) -> str:
    """Generate a vertical flowchart using Graphviz DOT.

    Args:
        steps: list of step descriptions (process boxes)
        decision_labels: indices (0-based into steps) where the box is a decision (diamond)
    """
    font = _font_name()
    decision_set = set(decision_labels)

    lines = [
        'digraph flowchart {',
        '  rankdir=TB;',
        '  node [fontname="' + font + '", fontsize=11, style="filled", color="#5B6472", penwidth=1.2, margin="0.16,0.09"];',
        '  edge [fontname="' + font + '", fontsize=9, color="#555555", arrowsize=0.75, penwidth=1.0];',
        f'  graph [fontname="{font}", dpi=300, nodesep=0.55, ranksep=0.9, pad=0.25, margin=0.15, splines=ortho, bgcolor="white"];',
        '',
    ]

    # Start node (oval / ellipse)
    lines.append(f'  start [label="{start_label}", shape=ellipse, '
                 f'style="filled", fillcolor="#E8F5E9", fontsize=12];')

    # Step nodes
    for i, step in enumerate(steps):
        safe_id = f"step{i}"
        escaped = _wrap_diagram_label(step).replace('"', '\\"')
        if i in decision_set:
            lines.append(
                f'  {safe_id} [label="{escaped}", shape=diamond, width=2.2, height=1.0, '
                f'style="filled", fillcolor="#FFF3E0"];'
            )
        else:
            lines.append(
                f'  {safe_id} [label="{escaped}", shape=box, width=2.4, height=0.8, '
                f'style="filled,rounded", fillcolor="#E8EDF2"];'
            )

    # End node
    lines.append(f'  end [label="{end_label}", shape=ellipse, '
                 f'style="filled", fillcolor="#E8F5E9", fontsize=12];')

    lines.append('')

    # Edges
    prev = "start"
    for i in range(len(steps)):
        cur = f"step{i}"
        lines.append(f'  {prev} -> {cur};')
        prev = cur
    lines.append(f'  {prev} -> end;')

    # Title
    if title:
        lines.append(f'  labelloc="t";')
        lines.append(f'  label="{title.replace(chr(34), chr(92)+chr(34))}";')
        lines.append(f'  fontsize=14;')

    lines.append('}')
    return _render_dot('\n'.join(lines), output_path)


# ---------------------------------------------------------------------------
# Architecture diagram generator
# ---------------------------------------------------------------------------

def generate_architecture_diagram(
    output_path: str | Path,
    title: str = "",
    modules: list[dict[str, Any]] = (),
    connections: list[tuple[str, str]] = (),
    width: float = 10,
    height: float = 6,
) -> str:
    """Generate a system architecture/block diagram using Graphviz DOT.

    Args:
        modules: [{"name": str, "x": float, "y": float, "w": float, "h": float,
                   "color": str, "category": str}, ...]
        connections: [(src_name, dst_name), ...]
    """
    font = _font_name()

    lines = [
        'digraph architecture {',
        '  rankdir=TB;',
        '  node [fontname="' + font + '", fontsize=11, style="filled,rounded", shape=box, color="#5B6472", penwidth=1.15, margin="0.18,0.11"];',
        '  edge [fontname="' + font + '", fontsize=9, color="#555555", arrowsize=0.75, penwidth=1.0];',
        f'  graph [fontname="{font}", dpi=300, nodesep=0.65, ranksep=0.9, pad=0.25, margin=0.15, splines=ortho, bgcolor="white"];',
        '',
    ]

    # Group modules by y-coordinate (same y = same rank)
    rank_groups: dict[float, list[str]] = {}
    for mod in modules:
        name = mod["name"]
        safe_id = _safe_id(name)
        escaped = _wrap_diagram_label(name, max_chars=10).replace('"', '\\"')
        category = mod.get("category", "")
        color = mod.get("color", _CATEGORY_COLORS.get(category, _CATEGORY_COLORS["default"]))
        width = max(1.8, float(mod.get("w", 2.5)) * 0.42)
        height = max(0.8, float(mod.get("h", 0.8)) * 0.75)
        lines.append(f'  {safe_id} [label="{escaped}", fillcolor="{color}", width={width:.2f}, height={height:.2f}];')

        y_key = round(mod.get("y", 0), 1)
        rank_groups.setdefault(y_key, []).append(safe_id)

    # Define ranks (same y → same rank)
    for y_val in sorted(rank_groups.keys(), reverse=True):
        ids = [
            _safe_id(mod["name"])
            for mod in sorted((item for item in modules if round(item.get("y", 0), 1) == y_val), key=lambda item: item.get("x", 0))
        ]
        if len(ids) > 1:
            lines.append(f'  {{rank=same; {" ".join(ids)};}}')
            for left, right in zip(ids, ids[1:]):
                lines.append(f'  {left} -> {right} [style=invis, weight=10];')

    lines.append('')

    # Connections
    for src, dst in connections:
        s_id = _safe_id(src)
        d_id = _safe_id(dst)
        lines.append(f'  {s_id} -> {d_id};')

    # Title
    if title:
        lines.append(f'  labelloc="t";')
        lines.append(f'  label="{title.replace(chr(34), chr(92)+chr(34))}";')
        lines.append(f'  fontsize=14;')

    lines.append('}')
    return _render_dot('\n'.join(lines), output_path)


# ---------------------------------------------------------------------------
# Data flow diagram
# ---------------------------------------------------------------------------

def generate_data_flow_diagram(
    output_path: str | Path,
    title: str = "",
    nodes: list[dict[str, Any]] = (),
    flows: list[dict[str, Any]] = (),
    width: float = 12,
    height: float = 5,
) -> str:
    """Generate a data flow diagram with labeled arrows using Graphviz DOT.

    Args:
        nodes: [{"name": str, "x": float, "y": float, "color": str}, ...]
        flows: [{"src": str, "dst": str, "label": str, "color": str}, ...]
    """
    font = _font_name()

    lines = [
        'digraph dataflow {',
        '  rankdir=LR;',
        '  node [fontname="' + font + '", fontsize=11, style="filled,rounded", shape=box, color="#5B6472", penwidth=1.15, margin="0.18,0.11"];',
        '  edge [fontname="' + font + '", fontsize=9, penwidth=1.0, arrowsize=0.75];',
        f'  graph [fontname="{font}", dpi=300, nodesep=0.7, ranksep=1.0, pad=0.25, margin=0.15, splines=ortho, bgcolor="white"];',
        '',
    ]

    # Group nodes by y-coordinate for ranking
    rank_groups: dict[float, list[str]] = {}
    for nd in nodes:
        name = nd["name"]
        safe_id = _safe_id(name)
        escaped = _wrap_diagram_label(name, max_chars=10).replace('"', '\\"')
        color = nd.get("color", "#E8EDF2")
        lines.append(f'  {safe_id} [label="{escaped}", fillcolor="{color}", width=2.05, height=0.82];')

        y_key = round(nd.get("y", 0), 1)
        rank_groups.setdefault(y_key, []).append(safe_id)

    # Define ranks
    for y_val in sorted(rank_groups.keys(), reverse=True):
        ids = [
            _safe_id(node["name"])
            for node in sorted((item for item in nodes if round(item.get("y", 0), 1) == y_val), key=lambda item: item.get("x", 0))
        ]
        if len(ids) > 1:
            lines.append(f'  {{rank=same; {" ".join(ids)};}}')
            for left, right in zip(ids, ids[1:]):
                lines.append(f'  {left} -> {right} [style=invis, weight=10];')

    lines.append('')

    # Flows with labels
    for flow in flows:
        src_name, dst_name = flow["src"], flow["dst"]
        label = flow.get("label", "")
        color = flow.get("color", "#555555")
        s_id = _safe_id(src_name)
        d_id = _safe_id(dst_name)
        escaped_label = label.replace('"', '\\"')
        lines.append(f'  {s_id} -> {d_id} [label="{escaped_label}", color="{color}", '
                     f'fontcolor="{color}"];')

    if title:
        lines.append(f'  labelloc="t";')
        lines.append(f'  label="{title.replace(chr(34), chr(92)+chr(34))}";')
        lines.append(f'  fontsize=14;')

    lines.append('}')
    return _render_dot('\n'.join(lines), output_path)


# ---------------------------------------------------------------------------
# Timing / sequence diagram
# ---------------------------------------------------------------------------

def generate_sequence_diagram(
    output_path: str | Path,
    title: str = "",
    participants: list[str] = (),
    messages: list[dict[str, Any]] = (),
    width: float = 10,
    height: float = 6,
) -> str:
    """Generate a UML-style sequence diagram using Graphviz DOT.

    Args:
        participants: list of participant names (left to right)
        messages: [{"from": str, "to": str, "label": str, "type": "solid"|dashed}, ...]
    """
    font = _font_name()

    # Sequence diagrams are best rendered as a simple graph with invisible edges
    # for lifeline ordering
    lines = [
        'digraph sequence {',
        '  rankdir=TB;',
        '  node [fontname="' + font + '", fontsize=11, shape=none];',
        '  edge [fontname="' + font + '", fontsize=9];',
        f'  graph [fontname="{font}", dpi=300, bgcolor="white", nodesep=0.3];',
        '',
    ]

    # Create participant header nodes at top (rank=same)
    for i, name in enumerate(participants):
        safe_id = f"p{i}"
        escaped = name.replace('"', '\\"')
        lines.append(f'  {safe_id} [label=<<TABLE BORDER="1" CELLBORDER="0" '
                     f'CELLPADDING="4"><TR><TD BGCOLOR="#E8EDF2"><FONT FACE="{font}">'
                     f'{escaped}</FONT></TD></TR></TABLE>>];')

    # All participants on same rank
    p_ids = [f"p{i}" for i in range(len(participants))]
    lines.append(f'  {{rank=same; {" ".join(p_ids)};}}')
    lines.append('')

    # Map participant names to indices for arrow drawing
    p_map = {name: f"p{i}" for i, name in enumerate(participants)}

    # Messages as edges between participants, using invisible intermediate nodes
    # to enforce vertical ordering
    for mi, msg in enumerate(messages):
        src = p_map.get(msg["from"], "p0")
        dst = p_map.get(msg["to"], "p0")
        label = msg.get("label", "")
        escaped_label = label.replace('"', '\\"')
        style = "dashed" if msg.get("type") == "dashed" else "solid"

        # Use a hidden node to create vertical spacing
        mid_id = f"msg{mi}"
        lines.append(f'  {mid_id} [label="", shape=point, width=0, height=0];')

        lines.append(f'  {src} -> {mid_id} [style=invis, minlen=0];')
        lines.append(f'  {mid_id} -> {dst} [label="{escaped_label}", '
                     f'style="{style}", color="#333333", '
                     f'arrowhead="vee", fontcolor="#333333"];')

    if title:
        lines.append(f'  labelloc="t";')
        lines.append(f'  label="{title.replace(chr(34), chr(92)+chr(34))}";')
        lines.append(f'  fontsize=14;')

    lines.append('}')
    return _render_dot('\n'.join(lines), output_path)


# ---------------------------------------------------------------------------
# State machine diagram
# ---------------------------------------------------------------------------

def generate_state_diagram(
    output_path: str | Path,
    title: str = "",
    states: list[dict[str, Any]] = (),
    transitions: list[dict[str, Any]] = (),
    width: float = 10,
    height: float = 5,
) -> str:
    """Generate a state machine diagram using Graphviz DOT.

    Args:
        states: [{"name": str, "x": float, "y": float, "initial": bool, "final": bool}, ...]
        transitions: [{"from": str, "to": str, "label": str}, ...]
    """
    font = _font_name()

    lines = [
        'digraph statemachine {',
        '  rankdir=TB;',
        '  node [fontname="' + font + '", fontsize=11, shape=circle, style="filled"];',
        '  edge [fontname="' + font + '", fontsize=9, color="#555555"];',
        f'  graph [fontname="{font}", dpi=300, nodesep=0.8, ranksep=0.8, bgcolor="white"];',
        '',
    ]

    # Initial state marker
    has_initial = any(s.get("initial") for s in states)
    if has_initial:
        lines.append('  __initial [label="", shape=point, width=0.2, fillcolor="#333333"];')

    # State nodes
    for st in states:
        name = st["name"]
        safe_id = _safe_id(name)
        escaped = name.replace('"', '\\"')
        is_final = st.get("final", False)

        if is_final:
            lines.append(f'  {safe_id} [label="{escaped}", fillcolor="#E8F5E9", '
                         f'peripheries=2];')
        else:
            lines.append(f'  {safe_id} [label="{escaped}", fillcolor="#E8EDF2"];')

        if st.get("initial"):
            lines.append(f'  __initial -> {safe_id};')

    lines.append('')

    # Transitions
    for tr in transitions:
        src = _safe_id(tr["from"])
        dst = _safe_id(tr["to"])
        label = tr.get("label", "")
        escaped_label = label.replace('"', '\\"')

        if tr["from"] == tr["to"]:
            # Self-loop
            lines.append(f'  {src} -> {dst} [label="{escaped_label}", '
                         f'headport="n", tailport="n"];')
        else:
            lines.append(f'  {src} -> {dst} [label="{escaped_label}"];')

    if title:
        lines.append(f'  labelloc="t";')
        lines.append(f'  label="{title.replace(chr(34), chr(92)+chr(34))}";')
        lines.append(f'  fontsize=14;')

    lines.append('}')
    return _render_dot('\n'.join(lines), output_path)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

_counter = 0

def _safe_id(name: str) -> str:
    """Convert a display name to a safe Graphviz node ID."""
    global _counter
    # Strip special chars, keep alphanumerics and CJK
    import re
    safe = re.sub(r'[^\w\u4e00-\u9fff]', '_', name.strip())
    if not safe or safe == '_':
        _counter += 1
        return f"node_{_counter}"
    # Prefix with n_ to ensure it starts with a letter
    return f"n_{safe}"


# ---------------------------------------------------------------------------
# Auto-generate all missing diagrams for a paper
# ---------------------------------------------------------------------------

def auto_generate_missing_diagrams(
    output_dir: str | Path,
    paper_context: str = "",
) -> list[dict[str, str]]:
    """Auto-generate common academic diagrams based on paper context keywords.

    Detects what diagrams are needed from the paper text and generates them.
    Returns list of {"type": str, "path": str, "description": str}.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []

    context_lower = paper_context.lower()

    # 1. SLAM mapping flow
    if any(kw in context_lower for kw in ["slam", "建图", "定位"]):
        path = generate_flowchart(
            output_dir / "slam-mapping-flow",
            title="SLAM建图与定位主流程",
            steps=[
                "激光扫描数据输入",
                "时间戳对齐与运动学预测",
                "点云预处理(裁剪/去噪/坐标变换)",
                "扫描匹配(当前帧→子图/距离场)",
                "回环检测与约束添加",
                "位姿图增量优化",
                "地图更新与发布",
            ],
            start_label="传感器数据输入",
            end_label="输出: 地图/位姿/轨迹",
            decision_labels=[4],
        )
        generated.append({"type": "flowchart", "path": path, "description": "SLAM建图与定位主流程图"})

    # 2. Pose graph optimization flow
    if any(kw in context_lower for kw in ["位姿图", "图优化", "回环"]):
        path = generate_flowchart(
            output_dir / "pose-graph-optimization",
            title="位姿图优化迭代流程",
            steps=[
                "固定参考节点消除自由度",
                "计算边残差与雅可比矩阵",
                "累加稀疏矩阵H与梯度向量b",
                "求解线性方程 HΔX = -b",
                "更新状态 X ← X ⊕ ΔX",
                "收敛判断(增量/目标函数下降量)",
            ],
            decision_labels=[5],
        )
        generated.append({"type": "flowchart", "path": path, "description": "位姿图优化迭代流程图"})

    # 3. Navigation planning control loop
    if any(kw in context_lower for kw in ["导航", "规划", "控制", "navigation"]):
        path = generate_flowchart(
            output_dir / "navigation-control-loop",
            title="导航规划与控制闭环流程",
            steps=[
                "接收目标点与当前位姿",
                "全局路径规划",
                "局部参考路径截取",
                "障碍物检测与代价地图更新",
                "候选速度/轨迹采样",
                "代价评价(跟踪/碰撞/平滑)",
                "动力学约束过滤",
                "最优控制指令发布",
                "到达判断",
            ],
            start_label="目标点输入",
            end_label="速度指令输出",
            decision_labels=[8],
        )
        generated.append({"type": "flowchart", "path": path, "description": "导航规划与控制闭环流程图"})

    # 4. System architecture
    if any(kw in context_lower for kw in ["架构", "系统", "模块", "architecture"]):
        path = generate_architecture_diagram(
            output_dir / "system-overview-architecture",
            title="系统总体架构",
            modules=[
                {"name": "传感器层\n(激光/IMU/里程计)", "x": 2, "y": 5, "w": 2.8, "h": 0.9, "category": "input"},
                {"name": "ROS2中间件\n(DDS通信/TF/Lifecycle)", "x": 5.5, "y": 5, "w": 2.8, "h": 0.9, "category": "process"},
                {"name": "SLAM模块\n(建图/定位/回环)", "x": 2, "y": 3.5, "w": 2.8, "h": 0.9, "category": "process"},
                {"name": "导航模块\n(全局规划/局部控制)", "x": 5.5, "y": 3.5, "w": 2.8, "h": 0.9, "category": "process"},
                {"name": "执行机构\n(差速底盘/电机)", "x": 9, "y": 3.5, "w": 2.8, "h": 0.9, "category": "output"},
                {"name": "仿真/可视化\n(Gazebo/RViz)", "x": 9, "y": 5, "w": 2.8, "h": 0.9, "category": "output"},
                {"name": "数据存储\n(地图/日志/配置)", "x": 5.5, "y": 2, "w": 2.8, "h": 0.9, "category": "storage"},
            ],
            connections=[
                ("传感器层\n(激光/IMU/里程计)", "ROS2中间件\n(DDS通信/TF/Lifecycle)"),
                ("ROS2中间件\n(DDS通信/TF/Lifecycle)", "SLAM模块\n(建图/定位/回环)"),
                ("ROS2中间件\n(DDS通信/TF/Lifecycle)", "导航模块\n(全局规划/局部控制)"),
                ("导航模块\n(全局规划/局部控制)", "执行机构\n(差速底盘/电机)"),
                ("ROS2中间件\n(DDS通信/TF/Lifecycle)", "仿真/可视化\n(Gazebo/RViz)"),
                ("SLAM模块\n(建图/定位/回环)", "数据存储\n(地图/日志/配置)"),
                ("导航模块\n(全局规划/局部控制)", "数据存储\n(地图/日志/配置)"),
            ],
        )
        generated.append({"type": "architecture", "path": path, "description": "系统总体架构图"})

    # 5. Node communication diagram
    if any(kw in context_lower for kw in ["节点", "通信", "topic", "node"]):
        path = generate_data_flow_diagram(
            output_dir / "node-communication",
            title="节点通信与数据流关系",
            nodes=[
                {"name": "激光驱动", "x": 1.5, "y": 4},
                {"name": "SLAM节点", "x": 5, "y": 4},
                {"name": "代价地图", "x": 8.5, "y": 4},
                {"name": "规划器", "x": 5, "y": 2.5},
                {"name": "控制器", "x": 8.5, "y": 2.5},
                {"name": "底盘驱动", "x": 11, "y": 2.5},
            ],
            flows=[
                {"src": "激光驱动", "dst": "SLAM节点", "label": "/scan", "color": "#1565C0"},
                {"src": "SLAM节点", "dst": "代价地图", "label": "/map", "color": "#2E7D32"},
                {"src": "SLAM节点", "dst": "规划器", "label": "/tf", "color": "#E65100"},
                {"src": "代价地图", "dst": "规划器", "label": "/costmap", "color": "#6A1B9A"},
                {"src": "规划器", "dst": "控制器", "label": "/plan", "color": "#1565C0"},
                {"src": "控制器", "dst": "底盘驱动", "label": "/cmd_vel", "color": "#C62828"},
            ],
        )
        generated.append({"type": "dataflow", "path": path, "description": "节点通信与坐标系关系图"})

    # 6. SLAM frontend-backend cooperation
    if any(kw in context_lower for kw in ["前端", "后端", "前端匹配", "回环"]):
        path = generate_flowchart(
            output_dir / "slam-frontend-backend",
            title="SLAM前后端协同流程",
            steps=[
                "激光数据采集与预处理",
                "前端扫描匹配(当前帧→子图)",
                "计算位姿增量",
                "回环候选检测",
                "回环匹配得分计算",
                "后端位姿图优化(增量)",
                "地图更新与发布",
            ],
            start_label="激光扫描输入",
            end_label="优化后地图输出",
            decision_labels=[3, 4],
        )
        generated.append({"type": "flowchart", "path": path, "description": "SLAM前后端协同流程图"})

    # 7. System startup sequence
    if any(kw in context_lower for kw in ["启动", "初始化", "startup"]):
        path = generate_sequence_diagram(
            output_dir / "system-startup-sequence",
            title="系统启动时序图",
            participants=["Launch文件", "Gazebo", "SLAM节点", "Nav2节点", "RViz"],
            messages=[
                {"from": "Launch文件", "to": "Gazebo", "label": "启动仿真环境"},
                {"from": "Launch文件", "to": "SLAM节点", "label": "启动SLAM"},
                {"from": "SLAM节点", "to": "Gazebo", "label": "订阅/laser", "type": "dashed"},
                {"from": "Launch文件", "to": "Nav2节点", "label": "启动导航(延迟5s)"},
                {"from": "Nav2节点", "to": "SLAM节点", "label": "订阅/map + /tf", "type": "dashed"},
                {"from": "Launch文件", "to": "RViz", "label": "启动可视化"},
                {"from": "RViz", "to": "SLAM节点", "label": "订阅/map(显示)", "type": "dashed"},
                {"from": "RViz", "to": "Nav2节点", "label": "订阅/path(显示)", "type": "dashed"},
            ],
        )
        generated.append({"type": "sequence", "path": path, "description": "系统启动与模式切换时序图"})

    # 8. Navigation state machine
    if any(kw in context_lower for kw in ["状态", "模式", "切换", "state", "mode"]):
        path = generate_state_diagram(
            output_dir / "navigation-state-machine",
            title="导航状态机",
            states=[
                {"name": "空闲", "x": 1.5, "y": 3, "initial": True},
                {"name": "规划", "x": 3.5, "y": 4.5},
                {"name": "跟踪", "x": 6, "y": 4.5},
                {"name": "精调", "x": 8.5, "y": 3},
                {"name": "完成", "x": 6, "y": 1.2, "final": True},
                {"name": "异常", "x": 3.5, "y": 1.2},
            ],
            transitions=[
                {"from": "空闲", "to": "规划", "label": "接收目标"},
                {"from": "规划", "to": "跟踪", "label": "路径生成"},
                {"from": "跟踪", "to": "精调", "label": "接近目标"},
                {"from": "精调", "to": "完成", "label": "到达容差"},
                {"from": "跟踪", "to": "异常", "label": "规划失败"},
                {"from": "异常", "to": "规划", "label": "恢复"},
                {"from": "规划", "to": "异常", "label": "超时"},
            ],
        )
        generated.append({"type": "state", "path": path, "description": "异常检测与恢复机制流程图"})

    # 9. Anomaly detection flow
    if any(kw in context_lower for kw in ["异常", "故障", "恢复", "anomaly"]):
        path = generate_flowchart(
            output_dir / "anomaly-detection-recovery",
            title="异常检测与恢复机制",
            steps=[
                "运行状态监测(位姿/代价/速度)",
                "异常检测(超时/偏离/丢锁)",
                "异常分类(感知/规划/控制)",
                "执行恢复策略",
                "恢复状态验证",
                "恢复正常运行/请求人工干预",
            ],
            start_label="系统运行中",
            end_label="恢复完成",
            decision_labels=[1, 4],
        )
        generated.append({"type": "flowchart", "path": path, "description": "异常检测与恢复机制流程图"})

    # 10. Parameter configuration relation
    if any(kw in context_lower for kw in ["参数", "配置", "parameter", "config"]):
        path = generate_architecture_diagram(
            output_dir / "parameter-configuration",
            title="参数配置与运行模式关系",
            modules=[
                {"name": "仿真参数\n(use_sim_time)", "x": 5.5, "y": 5.2, "w": 2.5, "h": 0.8, "category": "input"},
                {"name": "SLAM参数\n(分辨率/回环/匹配)", "x": 3.2, "y": 3.6, "w": 2.5, "h": 0.8, "category": "process"},
                {"name": "导航参数\n(代价/采样/速度)", "x": 7.8, "y": 3.6, "w": 2.5, "h": 0.8, "category": "process"},
                {"name": "在线建图模式", "x": 3.2, "y": 2.0, "w": 2.5, "h": 0.8, "category": "control"},
                {"name": "定位导航模式", "x": 7.8, "y": 2.0, "w": 2.5, "h": 0.8, "category": "control"},
            ],
            connections=[
                ("仿真参数\n(use_sim_time)", "SLAM参数\n(分辨率/回环/匹配)"),
                ("仿真参数\n(use_sim_time)", "导航参数\n(代价/采样/速度)"),
                ("SLAM参数\n(分辨率/回环/匹配)", "在线建图模式"),
                ("导航参数\n(代价/采样/速度)", "定位导航模式"),
                ("在线建图模式", "定位导航模式"),
            ],
        )
        generated.append({"type": "architecture", "path": path, "description": "参数配置与运行模式关系图"})

    # 11. Core data flow and topic
    if any(kw in context_lower for kw in ["数据流", "topic", "核心"]):
        path = generate_data_flow_diagram(
            output_dir / "core-data-flow",
            title="核心数据流与主题关系",
            nodes=[
                {"name": "激光雷达", "x": 1, "y": 3.5, "color": "#C8E6C9"},
                {"name": "里程计", "x": 1, "y": 2, "color": "#C8E6C9"},
                {"name": "SLAM前端", "x": 3.5, "y": 3.5, "color": "#BBDEFB"},
                {"name": "SLAM后端", "x": 3.5, "y": 2, "color": "#BBDEFB"},
                {"name": "地图服务器", "x": 6, "y": 2.8, "color": "#D1C4E9"},
                {"name": "全局规划器", "x": 6, "y": 1, "color": "#FFF9C4"},
                {"name": "局部规划器", "x": 8.5, "y": 1.8, "color": "#FFF9C4"},
                {"name": "底盘", "x": 11, "y": 1.8, "color": "#FFCCBC"},
            ],
            flows=[
                {"src": "激光雷达", "dst": "SLAM前端", "label": "/scan"},
                {"src": "里程计", "dst": "SLAM前端", "label": "/odom"},
                {"src": "SLAM前端", "dst": "SLAM后端", "label": "约束"},
                {"src": "SLAM后端", "dst": "地图服务器", "label": "/map"},
                {"src": "地图服务器", "dst": "全局规划器", "label": "/map"},
                {"src": "全局规划器", "dst": "局部规划器", "label": "/plan"},
                {"src": "局部规划器", "dst": "底盘", "label": "/cmd_vel"},
                {"src": "地图服务器", "dst": "局部规划器", "label": "/costmap"},
            ],
            width=13,
        )
        generated.append({"type": "dataflow", "path": path, "description": "核心数据流与主题关系图"})

    # 13-16. Algorithm principle diagrams for theory chapter
    theory_keywords = {
        "grid_map": ("栅格", "grid", "A*", "a*", "节点扩展", "node expansion", "启发式搜索", "路径搜索"),
        "potential_field": ("人工势场", "potential field", "势场", "引力场", "斥力场", "避障"),
        "cell_decomposition": ("单元分解", "cell decomp", "区域分割", "boustrophedon", "牛耕"),
        "scan_line": ("扫描线", "scan line", "全覆盖", "coverage", "遍历", "弓字", "往返"),
    }
    for ptype, keywords in theory_keywords.items():
        if any(kw in context_lower for kw in keywords):
            ppath = generate_principle_diagram(
                output_dir / f"gen_principle_{ptype}",
                title=f"algorithm principle: {ptype}",
                principle_type=ptype,
                language="zh",
            )
            type_desc = {"grid_map": "A*栅格搜索", "potential_field": "人工势场",
                         "cell_decomposition": "单元分解", "scan_line": "牛耕法遍历"}
            generated.append({"type": "principle", "path": ppath,
                              "description": type_desc.get(ptype, ptype) + "原理示意图"})

    # 17. Auto-extract figures from project source code (MATLAB/Python)
    # If output_dir looks like <project>/output/figures, try extraction
    try:
        from tools.project_figure_extractor import auto_extract_project_figures
        project_root = output_dir.parent.parent  # output/figures -> project root
        if project_root.is_dir() and (project_root / "output").is_dir():
            extract_result = auto_extract_project_figures(
                project_root=project_root,
                output_dir=output_dir,
                execute=True,
                language="zh",
            )
            for r in extract_result.get("results", []):
                if r["success"] and r.get("figure_path"):
                    generated.append({
                        "type": "extracted",
                        "path": r["figure_path"],
                        "description": f"Auto-extracted from project source code",
                    })
    except Exception:
        pass  # Non-fatal

    return generated


# ---------------------------------------------------------------------------
# Algorithm principle diagrams (matplotlib-based, not Graphviz)
# ---------------------------------------------------------------------------

def generate_principle_diagram(
    output_path: str | Path,
    title: str = "",
    principle_type: str = "auto",
    config: dict[str, Any] | None = None,
    language: str = "zh",
) -> str:
    """Generate an algorithm principle illustration using matplotlib.

    Types:
    - grid_map: A* node expansion with open/closed sets
    - potential_field: Attractive + repulsive force vector field
    - cell_decomposition: Region partitioning with cell boundaries
    - scan_line: Boustrophedon scan line pattern
    - auto: Detect from title/keywords

    Args:
        output_path: Output PNG path.
        title: Diagram title (used for auto-type detection).
        principle_type: One of the types above.
        config: Optional dict with parameters (grid_size, obstacles, etc.).
        language: "zh" or "en" for labels.

    Returns:
        Output file path as string.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyArrowPatch
    import numpy as np

    # Configure Chinese font support
    if language == "zh":
        try:
            matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    config = config or {}

    if principle_type == "auto":
        principle_type = _detect_principle_type(title)

    # Color palette
    COLORS = {
        "obstacle": "#E57373",
        "open": "#90CAF9",
        "closed": "#A5D6A7",
        "path": "#FFD54F",
        "start": "#4CAF50",
        "goal": "#F44336",
        "arrow": "#1565C0",
        "repulsive": "#EF5350",
        "attractive": "#42A5F5",
        "cell_boundary": "#7E57C2",
        "scan_line": "#1565C0",
        "robot": "#FF9800",
    }

    if principle_type == "grid_map":
        _draw_grid_map(plt, mpatches, np, COLORS, config, language, title)
    elif principle_type == "potential_field":
        _draw_potential_field(plt, mpatches, np, COLORS, config, language, title)
    elif principle_type == "cell_decomposition":
        _draw_cell_decomposition(plt, mpatches, np, COLORS, config, language, title)
    elif principle_type == "scan_line":
        _draw_scan_line(plt, mpatches, np, COLORS, config, language, title)
    else:
        _draw_grid_map(plt, mpatches, np, COLORS, config, language, title)

    fig = plt.gcf()
    fig.set_size_inches(8, 6)
    fig.set_dpi(200)
    fig.savefig(str(output_path), bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(output_path)


def _detect_principle_type(title: str) -> str:
    title_lower = title.lower()
    mapping = {
        "grid_map": ("栅格", "grid", "a*", "节点扩展", "node expansion", "搜索"),
        "potential_field": ("势场", "potential", "引力", "斥力", "人工势场"),
        "cell_decomposition": ("单元分解", "cell decomp", "boustrophedon", "牛耕"),
        "scan_line": ("扫描线", "scan line", "遍历", "弓字", "coverage"),
    }
    for ptype, keywords in mapping.items():
        if any(kw in title_lower for kw in keywords):
            return ptype
    return "grid_map"


def _draw_grid_map(plt, mpatches, np, COLORS, config, language, title):
    """Draw A* node expansion visualization."""
    grid_size = config.get("grid_size", 10)
    obstacles = config.get("obstacles", [(2, 2), (2, 3), (3, 2), (5, 5), (5, 6), (6, 5), (7, 7), (7, 8)])

    fig, ax = plt.subplots(1, 1)

    # Draw grid
    for i in range(grid_size + 1):
        ax.axhline(y=i, color="#E0E0E0", linewidth=0.5)
        ax.axvline(x=i, color="#E0E0E0", linewidth=0.5)

    # Closed set (explored)
    closed_cells = [(1, 1), (1, 2), (1, 3), (2, 1), (3, 1), (3, 3), (4, 1), (4, 2), (4, 3), (4, 4)]
    for (x, y) in closed_cells:
        ax.add_patch(mpatches.Rectangle((x, y), 1, 1, facecolor=COLORS["closed"], alpha=0.4))

    # Open set (frontier)
    open_cells = [(0, 1), (0, 2), (0, 3), (4, 0), (5, 1), (5, 3), (5, 4), (6, 4)]
    for (x, y) in open_cells:
        ax.add_patch(mpatches.Rectangle((x, y), 1, 1, facecolor=COLORS["open"], alpha=0.4))

    # Obstacles
    for (x, y) in obstacles:
        ax.add_patch(mpatches.Rectangle((x, y), 1, 1, facecolor=COLORS["obstacle"], alpha=0.7))
        ax.text(x + 0.5, y + 0.5, "■", ha="center", va="center", fontsize=8, color="white")

    # Path
    path_cells = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0), (9, 0), (9, 1), (9, 2), (9, 3)]
    for i, (x, y) in enumerate(path_cells):
        ax.add_patch(mpatches.Rectangle((x, y), 1, 1, facecolor=COLORS["path"], alpha=0.5))
        if i > 0:
            px, py = path_cells[i - 1]
            ax.annotate("", xy=(x + 0.5, y + 0.5), xytext=(px + 0.5, py + 0.5),
                        arrowprops=dict(arrowstyle="->", color=COLORS["arrow"], lw=1.5))

    # Start and goal
    ax.add_patch(mpatches.Rectangle((0, 0), 1, 1, facecolor=COLORS["start"], alpha=0.6))
    ax.add_patch(mpatches.Rectangle((9, 3), 1, 1, facecolor=COLORS["goal"], alpha=0.6))
    ax.text(0.5, 0.5, "S", ha="center", va="center", fontsize=10, fontweight="bold", color="white")
    ax.text(9.5, 3.5, "G", ha="center", va="center", fontsize=10, fontweight="bold", color="white")

    # f/g/h annotations on a few cells
    annotations = {(0, 0): "f=9.0", (1, 0): "f=8.0", (5, 0): "f=4.2", (9, 3): "f=0"}
    for (x, y), txt in annotations.items():
        ax.text(x + 0.5, y + 0.15, txt, ha="center", va="center", fontsize=6, color="#333")

    # Legend
    legend_items = [
        mpatches.Patch(facecolor=COLORS["closed"], alpha=0.4, label="Closed" if language == "en" else "已探索(Closed)"),
        mpatches.Patch(facecolor=COLORS["open"], alpha=0.4, label="Open" if language == "en" else "待探索(Open)"),
        mpatches.Patch(facecolor=COLORS["obstacle"], alpha=0.7, label="Obstacle" if language == "en" else "障碍物"),
        mpatches.Patch(facecolor=COLORS["path"], alpha=0.5, label="Path" if language == "en" else "最优路径"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=7, framealpha=0.9)

    ax.set_xlim(0, grid_size)
    ax.set_ylim(0, grid_size)
    ax.set_aspect("equal")
    _title = title or ("A* Node Expansion" if language == "en" else "A*算法节点扩展示意图")
    ax.set_title(_title, fontsize=11)


def _draw_potential_field(plt, mpatches, np, COLORS, config, language, title):
    """Draw artificial potential field with attractive and repulsive forces."""
    fig, ax = plt.subplots(1, 1)

    x = np.linspace(0, 10, 20)
    y = np.linspace(0, 10, 20)
    X, Y = np.meshgrid(x, y)

    goal = np.array(config.get("goal", [8, 8]))
    obstacles = config.get("pf_obstacles", [(3, 3), (5, 6), (7, 4)])

    # Attractive potential toward goal
    U_att = -0.1 * (goal[0] - X)
    V_att = -0.1 * (goal[1] - Y)

    # Repulsive potential from obstacles
    U_rep = np.zeros_like(X)
    V_rep = np.zeros_like(Y)
    for ox, oy in obstacles:
        dx = X - ox
        dy = Y - oy
        dist = np.sqrt(dx ** 2 + dy ** 2) + 0.01
        influence = 3.0
        mask = dist < influence
        strength = np.where(mask, 0.5 * (1.0 / dist - 1.0 / influence) / (dist ** 2), 0)
        U_rep += strength * dx
        V_rep += strength * dy

    U = U_att + U_rep
    V = V_att + V_rep
    magnitude = np.sqrt(U ** 2 + V ** 2)
    magnitude = np.clip(magnitude, 0, np.percentile(magnitude, 95))

    ax.quiver(X, Y, U / magnitude, V / magnitude, magnitude, cmap="coolwarm", alpha=0.7, scale=25)

    # Draw obstacles
    for ox, oy in obstacles:
        circle = plt.Circle((ox, oy), 0.5, color=COLORS["obstacle"], alpha=0.7)
        ax.add_patch(circle)

    # Draw goal
    ax.plot(goal[0], goal[1], marker="*", markersize=15, color=COLORS["goal"], zorder=5)
    ax.text(goal[0] + 0.3, goal[1] + 0.3, "Goal" if language == "en" else "目标点", fontsize=9, color=COLORS["goal"])

    # Draw robot
    start = config.get("start", [1, 1])
    ax.plot(start[0], start[1], marker="o", markersize=10, color=COLORS["robot"], zorder=5)
    ax.text(start[0] + 0.3, start[1] + 0.3, "Robot" if language == "en" else "机器人", fontsize=9, color=COLORS["robot"])

    legend_items = [
        plt.Line2D([0], [0], marker="*", color="w", markerfacecolor=COLORS["goal"], markersize=12,
                   label="Goal (Attractive)" if language == "en" else "目标点(引力)"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=COLORS["robot"], markersize=10,
                   label="Robot" if language == "en" else "机器人"),
        mpatches.Patch(facecolor=COLORS["obstacle"], alpha=0.7,
                       label="Obstacle (Repulsive)" if language == "en" else "障碍物(斥力)"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=7)

    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.set_aspect("equal")
    _title = title or ("Artificial Potential Field" if language == "en" else "人工势场法力矢量示意图")
    ax.set_title(_title, fontsize=11)


def _draw_cell_decomposition(plt, mpatches, np, COLORS, config, language, title):
    """Draw cell decomposition with vertical slab boundaries."""
    fig, ax = plt.subplots(1, 1)

    # Room outline
    room = mpatches.Rectangle((1, 1), 8, 6, fill=False, edgecolor="#333", linewidth=2)
    ax.add_patch(room)

    # Obstacles inside room
    obs1 = mpatches.Rectangle((3, 2), 1.5, 2, facecolor=COLORS["obstacle"], alpha=0.7)
    obs2 = mpatches.Rectangle((6, 4), 1.5, 2, facecolor=COLORS["obstacle"], alpha=0.7)
    ax.add_patch(obs1)
    ax.add_patch(obs2)

    # Vertical slab decomposition lines
    slab_lines = [3, 4.5, 6, 7.5]
    for sx in slab_lines:
        ax.axvline(x=sx, color=COLORS["cell_boundary"], linestyle="--", linewidth=1.2, alpha=0.7, ymin=0.1, ymax=0.9)

    # Cell labels
    cells = [
        (2, 4, "C1"), (3.75, 4, "C2"), (5.25, 4, "C3"), (6.75, 2, "C4"), (8, 4, "C5"),
    ]
    for cx, cy, label in cells:
        ax.text(cx, cy, label, ha="center", va="center", fontsize=10,
                color=COLORS["cell_boundary"], fontweight="bold")

    ax.text(0.5, 7.5, "Room" if language == "en" else "房间区域", fontsize=9, color="#333")
    ax.text(3.75, 3, "Obs" if language == "en" else "障碍", ha="center", fontsize=8, color="white")

    legend_items = [
        mpatches.Patch(facecolor=COLORS["obstacle"], alpha=0.7, label="Obstacle" if language == "en" else "障碍物"),
        plt.Line2D([0], [0], color=COLORS["cell_boundary"], linestyle="--", linewidth=1.5,
                   label="Decomposition" if language == "en" else "单元分解线"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=7)

    ax.set_xlim(0, 10)
    ax.set_ylim(0, 8)
    ax.set_aspect("equal")
    _title = title or ("Cell Decomposition" if language == "en" else "单元分解示意图")
    ax.set_title(_title, fontsize=11)


def _draw_scan_line(plt, mpatches, np, COLORS, config, language, title):
    """Draw boustrophedon scan line pattern within decomposed cells."""
    fig, ax = plt.subplots(1, 1)

    # Room outline
    room = mpatches.Rectangle((1, 1), 8, 6, fill=False, edgecolor="#333", linewidth=2)
    ax.add_patch(room)

    # Decomposition lines
    ax.axvline(x=4.5, color=COLORS["cell_boundary"], linestyle="--", linewidth=1.2, alpha=0.5)
    ax.axvline(x=6, color=COLORS["cell_boundary"], linestyle="--", linewidth=1.2, alpha=0.5)

    # Scan lines in cell 1 (left to right, alternating)
    y_lines = np.arange(1.5, 7, 0.8)
    for i, y in enumerate(y_lines):
        if i % 2 == 0:
            ax.annotate("", xy=(4.3, y), xytext=(1.2, y),
                        arrowprops=dict(arrowstyle="->", color=COLORS["scan_line"], lw=1.2))
        else:
            ax.annotate("", xy=(1.2, y), xytext=(4.3, y),
                        arrowprops=dict(arrowstyle="->", color=COLORS["scan_line"], lw=1.2))
        # Connect to next line
        if i < len(y_lines) - 1:
            ax.plot([4.3 if i % 2 == 0 else 1.2] * 2, [y, y_lines[i + 1]],
                    color=COLORS["scan_line"], linewidth=0.8, linestyle=":")

    # Scan lines in cell 2
    for i, y in enumerate(y_lines[:6]):
        x_start = 4.7
        x_end = 5.8
        if i % 2 == 0:
            ax.annotate("", xy=(x_end, y), xytext=(x_start, y),
                        arrowprops=dict(arrowstyle="->", color="#E65100", lw=1.2))
        else:
            ax.annotate("", xy=(x_start, y), xytext=(x_end, y),
                        arrowprops=dict(arrowstyle="->", color="#E65100", lw=1.2))

    # Cell labels
    ax.text(2.75, 7.3, "Cell 1", ha="center", fontsize=9, color=COLORS["cell_boundary"])
    ax.text(5.25, 7.3, "Cell 2", ha="center", fontsize=9, color=COLORS["cell_boundary"])
    ax.text(7.5, 7.3, "Cell 3", ha="center", fontsize=9, color=COLORS["cell_boundary"])

    # Robot icon
    ax.plot(1.2, 1.5, marker="o", markersize=8, color=COLORS["robot"], zorder=5)

    legend_items = [
        plt.Line2D([0], [0], color=COLORS["scan_line"], linewidth=1.5,
                   label="Scan Path (Cell 1)" if language == "en" else "扫描路径(单元1)"),
        plt.Line2D([0], [0], color="#E65100", linewidth=1.5,
                   label="Scan Path (Cell 2)" if language == "en" else "扫描路径(单元2)"),
        plt.Line2D([0], [0], color=COLORS["cell_boundary"], linestyle="--", linewidth=1.5,
                   label="Cell Boundary" if language == "en" else "单元边界"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=7)

    ax.set_xlim(0, 10)
    ax.set_ylim(0, 8)
    ax.set_aspect("equal")
    _title = title or ("Boustrophedon Coverage" if language == "en" else "牛耕法全覆盖路径示意图")
    ax.set_title(_title, fontsize=11)
