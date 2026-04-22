from __future__ import annotations

import csv
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


IGNORED_DIR_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
}

PRIORITY_FILE_NAMES = [
    "README.md",
    "README.txt",
    "readme.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "setup.py",
]

RESULT_FILE_NAME_HINTS = ("result", "metric", "eval", "test", "report", "benchmark", "log")
SOURCE_PATH_HINTS = ("src", "app", "model", "models", "train", "infer", "pipeline", "service")
SKIP_METHOD_NAMES = {"if", "for", "while", "switch", "catch", "return"}

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".rs", ".go", ".cpp", ".cc", ".cxx", ".c"}
RESULT_EXTENSIONS = {".json", ".csv", ".tsv", ".md", ".txt", ".log"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".rviz", ".urdf", ".xacro", ".toml"}
FIGURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".pdf"}
TABLE_EXTENSIONS = {".csv", ".tsv"}
METRIC_HINTS = (
    "accuracy",
    "acc",
    "precision",
    "recall",
    "f1",
    "auc",
    "mae",
    "mse",
    "rmse",
    "iou",
    "miou",
    "psnr",
    "ssim",
    "loss",
    "latency",
    "throughput",
    "delay",
    "fps",
    "map",
    "ndcg",
    "bleu",
    "rouge",
    "wer",
    "cer",
    "score",
    "reward",
    "success_rate",
)
VARIABLE_HINTS = (
    "x",
    "y",
    "z",
    "t",
    "dt",
    "dx",
    "dy",
    "dz",
    "vx",
    "vy",
    "vz",
    "ax",
    "ay",
    "az",
    "theta",
    "phi",
    "psi",
    "omega",
    "alpha",
    "beta",
    "gamma",
    "lambda",
    "mu",
    "sigma",
    "rho",
    "eta",
    "kappa",
    "loss",
    "cost",
    "reward",
    "state",
    "control",
    "residual",
    "jacobian",
    "hessian",
)
FORMULA_HINTS = (
    "jacobian",
    "hessian",
    "gradient",
    "loss",
    "objective",
    "optimizer",
    "constraint",
    "state",
    "observation",
    "measurement",
    "covariance",
    "kalman",
    "quaternion",
    "transform",
    "pose",
    "likelihood",
    "posterior",
    "bayes",
)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _looks_garbled(text: str) -> bool:
    if not text:
        return False
    markers = ("鍏", "鈥", "馃", "銆", "锛", "寮", "缁", "鏂", "璁")
    score = sum(text.count(marker) for marker in markers)
    return score >= 3


def _read_text(path: Path, max_chars: int = 12000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return text[:max_chars]


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def _collect_priority_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in PRIORITY_FILE_NAMES:
        candidate = root / name
        if candidate.exists() and candidate.is_file():
            files.append(candidate)
    return files


def _guess_stack(root: Path, files: list[Path]) -> list[str]:
    stack: list[str] = []
    file_names = {path.name for path in files}
    suffixes = {path.suffix for path in files}

    if "pyproject.toml" in file_names or "requirements.txt" in file_names or ".py" in suffixes:
        stack.append("Python")
    if "package.json" in file_names or {".ts", ".tsx", ".js", ".jsx"} & suffixes:
        stack.append("TypeScript/JavaScript")
    if "Cargo.toml" in file_names or ".rs" in suffixes:
        stack.append("Rust")
    if "go.mod" in file_names or ".go" in suffixes:
        stack.append("Go")
    if "pom.xml" in file_names or "build.gradle" in file_names or ".java" in suffixes:
        stack.append("Java")
    if {".cpp", ".cc", ".cxx", ".c"} & suffixes:
        stack.append("C/C++")
    return stack or ["Unknown stack"]


def _extract_project_summary(root: Path, priority_files: list[Path]) -> str:
    for path in priority_files:
        if path.name.lower().startswith("readme"):
            text = _read_text(path, max_chars=8000)
            if text.strip():
                if _looks_garbled(text):
                    continue
                text = re.sub(r"<[^>]+>", " ", text)
                raw_lines = [line.strip("# ").strip() for line in text.splitlines() if line.strip()]
                lines: list[str] = []
                for line in raw_lines:
                    normalized = re.sub(r"\s+", " ", line).strip()
                    lowered = normalized.lower()
                    if not normalized:
                        continue
                    if lowered.startswith(("🌐 language", "english |", "[![]", "![", "http://", "https://")):
                        continue
                    if "official site" in lowered or "documents" in lowered:
                        continue
                    if len(normalized) < 12:
                        continue
                    lines.append(normalized)
                for line in lines:
                    sentence_match = re.search(r"([A-Z][^.]{20,200}\.)", line)
                    if sentence_match:
                        return sentence_match.group(1).strip()
                    if " is a " in line.lower():
                        return line[:220].strip()
                snippet = " ".join(lines[:4])
                if snippet:
                    return re.sub(r"\s+", " ", snippet).strip()
    return f"Project located at {root}."


def _find_candidate_files(files: list[Path], extensions: set[str], limit: int = 12) -> list[Path]:
    def score(path: Path) -> tuple[int, int, int, str]:
        normalized = str(path).lower().replace("\\", "/")
        hint_score = sum(1 for hint in SOURCE_PATH_HINTS if hint in normalized)
        return (-hint_score, len(path.parts), len(path.name), normalized)

    return sorted([path for path in files if path.suffix.lower() in extensions], key=score)[:limit]


def _find_candidate_result_files(files: list[Path], limit: int = 12) -> list[Path]:
    candidates = []
    for path in files:
        normalized = str(path).lower().replace("\\", "/")
        if path.suffix.lower() not in RESULT_EXTENSIONS:
            continue
        if path.name.lower() in {"readme.md", "claude.md", "agents.md", "package.json", "security.md", "contributing.md"}:
            continue
        if any(hint in normalized for hint in RESULT_FILE_NAME_HINTS):
            candidates.append(path)
    return sorted(candidates, key=lambda path: (len(path.parts), len(path.name), str(path).lower()))[:limit]


def _find_candidate_figure_files(files: list[Path], limit: int = 24) -> list[Path]:
    candidates: list[Path] = []
    for path in files:
        if path.suffix.lower() not in FIGURE_EXTENSIONS:
            continue
        normalized = str(path).lower().replace("\\", "/")
        if not any(token in normalized for token in ("figure", "fig", "plot", "chart", "curve", "output", "result", "image")):
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda path: (len(path.parts), len(path.name), str(path).lower()))[:limit]


def _find_candidate_table_files(files: list[Path], limit: int = 16) -> list[Path]:
    candidates: list[Path] = []
    for path in files:
        if path.suffix.lower() not in TABLE_EXTENSIONS:
            continue
        normalized = str(path).lower().replace("\\", "/")
        if any(token in normalized for token in ("result", "metric", "benchmark", "eval", "report", "summary", "table")):
            candidates.append(path)
    return sorted(candidates, key=lambda path: (len(path.parts), len(path.name), str(path).lower()))[:limit]


def _stem_caption(path: Path) -> str:
    stem = path.stem.replace("-", " ").replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or path.stem


def _figure_role_from_path(path: Path) -> str:
    lowered = path.stem.lower()
    if any(token in lowered for token in ("arch", "framework", "module", "system", "pipeline", "flow")):
        return "design"
    if any(token in lowered for token in ("scene", "map", "environment", "demo", "sample", "qualitative")):
        return "scene"
    if any(token in lowered for token in ("compare", "ablation", "radar", "heatmap", "confusion")):
        return "comparison"
    if any(token in lowered for token in ("curve", "loss", "acc", "metric", "result", "benchmark")):
        return "result"
    return "result"


def _chapter_key_for_asset_role(role: str) -> str:
    if role in {"design", "scene"}:
        return "design"
    if role in {"result", "comparison"}:
        return "experiment"
    return "implementation"


def _read_csv_preview(path: Path, max_rows: int = 3) -> tuple[list[str], list[list[str]]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.reader(handle)
            headers = next(reader, [])
            rows = []
            for row in reader:
                if any(cell.strip() for cell in row):
                    rows.append(row[: len(headers) or None])
                if len(rows) >= max_rows:
                    break
    except OSError:
        return [], []
    return [str(item).strip() for item in headers if str(item).strip()], rows


def _extract_metric_inventory(result_files: list[Path]) -> list[str]:
    counter: Counter[str] = Counter()
    for path in result_files[:12]:
        headers, _rows = _read_csv_preview(path, max_rows=1)
        for header in headers:
            lowered = header.strip().lower()
            for hint in METRIC_HINTS:
                if hint in lowered:
                    counter[hint] += 2
        text = _read_text(path, max_chars=2400).lower()
        for hint in METRIC_HINTS:
            if hint in text:
                counter[hint] += text.count(hint)
    return [item for item, _count in counter.most_common(8)]


def _extract_variable_inventory(source_files: list[Path]) -> list[dict[str, str]]:
    counter: Counter[str] = Counter()
    evidence_map: dict[str, str] = {}
    name_pattern = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\b")
    for path in source_files[:8]:
        text = _read_text(path, max_chars=8000)
        for match in name_pattern.findall(text):
            lowered = match.lower()
            if lowered in VARIABLE_HINTS:
                counter[lowered] += 1
                evidence_map.setdefault(lowered, path.name)
    inventory: list[dict[str, str]] = []
    for symbol, _count in counter.most_common(10):
        inventory.append(
            {
                "symbol": symbol,
                "meaning": f"Derived from source usage in {evidence_map.get(symbol, 'project code')}",
                "evidence": evidence_map.get(symbol, ""),
            }
        )
    return inventory


def _extract_equation_candidates(
    source_files: list[Path],
    method_clues: list[str],
    metric_inventory: list[str],
    variable_inventory: list[dict[str, str]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    combined_text = " ".join(_read_text(path, max_chars=2500).lower() for path in source_files[:6])
    formula_hits = [hint for hint in FORMULA_HINTS if hint in combined_text]
    variable_symbols = [item.get("symbol", "") for item in variable_inventory if item.get("symbol")]
    if formula_hits or variable_symbols:
        candidates.append(
            {
                "section": "theory",
                "label": "core-model",
                "focus": ", ".join((formula_hits[:4] or ["core state update"]) + variable_symbols[:3]),
                "source": "source-code patterns",
            }
        )
    if metric_inventory:
        candidates.append(
            {
                "section": "experiment",
                "label": "metric-definition",
                "focus": ", ".join(metric_inventory[:4]),
                "source": "result metrics",
            }
        )
    if method_clues:
        candidates.append(
            {
                "section": "design",
                "label": "method-process",
                "focus": method_clues[0],
                "source": "method clues",
            }
        )
    return candidates[:6]


def _extract_figure_candidates(root: Path, figure_files: list[Path]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for path in figure_files[:24]:
        role = _figure_role_from_path(path)
        candidates.append(
            {
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "caption": _stem_caption(path),
                "role": role,
                "section": _chapter_key_for_asset_role(role),
                "source": "project-figure-scan",
            }
        )
    return candidates


def _extract_table_candidates(root: Path, table_files: list[Path]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in table_files[:16]:
        headers, rows = _read_csv_preview(path)
        metric_hits = [header for header in headers if any(hint in header.lower() for hint in METRIC_HINTS)]
        candidates.append(
            {
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "caption": _stem_caption(path),
                "section": "experiment",
                "headers": headers,
                "preview_rows": rows,
                "metrics": metric_hits[:6],
                "source": "project-table-scan",
            }
        )
    return candidates


def _derive_chapter_budget(
    *,
    figure_candidates: list[dict[str, Any]],
    table_candidates: list[dict[str, Any]],
    equation_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    chapter_names = ("background", "theory", "design", "implementation", "experiment", "conclusion")
    figure_budget = {name: 0 for name in chapter_names}
    table_budget = {name: 0 for name in chapter_names}
    equation_budget = {name: 0 for name in chapter_names}

    for item in figure_candidates:
        chapter = str(item.get("section") or "experiment")
        if chapter in figure_budget:
            figure_budget[chapter] += 1
    for item in table_candidates:
        chapter = str(item.get("section") or "experiment")
        if chapter in table_budget:
            table_budget[chapter] += 1
    for item in equation_candidates:
        chapter = str(item.get("section") or "theory")
        if chapter in equation_budget:
            equation_budget[chapter] += 1

    figure_budget["design"] = max(figure_budget["design"], 1 if figure_candidates else 0)
    figure_budget["experiment"] = max(figure_budget["experiment"], min(max(len(figure_candidates) // 2, 1), 6) if figure_candidates else 0)
    table_budget["experiment"] = max(table_budget["experiment"], min(max(len(table_candidates), 1), 4) if table_candidates else 0)
    equation_budget["theory"] = max(equation_budget["theory"], 1 if equation_candidates else 0)
    if equation_candidates:
        equation_budget["design"] = max(equation_budget["design"], 1)

    return {
        "figures": figure_budget,
        "tables": table_budget,
        "equations": equation_budget,
        "total_figures": sum(figure_budget.values()),
        "total_tables": sum(table_budget.values()),
        "total_equations": sum(equation_budget.values()),
    }


def _extract_method_clues(source_files: list[Path]) -> list[str]:
    clues: list[str] = []
    patterns = [
        (r"class\s+([A-Z][A-Za-z0-9_]+)", "class"),
        (r"def\s+([a-zA-Z_][A-Za-z0-9_]*)", "function"),
        (r"function\s+([a-zA-Z_][A-Za-z0-9_]*)", "function"),
    ]

    for path in source_files[:6]:
        text = _read_text(path, max_chars=4000)
        for pattern, kind in patterns:
            matches = re.findall(pattern, text)
            for match in matches[:3]:
                if match in SKIP_METHOD_NAMES:
                    continue
                clues.append(f"{kind} `{match}` in `{path.name}`")
        if len(clues) >= 12:
            break
    return clues[:12]


def _extract_result_clues(result_files: list[Path]) -> list[str]:
    clues: list[str] = []
    keywords = ("accuracy", "acc", "f1", "auc", "mae", "rmse", "result", "metric", "score", "loss", "roc")

    for path in result_files[:8]:
        text = _read_text(path, max_chars=4000)
        normalized = re.sub(r"\s+", " ", text)
        if any(keyword in normalized.lower() for keyword in keywords):
            clues.append(f"Potential result evidence in `{path.name}`")
        if len(clues) >= 8:
            break
    return clues


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def _render_project_analysis_markdown(context: dict[str, Any]) -> str:
    use_chinese = _contains_cjk(str(context.get("topic", ""))) or _contains_cjk(str(context.get("project_summary", "")))
    if use_chinese:
        lines = [
            f"# 项目论文分析：{context['project_name']}",
            "",
            f"- 源项目路径：`{context['source_project_path']}`",
            f"- 论文主题：{context['topic']}",
            f"- 技术栈：{'、'.join(context['stack'])}",
            "",
            "## 项目概述",
            "",
            context["project_summary"],
            "",
            "## 候选源码文件",
            "",
        ]
        lines.extend(f"- `{path}`" for path in context["candidate_source_files"] or ["未识别到关键源码文件。"])
        lines.extend(["", "## 候选配置文件", ""])
        lines.extend(f"- `{path}`" for path in context.get("candidate_config_files") or ["暂未识别到关键配置文件。"])
        lines.extend(["", "## 候选结果文件", ""])
        lines.extend(f"- `{path}`" for path in context["candidate_result_files"] or ["暂未发现明显的结果文件。"])
        lines.extend(["", "## 方法线索", ""])
        lines.extend(f"- {item}" for item in context["method_clues"] or ["暂未提取到明显的方法线索。"])
        lines.extend(["", "## 结果线索", ""])
        lines.extend(f"- {item}" for item in context["result_clues"] or ["暂未提取到明显的结果线索。"])
        lines.extend(["", "## 候选图片", ""])
        lines.extend(
            f"- {item.get('section', 'general')}: `{item.get('path', '')}` -> {item.get('caption', '')}"
            for item in context.get("figure_candidates") or [{"path": "", "caption": "暂未发现明显的图片候选。"}]
        )
        lines.extend(["", "## 候选表格", ""])
        lines.extend(
            f"- {item.get('section', 'general')}: `{item.get('path', '')}` -> {item.get('caption', '')}"
            for item in context.get("table_candidates") or [{"path": "", "caption": "暂未发现明显的表格候选。"}]
        )
        lines.extend(["", "## 公式与变量资产", ""])
        lines.extend(
            f"- 公式焦点：{item.get('focus', '')} ({item.get('source', '')})"
            for item in context.get("equation_candidates") or [{"focus": "暂未发现明显的公式候选。", "source": ""}]
        )
        lines.extend(
            f"- 变量：`{item.get('symbol', '')}`，证据：{item.get('evidence', '') or '项目源码'}"
            for item in context.get("variable_inventory") or []
        )
        lines.extend(["", "## 章节预算", ""])
        chapter_budget = context.get("chapter_budget") or {}
        for key in ("figures", "tables", "equations"):
            budget = chapter_budget.get(key) or {}
            if budget:
                lines.append(f"- {key}: " + "，".join(f"{name}={value}" for name, value in budget.items() if value))
        lines.extend(
            [
                "",
                "## 写作建议",
                "",
                "- 方法章节要严格对应现有源码、启动脚本和参数文件。",
                "- 没有日志、截图或统计结果支撑的结论，不应直接写成最终实验结论。",
                "- 可先以项目概述作为论文背景，再补充文献综述与实验数据形成终稿。",
                "",
            ]
        )
        return "\n".join(lines)

    lines = [
        f"# Project Paper Analysis: {context['project_name']}",
        "",
        f"- Source project: `{context['source_project_path']}`",
        f"- Topic hint: {context['topic']}",
        f"- Stack: {', '.join(context['stack'])}",
        "",
        "## Project Summary",
        "",
        context["project_summary"],
        "",
        "## Candidate Source Files",
        "",
    ]
    lines.extend(f"- `{path}`" for path in context["candidate_source_files"])
    lines.extend(["", "## Candidate Config Files", ""])
    lines.extend(f"- `{path}`" for path in context.get("candidate_config_files") or ["No obvious config files found."])
    lines.extend(["", "## Candidate Result Files", ""])
    lines.extend(f"- `{path}`" for path in context["candidate_result_files"] or ["No obvious result files found."])
    lines.extend(["", "## Method Clues", ""])
    lines.extend(f"- {item}" for item in context["method_clues"] or ["No strong method clues detected."])
    lines.extend(["", "## Result Clues", ""])
    lines.extend(f"- {item}" for item in context["result_clues"] or ["No strong result clues detected."])
    lines.extend(["", "## Figure Candidates", ""])
    lines.extend(
        f"- {item.get('section', 'general')}: `{item.get('path', '')}` -> {item.get('caption', '')}"
        for item in context.get("figure_candidates") or [{"path": "", "caption": "No obvious figure candidates found."}]
    )
    lines.extend(["", "## Table Candidates", ""])
    lines.extend(
        f"- {item.get('section', 'general')}: `{item.get('path', '')}` -> {item.get('caption', '')}"
        for item in context.get("table_candidates") or [{"path": "", "caption": "No obvious table candidates found."}]
    )
    lines.extend(["", "## Equation / Variable Assets", ""])
    lines.extend(
        f"- Equation focus: {item.get('focus', '')} ({item.get('source', '')})"
        for item in context.get("equation_candidates") or [{"focus": "No strong equation candidates found.", "source": ""}]
    )
    lines.extend(
        f"- Variable: `{item.get('symbol', '')}` from {item.get('evidence', '') or 'project code'}"
        for item in context.get("variable_inventory") or []
    )
    lines.extend(["", "## Chapter Budgets", ""])
    chapter_budget = context.get("chapter_budget") or {}
    for key in ("figures", "tables", "equations"):
        budget = chapter_budget.get(key) or {}
        if budget:
            lines.append(f"- {key}: " + ", ".join(f"{name}={value}" for name, value in budget.items() if value))
    lines.extend(["", "## Writing Guidance", ""])
    lines.extend(
        [
            "- Align the method section with actual modules, scripts, and configuration files.",
            "- Do not finalize results until tables or metrics are verified from logs or exported files.",
            "- Use the project summary as the problem and system background, then refine with paper search.",
            "",
        ]
    )
    return "\n".join(lines)


def analyze_project_for_paper(
    workspace_root: str | Path,
    source_project_path: str | Path,
    topic: str,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    source_root = Path(source_project_path).resolve()
    if not source_root.exists():
        raise FileNotFoundError(f"Source project path does not exist: {source_root}")
    if not source_root.is_dir():
        raise NotADirectoryError(f"Source project path is not a directory: {source_root}")

    files = _iter_files(source_root)
    priority_files = _collect_priority_files(source_root)
    source_files = _find_candidate_files(files, SOURCE_EXTENSIONS)
    config_files = _find_candidate_files(files, CONFIG_EXTENSIONS, limit=16)
    result_files = _find_candidate_result_files(files)
    method_clues = _extract_method_clues(source_files)
    figure_files = _find_candidate_figure_files(files)
    table_files = _find_candidate_table_files(files)
    metric_inventory = _extract_metric_inventory(result_files + table_files)
    variable_inventory = _extract_variable_inventory(source_files)
    equation_candidates = _extract_equation_candidates(
        source_files,
        method_clues,
        metric_inventory,
        variable_inventory,
    )
    figure_candidates = _extract_figure_candidates(source_root, figure_files)
    table_candidates = _extract_table_candidates(source_root, table_files)
    chapter_budget = _derive_chapter_budget(
        figure_candidates=figure_candidates,
        table_candidates=table_candidates,
        equation_candidates=equation_candidates,
    )
    context = {
        "source_project_path": str(source_root),
        "project_name": source_root.name,
        "topic": topic.strip() or source_root.name,
        "project_summary": _extract_project_summary(source_root, priority_files),
        "stack": _guess_stack(source_root, files),
        "candidate_source_files": [str(path.relative_to(source_root)).replace("\\", "/") for path in source_files],
        "candidate_config_files": [str(path.relative_to(source_root)).replace("\\", "/") for path in config_files],
        "candidate_result_files": [str(path.relative_to(source_root)).replace("\\", "/") for path in result_files[:12]],
        "method_clues": method_clues,
        "result_clues": _extract_result_clues(result_files),
        "priority_files": [str(path.relative_to(source_root)).replace("\\", "/") for path in priority_files],
        "figure_candidates": figure_candidates,
        "table_candidates": table_candidates,
        "equation_candidates": equation_candidates,
        "variable_inventory": variable_inventory,
        "metric_inventory": metric_inventory,
        "chapter_budget": chapter_budget,
    }

    analysis_path = workspace / "drafts" / "project-analysis.md"
    json_path = workspace / "output" / "project-analysis.json"
    _write_text(analysis_path, _render_project_analysis_markdown(context))
    _write_text(json_path, json.dumps(context, ensure_ascii=False, indent=2))

    context["analysis_path"] = str(analysis_path)
    context["analysis_json_path"] = str(json_path)

    # Agent-assisted deep analysis (Stage 1)
    try:
        from tools.agent_bridge import agent_enabled, run_agent_task, build_analysis_prompt, merge_agent_analysis
        if agent_enabled():
            print(f"[ProjectContext] 调用 AI 代理深度分析项目代码...")
            agent_result = run_agent_task(
                task=build_analysis_prompt(context),
                project_path=str(source_root),
                max_turns=5,
            )
            if agent_result["success"]:
                merge_agent_analysis(context, agent_result["output"])
                print(f"[ProjectContext] 代理分析完成，丰富了 method_clues 和 result_clues")
                # Update analysis files with enriched context
                _write_text(analysis_path, _render_project_analysis_markdown(context))
                _write_text(json_path, json.dumps(context, ensure_ascii=False, indent=2))
            else:
                print(f"[ProjectContext] 代理分析失败: {agent_result.get('error', 'unknown')}")
    except ImportError:
        pass
    except Exception as exc:
        print(f"[ProjectContext] Agent 分析异常（继续）: {exc}")

    return context
