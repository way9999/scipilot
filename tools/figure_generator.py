"""Scientific figure generation module.

Generates publication-ready figures from experimental data.
All outputs: PDF (vector) + PNG (preview), colorblind-friendly palette.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# ---------------------------------------------------------------------------
# Style defaults
# ---------------------------------------------------------------------------

STYLE_DEFAULTS = {
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.5,
}

CB_PALETTE = [
    "#006BA4", "#FF800E", "#ABABAB", "#595959",
    "#5F9ED1", "#C85200", "#898989", "#A2C8EC",
    "#FFBC79", "#CFCFCF",
]


def _ensure_mpl():
    if not HAS_MPL:
        raise ImportError("matplotlib and seaborn are required: pip install matplotlib seaborn")


def _ensure_pandas():
    if not HAS_PANDAS:
        raise ImportError("pandas is required: pip install pandas")


def _apply_style(language: str = "zh"):
    plt.rcParams.update(STYLE_DEFAULTS)
    plt.rcParams["font.family"] = "sans-serif"
    import platform
    import matplotlib.font_manager as fm
    # Rebuild font cache to discover system fonts
    try:
        fm.fontManager.addfont("")
    except Exception:
        pass
    available = {f.name for f in fm.fontManager.ttflist}
    system = platform.system()
    if language == "zh":
        if system == "Windows":
            candidates = ["Microsoft YaHei", "SimHei", "KaiTi", "FangSong"]
            chosen = [c for c in candidates if c in available]
            if not chosen:
                chosen = [f.name for f in fm.fontManager.ttflist if any(k in f.name for k in ("YaHei", "Hei", "CJK", "Song", "Kai"))][:1]
            plt.rcParams["font.sans-serif"] = chosen + ["DejaVu Sans"]
        elif system == "Darwin":
            plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "DejaVu Sans"]
        else:
            plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "Noto Sans CJK SC", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
    else:
        plt.rcParams["font.sans-serif"] = ["Times New Roman", "Arial", "DejaVu Sans"]
    sns.set_palette(CB_PALETTE)


def _save_fig(fig: Any, output_path: str | Path, caption: str, label: str, fig_type: str) -> dict[str, Any]:
    """Save figure as PDF + PNG and return metadata dict."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    pdf_path = p.with_suffix(".pdf")
    png_path = p.with_suffix(".png")
    fig.savefig(str(pdf_path), format="pdf")
    fig.savefig(str(png_path), format="png")
    plt.close(fig)
    return {
        "path_pdf": str(pdf_path),
        "path_png": str(png_path),
        "caption": caption,
        "label": label,
        "type": fig_type,
    }


# ---------------------------------------------------------------------------
# Core plot functions
# ---------------------------------------------------------------------------

def plot_comparison_bar(
    data: dict[str, dict[str, float]],
    output_path: str | Path,
    title: str = "",
    ylabel: str = "Score",
    language: str = "zh",
    **style: Any,
) -> dict[str, Any]:
    """Bar chart comparing methods across metrics.

    Args:
        data: {method_name: {metric_name: value, ...}, ...}
        language: 'zh' for Chinese fonts, 'en' for English fonts.
    """
    _ensure_mpl()
    _apply_style(language)

    methods = list(data.keys())
    metrics = list(next(iter(data.values())).keys())
    n_methods = len(methods)
    n_metrics = len(metrics)
    x = np.arange(n_metrics)
    width = 0.8 / n_methods

    fig, ax = plt.subplots(figsize=style.get("figsize", (max(8, n_metrics * 2.0), 5)), constrained_layout=True)
    for i, method in enumerate(methods):
        values = [data[method].get(m, 0) for m in metrics]
        ax.bar(x + i * width - 0.4 + width / 2, values, width, label=method, color=CB_PALETTE[i % len(CB_PALETTE)], edgecolor="white", linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics, rotation=25, ha="right")
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=12)
    ax.legend(frameon=False, loc="best")
    fig.tight_layout(pad=1.5)

    best_method = max(methods, key=lambda m: sum(data[m].values()))
    caption = title or f"Comparison of {n_methods} methods across {n_metrics} metrics. {best_method} achieves the best overall performance."
    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    return _save_fig(fig, output_path, caption, f"fig:{label}", "bar")


def plot_ablation_heatmap(
    data: dict[str, dict[str, float]],
    output_path: str | Path,
    title: str = "Ablation Study",
    **style: Any,
) -> dict[str, Any]:
    """Heatmap for ablation experiments.

    Args:
        data: {variant_name: {metric_name: value, ...}, ...}
    """
    _ensure_mpl()
    _ensure_pandas()
    _apply_style()

    df = pd.DataFrame(data).T
    fig, ax = plt.subplots(figsize=style.get("figsize", (max(6, len(df.columns) * 1.4), max(4, len(df) * 0.7))))
    sns.heatmap(df, annot=True, fmt=".3f", cmap="YlOrRd", ax=ax, linewidths=0.5)
    ax.set_title(title, pad=10)
    fig.tight_layout()

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = f"{title}. Each row represents a model variant; columns show evaluation metrics."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "heatmap")


def plot_training_curve(
    log_data: dict[str, list[float]] | str | Path,
    output_path: str | Path,
    title: str = "Training Curve",
    **style: Any,
) -> dict[str, Any]:
    """Line plot for training curves (loss, accuracy, etc.).

    Args:
        log_data: {metric_name: [values_per_epoch]} or path to JSON/CSV log file.
    """
    _ensure_mpl()
    _apply_style()

    if isinstance(log_data, (str, Path)):
        p = Path(log_data)
        if p.suffix == ".json":
            log_data = json.loads(p.read_text(encoding="utf-8"))
        elif p.suffix == ".csv":
            _ensure_pandas()
            df = pd.read_csv(p)
            log_data = {col: df[col].tolist() for col in df.columns if col.lower() not in ("epoch", "step", "iteration")}
        else:
            raise ValueError(f"Unsupported log format: {p.suffix}")

    fig, ax = plt.subplots(figsize=style.get("figsize", (8, 4.5)), constrained_layout=True)
    for i, (name, values) in enumerate(log_data.items()):
        epochs = list(range(1, len(values) + 1))
        ax.plot(epochs, values, label=name, color=CB_PALETTE[i % len(CB_PALETTE)], linewidth=1.5, marker="o", markersize=3)

    ax.set_xlabel("Epoch")
    ax.set_ylabel("Value")
    ax.set_title(title, pad=10)
    ax.legend(frameon=False, loc="best")
    fig.tight_layout()

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = f"{title} over {len(next(iter(log_data.values())))} epochs."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "line")


def plot_confusion_matrix(
    y_true: list,
    y_pred: list,
    labels: list[str],
    output_path: str | Path,
    title: str = "Confusion Matrix",
    **style: Any,
) -> dict[str, Any]:
    """Confusion matrix heatmap."""
    _ensure_mpl()
    _ensure_pandas()
    _apply_style()

    n = len(labels)
    matrix = np.zeros((n, n), dtype=int)
    label_to_idx = {l: i for i, l in enumerate(labels)}
    for t, p in zip(y_true, y_pred):
        ti, pi = label_to_idx.get(t), label_to_idx.get(p)
        if ti is not None and pi is not None:
            matrix[ti][pi] += 1

    fig, ax = plt.subplots(figsize=style.get("figsize", (max(4, n * 0.8), max(4, n * 0.8))))
    sns.heatmap(matrix, annot=True, fmt="d", cmap="Blues", xticklabels=labels, yticklabels=labels, ax=ax)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(title)
    fig.tight_layout()

    total = matrix.sum()
    correct = np.trace(matrix)
    acc = correct / total if total > 0 else 0
    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = f"{title}. Overall accuracy: {acc:.1%} ({correct}/{total})."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "confusion_matrix")


def plot_distribution(
    data: dict[str, list[float]],
    output_path: str | Path,
    kind: str = "violin",
    title: str = "",
    ylabel: str = "Value",
    **style: Any,
) -> dict[str, Any]:
    """Distribution plot (violin, box, or strip).

    Args:
        data: {group_name: [values], ...}
        kind: "violin", "box", or "strip"
    """
    _ensure_mpl()
    _ensure_pandas()
    _apply_style()

    records = []
    for group, values in data.items():
        for v in values:
            records.append({"Group": group, "Value": v})
    df = pd.DataFrame(records)

    fig, ax = plt.subplots(figsize=style.get("figsize", (max(6, len(data) * 1.4), 5)), constrained_layout=True)
    if kind == "violin":
        sns.violinplot(data=df, x="Group", y="Value", ax=ax, palette=CB_PALETTE, inner="box")
    elif kind == "box":
        sns.boxplot(data=df, x="Group", y="Value", ax=ax, palette=CB_PALETTE)
    else:
        sns.stripplot(data=df, x="Group", y="Value", ax=ax, palette=CB_PALETTE, jitter=True, alpha=0.6)

    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=10)
    fig.tight_layout()

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Distribution comparison across {len(data)} groups ({kind} plot)."
    return _save_fig(fig, output_path, caption, f"fig:{label}", kind)


def plot_scatter_with_regression(
    x: list[float],
    y: list[float],
    output_path: str | Path,
    xlabel: str = "X",
    ylabel: str = "Y",
    title: str = "",
    **style: Any,
) -> dict[str, Any]:
    """Scatter plot with linear regression line."""
    _ensure_mpl()
    _apply_style()

    fig, ax = plt.subplots(figsize=style.get("figsize", (7, 5)), constrained_layout=True)
    ax.scatter(x, y, alpha=0.6, color=CB_PALETTE[0], s=25, edgecolor="white", linewidth=0.3)

    if len(x) >= 2:
        coeffs = np.polyfit(x, y, 1)
        xs = np.linspace(min(x), max(x), 100)
        ax.plot(xs, np.polyval(coeffs, xs), color=CB_PALETTE[1], linewidth=1.5, linestyle="--")
        corr = np.corrcoef(x, y)[0, 1]
        ax.annotate(f"r = {corr:.3f}", xy=(0.05, 0.95), xycoords="axes fraction", fontsize=9, va="top")

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=10)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Scatter plot of {xlabel} vs {ylabel} with linear regression."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "scatter")


def plot_radar(
    data: dict[str, list[float]],
    categories: list[str],
    output_path: str | Path,
    title: str = "",
    **style: Any,
) -> dict[str, Any]:
    """Radar (spider) chart for multi-dimensional comparison.

    Args:
        data: {method_name: [value_per_category], ...}
        categories: list of dimension names
    """
    _ensure_mpl()
    _apply_style()

    n = len(categories)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=style.get("figsize", (7, 7)), subplot_kw={"polar": True}, constrained_layout=True)
    for i, (method, values) in enumerate(data.items()):
        vals = values + values[:1]
        ax.plot(angles, vals, linewidth=1.5, label=method, color=CB_PALETTE[i % len(CB_PALETTE)])
        ax.fill(angles, vals, alpha=0.1, color=CB_PALETTE[i % len(CB_PALETTE)])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, size=9)
    if title:
        ax.set_title(title, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1), frameon=False, fontsize=8)

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Radar chart comparing {len(data)} methods across {n} dimensions."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "radar")


def plot_grouped_bar_with_error(
    data: dict[str, dict[str, tuple[float, float]]],
    output_path: str | Path,
    title: str = "",
    ylabel: str = "Score",
    **style: Any,
) -> dict[str, Any]:
    """Grouped bar chart with error bars.

    Args:
        data: {method_name: {metric_name: (mean, std), ...}, ...}
    """
    _ensure_mpl()
    _apply_style()

    methods = list(data.keys())
    metrics = list(next(iter(data.values())).keys())
    n_methods = len(methods)
    n_metrics = len(metrics)
    x = np.arange(n_metrics)
    width = 0.8 / n_methods

    fig, ax = plt.subplots(figsize=style.get("figsize", (max(8, n_metrics * 2.0), 5)), constrained_layout=True)
    for i, method in enumerate(methods):
        means = [data[method].get(m, (0, 0))[0] for m in metrics]
        stds = [data[method].get(m, (0, 0))[1] for m in metrics]
        ax.bar(x + i * width - 0.4 + width / 2, means, width, yerr=stds,
               label=method, color=CB_PALETTE[i % len(CB_PALETTE)],
               capsize=3, error_kw={"linewidth": 0.8}, edgecolor="white", linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics, rotation=25, ha="right")
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=10)
    ax.legend(frameon=False, loc="best")
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Grouped comparison of {n_methods} methods (mean ± std)."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "bar_error")


def plot_multi_line(
    data: dict[str, dict[str, list[float]]],
    output_path: str | Path,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "Value",
    **style: Any,
) -> dict[str, Any]:
    """Multi-line chart comparing multiple methods across a shared x-axis.

    Args:
        data: {method_name: {metric_name: [values...], ...}, ...}
              Each method has the same x-axis (index-based) and one or more metric series.
    """
    _ensure_mpl()
    _apply_style()

    n_metrics = len(next(iter(data.values())))
    fig, axes = plt.subplots(1, n_metrics, figsize=style.get("figsize", (max(8, n_metrics * 4), 4.5)),
                             sharey=True, squeeze=False, constrained_layout=True)
    axes = axes.flatten()

    methods = list(data.keys())
    metric_names = list(next(iter(data.values())).keys())

    for ax_idx, metric in enumerate(metric_names):
        ax = axes[ax_idx]
        for m_idx, method in enumerate(methods):
            values = data[method].get(metric, [])
            ax.plot(range(len(values)), values, marker="o", markersize=3,
                    label=method, color=CB_PALETTE[m_idx % len(CB_PALETTE)], linewidth=1.2)
        ax.set_xlabel(metric if not xlabel else xlabel)
        if ax_idx == 0:
            ax.set_ylabel(ylabel)
        ax.legend(frameon=False, fontsize=7)

    if title:
        fig.suptitle(title)

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Multi-line comparison of {len(methods)} methods."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "line")


def plot_stacked_bar(
    data: dict[str, dict[str, float]],
    output_path: str | Path,
    title: str = "",
    ylabel: str = "Score",
    stack_metric: str | None = None,
    **style: Any,
) -> dict[str, Any]:
    """Stacked bar chart for composition analysis.

    Args:
        data: {group_name: {component_name: value, ...}, ...}
        stack_metric: if provided, only show this metric stacked across groups
    """
    _ensure_mpl()
    _apply_style()

    groups = list(data.keys())
    if stack_metric:
        components = [stack_metric]
    else:
        components = list(next(iter(data.values())).keys())

    fig, ax = plt.subplots(figsize=style.get("figsize", (max(6, len(groups) * 1.5), 5)), constrained_layout=True)
    x = np.arange(len(groups))
    bottom = np.zeros(len(groups))

    for c_idx, comp in enumerate(components):
        values = [data[g].get(comp, 0) for g in groups]
        ax.bar(x, values, bottom=bottom, label=comp, color=CB_PALETTE[c_idx % len(CB_PALETTE)], edgecolor="white", linewidth=0.5)
        bottom += np.array(values)

    ax.set_xticks(x)
    ax.set_xticklabels(groups, rotation=25, ha="right")
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=10)
    ax.legend(frameon=False, loc="best")

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Stacked bar chart of {len(groups)} groups."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "stacked_bar")


def plot_box_comparison(
    data: dict[str, list[float]],
    output_path: str | Path,
    title: str = "",
    ylabel: str = "Value",
    **style: Any,
) -> dict[str, Any]:
    """Box plot for comparing distributions across groups.

    Args:
        data: {group_name: [values...], ...}
    """
    _ensure_mpl()
    _apply_style()

    groups = list(data.keys())
    values = [data[g] for g in groups]

    fig, ax = plt.subplots(figsize=style.get("figsize", (max(6, len(groups) * 1.4), 5)), constrained_layout=True)
    bp = ax.boxplot(values, labels=groups, patch_artist=True, widths=0.5)
    for patch, color in zip(bp["boxes"], [CB_PALETTE[i % len(CB_PALETTE)] for i in range(len(groups))]):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
        patch.set_edgecolor("gray")

    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=10)

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = title or f"Box plot comparing {len(groups)} groups."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "box")


# ---------------------------------------------------------------------------
# Auto-generation from result files
# ---------------------------------------------------------------------------


def _auto_from_csv(path: Path, output_dir: Path) -> list[dict[str, Any]]:
    _ensure_pandas()
    figs = []
    try:
        df = pd.read_csv(path)
    except Exception:
        return figs

    cols_lower = {c.lower(): c for c in df.columns}
    stem = re.sub(r"[^0-9A-Za-z]+", "-", path.stem).strip("-")

    # Training curve: has epoch/step column
    time_col = None
    for candidate in ("epoch", "step", "iteration"):
        if candidate in cols_lower:
            time_col = cols_lower[candidate]
            break

    if time_col is not None:
        metric_cols = [c for c in df.columns if c != time_col and df[c].dtype in ("float64", "int64", "float32")]
        if metric_cols:
            log_data = {c: df[c].tolist() for c in metric_cols}
            fig_meta = plot_training_curve(log_data, output_dir / f"{stem}-training-curve", title=f"Training: {path.stem}")
            figs.append(fig_meta)
        return figs

    # Comparison: has method/model column
    group_col = None
    for candidate in ("method", "model", "approach", "variant", "name"):
        if candidate in cols_lower:
            group_col = cols_lower[candidate]
            break

    if group_col is not None:
        numeric_cols = [c for c in df.columns if c != group_col and df[c].dtype in ("float64", "int64", "float32")]
        if numeric_cols:
            data = {}
            for _, row in df.iterrows():
                method = str(row[group_col])
                data[method] = {c: float(row[c]) for c in numeric_cols}
            fig_meta = plot_comparison_bar(data, output_dir / f"{stem}-comparison", title=f"Comparison: {path.stem}")
            figs.append(fig_meta)
        return figs

    # Fallback: all-numeric → distribution
    numeric_cols = [c for c in df.columns if df[c].dtype in ("float64", "int64", "float32")]
    if len(numeric_cols) >= 2:
        dist_data = {c: df[c].dropna().tolist() for c in numeric_cols[:6]}
        fig_meta = plot_distribution(dist_data, output_dir / f"{stem}-distribution", title=f"Distribution: {path.stem}")
        figs.append(fig_meta)

    return figs


def _auto_from_json(path: Path, output_dir: Path) -> list[dict[str, Any]]:
    figs = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return figs

    stem = re.sub(r"[^0-9A-Za-z]+", "-", path.stem).strip("-")

    if not isinstance(raw, dict):
        return figs

    # Check if values are lists of numbers → training curve
    all_lists = all(isinstance(v, list) and len(v) > 1 and all(isinstance(x, (int, float)) for x in v) for v in raw.values())
    if all_lists and raw:
        fig_meta = plot_training_curve(raw, output_dir / f"{stem}-curve", title=f"Curve: {path.stem}")
        figs.append(fig_meta)
        return figs

    # Check if values are dicts of numbers → comparison bar or heatmap
    all_dicts = all(isinstance(v, dict) for v in raw.values())
    if all_dicts and raw:
        inner_all_numeric = all(
            all(isinstance(x, (int, float)) for x in v.values())
            for v in raw.values()
        )
        if inner_all_numeric:
            n_items = len(raw)
            if n_items <= 8:
                fig_meta = plot_comparison_bar(raw, output_dir / f"{stem}-comparison", title=f"Comparison: {path.stem}")
            else:
                fig_meta = plot_ablation_heatmap(raw, output_dir / f"{stem}-heatmap", title=f"Heatmap: {path.stem}")
            figs.append(fig_meta)

    return figs


# ---------------------------------------------------------------------------
# Figure inventory
# ---------------------------------------------------------------------------

def generate_figure_inventory(figures: list[dict[str, Any]], output_path: str | Path) -> Path:
    """Write a Markdown figure inventory file."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Figure Inventory\n"]
    lines.append(f"Total figures: {len(figures)}\n")
    lines.append("| # | Label | Type | Caption | PDF Path |")
    lines.append("|---|-------|------|---------|----------|")
    for i, fig in enumerate(figures, 1):
        label = fig.get("label", "")
        ftype = fig.get("type", "")
        caption = fig.get("caption", "").replace("|", "\\|")[:80]
        pdf = fig.get("path_pdf", "")
        lines.append(f"| {i} | `{label}` | {ftype} | {caption} | `{pdf}` |")

    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


_FIGURE_TEXT_MAP_ZH = {
    "map_accuracy": "建图精度",
    "loop_closure_rate": "回环成功率",
    "avg_processing_time_ms": "平均处理时延/ms",
    "memory_usage_mb": "内存占用/MB",
    "path_length_m": "路径长度/m",
    "planning_time_ms": "规划时间/ms",
    "path_smoothness": "路径平滑度",
    "success_rate": "成功率",
    "obstacle_clearance_m": "最小障碍间距/m",
    "goal_reach_rate": "目标到达率",
    "avg_velocity_ms": "平均速度/(m/s)",
    "collision_rate": "碰撞率",
    "trajectory_smoothness": "轨迹平滑度",
    "dynamic_obstacle_avoidance": "动态障碍规避能力",
    "avg_time_s": "平均完成时间/s",
    "avg_path_deviation_m": "平均路径偏差/m",
    "map_error": "地图误差",
    "loop_closure_residual": "回环残差",
    "epoch": "迭代轮次",
    "step": "步数",
    "iteration": "迭代次数",
}

_FIGURE_VALUE_MAP_ZH = {
    "simple room": "简单房间",
    "l-shaped corridor": "L形走廊",
    "cluttered room": "复杂房间",
    "dynamic obstacles": "动态障碍场景",
    "narrow passage": "狭窄通道",
}

_FIGURE_TITLE_HINTS_ZH = {
    "slam_comparison": "不同SLAM方法性能对比",
    "planner_comparison": "不同全局规划器性能对比",
    "mppi_controller_results": "不同局部控制器性能对比",
    "navigation_scenarios": "典型导航场景结果对比",
    "slam_convergence": "SLAM收敛过程曲线",
}


def _fg_localize_label(text: str, language: str) -> str:
    normalized = str(text).strip()
    if language != "zh":
        return normalized
    mapped = _FIGURE_TEXT_MAP_ZH.get(normalized)
    if mapped:
        return mapped
    lowered = normalized.lower()
    if lowered in _FIGURE_VALUE_MAP_ZH:
        return _FIGURE_VALUE_MAP_ZH[lowered]
    return normalized.replace("_", " ")


def _fg_title_for_stem(stem: str, language: str) -> str:
    if language == "zh":
        return _FIGURE_TITLE_HINTS_ZH.get(stem, stem.replace("_", " "))
    return stem.replace("_", " ").title()


def _fg_caption_for_title(title: str, language: str) -> str:
    if language == "zh":
        return f"{title}。"
    return f"{title}."


def _fg_attach_language(meta: dict[str, Any], title: str, language: str) -> dict[str, Any]:
    meta["language"] = language
    meta["caption"] = _fg_caption_for_title(title, language)
    return meta


def plot_training_curve(
    log_data: dict[str, list[float]] | str | Path,
    output_path: str | Path,
    title: str = "Training Curve",
    xlabel: str = "Epoch",
    ylabel: str = "Value",
    **style: Any,
) -> dict[str, Any]:
    _ensure_mpl()
    _apply_style()

    if isinstance(log_data, (str, Path)):
        p = Path(log_data)
        if p.suffix == ".json":
            log_data = json.loads(p.read_text(encoding="utf-8"))
        elif p.suffix == ".csv":
            _ensure_pandas()
            df = pd.read_csv(p)
            log_data = {col: df[col].tolist() for col in df.columns if col.lower() not in ("epoch", "step", "iteration")}
        else:
            raise ValueError(f"Unsupported log format: {p.suffix}")

    fig, ax = plt.subplots(figsize=style.get("figsize", (7, 4)))
    for i, (name, values) in enumerate(log_data.items()):
        epochs = list(range(1, len(values) + 1))
        ax.plot(epochs, values, label=name, color=CB_PALETTE[i % len(CB_PALETTE)], linewidth=1.5)

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    label = Path(output_path).stem.replace("-", "_").replace(" ", "_")
    caption = f"{title} over {len(next(iter(log_data.values())))} epochs."
    return _save_fig(fig, output_path, caption, f"fig:{label}", "line")


def auto_figures_from_results(
    results_dir: str | Path,
    output_dir: str | Path,
    language: str = "en",
) -> list[dict[str, Any]]:
    _ensure_pandas()
    results_dir = Path(results_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, Any]] = []

    for f in sorted(results_dir.iterdir()):
        if f.suffix == ".csv":
            figures.extend(_auto_from_csv(f, output_dir, language))
        elif f.suffix == ".json":
            figures.extend(_auto_from_json(f, output_dir, language))

    return figures


def _auto_from_csv(path: Path, output_dir: Path, language: str = "en") -> list[dict[str, Any]]:
    _ensure_pandas()
    figs: list[dict[str, Any]] = []
    try:
        df = pd.read_csv(path)
    except Exception:
        return figs

    cols_lower = {c.lower(): c for c in df.columns}
    stem = re.sub(r"[^0-9A-Za-z_]+", "-", path.stem).strip("-")
    title = _fg_title_for_stem(path.stem, language)

    time_col = None
    for candidate in ("epoch", "step", "iteration"):
        if candidate in cols_lower:
            time_col = cols_lower[candidate]
            break

    if time_col is not None:
        metric_cols = [c for c in df.columns if c != time_col and df[c].dtype in ("float64", "int64", "float32")]
        if metric_cols:
            log_data = {_fg_localize_label(c, language): df[c].tolist() for c in metric_cols}
            fig_meta = plot_training_curve(
                log_data,
                output_dir / f"{stem}-training-curve",
                title=title,
                xlabel=_fg_localize_label(time_col, language),
                ylabel="指标值" if language == "zh" else "Value",
            )
            figs.append(_fg_attach_language(fig_meta, title, language))
        return figs

    group_col = None
    for candidate in ("method", "model", "approach", "variant", "name"):
        if candidate in cols_lower:
            group_col = cols_lower[candidate]
            break

    if group_col is not None:
        numeric_cols = [c for c in df.columns if c != group_col and df[c].dtype in ("float64", "int64", "float32")]
        if numeric_cols:
            data = {}
            for _, row in df.iterrows():
                method = _fg_localize_label(str(row[group_col]), language)
                data[method] = {_fg_localize_label(c, language): float(row[c]) for c in numeric_cols}
            fig_meta = plot_comparison_bar(
                data,
                output_dir / f"{stem}-comparison",
                title=title,
                ylabel="指标值" if language == "zh" else "Score",
            )
            figs.append(_fg_attach_language(fig_meta, title, language))
            # Generate radar chart for all comparison CSVs with >=3 metrics and >=2 methods
            categories = list(next(iter(data.values())).keys())
            if len(categories) >= 3 and len(data) >= 2:
                radar_title = f"{title}雷达图" if language == "zh" else f"{title} Radar"
                radar_meta = plot_radar(
                    {name: [metrics[key] for key in metrics.keys()] for name, metrics in data.items()},
                    categories,
                    output_dir / f"{stem}-radar",
                    title=radar_title,
                )
                figs.append(_fg_attach_language(radar_meta, radar_meta["caption"].rstrip(".。"), language))
            # Generate heatmap for all comparison CSVs with >=2 methods and >=2 metrics
            if len(data) >= 2 and len(categories) >= 2:
                heatmap_title = f"{title}热力图" if language == "zh" else f"{title} Heatmap"
                heatmap_meta = plot_ablation_heatmap(
                    {name: dict(metrics) for name, metrics in data.items()},
                    output_dir / f"{stem}-heatmap",
                    title=heatmap_title,
                )
                figs.append(_fg_attach_language(heatmap_meta, heatmap_meta["caption"].rstrip(".。"), language))
            # Generate stacked bar for composition analysis (if >=3 methods and >=2 metrics)
            if len(data) >= 3 and len(categories) >= 2:
                stacked_title = f"{title}组成分析" if language == "zh" else f"{title} Composition"
                stacked_meta = plot_stacked_bar(
                    data,
                    output_dir / f"{stem}-stacked",
                    title=stacked_title,
                    ylabel="指标值" if language == "zh" else "Score",
                )
                figs.append(_fg_attach_language(stacked_meta, stacked_meta["caption"].rstrip(".。"), language))
        return figs

    # Check for error bar data: columns with "_std", "_std_dev", or paired mean/std columns
    std_cols = [c for c in df.columns if any(c.lower().endswith(s) for s in ("_std", "_std_dev", "_sd"))]
    if std_cols and numeric_cols:
        for std_c in std_cols:
            base_name = std_c.rsplit("_", 1)[0] if "_" in std_c else std_c.replace("_std", "").replace("_std_dev", "").replace("_sd", "")
            # Find matching mean column
            mean_candidates = [c for c in df.columns if c.lower() == base_name.lower() or c.lower() == base_name.lower() + "_mean"]
            if mean_candidates:
                mean_c = mean_candidates[0]
                error_data = {}
                for _, row in df.iterrows():
                    group_label = str(row.iloc[0]) if df.columns[0] != mean_c else f"Row{row.name}"
                    error_data[group_label] = {_fg_localize_label(mean_c, language): (float(row[mean_c]), float(row[std_c]))}
                if len(error_data) >= 2:
                    err_title = f"{title}误差分析" if language == "zh" else f"{title} Error"
                    err_meta = plot_grouped_bar_with_error(
                        error_data,
                        output_dir / f"{stem}-error",
                        title=err_title,
                        ylabel="指标值" if language == "zh" else "Score",
                    )
                    figs.append(_fg_attach_language(err_meta, err_meta["caption"].rstrip(".。"), language))
        return figs

    numeric_cols = [c for c in df.columns if df[c].dtype in ("float64", "int64", "float32")]
    if len(numeric_cols) >= 2:
        dist_data = {_fg_localize_label(c, language): df[c].dropna().tolist() for c in numeric_cols[:6]}
        fig_meta = plot_distribution(
            dist_data,
            output_dir / f"{stem}-distribution",
            title=title,
            ylabel="指标值" if language == "zh" else "Value",
        )
        figs.append(_fg_attach_language(fig_meta, title, language))

    return figs


def _auto_from_json(path: Path, output_dir: Path, language: str = "en") -> list[dict[str, Any]]:
    figs: list[dict[str, Any]] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return figs

    stem = re.sub(r"[^0-9A-Za-z_]+", "-", path.stem).strip("-")
    title = _fg_title_for_stem(path.stem, language)

    if not isinstance(raw, dict):
        return figs

    all_lists = all(isinstance(v, list) and len(v) > 1 and all(isinstance(x, (int, float)) for x in v) for v in raw.values())
    if all_lists and raw:
        localized = {_fg_localize_label(k, language): v for k, v in raw.items()}
        fig_meta = plot_training_curve(
            localized,
            output_dir / f"{stem}-curve",
            title=title,
            xlabel="迭代轮次" if language == "zh" else "Epoch",
            ylabel="指标值" if language == "zh" else "Value",
        )
        figs.append(_fg_attach_language(fig_meta, title, language))
        return figs

    all_dicts = all(isinstance(v, dict) for v in raw.values())
    if all_dicts and raw:
        inner_all_numeric = all(all(isinstance(x, (int, float)) for x in v.values()) for v in raw.values())
        if inner_all_numeric:
            localized = {
                _fg_localize_label(name, language): {_fg_localize_label(k, language): float(v) for k, v in values.items()}
                for name, values in raw.items()
            }
            fig_meta = plot_comparison_bar(
                localized,
                output_dir / f"{stem}-comparison",
                title=title,
                ylabel="指标值" if language == "zh" else "Score",
            )
            figs.append(_fg_attach_language(fig_meta, title, language))

    return figs


def generate_figure_inventory(figures: list[dict[str, Any]], output_path: str | Path, language: str = "en") -> Path:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if language == "zh":
        lines = ["# 图表清单\n", f"图表总数：{len(figures)}\n", "| 序号 | 标识 | 类型 | 图题 | PDF 路径 |", "|---|---|---|---|---|"]
    else:
        lines = ["# Figure Inventory\n", f"Total figures: {len(figures)}\n", "| # | Label | Type | Caption | PDF Path |", "|---|-------|------|---------|----------|"]

    for i, fig in enumerate(figures, 1):
        label = fig.get("label", "")
        ftype = fig.get("type", "")
        caption = fig.get("caption", "").replace("|", "\\|")[:80]
        pdf = fig.get("path_pdf", "")
        lines.append(f"| {i} | `{label}` | {ftype} | {caption} | `{pdf}` |")

    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
