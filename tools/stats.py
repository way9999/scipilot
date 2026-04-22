"""实验结果统计显著性检验

提供 Welch t检验、Wilcoxon符号秩检验（含符号检验回退），
以及多组对比基线的便捷函数。适用于论文中的消融实验。

移植自 PaperForge engine/stats.py，集成到 sci 工具链。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb, erf, sqrt
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np


@dataclass
class TestResult:
    test_name: str
    statistic: float
    p_value: float
    n: int


def mean_std(values: Sequence[float]) -> Tuple[float, float]:
    """返回 (均值, 标准差)。"""
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return float("nan"), float("nan")
    return float(np.mean(arr)), float(np.std(arr, ddof=1) if arr.size > 1 else 0.0)


def _normal_cdf(x: float) -> float:
    return float(0.5 * (1.0 + erf(x / sqrt(2.0))))


def welch_t_test(x: Sequence[float], y: Sequence[float]) -> TestResult:
    """双样本 Welch t检验（不等方差）。"""
    x_arr = np.asarray(list(x), dtype=float)
    y_arr = np.asarray(list(y), dtype=float)
    x_arr = x_arr[np.isfinite(x_arr)]
    y_arr = y_arr[np.isfinite(y_arr)]

    if x_arr.size < 2 or y_arr.size < 2:
        return TestResult(
            "welch_ttest", float("nan"), float("nan"),
            int(min(x_arr.size, y_arr.size))
        )

    try:
        from scipy import stats
        stat, p = stats.ttest_ind(x_arr, y_arr, equal_var=False)
        return TestResult("welch_ttest", float(stat), float(p), int(min(x_arr.size, y_arr.size)))
    except Exception:
        # 无 scipy 时的纯 numpy 回退实现
        mx, my = float(np.mean(x_arr)), float(np.mean(y_arr))
        vx, vy = float(np.var(x_arr, ddof=1)), float(np.var(y_arr, ddof=1))
        denom = sqrt(vx / x_arr.size + vy / y_arr.size)
        if denom <= 0:
            return TestResult("welch_ttest_fallback", 0.0, 1.0, int(min(x_arr.size, y_arr.size)))
        t_stat = (mx - my) / denom
        p_val = float(2.0 * (1.0 - _normal_cdf(abs(t_stat))))
        return TestResult("welch_ttest_fallback", float(t_stat), p_val, int(min(x_arr.size, y_arr.size)))


def _sign_test_p_value(x: Sequence[float], y: Sequence[float]) -> float:
    """符号检验 p 值（二项分布精确检验）。"""
    diffs = np.asarray(list(x), dtype=float) - np.asarray(list(y), dtype=float)
    diffs = diffs[np.isfinite(diffs)]
    diffs = diffs[diffs != 0]
    n = len(diffs)
    if n == 0:
        return 1.0
    k = int(np.sum(diffs > 0))
    # 双侧符号检验
    p = 2.0 * sum(comb(n, i) * (0.5 ** n) for i in range(min(k, n - k) + 1))
    return min(p, 1.0)


def wilcoxon_or_sign_test(x: Sequence[float], y: Sequence[float]) -> TestResult:
    """Wilcoxon符号秩检验，样本量不足时回退到符号检验。"""
    x_arr = np.asarray(list(x), dtype=float)
    y_arr = np.asarray(list(y), dtype=float)
    min_n = min(x_arr.size, y_arr.size)
    x_arr = x_arr[:min_n]
    y_arr = y_arr[:min_n]

    diffs = x_arr - y_arr
    diffs = diffs[np.isfinite(diffs) & (diffs != 0)]

    if len(diffs) < 5:
        p = _sign_test_p_value(x_arr, y_arr)
        return TestResult("sign_test", float("nan"), p, min_n)

    try:
        from scipy import stats
        stat, p = stats.wilcoxon(x_arr[:len(diffs)], y_arr[:len(diffs)])
        return TestResult("wilcoxon", float(stat), float(p), min_n)
    except Exception:
        p = _sign_test_p_value(x_arr, y_arr)
        return TestResult("sign_test_fallback", float("nan"), p, min_n)


def compare_groups(
    records: Iterable[Dict],
    metric_key: str,
    group_key: str,
    baseline_name: str,
    alpha: float = 0.05,
) -> List[Dict]:
    """将多组实验结果与基线进行统计比较。

    Args:
        records: 数据记录列表，每条包含 group_key 和 metric_key 字段
        metric_key: 指标名称（如 "accuracy"、"AUROC"）
        group_key: 分组字段名（如 "method"、"model"）
        baseline_name: 基线组名称
        alpha: 显著性水平，默认 0.05

    Returns:
        每组的统计摘要列表，包含均值、标准差、delta、检验结果和显著性

    示例:
        records = [
            {"method": "Ours", "accuracy": 0.92},
            {"method": "Ours", "accuracy": 0.91},
            {"method": "Baseline", "accuracy": 0.85},
            {"method": "Baseline", "accuracy": 0.86},
        ]
        results = compare_groups(records, "accuracy", "method", "Baseline")
    """
    grouped: Dict[str, List[float]] = {}
    for rec in records:
        g = str(rec.get(group_key, "unknown"))
        v = rec.get(metric_key)
        if v is not None:
            try:
                grouped.setdefault(g, []).append(float(v))
            except (TypeError, ValueError):
                pass

    baseline_vals = grouped.get(baseline_name, [])
    output: List[Dict] = []

    for group_name, values in sorted(grouped.items()):
        mu, sd = mean_std(values)
        entry: Dict = {
            "group": group_name,
            "metric": metric_key,
            "n": len(values),
            "mean": mu,
            "std": sd,
            "baseline": baseline_name,
            "delta_vs_baseline": float("nan"),
            "test": "",
            "statistic": float("nan"),
            "p_value": float("nan"),
            "significant": False,
        }
        if group_name != baseline_name and baseline_vals and values:
            baseline_mu, _ = mean_std(baseline_vals)
            entry["delta_vs_baseline"] = float(mu - baseline_mu)
            test_res = welch_t_test(values, baseline_vals)
            if not np.isfinite(test_res.p_value):
                test_res = wilcoxon_or_sign_test(
                    values[: len(baseline_vals)], baseline_vals[: len(values)]
                )
            entry["test"] = test_res.test_name
            entry["statistic"] = test_res.statistic
            entry["p_value"] = test_res.p_value
            entry["significant"] = bool(
                np.isfinite(test_res.p_value) and test_res.p_value < alpha
            )
        output.append(entry)

    return output


def format_comparison_table(results: List[Dict], fmt: str = "markdown") -> str:
    """将 compare_groups 结果格式化为表格。

    Args:
        results: compare_groups 的输出
        fmt: "markdown" 或 "latex"
    """
    if not results:
        return ""

    if fmt == "markdown":
        lines = [
            "| Method | N | Mean ± Std | Δ vs Baseline | p-value | Sig. |",
            "|--------|---|------------|---------------|---------|------|",
        ]
        for r in results:
            mean_str = f"{r['mean']:.4f} ± {r['std']:.4f}"
            delta_str = f"{r['delta_vs_baseline']:+.4f}" if np.isfinite(r['delta_vs_baseline']) else "—"
            p_str = f"{r['p_value']:.4f}" if np.isfinite(r['p_value']) else "—"
            sig_str = "*" if r['significant'] else ""
            lines.append(f"| {r['group']} | {r['n']} | {mean_str} | {delta_str} | {p_str} | {sig_str} |")
        return "\n".join(lines)

    elif fmt == "latex":
        lines = [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\begin{tabular}{lrcccl}",
            r"\toprule",
            r"Method & N & Mean $\pm$ Std & $\Delta$ & $p$-value & \\",
            r"\midrule",
        ]
        for r in results:
            mean_str = f"{r['mean']:.4f} $\\pm$ {r['std']:.4f}"
            delta_str = f"{r['delta_vs_baseline']:+.4f}" if np.isfinite(r['delta_vs_baseline']) else "---"
            p_str = f"{r['p_value']:.4f}" if np.isfinite(r['p_value']) else "---"
            sig = r"$^*$" if r['significant'] else ""
            lines.append(f"{r['group']} & {r['n']} & {mean_str} & {delta_str} & {p_str} & {sig} \\\\")
        lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
        return "\n".join(lines)

    else:
        raise ValueError(f"未知格式: {fmt}，请使用 'markdown' 或 'latex'")


# ── CLI 示例 ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 示例：对比两种方法
    sample_records = [
        {"method": "Ours",     "accuracy": v} for v in [0.92, 0.91, 0.93, 0.90, 0.92]
    ] + [
        {"method": "Baseline", "accuracy": v} for v in [0.85, 0.84, 0.86, 0.85, 0.83]
    ]

    results = compare_groups(sample_records, "accuracy", "method", "Baseline")
    print(format_comparison_table(results, fmt="markdown"))
    print()
    print(format_comparison_table(results, fmt="latex"))
