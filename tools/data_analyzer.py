"""Data analysis module for scientific paper writing.

Loads experimental results, computes summary statistics, runs statistical tests,
and formats publication-ready tables in LaTeX or Markdown.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from scipy import stats as sp_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


def _ensure_pandas():
    if not HAS_PANDAS:
        raise ImportError("pandas is required: pip install pandas")


def _ensure_numpy():
    if not HAS_NUMPY:
        raise ImportError("numpy is required: pip install numpy")


def _ensure_scipy():
    if not HAS_SCIPY:
        raise ImportError("scipy is required: pip install scipy")


# ---------------------------------------------------------------------------
# Result file discovery
# ---------------------------------------------------------------------------

RESULT_EXTENSIONS = {".csv", ".json", ".tsv", ".log", ".txt"}
RESULT_NAME_HINTS = ("result", "metric", "eval", "test", "report", "benchmark", "score", "output", "log")


def find_result_files(project_dir: str | Path, max_depth: int = 4) -> list[Path]:
    """Recursively find likely result/metric files in a project directory."""
    root = Path(project_dir).resolve()
    found: list[Path] = []
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"}

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            depth = len(path.relative_to(root).parts)
        except ValueError:
            continue
        if depth > max_depth:
            continue
        if path.suffix.lower() not in RESULT_EXTENSIONS:
            continue
        name_lower = path.stem.lower()
        if any(hint in name_lower for hint in RESULT_NAME_HINTS):
            found.append(path)

    return sorted(found)


# ---------------------------------------------------------------------------
# Result loading
# ---------------------------------------------------------------------------

def load_results(path: str | Path) -> "pd.DataFrame":
    """Auto-detect format and load results into a DataFrame.

    Supports: CSV, TSV, JSON (records or dict-of-lists), and simple log files.
    """
    _ensure_pandas()
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(p)
    elif suffix == ".tsv":
        return pd.read_csv(p, sep="\t")
    elif suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return pd.DataFrame(raw)
        elif isinstance(raw, dict):
            # dict of lists → DataFrame
            if raw and isinstance(next(iter(raw.values())), list):
                return pd.DataFrame(raw)
            # dict of dicts → DataFrame.from_dict orient=index
            if raw and isinstance(next(iter(raw.values())), dict):
                return pd.DataFrame.from_dict(raw, orient="index")
            # flat dict → single-row DataFrame
            return pd.DataFrame([raw])
        return pd.DataFrame()
    elif suffix in (".log", ".txt"):
        return _parse_log_file(p)
    else:
        raise ValueError(f"Unsupported file format: {suffix}")


def _parse_log_file(path: Path) -> "pd.DataFrame":
    """Best-effort parsing of training log files.

    Looks for lines with key=value or key: value patterns.
    """
    _ensure_pandas()
    records: list[dict[str, Any]] = []
    kv_pattern = re.compile(r"(\w+)\s*[=:]\s*([\d.eE+-]+)")

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        matches = kv_pattern.findall(line)
        if len(matches) >= 2:
            record = {}
            for key, val in matches:
                try:
                    record[key] = float(val)
                except ValueError:
                    record[key] = val
            records.append(record)

    return pd.DataFrame(records) if records else pd.DataFrame()


# ---------------------------------------------------------------------------
# Training log extraction
# ---------------------------------------------------------------------------

def extract_training_log(log_path: str | Path) -> "pd.DataFrame":
    """Extract epoch-level metrics from a training log file.

    Handles common formats: CSV logs, JSON-lines, and key=value text logs.
    """
    _ensure_pandas()
    p = Path(log_path)

    if p.suffix == ".csv":
        return pd.read_csv(p)
    elif p.suffix == ".json":
        # Try JSON-lines first
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                break
        if records:
            return pd.DataFrame(records)
        # Fall back to regular JSON
        return load_results(p)
    else:
        return _parse_log_file(p)


# ---------------------------------------------------------------------------
# Statistical analysis
# ---------------------------------------------------------------------------

def compute_metrics_summary(
    df: "pd.DataFrame",
    metrics: list[str] | None = None,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Compute mean ± std for each metric, optionally grouped.

    Returns:
        {metric: {"mean": float, "std": float, "min": float, "max": float, "n": int}}
        or if group_by:
        {group: {metric: {"mean": ..., "std": ..., ...}}}
    """
    _ensure_pandas()
    _ensure_numpy()

    if metrics is None:
        metrics = [c for c in df.columns if df[c].dtype in ("float64", "int64", "float32")]

    if group_by and group_by in df.columns:
        result = {}
        for group_name, group_df in df.groupby(group_by):
            result[str(group_name)] = _summarize_cols(group_df, metrics)
        return result

    return _summarize_cols(df, metrics)


def _summarize_cols(df: "pd.DataFrame", metrics: list[str]) -> dict[str, Any]:
    summary = {}
    for m in metrics:
        if m not in df.columns:
            continue
        col = df[m].dropna()
        summary[m] = {
            "mean": float(np.mean(col)),
            "std": float(np.std(col, ddof=1)) if len(col) > 1 else 0.0,
            "min": float(np.min(col)),
            "max": float(np.max(col)),
            "n": int(len(col)),
        }
    return summary


def statistical_test(
    group_a: list[float],
    group_b: list[float],
    test: str = "t",
) -> dict[str, Any]:
    """Run a statistical test between two groups.

    Args:
        test: "t" (independent t-test), "welch" (Welch's t-test),
              "wilcoxon" (Wilcoxon signed-rank), "mannwhitney" (Mann-Whitney U)

    Returns:
        {"test": str, "statistic": float, "p_value": float, "significant_005": bool}
    """
    _ensure_scipy()
    _ensure_numpy()

    a, b = np.array(group_a, dtype=float), np.array(group_b, dtype=float)

    if test == "t":
        stat, p = sp_stats.ttest_ind(a, b, equal_var=True)
    elif test == "welch":
        stat, p = sp_stats.ttest_ind(a, b, equal_var=False)
    elif test == "wilcoxon":
        stat, p = sp_stats.wilcoxon(a, b)
    elif test == "mannwhitney":
        stat, p = sp_stats.mannwhitneyu(a, b, alternative="two-sided")
    else:
        raise ValueError(f"Unknown test: {test}. Use 't', 'welch', 'wilcoxon', or 'mannwhitney'.")

    return {
        "test": test,
        "statistic": float(stat),
        "p_value": float(p),
        "significant_005": bool(p < 0.05),
        "significant_001": bool(p < 0.01),
    }


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------

def format_results_table(
    df: "pd.DataFrame",
    fmt: str = "latex",
    bold_best: bool = True,
    caption: str = "",
    label: str = "",
) -> str:
    """Format a DataFrame as a publication-ready table.

    Args:
        fmt: "latex" or "markdown"
        bold_best: Bold the best value in each numeric column
    """
    _ensure_pandas()

    if bold_best:
        df = _bold_best_values(df)

    if fmt == "latex":
        return _format_latex_table(df, caption, label)
    elif fmt == "markdown":
        return _format_markdown_table(df)
    else:
        raise ValueError(f"Unknown format: {fmt}. Use 'latex' or 'markdown'.")


def _bold_best_values(df: "pd.DataFrame") -> "pd.DataFrame":
    """Return a copy with best values wrapped in bold markers."""
    _ensure_pandas()
    result = df.copy().astype(str)
    for col in df.columns:
        if df[col].dtype not in ("float64", "int64", "float32"):
            continue
        best_idx = df[col].idxmax()
        for idx in df.index:
            val = df.at[idx, col]
            formatted = f"{val:.4f}" if isinstance(val, float) else str(val)
            if idx == best_idx:
                result.at[idx, col] = f"**{formatted}**"
            else:
                result.at[idx, col] = formatted
    return result


def _format_latex_table(df: "pd.DataFrame", caption: str, label: str) -> str:
    n_cols = len(df.columns)
    col_spec = "l" + "c" * (n_cols - 1) if n_cols > 1 else "l"

    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
    ]
    if caption:
        lines.append(rf"\caption{{{caption}}}")
    if label:
        lines.append(rf"\label{{{label}}}")
    lines.append(rf"\begin{{tabular}}{{{col_spec}}}")
    lines.append(r"\toprule")

    # Header
    headers = " & ".join(_latex_escape(str(c)) for c in df.columns)
    lines.append(headers + r" \\")
    lines.append(r"\midrule")

    # Rows
    for _, row in df.iterrows():
        cells = []
        for val in row:
            s = str(val)
            if s.startswith("**") and s.endswith("**"):
                s = r"\textbf{" + _latex_escape(s[2:-2]) + "}"
            else:
                s = _latex_escape(s)
            cells.append(s)
        lines.append(" & ".join(cells) + r" \\")

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


def _format_markdown_table(df: "pd.DataFrame") -> str:
    headers = "| " + " | ".join(str(c) for c in df.columns) + " |"
    separator = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows = []
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join([headers, separator] + rows)


def _latex_escape(text: str) -> str:
    for char in ("&", "%", "$", "#", "_", "{", "}"):
        text = text.replace(char, "\\" + char)
    text = text.replace("~", r"\textasciitilde{}")
    text = text.replace("^", r"\textasciicircum{}")
    return text
