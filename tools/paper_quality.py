"""Paper quality validation module for the sci writing system.

Scans completed markdown papers and reports issues across 7 dimensions:
  1. Parameter consistency — detects contradictory numeric values across chapters
  2. Cross-reference integrity — dangling figure/table/citation references
  3. Placeholder detection — TODO, TBD, XXX, [待补充] markers
  4. Chapter figure minimum — warns when a chapter has zero figures
  5. Data table anomaly — detects suspiciously uniform columns (possible fake data)
  6. Residual English captions — Figure/Table lines in Chinese papers
  7. Reference count — warns when citation count is below minimum

Usage::

    from tools.paper_quality import validate_paper
    report = validate_paper("drafts/paper-draft.md", language="zh")
    for issue in report["issues"]:
        print(f"[{issue['severity']}] {issue['category']}: {issue['message']}")

This module is advisory only — it never modifies files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chinese_headings(lines: list[str]) -> dict[int, str]:
    """Return {line_number: chapter_title} for ## N. headings."""
    result = {}
    for i, line in enumerate(lines):
        m = re.match(r"^\s*##\s+(\d+)\.\s+(.*)", line)
        if m:
            result[i] = f"{m.group(1)}. {m.group(2).strip()}"
    return result


def _current_chapter(lines: list[str], target_line: int) -> int:
    """Find which chapter a line belongs to."""
    ch = 0
    for i in range(target_line, -1, -1):
        m = re.match(r"^\s*##\s+(\d+)\.", lines[i] if i < len(lines) else "")
        if m:
            return int(m.group(1))
    return ch


# ---------------------------------------------------------------------------
# 1. Parameter consistency
# ---------------------------------------------------------------------------

def _check_parameter_consistency(lines: list[str]) -> list[dict[str, str]]:
    """Detect contradictory numeric parameter values across the paper."""
    issues = []
    # Collect all "param = value" or "param为value" patterns
    param_values: dict[str, list[tuple[int, str]]] = {}
    patterns = [
        re.compile(r"(?:分辨率|resolution)\s*(?:为|=|设定在?)\s*([\d.]+)\s*m?", re.IGNORECASE),
        re.compile(r"(?:线速度|maxV|速度)\s*(?:为|=|限制在?)\s*([\d.]+)\s*m/s?", re.IGNORECASE),
        re.compile(r"(?:角速度|maxW)\s*(?:为|=)\s*([\d.]+)\s*rad/s?", re.IGNORECASE),
        re.compile(r"(?:半径|radius)\s*(?:为|=)\s*([\d.]+)\s*m?", re.IGNORECASE),
        re.compile(r"(?:膨胀|margin)\s*(?:为|=)\s*([\d.]+)\s*m?", re.IGNORECASE),
    ]
    param_names = ["分辨率/resolution", "线速度/maxV", "角速度/maxW", "半径/radius", "膨胀半径/margin"]

    for i, line in enumerate(lines):
        for pat, pname in zip(patterns, param_names):
            m = pat.search(line)
            if m:
                val = m.group(1)
                param_values.setdefault(pname, []).append((i, val))

    for pname, occurrences in param_values.items():
        unique_vals = set(v for _, v in occurrences)
        if len(unique_vals) > 1:
            locations = ", ".join(f"行{ln+1}={v}" for ln, v in occurrences)
            issues.append({
                "severity": "high",
                "category": "参数一致性",
                "message": f"{pname} 在不同位置取值不同: {locations}",
            })

    return issues


# ---------------------------------------------------------------------------
# 2. Cross-reference integrity
# ---------------------------------------------------------------------------

def _check_cross_references(lines: list[str]) -> list[dict[str, str]]:
    """Check for dangling figure/table/citation references in text."""
    issues = []
    text = "\n".join(lines)

    # Collect defined figures/tables (图X-Y lines and 表X-Y text)
    defined_figs = set()
    defined_tables = set()
    for line in lines:
        # 图X-Y format
        m = re.match(r"^\s*图\s*(\d+)\s*[-．.]\s*(\d+)", line.strip())
        if m:
            defined_figs.add(f"{m.group(1)}-{m.group(2)}")
        # 表X-Y format (may be inline like "表3-1 核心参数配置表。")
        for m in re.finditer(r"表\s*(\d+)\s*[-．.]\s*(\d+)", line):
            defined_tables.add(f"{m.group(1)}-{m.group(2)}")

    # Find in-text references like 图4-3, 表3-1
    fig_refs = set()
    table_refs = set()
    for m in re.finditer(r"图\s*(\d+)\s*[-．.]\s*(\d+)(?:\s*\([a-z]\))?", text):
        fig_refs.add(f"{m.group(1)}-{m.group(2)}")
    for m in re.finditer(r"表\s*(\d+)\s*[-．.]\s*(\d+)", text):
        table_refs.add(f"{m.group(1)}-{m.group(2)}")

    # Dangling figure references
    for ref in sorted(fig_refs):
        if ref not in defined_figs:
            issues.append({
                "severity": "medium",
                "category": "交叉引用",
                "message": f"正文引用了 图{ref}，但未找到对应的图定义",
            })

    # Dangling table references
    for ref in sorted(table_refs):
        if ref not in defined_tables:
            issues.append({
                "severity": "medium",
                "category": "交叉引用",
                "message": f"正文引用了 表{ref}，但未找到对应的表定义",
            })

    return issues


# ---------------------------------------------------------------------------
# 3. Placeholder detection
# ---------------------------------------------------------------------------

def _check_placeholders(lines: list[str]) -> list[dict[str, str]]:
    """Detect TODO/TBD/placeholder markers left in the paper."""
    issues = []
    patterns = [
        (re.compile(r"\[TODO\]|\[待补充\]|\[TBD\]", re.IGNORECASE), "待补充标记"),
        (re.compile(r"XXX{3,}"), "XXX占位符"),
        (re.compile(r"在此处插入|此处插入|请插入|insert\s+here", re.IGNORECASE), "图片占位符"),
    ]
    for i, line in enumerate(lines):
        for pat, label in patterns:
            if pat.search(line):
                issues.append({
                    "severity": "high",
                    "category": "占位符",
                    "message": f"行{i+1}: 发现{label}: {line.strip()[:60]}",
                })
    return issues


# ---------------------------------------------------------------------------
# 4. Chapter figure minimum
# ---------------------------------------------------------------------------

def _check_chapter_figures(lines: list[str]) -> list[dict[str, str]]:
    """Warn when a chapter has zero figures."""
    issues = []
    chapter_has_fig: dict[int, bool] = {}
    current_ch = 0

    for line in lines:
        m = re.match(r"^\s*##\s+(\d+)\.", line)
        if m:
            current_ch = int(m.group(1))
            chapter_has_fig.setdefault(current_ch, False)
        if line.strip().startswith("!["):
            chapter_has_fig[current_ch] = True

    for ch in sorted(chapter_has_fig):
        if not chapter_has_fig[ch] and ch <= 4:
            issues.append({
                "severity": "medium",
                "category": "章节配图",
                "message": f"第{ch}章没有任何配图，工科论文建议每章至少1张图",
            })

    return issues


# ---------------------------------------------------------------------------
# 5. Data table anomaly detection
# ---------------------------------------------------------------------------

def _check_table_anomalies(lines: list[str]) -> list[dict[str, str]]:
    """Detect suspiciously uniform numeric columns (possible fake data)."""
    issues = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and "---" not in line:
            # Collect table rows
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                stripped = lines[i].strip()
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                # Skip separator rows
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1

            if len(rows) < 4:  # header + 3+ data rows
                continue

            # Check each numeric column for uniformity
            ncols = len(rows[0])
            for col in range(ncols):
                values = []
                for row in rows[1:]:  # skip header
                    if col < len(row):
                        m = re.match(r"^([\d.]+)$", row[col])
                        if m:
                            values.append(float(m.group(1)))

                if len(values) >= 3:
                    mean = sum(values) / len(values)
                    if mean > 0:
                        variance = sum((v - mean) ** 2 for v in values) / len(values)
                        cv = (variance ** 0.5) / mean  # coefficient of variation
                        if cv < 0.02:  # less than 2% variation
                            col_name = rows[0][col] if col < len(rows[0]) else f"列{col}"
                            issues.append({
                                "severity": "low",
                                "category": "数据真实性",
                                "message": f"表格列 '{col_name}' 数据过于均匀 "
                                           f"(变异系数={cv:.3f}，值={[v for v in values]})，"
                                           f"建议检查是否为真实仿真数据",
                            })
        else:
            i += 1

    return issues


# ---------------------------------------------------------------------------
# 6. Residual English captions
# ---------------------------------------------------------------------------

def _check_english_captions(lines: list[str], language: str = "zh") -> list[dict[str, str]]:
    """Detect English Figure/Table caption lines in Chinese papers."""
    if language != "zh":
        return []
    issues = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(?:Figure|Table)\s+\d+[-．.]\s*\d+", stripped) and len(stripped) < 200:
            issues.append({
                "severity": "medium",
                "category": "英文残留",
                "message": f"行{i+1}: 英文caption残留: {stripped[:60]}",
            })
    return issues


# ---------------------------------------------------------------------------
# 7. Formula formatting
# ---------------------------------------------------------------------------

def _check_formula_formatting(lines: list[str]) -> list[dict[str, str]]:
    """Detect common formula formatting issues."""
    issues = []
    for i, line in enumerate(lines):
        # $$ immediately followed by Chinese text without blank line
        if re.search(r"\$\$.*\$\$。?[^\s]", line):
            issues.append({
                "severity": "low",
                "category": "公式格式",
                "message": f"行{i+1}: 公式后缺少空行或有多余文字",
            })
        # :。 pattern (colon followed by period)
        if "：" in line and line.rstrip().endswith("。") and "定义为：" in line:
            idx = line.index("定义为：")
            rest = line[idx + len("定义为："):]
            if rest.startswith("。") or rest.startswith("."):
                issues.append({
                    "severity": "low",
                    "category": "公式格式",
                    "message": f"行{i+1}: '定义为'后有多余句号",
                })
    return issues


# ---------------------------------------------------------------------------
# 8. Structural completeness
# ---------------------------------------------------------------------------

def _check_structural_completeness(lines: list[str], language: str = "zh") -> list[dict[str, str]]:
    """Check that all required thesis sections are present."""
    issues = []
    text = "\n".join(lines)

    if language == "zh":
        required = [
            ("摘要", "摘要"),
            ("关键词", "关键词"),
            ("参考文献", "参考文献"),
        ]
        # English abstract + Keywords are strongly recommended for Chinese
        # theses. 致谢/附录 are author-supplied; their absence is not flagged.
        optional = [
            ("English Abstract", "English Abstract"),
            ("Keywords", "Keywords"),
        ]
    else:
        required = [
            ("Abstract", r"(?:^|\n)#+\s*Abstract"),
            ("References", r"(?:^|\n)#+\s*References"),
        ]
        optional: list[tuple[str, str]] = []

    for name, pattern in required:
        if not re.search(pattern, text):
            issues.append({
                "severity": "high",
                "category": "结构完整性",
                "message": f"缺少必需章节: {name}",
            })

    for name, pattern in optional:
        if not re.search(pattern, text):
            issues.append({
                "severity": "medium",
                "category": "结构完整性",
                "message": f"建议补充章节: {name}",
            })

    return issues


# ---------------------------------------------------------------------------
# 9. Word count
# ---------------------------------------------------------------------------

def _check_word_count(
    lines: list[str],
    min_total_words: int = 12000,
    language: str = "zh",
) -> list[dict[str, str]]:
    """Check total and per-chapter word counts."""
    issues = []
    text = "\n".join(lines)
    total_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total_en_words = len(re.findall(r"[a-zA-Z]+", text))
    total_words = total_chars + total_en_words

    if total_words < min_total_words:
        ratio = total_words / min_total_words
        issues.append({
            "severity": "high",
            "category": "字数",
            "message": f"总字数约{total_words}字，低于最低要求{min_total_words}字 "
                       f"(完成度{ratio:.0%})",
        })

    # Per-chapter check
    chapter_minimums = {1: 800, 2: 1200, 3: 1200, 4: 1500, 5: 600}
    chapter_text: dict[int, str] = {}
    current_ch = 0
    for line in lines:
        m = re.match(r"^\s*##\s+(\d+)\.", line)
        if m:
            current_ch = int(m.group(1))
            chapter_text.setdefault(current_ch, "")
        if current_ch > 0:
            chapter_text[current_ch] = chapter_text.get(current_ch, "") + line + "\n"

    for ch, minimum in chapter_minimums.items():
        ch_str = chapter_text.get(ch, "")
        ch_chars = len(re.findall(r"[\u4e00-\u9fff]", ch_str))
        ch_en = len(re.findall(r"[a-zA-Z]+", ch_str))
        ch_total = ch_chars + ch_en
        if ch_total < minimum and ch in chapter_text:
            issues.append({
                "severity": "medium",
                "category": "字数",
                "message": f"第{ch}章字数约{ch_total}字，低于建议最低{minimum}字",
            })

    return issues


# ---------------------------------------------------------------------------
# 10. Formula count in theory chapter
# ---------------------------------------------------------------------------

def _check_formula_count(lines: list[str]) -> list[dict[str, str]]:
    """Check that theory chapters contain enough mathematical formulas."""
    issues = []
    theory_chapter = ""
    current_ch = 0
    for line in lines:
        m = re.match(r"^\s*##\s+(\d+)\.", line)
        if m:
            current_ch = int(m.group(1))
        if current_ch == 2:  # Theory chapter
            theory_chapter += line + "\n"

    if not theory_chapter:
        return issues

    # Count display formulas ($$...$$) and tagged formulas (\tag{)
    display_formulas = len(re.findall(r"\$\$", theory_chapter)) // 2
    tagged_formulas = len(re.findall(r"\\tag\{", theory_chapter))
    formula_count = max(display_formulas, tagged_formulas)

    if formula_count < 3:
        issues.append({
            "severity": "medium",
            "category": "公式数量",
            "message": f"第2章（理论）仅有{formula_count}个独立公式，工科论文建议至少3个",
        })

    return issues


# ---------------------------------------------------------------------------
# 11. Reference section quality
# ---------------------------------------------------------------------------

def _check_reference_section(lines: list[str], min_references: int = 5, language: str = "zh") -> list[dict[str, str]]:
    """Check reference section existence and entry count."""
    issues = []
    text = "\n".join(lines)

    ref_heading = "参考文献" if language == "zh" else "References"
    has_ref_section = any(ref_heading in line for line in lines)

    if not has_ref_section:
        issues.append({
            "severity": "high",
            "category": "参考文献",
            "message": f"缺少「{ref_heading}」章节",
        })
        return issues

    # Count reference entries. Accept both legacy '1. Author...' and
    # GB/T 7714-style '[1] Author...' formats.
    in_ref = False
    ref_count = 0
    entry_pat = re.compile(r"^\s*(?:\d{1,3}\.|\[\d{1,3}\])\s+\S")
    for line in lines:
        if ref_heading in line:
            in_ref = True
            continue
        if in_ref and re.match(r"^\s*##", line):
            break  # Next section
        if in_ref and entry_pat.match(line):
            ref_count += 1

    if ref_count < min_references:
        issues.append({
            "severity": "medium",
            "category": "参考文献",
            "message": f"参考文献仅{ref_count}条，建议至少{min_references}条（本科毕设通常15-20条）",
        })

    # Count in-text citations across the common academic formats we emit.
    # The body may use 【Author年】, \cite{key}, [@AuthorYear], or [N] —
    # all four are legitimate and should count equally.
    citation_pat = re.compile(
        r"【[^】]+?\d{4}】"            # 【Author2024】
        r"|\\cite\{[^}]+\}"           # \cite{smith2024}
        r"|\[@[^\]]+\]"               # [@smith2024]
        r"|(?<!\w)\[\d{1,3}\](?!\()"  # [1] — avoid markdown link [1](url)
    )
    citations = citation_pat.findall(text)
    if len(citations) < 3:
        issues.append({
            "severity": "medium",
            "category": "文献引用",
            "message": f"正文仅引用了{len(citations)}处文献，远低于建议的{min_references}处",
        })

    return issues


# ---------------------------------------------------------------------------
# 12. Figure existence check
# ---------------------------------------------------------------------------

def _check_figure_existence(lines: list[str], base_dir: Path) -> list[dict[str, str]]:
    """Check that ![](path) references point to files that exist."""
    issues = []
    for i, line in enumerate(lines):
        for m in re.finditer(r"!\[.*?\]\(([^)]+)\)", line):
            path_str = m.group(1).strip()
            # Skip http/https URLs
            if path_str.startswith(("http://", "https://")):
                continue
            # Resolve relative to base_dir
            target = (base_dir / path_str).resolve()
            if not target.exists():
                issues.append({
                    "severity": "medium",
                    "category": "图表格式",
                    "message": f"行{i+1}: 图片文件不存在: {path_str}",
                })
    return issues


# ---------------------------------------------------------------------------
# 13. Figure caption format check
# ---------------------------------------------------------------------------

def _check_figure_caption_format(lines: list[str], language: str = "zh") -> list[dict[str, str]]:
    """Check figure numbering uses 图X-Y not 图X.Y."""
    if language != "zh":
        return []
    issues = []
    for i, line in enumerate(lines):
        # Match 图X.Y pattern (e.g., 图2.1, 图3.2) but not 图2-1
        for m in re.finditer(r"图\s*(\d+)\.(\d+)", line):
            issues.append({
                "severity": "low",
                "category": "图表格式",
                "message": f"行{i+1}: 图编号使用了点号分隔 '图{m.group(1)}.{m.group(2)}'，"
                           f"建议使用连字符 '图{m.group(1)}-{m.group(2)}'",
            })
    return issues


# ---------------------------------------------------------------------------
# 14. Figure style check (resolution and DPI)
# ---------------------------------------------------------------------------

def _check_figure_style(lines: list[str], base_dir: Path) -> list[dict[str, str]]:
    """Check PNG resolution (<400px flagged) and DPI (<150 flagged)."""
    issues = []
    try:
        from PIL import Image
    except ImportError:
        return issues

    seen_paths: set[str] = set()
    for i, line in enumerate(lines):
        for m in re.finditer(r"!\[.*?\]\(([^)]+)\)", line):
            path_str = m.group(1).strip()
            if path_str.startswith(("http://", "https://")):
                continue
            if path_str in seen_paths:
                continue
            seen_paths.add(path_str)
            target = (base_dir / path_str).resolve()
            if not target.exists():
                continue
            if not target.suffix.lower() == ".png":
                continue
            try:
                with Image.open(target) as img:
                    w, h = img.size
                    if w < 400 or h < 400:
                        issues.append({
                            "severity": "medium",
                            "category": "图表样式",
                            "message": f"图片分辨率过低: {path_str} ({w}x{h}px，建议至少400px)",
                        })
                    dpi = img.info.get("dpi")
                    if dpi:
                        min_dpi = min(dpi[0], dpi[1])
                        if min_dpi < 150:
                            issues.append({
                                "severity": "low",
                                "category": "图表样式",
                                "message": f"图片DPI过低: {path_str} ({min_dpi:.0f} DPI，建议至少150 DPI)",
                            })
            except Exception:
                continue
    return issues


# ---------------------------------------------------------------------------
# Master validation
# ---------------------------------------------------------------------------

def validate_paper(
    source: str | Path,
    language: str = "zh",
    min_references: int = 5,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a paper markdown file and return a quality report.

    Args:
        source: Path to the markdown file.
        language: "zh" or "en".
        min_references: Minimum expected citation count.
        base_dir: Base directory for resolving relative paths (defaults to source.parent).

    Returns:
        Dict with keys:
          - score: int (0-100)
          - issues: list of {severity, category, message}
          - summary: dict of counts by severity
          - total_checks: int
    """
    source = Path(source)
    if not source.exists():
        return {"score": 0, "issues": [{"severity": "high", "category": "文件", "message": f"文件不存在: {source}"}], "summary": {}, "total_checks": 0}

    if base_dir is None:
        base_dir = source.parent
    base_dir = Path(base_dir)

    lines = source.read_text(encoding="utf-8").splitlines()
    all_issues: list[dict[str, str]] = []

    all_issues.extend(_check_parameter_consistency(lines))
    all_issues.extend(_check_cross_references(lines))
    all_issues.extend(_check_placeholders(lines))
    all_issues.extend(_check_chapter_figures(lines))
    all_issues.extend(_check_table_anomalies(lines))
    all_issues.extend(_check_english_captions(lines, language))
    all_issues.extend(_check_formula_formatting(lines))
    all_issues.extend(_check_structural_completeness(lines, language))
    all_issues.extend(_check_word_count(lines, language=language))
    all_issues.extend(_check_formula_count(lines))
    all_issues.extend(_check_reference_section(lines, min_references=min_references, language=language))
    all_issues.extend(_check_figure_existence(lines, base_dir))
    all_issues.extend(_check_figure_caption_format(lines, language))
    all_issues.extend(_check_figure_style(lines, base_dir))

    # Deduplicate
    seen = set()
    unique_issues = []
    for issue in all_issues:
        key = f"{issue['category']}:{issue['message']}"
        if key not in seen:
            seen.add(key)
            unique_issues.append(issue)

    # Score: start at 100, deduct per issue
    deductions = {"high": 8, "medium": 4, "low": 1}
    score = max(0, 100 - sum(deductions.get(i["severity"], 2) for i in unique_issues))

    # Summary
    summary: dict[str, int] = {}
    for i in unique_issues:
        summary[i["severity"]] = summary.get(i["severity"], 0) + 1

    return {
        "score": score,
        "issues": unique_issues,
        "summary": summary,
        "total_checks": 14,
    }


def format_report(report: dict[str, Any]) -> str:
    """Format a validation report as a readable string."""
    lines = [
        f"=== 论文质量报告 === 得分: {report['score']}/100",
        f"问题总数: {len(report['issues'])} "
        f"(高={report['summary'].get('high', 0)} "
        f"中={report['summary'].get('medium', 0)} "
        f"低={report['summary'].get('low', 0)})",
        "",
    ]

    by_cat: dict[str, list] = {}
    for issue in report["issues"]:
        by_cat.setdefault(issue["category"], []).append(issue)

    for cat, issues in sorted(by_cat.items()):
        lines.append(f"【{cat}】")
        for issue in issues:
            lines.append(f"  [{issue['severity']}] {issue['message']}")
        lines.append("")

    return "\n".join(lines)
