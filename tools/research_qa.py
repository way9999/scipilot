from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.landscape_analysis import extract_paper_methods
from tools.paper_dashboard import build_dashboard
from tools.project_state import load_paper_index, register_search_results, sync_project_state
from tools.unified_search import auto_search


EN_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "what",
    "which",
    "about",
    "into",
    "how",
    "why",
    "when",
    "where",
    "are",
    "is",
    "was",
    "were",
    "can",
    "could",
    "should",
}

ZH_STOPWORDS = {"研究", "方法", "问题", "论文", "相关", "如何", "哪些", "什么", "以及", "对于", "基于"}


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _resolve_language(question: str, requested_language: str | None) -> str:
    if requested_language and requested_language != "auto":
        return requested_language
    return "zh" if _contains_cjk(question) else "en"


def _keywords(text: str) -> list[str]:
    english_tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9/_-]{1,}", text.lower())
        if token not in EN_STOPWORDS and len(token) >= 2
    ]
    chinese_tokens = [
        token
        for token in re.findall(r"[\u4e00-\u9fff]{2,}", text)
        if token not in ZH_STOPWORDS
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for token in [*english_tokens, *chinese_tokens]:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r", "\n")
    raw_parts = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    paragraphs: list[str] = []
    for part in raw_parts:
        compact = re.sub(r"\s+", " ", part).strip()
        if len(compact) <= 500:
            paragraphs.append(compact)
            continue
        sentences = re.split(r"(?<=[。！？.!?])\s+", compact)
        buffer = ""
        for sentence in sentences:
            if not sentence:
                continue
            candidate = f"{buffer} {sentence}".strip()
            if buffer and len(candidate) > 320:
                paragraphs.append(buffer)
                buffer = sentence
            else:
                buffer = candidate
        if buffer:
            paragraphs.append(buffer)

    merged: list[str] = []
    for paragraph in paragraphs:
        if merged and len(paragraph) < 70:
            merged[-1] = f"{merged[-1]} {paragraph}".strip()
        else:
            merged.append(paragraph)
    return merged[:24]


def _paper_text_blob(root: Path, paper: dict[str, Any]) -> tuple[str, str]:
    content_path = ""
    chunks = [
        str(paper.get("title") or ""),
        str(paper.get("abstract") or ""),
        str(paper.get("content_excerpt") or ""),
        str(paper.get("venue") or ""),
    ]
    content_json_path = paper.get("content_json_path")
    if content_json_path:
        payload = _read_json_if_exists(root / str(content_json_path))
        if payload:
            content_path = str(content_json_path)
            chunks.append(str(payload.get("content") or ""))
    return "\n\n".join(chunk for chunk in chunks if chunk.strip()), content_path


def _chunk_score(paragraph: str, keywords: list[str], analysis: dict[str, Any]) -> int:
    text = paragraph.lower()
    keyword_hits = sum(2 if keyword in text else 0 for keyword in keywords)
    analysis_hits = sum(
        1
        for item in [*(analysis.get("methods") or []), *(analysis.get("datasets") or []), *(analysis.get("metrics") or [])]
        if str(item).lower() in text
    )
    structural_bonus = 1 if len(paragraph) >= 80 else 0
    return keyword_hits + analysis_hits + structural_bonus


def _paper_chunks(root: Path, paper: dict[str, Any], keywords: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    analysis = extract_paper_methods(paper)
    blob, source_path = _paper_text_blob(root, paper)
    paragraphs = _split_paragraphs(blob) if blob.strip() else []
    if not paragraphs:
        fallback = re.sub(r"\s+", " ", str(paper.get("abstract") or paper.get("content_excerpt") or "")).strip()
        if fallback:
            paragraphs = [fallback]

    chunks: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        score = _chunk_score(paragraph, keywords, analysis)
        chunks.append(
            {
                "paragraph_index": index,
                "score": score,
                "snippet": paragraph[:320].rstrip() + ("..." if len(paragraph) > 320 else ""),
            }
        )

    ranked_chunks = sorted(chunks, key=lambda item: (item["score"], -item["paragraph_index"]), reverse=True)
    return {
        "paper": paper,
        "analysis": analysis,
        "source_path": source_path,
    }, ranked_chunks[:4]


def _paper_score(root: Path, paper: dict[str, Any], keywords: list[str]) -> tuple[int, int, int, int]:
    info, chunks = _paper_chunks(root, paper, keywords)
    max_chunk_score = max((chunk["score"] for chunk in chunks), default=0)
    return (
        max_chunk_score,
        int(bool(info["source_path"] or paper.get("content_crawled"))),
        int(bool(paper.get("verified"))),
        int(paper.get("citation_count") or 0),
    )


def _select_papers(root: Path, question: str, limit: int = 6) -> list[dict[str, Any]]:
    papers = load_paper_index(root)
    keywords = _keywords(question)
    ranked = sorted(papers, key=lambda paper: _paper_score(root, paper, keywords), reverse=True)
    matched = [paper for paper in ranked if _paper_score(root, paper, keywords)[0] >= 2]
    if matched:
        return matched[:limit]
    try:
        fetched = auto_search(question, discipline="generic", limit=8)
        if fetched:
            register_search_results(fetched, project_root=root, discipline="generic", query=question)
            papers = load_paper_index(root)
            ranked = sorted(papers, key=lambda paper: _paper_score(root, paper, keywords), reverse=True)
            matched = [paper for paper in ranked if _paper_score(root, paper, keywords)[0] >= 1]
            return matched[:limit]
    except Exception:
        pass
    return ranked[:limit]


def _label(index: int) -> str:
    return f"R{index}"


def _question_focus(question: str) -> str:
    normalized = question.lower()
    if any(word in normalized for word in ["dataset", "data", "数据", "场景"]):
        return "dataset"
    if any(word in normalized for word in ["metric", "指标", "评价", "评估"]):
        return "metric"
    if any(word in normalized for word in ["method", "approach", "方法", "模型", "技术路线"]):
        return "method"
    if any(word in normalized for word in ["gap", "不足", "问题", "challenge", "limitation"]):
        return "gap"
    return "general"


def _join_items(items: list[str], language: str, limit: int = 4, fallback: str = "") -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    selected = cleaned[:limit]
    if selected:
        return "、".join(selected) if language == "zh" else ", ".join(selected)
    return fallback


def _evidence_ref(label: str, chunk: dict[str, Any]) -> str:
    return f"{label}-P{chunk['paragraph_index']}"


def _prepare_evidence(root: Path, question: str, papers: list[dict[str, Any]], language: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keywords = _keywords(question)
    evidence: list[dict[str, Any]] = []
    decorated: list[dict[str, Any]] = []

    for index, paper in enumerate(papers, start=1):
        label = _label(index)
        info, top_chunks = _paper_chunks(root, paper, keywords)
        methods = info["analysis"].get("methods") or []
        datasets = info["analysis"].get("datasets") or []
        metrics = info["analysis"].get("metrics") or []
        evidence_entry = {
            "label": label,
            "title": str(paper.get("title") or "Untitled"),
            "year": str(paper.get("year") or "-"),
            "url": str(paper.get("url") or paper.get("pdf_url") or ""),
            "source_path": info["source_path"],
            "methods": methods,
            "datasets": datasets,
            "metrics": metrics,
            "top_chunks": [
                {
                    "chunk_id": _evidence_ref(label, chunk),
                    "paragraph_index": chunk["paragraph_index"],
                    "score": chunk["score"],
                    "snippet": chunk["snippet"],
                }
                for chunk in top_chunks
            ],
        }
        evidence.append(evidence_entry)
        decorated.append(
            {
                "label": label,
                "paper": paper,
                "analysis": info["analysis"],
                "top_chunks": top_chunks,
            }
        )

    return decorated, evidence


def _build_answer_blocks(question: str, decorated: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    focus = _question_focus(question)
    methods = Counter()
    datasets = Counter()
    metrics = Counter()

    for item in decorated:
        methods.update(item["analysis"].get("methods") or [])
        datasets.update(item["analysis"].get("datasets") or [])
        metrics.update(item["analysis"].get("metrics") or [])

    top_methods = [name for name, _ in methods.most_common(5)]
    top_datasets = [name for name, _ in datasets.most_common(5)]
    top_metrics = [name for name, _ in metrics.most_common(5)]
    leading_refs = [
        _evidence_ref(item["label"], item["top_chunks"][0])
        for item in decorated
        if item["top_chunks"]
    ][:4]
    label_refs = [item["label"] for item in decorated[:4]]

    if language == "zh":
        overview_text = (
            f"围绕“{question}”，当前本地证据主要集中在 {', '.join(f'[{label}]' for label in label_refs) or '已索引论文'}。"
            f" 从段落级证据看，高频主题集中在{_join_items(top_methods, language, fallback='系统设计与实验验证')}，"
            "说明相关工作已经形成比较稳定的技术主线，但不同论文对实验设定和工程约束的展开深度并不一致。"
        )
        if focus == "method":
            focus_text = (
                f"若只看方法路径，现有工作最常见的路线包括{_join_items(top_methods, language, fallback='系统集成、任务建模与对比验证')}。"
                f" 这些结论可以直接回溯到 {', '.join(f'[{ref}]' for ref in leading_refs) or '高相关证据段落'}，"
                "它们普遍强调通过模块组合、表示优化或流程编排来提升整体效果。"
            )
        elif focus == "dataset":
            focus_text = (
                f"若聚焦数据与场景，相关研究主要在{_join_items(top_datasets, language, fallback='公开数据集和代表性应用场景')}上完成验证，"
                "这意味着当前领域已有可比对的实验框架，但跨场景泛化与真实部署约束往往没有被系统展开。"
            )
        elif focus == "metric":
            focus_text = (
                f"若聚焦评估标准，文献通常围绕{_join_items(top_metrics, language, fallback='准确率、效率与稳定性指标')}建立比较。"
                "这说明高质量回答不能只给出单一分数，而应同时说明性能、代价和鲁棒性之间的权衡。"
            )
        elif focus == "gap":
            focus_text = (
                "若聚焦研究不足，当前最明显的问题并不是没有方法，而是缺少对失败场景、复现实验控制和工程成本的统一交代。"
                "这类缺口在高分段落中反复出现，适合直接转化为你的论文问题定义或实验设计补强项。"
            )
        else:
            focus_text = (
                f"综合来看，现有研究已经覆盖方法设计、验证场景与评价口径，尤其集中在{_join_items(top_methods, language, fallback='核心方法')}、"
                f"{_join_items(top_datasets, language, fallback='实验场景')}和{_join_items(top_metrics, language, fallback='关键指标')}三个维度。"
            )
        next_text = (
            "如果要把这套能力继续用于论文或开题写作，最有价值的做法是直接引用这些段落级证据，"
            "并把每个论断绑定到具体文献片段，而不是只保留题目和摘要层面的泛化表述。"
        )
        return [
            {"heading": "总体判断", "text": overview_text, "evidence_refs": [f"[{ref}]" for ref in leading_refs[:3]]},
            {"heading": "核心结论", "text": focus_text, "evidence_refs": [f"[{ref}]" for ref in leading_refs]},
            {"heading": "落地建议", "text": next_text, "evidence_refs": [f"[{ref}]" for ref in leading_refs[:2]]},
        ]

    overview_text = (
        f"To answer “{question}”, the strongest local evidence currently clusters around "
        f"{', '.join(f'[{label}]' for label in label_refs) or 'the indexed papers'}. "
        f"Across paragraph-level evidence, the dominant themes are {_join_items(top_methods, language, fallback='system design and evaluation practice')}."
    )
    if focus == "method":
        focus_text = (
            f"The most common method directions are {_join_items(top_methods, language, fallback='system integration, task framing, and evaluation design')}. "
            f"These claims are backed by {', '.join(f'[{ref}]' for ref in leading_refs) or 'the highest-ranked evidence chunks'}."
        )
    elif focus == "dataset":
        focus_text = (
            f"The literature is mainly validated on {_join_items(top_datasets, language, fallback='public benchmarks and representative scenarios')}, "
            "which defines the current comparison frame but leaves cross-setting generalization less explicit."
        )
    elif focus == "metric":
        focus_text = (
            f"The comparison space is usually organized around {_join_items(top_metrics, language, fallback='accuracy, efficiency, and stability metrics')}, "
            "so practical answers should discuss trade-offs rather than a single score."
        )
    elif focus == "gap":
        focus_text = (
            "The recurring gap is weak treatment of failure cases, reproducibility controls, and engineering cost, "
            "even when the method description itself is mature."
        )
    else:
        focus_text = (
            f"Taken together, the indexed papers already cover method design, validation setting, and evaluation criteria around "
            f"{_join_items(top_methods, language, fallback='core methods')}, {_join_items(top_datasets, language, fallback='benchmark settings')}, "
            f"and {_join_items(top_metrics, language, fallback='key metrics')}."
        )
    next_text = (
        "The highest-value next step is to reuse these paragraph-level evidence links directly in writing, review, and slide generation."
    )
    return [
        {"heading": "Overview", "text": overview_text, "evidence_refs": [f"[{ref}]" for ref in leading_refs[:3]]},
        {"heading": "Key Finding", "text": focus_text, "evidence_refs": [f"[{ref}]" for ref in leading_refs]},
        {"heading": "Next Step", "text": next_text, "evidence_refs": [f"[{ref}]" for ref in leading_refs[:2]]},
    ]


def _render_markdown(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    language = payload["language"]
    lines = [
        f"# {'科研问答结果' if language == 'zh' else 'Research QA Result'}",
        "",
        f"- {'问题' if language == 'zh' else 'Question'}：{payload['question']}",
        f"- {'输出文件' if language == 'zh' else 'Output path'}：`{markdown_path}`",
        f"- {'结构化数据' if language == 'zh' else 'Structured payload'}：`{json_path}`",
        f"- {'命中文献数' if language == 'zh' else 'Matched papers'}：{payload['paper_count']}",
        "",
    ]

    for block in payload["answer_blocks"]:
        lines.append(f"## {block['heading']}")
        lines.append("")
        lines.append(block["text"])
        if block["evidence_refs"]:
            lines.append("")
            lines.append(
                f"{'证据回链' if language == 'zh' else 'Evidence back-links'}：{'、'.join(block['evidence_refs']) if language == 'zh' else ', '.join(block['evidence_refs'])}"
            )
        lines.append("")

    lines.append("## 证据索引" if language == "zh" else "## Evidence Index")
    lines.append("")
    for item in payload["evidence"]:
        lines.append(f"### [{item['label']}] {item['title']}")
        lines.append("")
        lines.append(f"- {'年份' if language == 'zh' else 'Year'}：{item['year']}")
        if item["url"]:
            lines.append(f"- URL：{item['url']}")
        if item["source_path"]:
            lines.append(f"- {'内容文件' if language == 'zh' else 'Content file'}：`{item['source_path']}`")
        if item["methods"]:
            lines.append(f"- {'方法' if language == 'zh' else 'Methods'}：{_join_items(item['methods'], language, limit=5)}")
        if item["datasets"]:
            lines.append(f"- {'数据集' if language == 'zh' else 'Datasets'}：{_join_items(item['datasets'], language, limit=5)}")
        if item["metrics"]:
            lines.append(f"- {'指标' if language == 'zh' else 'Metrics'}：{_join_items(item['metrics'], language, limit=5)}")
        lines.append("")
        for chunk in item["top_chunks"]:
            lines.append(f"- [{chunk['chunk_id']}] ({chunk['score']}) {chunk['snippet']}")
        lines.append("")
    return "\n".join(lines)


def answer_research_question(
    question: str,
    project_root: str | Path = ".",
    language: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("Research question cannot be empty.")

    resolved_language = _resolve_language(normalized_question, language)
    papers = _select_papers(root, normalized_question)
    decorated, evidence = _prepare_evidence(root, normalized_question, papers, resolved_language)
    answer_blocks = _build_answer_blocks(normalized_question, decorated, resolved_language)

    payload = {
        "kind": "research_qa",
        "question": normalized_question,
        "language": resolved_language,
        "paper_count": len(papers),
        "answer_blocks": answer_blocks,
        "answer_paragraphs": [block["text"] for block in answer_blocks],
        "evidence": evidence,
    }

    markdown_path = root / "drafts" / "research-answer.md"
    json_path = root / "output" / "research-answer.json"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_markdown(payload, markdown_path, json_path), encoding="utf-8")
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
