"""Lightweight preflight gates for paper writer formatting and placement."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.paper_writer import (  # noqa: E402
    _figure_plan_item_present,
    _inject_missing_figure_placeholders,
    _inject_tables_by_plan,
    _normalize_final_manuscript_format,
    _relocate_figures_by_chapter,
    _repair_markdown_tables,
)


PASS = 0
FAIL = 0


def gate(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"PASS  {name}")
    except Exception as exc:
        FAIL += 1
        print(f"FAIL  {name}: {exc}")


def test_formula_block_and_numbering() -> None:
    md = (
        "## 2. Method\n\n"
        "$$loss = x^2$$\n"
        "式2.1\n\n"
        "表9.9 planner comparison\n"
        "| method | path_length_m | planning_time_ms |\n"
        "| A | 1 | 2 |\n"
    )
    normalized = _normalize_final_manuscript_format(md, language="zh", project_context={})
    assert "\\tag{式2.1}" in normalized
    assert "\n式2.1\n" not in normalized
    assert "表2.1 Planner Comparison" in normalized
    assert "| Method | Path Length (m) | Planning Time (ms) |" in normalized


def test_broken_formula_fragments() -> None:
    md = (
        "## 3. Design\n\n"
        "系统的基础变量定义如下。$$\n\n"
        "$$x = r \\\\cos(\\\\theta)\n"
        "\\tag{式3.1}\n"
        "$$\n\n"
        "$$\n"
        "$$y = r \\\\sin(\\\\theta)\n"
        "$$式3.2\n"
        "\\tag{式3.2}\n"
        "$$\n\n"
        "z = h\n"
        "\\tag{式3.3}\n"
        "$$\n\n"
        "后续正文继续说明系统实现。\n"
    )
    normalized = _normalize_final_manuscript_format(md, language="zh", project_context={})
    assert "$$\ny = r \\\\sin(\\\\theta)\n\\tag{式3.2}\n$$" in normalized
    assert "$$\nz = h\n\\tag{式3.3}\n$$" in normalized
    assert "$$式3.2" not in normalized
    assert not re.search(r"^\s*式\s*3\.2\s*$", normalized, flags=re.M)


def test_table_repair_and_caption() -> None:
    repaired = _repair_markdown_tables(
        "表2.1 planner comparison\n\n"
        "| method | path_length_m | planning_time_ms |\n"
        "| --- | --- | --- |\n"
        "| MPPI | 12.4 | 15.2 |\n"
    )
    assert "| Method | Path Length (m) | Planning Time (ms) |" in repaired
    assert "| :--- | ---: | ---: |" in repaired
    assert "||" not in repaired


def test_table_injection() -> None:
    md = (
        "## 4. 实验结果\n\n"
        "主要指标对比展示了方法相对基线的提升。\n\n"
        "进一步分析如下。\n"
    )
    project_context = {
        "table_plan": [
            {
                "caption": "主要指标对比",
                "section": "实验结果",
                "headers": ["指标", "数值"],
                "rows": [["Accuracy", "0.92"], ["Loss", "0.25"]],
            }
        ]
    }
    injected = _inject_tables_by_plan(md, project_context, language="zh")
    assert "表4.1 主要指标对比" in injected
    assert "| 指标 | 数值 |" in injected
    assert injected.index("表4.1 主要指标对比") > injected.index("主要指标对比展示了方法相对基线的提升。")


def test_figure_spread() -> None:
    md = (
        "## 1. Intro\n\n"
        "第一段说明系统结构。\n\n"
        "第二段说明参数配置。\n\n"
        "第三段说明控制流程。\n"
    )
    relocated = _relocate_figures_by_chapter(
        md,
        [
            {"caption": "结构框图", "path": "a.png"},
            {"caption": "参数关系图", "path": "b.png"},
            {"caption": "控制流程图", "path": "c.png"},
        ],
        language="zh",
        project_context={},
    )
    assert relocated.index("![结构框图](a.png)") < relocated.index("![参数关系图](b.png)")
    assert relocated.index("![参数关系图](b.png)") < relocated.index("![控制流程图](c.png)")
    assert relocated.index("![结构框图](a.png)") < relocated.index("第二段说明参数配置。")
    assert relocated.index("![参数关系图](b.png)") < relocated.index("第三段说明控制流程。")


def test_placeholder_matching_and_injection() -> None:
    existing = (
        "## 2. 方法设计\n\n"
        "![准确率曲线](output/figures/accuracy.png)\n"
        "图2-1 准确率曲线\n"
    )
    assert _figure_plan_item_present(existing, {"caption": "图2 准确率曲线", "existing_asset": "accuracy.png"})
    assert not _figure_plan_item_present(
        existing,
        {"caption": "图2 参数配置与运行模式关系", "goal": "说明参数配置与模式切换的映射关系"},
    )

    placeholder = _inject_missing_figure_placeholders(
        "## 2. 方法设计\n\n参数配置关系决定了运行模式切换逻辑。\n",
        {
            "figure_plan": [
                {
                    "caption": "图2 参数配置与运行模式关系",
                    "section": "方法设计",
                    "figure_type": "系统结构图",
                    "goal": "说明参数配置与模式切换的映射关系",
                    "evidence": "config/nav2.yaml",
                }
            ]
        },
        language="zh",
    )
    assert "[待补图]" in placeholder
    assert "config/nav2.yaml" in placeholder


def test_draft_scan(draft_path: Path, write_preview: bool) -> None:
    if not draft_path.exists():
        raise FileNotFoundError(str(draft_path))
    original = draft_path.read_text(encoding="utf-8")
    normalized = _normalize_final_manuscript_format(original, language="zh", project_context={})

    assert "$$式" not in normalized
    assert "||" not in normalized
    assert not re.search(r"^\s*式\s*\d+\.\d+\s*$", normalized, flags=re.M)
    assert not re.search(r"^\s*\\tag\{式\d+\.\d+\}\s*\n\s*\\tag\{式\d+\.\d+\}", normalized, flags=re.M)

    if write_preview:
        preview = ROOT / "output" / "paper-writer-gate-preview.md"
        preview.write_text(normalized, encoding="utf-8")
        print(f"INFO  preview written: {preview}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight paper writer preflight gates.")
    parser.add_argument("--draft", type=Path, help="Optional generated draft to scan after normalization.")
    parser.add_argument("--write-preview", action="store_true", help="Write normalized draft preview to output/.")
    args = parser.parse_args()

    print("=== Paper Writer Gates ===")
    gate("公式块编号与表格不串行", test_formula_block_and_numbering)
    gate("坏公式残片救援", test_broken_formula_fragments)
    gate("表格表头标准化", test_table_repair_and_caption)
    gate("表格按提及位置插入", test_table_injection)
    gate("图片分散插入", test_figure_spread)
    gate("缺图占位与匹配严格", test_placeholder_matching_and_injection)
    if args.draft:
        gate(f"草稿扫描: {args.draft}", lambda: test_draft_scan(args.draft, args.write_preview))

    print("=" * 44)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    print("=" * 44)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
