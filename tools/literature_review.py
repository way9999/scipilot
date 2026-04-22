from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.landscape_analysis import extract_paper_methods
from tools.paper_dashboard import build_dashboard
from tools.project_state import load_paper_index, register_search_results, sync_project_state
from tools.unified_search import auto_search


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _resolve_language(topic: str, requested_language: str | None) -> str:
    if requested_language and requested_language != "auto":
        return requested_language
    return "zh" if _contains_cjk(topic) else "en"


def _topic_keywords(topic: str) -> list[str]:
    english = re.findall(r"[a-z0-9][a-z0-9/_-]{1,}", topic.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", topic)
    ordered: list[str] = []
    seen: set[str] = set()
    for token in [*english, *chinese]:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def _paper_relevance_score(root: Path, paper: dict[str, Any], keywords: list[str]) -> tuple[int, int, int, int, int]:
    haystack = " ".join(
        str(value)
        for value in [
            paper.get("title", ""),
            paper.get("abstract", ""),
            paper.get("content_excerpt", ""),
            paper.get("venue", ""),
            paper.get("discipline", ""),
            paper.get("doi", ""),
        ]
    ).lower()
    content_bonus = 0
    content_json_path = paper.get("content_json_path")
    if content_json_path:
        content_path = root / str(content_json_path)
        if content_path.exists():
            content_bonus = 1
            try:
                payload = json.loads(content_path.read_text(encoding="utf-8"))
                haystack += " " + str(payload.get("content", ""))[:5000].lower()
            except Exception:
                pass
    keyword_hits = sum(1 for keyword in keywords if keyword and keyword in haystack)
    return (
        keyword_hits,
        content_bonus,
        int(bool(paper.get("verified"))),
        int(paper.get("citation_count") or 0),
        int(paper.get("year") or 0),
    )


def _select_review_papers(project_root: Path, topic: str, limit: int = 12) -> list[dict[str, Any]]:
    papers = load_paper_index(project_root)
    keywords = _topic_keywords(topic)
    ranked = sorted(papers, key=lambda paper: _paper_relevance_score(project_root, paper, keywords), reverse=True)
    matched = [paper for paper in ranked if _paper_relevance_score(project_root, paper, keywords)[0] >= 1]
    return matched[:limit]


def _label(index: int) -> str:
    return f"R{index}"


def _join_items(items: list[str], language: str, limit: int = 4, fallback: str = "") -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    picked = cleaned[:limit]
    if picked:
        return "、".join(picked) if language == "zh" else ", ".join(picked)
    return fallback


def _method_family_name(methods: list[str], language: str) -> str:
    joined = " ".join(methods).lower()
    families = [
        ("图神经网络/结构建模" if language == "zh" else "Graph modeling", ["graph", "gnn", "gat", "sage"]),
        ("Transformer 与注意力机制" if language == "zh" else "Transformers and attention", ["transformer", "attention", "bert", "gpt", "llm"]),
        ("视觉表示学习" if language == "zh" else "Vision representation learning", ["vision", "cnn", "vit", "swin"]),
        ("检索与问答系统" if language == "zh" else "Retrieval and QA systems", ["retrieval", "rag", "question answering", "bm25", "dpr"]),
        ("统计学习与传统机器学习" if language == "zh" else "Statistical and classical ML", ["svm", "random forest", "regression", "bayesian"]),
    ]
    for name, keywords in families:
        if any(keyword in joined for keyword in keywords):
            return name
    return "系统集成与工程实现" if language == "zh" else "System integration and engineering"


def _review_gap(analysis: dict[str, Any], language: str) -> str:
    methods = analysis.get("methods") or []
    metrics = analysis.get("metrics") or []
    datasets = analysis.get("datasets") or []
    if language == "zh":
        if not metrics:
            return "评估指标偏弱，量化对比依据不足。"
        if not datasets:
            return "数据条件交代不充分，跨场景泛化结论偏弱。"
        if len(methods) <= 1:
            return "方法空间较窄，对鲁棒性和部署约束讨论不足。"
        return "缺少对失败场景、复现控制和工程成本的统一分析。"
    if not metrics:
        return "Weak on evaluation detail, so quantitative comparison remains thin."
    if not datasets:
        return "Data assumptions are underspecified, leaving generalization claims weak."
    if len(methods) <= 1:
        return "Method diversity is narrow, leaving robustness and deployment trade-offs underexplored."
    return "Failure cases, reproducibility controls, and engineering cost are still underreported."


def _reference_line(label: str, paper: dict[str, Any], language: str) -> str:
    authors = ", ".join((paper.get("authors") or [])[:3]) or ("未知作者" if language == "zh" else "Unknown authors")
    title = paper.get("title") or ("未命名论文" if language == "zh" else "Untitled paper")
    venue = paper.get("venue") or paper.get("source") or ("未知来源" if language == "zh" else "unknown venue")
    year = paper.get("year") or ("未标注年份" if language == "zh" else "n.d.")
    return f"[{label}] {authors}. {title}. {venue}, {year}."


def _build_review_sections(topic: str, decorated: list[dict[str, Any]], language: str) -> list[dict[str, str]]:
    method_counter = Counter()
    dataset_counter = Counter()
    metric_counter = Counter()
    family_map: dict[str, list[str]] = defaultdict(list)

    for item in decorated:
        methods = item["analysis"].get("methods") or []
        datasets = item["analysis"].get("datasets") or []
        metrics = item["analysis"].get("metrics") or []
        method_counter.update(methods)
        dataset_counter.update(datasets)
        metric_counter.update(metrics)
        family_map[_method_family_name(methods, language)].append(item["label"])

    family_lines = []
    for family, labels in sorted(family_map.items(), key=lambda entry: len(entry[1]), reverse=True):
        if language == "zh":
            family_lines.append(f"{family}主要对应 {', '.join(f'[{label}]' for label in labels[:4])}。")
        else:
            family_lines.append(f"{family} is mainly represented by {', '.join(f'[{label}]' for label in labels[:4])}.")

    if language == "zh":
        return [
            {
                "title": "研究主线",
                "body": (
                    f"围绕“{topic}”的现有研究，已经形成若干稳定的方法族。"
                    f" 从本地索引结果看，高频方法主要包括{_join_items([name for name, _ in method_counter.most_common(6)], language, fallback='系统设计、表示建模与实验验证')}。"
                    f" {' '.join(family_lines) if family_lines else '当前样本仍需继续补充，以便形成更清晰的方法谱系。'}"
                ),
            },
            {
                "title": "数据与评价口径",
                "body": (
                    f"现有工作通常在{_join_items([name for name, _ in dataset_counter.most_common(5)], language, fallback='公开数据集与代表性应用场景')}上验证，"
                    f"并使用{_join_items([name for name, _ in metric_counter.most_common(5)], language, fallback='准确率、效率和稳定性指标')}进行比较。"
                    " 这说明综述写作不能只罗列论文题目，而要明确不同工作在数据条件、评价标准和实验边界上的可比性。"
                ),
            },
            {
                "title": "研究空白",
                "body": (
                    "综合各篇论文，可以看到真正的空白主要集中在三类问题："
                    "一是失败场景与边界条件描述不足，二是复现实验控制信息不统一，三是工程部署代价和系统复杂度讨论偏弱。"
                    " 这些部分应成为后续开题报告、实验设计和毕业论文讨论章节的重点。"
                ),
            },
        ]

    return [
        {
            "title": "Method Families",
            "body": (
                f"The current literature on {topic} already clusters into a few recurring method families. "
                f"The most common directions are {_join_items([name for name, _ in method_counter.most_common(6)], language, fallback='system design, representation learning, and evaluation practice')}. "
                f"{' '.join(family_lines) if family_lines else 'The local sample should still be expanded to sharpen method taxonomy.'}"
            ),
        },
        {
            "title": "Datasets and Metrics",
            "body": (
                f"Most papers validate on {_join_items([name for name, _ in dataset_counter.most_common(5)], language, fallback='public benchmarks and representative application scenarios')} "
                f"and compare using {_join_items([name for name, _ in metric_counter.most_common(5)], language, fallback='accuracy, efficiency, and stability metrics')}. "
                "A useful review should therefore compare data assumptions, evaluation criteria, and experimental boundaries instead of listing papers flatly."
            ),
        },
        {
            "title": "Research Gaps",
            "body": (
                "The strongest recurring gaps are weak treatment of failure cases, uneven reporting of reproducibility controls, "
                "and limited discussion of engineering cost under realistic deployment settings."
            ),
        },
    ]


def _render_review_markdown(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    language = payload["language"]
    lines = [
        f"# {'文献综述草稿' if language == 'zh' else 'Literature Review Draft'}：{payload['topic']}",
        "",
        f"- {'输出文件' if language == 'zh' else 'Draft path'}：`{markdown_path}`",
        f"- {'结构化数据' if language == 'zh' else 'Structured payload'}：`{json_path}`",
        f"- {'样本文献数' if language == 'zh' else 'Paper count'}：{payload['paper_count']}",
        "",
    ]

    for section in payload["sections"]:
        lines.append(f"## {section['title']}")
        lines.append("")
        lines.append(section["body"])
        lines.append("")

    lines.append("## 证据矩阵" if language == "zh" else "## Evidence Matrix")
    lines.append("")
    lines.append("| Label | Year | Title | Method Family | Methods | Datasets | Metrics | Gap |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in payload["matrix_rows"]:
        lines.append(
            f"| {row['label']} | {row['year']} | {row['title']} | {row['method_family']} | {row['methods']} | {row['datasets']} | {row['metrics']} | {row['gap']} |"
        )
    lines.extend([""])
    lines.append("## 参考文献候选" if language == "zh" else "## Reference Candidates")
    lines.append("")
    lines.extend(f"- {item}" for item in payload["references"])
    lines.append("")
    return "\n".join(lines)


def generate_literature_review(
    topic: str,
    project_root: str | Path = ".",
    language: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise ValueError("Literature review topic cannot be empty.")

    resolved_language = _resolve_language(normalized_topic, language)
    papers = _select_review_papers(root, normalized_topic)
    if not papers:
        try:
            fetched = auto_search(normalized_topic, discipline="generic", limit=8)
            if fetched:
                register_search_results(fetched, project_root=root, discipline="generic", query=normalized_topic)
                papers = _select_review_papers(root, normalized_topic)
        except Exception:
            pass

    decorated: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, str]] = []
    references: list[str] = []

    for index, paper in enumerate(papers, start=1):
        label = _label(index)
        analysis = extract_paper_methods(paper)
        method_family = _method_family_name(analysis.get("methods") or [], resolved_language)
        decorated.append({"label": label, "paper": paper, "analysis": analysis, "method_family": method_family})
        references.append(_reference_line(label, paper, resolved_language))
        matrix_rows.append(
            {
                "label": label,
                "year": str(paper.get("year") or "-"),
                "title": str(paper.get("title") or "Untitled").replace("|", "/"),
                "method_family": method_family.replace("|", "/"),
                "methods": _join_items(analysis.get("methods") or [], resolved_language, limit=3, fallback="未明确" if resolved_language == "zh" else "-").replace("|", "/"),
                "datasets": _join_items(analysis.get("datasets") or [], resolved_language, limit=2, fallback="未明确" if resolved_language == "zh" else "-").replace("|", "/"),
                "metrics": _join_items(analysis.get("metrics") or [], resolved_language, limit=2, fallback="未明确" if resolved_language == "zh" else "-").replace("|", "/"),
                "gap": _review_gap(analysis, resolved_language).replace("|", "/"),
            }
        )

    payload = {
        "kind": "literature_review",
        "topic": normalized_topic,
        "language": resolved_language,
        "paper_count": len(papers),
        "sections": _build_review_sections(normalized_topic, decorated, resolved_language),
        "matrix_rows": matrix_rows,
        "references": references
        or [
            "当前尚未检索到高相关文献，请先执行文献搜索。" if resolved_language == "zh" else "No topic-matched papers are indexed yet; run paper search first."
        ],
    }

    markdown_path = root / "drafts" / "literature-review.md"
    json_path = root / "output" / "literature-review.json"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_review_markdown(payload, markdown_path, json_path), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    state = sync_project_state(root)
    dashboard_path = build_dashboard(root)
    return {
        "project_root": str(root),
        "dashboard_path": str(dashboard_path),
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "artifact": payload,
        "state": state,
    }
