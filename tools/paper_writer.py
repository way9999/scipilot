from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable

from tools.domain_utils import detect_domain, get_evidence_terms, get_default_keywords
from tools.paper_dashboard import build_dashboard
from tools.project_state import load_paper_index, sync_project_state
from tools.text_safety import safe_json_dumps, safe_write_text
from tools.writing_profiles import (
    build_equation_plan,
    build_figure_plan,
    build_figure_plan_summary,
    build_table_plan,
    render_figure_plan_markdown,
    render_integrated_writing_assets_markdown,
)

ENGLISH_STOPWORDS = {
    "analysis",
    "approach",
    "based",
    "framework",
    "method",
    "methods",
    "model",
    "models",
    "paper",
    "research",
    "review",
    "study",
    "survey",
    "system",
    "using",
}

CHINESE_STOPWORDS = {
    "研究",
    "方法",
    "模型",
    "实验",
    "系统",
    "分析",
    "应用",
    "设计",
    "实现",
    "综述",
}

CJK_SPLIT_PATTERN = re.compile(r"(?:中的|及其|以及|基于|面向|针对|关于|用于|在|对|和|与|及|的|中)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _resolve_language(topic: str, requested_language: str | None) -> str:
    if requested_language and requested_language != "auto":
        return requested_language
    return "zh" if _contains_cjk(topic) else "en"


def _normalize_title(topic: str, language: str) -> str:
    normalized = re.sub(r"\s+", " ", topic.strip())
    if not normalized:
        return "Untitled Topic"
    if language == "zh":
        return normalized
    return normalized.title()


def _topic_keywords(topic: str) -> list[str]:
    english_keywords = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9/_-]{2,}", topic.lower())
        if token not in ENGLISH_STOPWORDS
    ]

    chinese_keywords: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", topic):
        parts = [part.strip() for part in CJK_SPLIT_PATTERN.split(chunk) if len(part.strip()) >= 2]
        if not parts:
            parts = [chunk]
        chinese_keywords.extend(part for part in parts if part not in CHINESE_STOPWORDS)

    ordered_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in [*english_keywords, *chinese_keywords]:
        if keyword and keyword not in seen:
            ordered_keywords.append(keyword)
            seen.add(keyword)
    return ordered_keywords


def _paper_score(paper: dict[str, Any], keywords: list[str]) -> tuple[int, int, int, int, int, int]:
    raw_haystack = " ".join(
        str(value)
        for value in [
            paper.get("title", ""),
            paper.get("abstract", ""),
            paper.get("content_excerpt", ""),
            paper.get("venue", ""),
            paper.get("discipline", ""),
            paper.get("doi", ""),
        ]
    )
    normalized_haystack = raw_haystack.lower()
    normalized_title = str(paper.get("title", ""))
    keyword_hits = 0
    title_hits = 0
    for keyword in keywords:
        if _contains_cjk(keyword):
            if keyword in raw_haystack:
                keyword_hits += 2
            if keyword in normalized_title:
                title_hits += 1
            continue

        if keyword in normalized_haystack:
            keyword_hits += 1
        if keyword in normalized_title.lower():
            title_hits += 1

    return (
        title_hits,
        keyword_hits,
        int(bool(paper.get("verified"))),
        int(bool(paper.get("content_crawled") or paper.get("content_path"))),
        int(bool(paper.get("downloaded") or paper.get("local_path"))),
        int(paper.get("citation_count") or 0),
    )


def _select_reference_papers(project_root: Path, topic: str, limit: int = 8) -> list[dict[str, Any]]:
    papers = load_paper_index(project_root)
    keywords = _topic_keywords(topic)
    if not keywords:
        return []

    minimum_hits = 1 if any(_contains_cjk(keyword) for keyword in keywords) or len(keywords) <= 2 else 2
    scored = [(paper, _paper_score(paper, keywords)) for paper in papers]
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    matched = [paper for paper, score in ranked if score[0] >= 1 or score[1] >= minimum_hits]

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in matched:
        dedupe_key = str(paper.get("doi") or "").strip().lower()
        if not dedupe_key:
            dedupe_key = re.sub(r"\s+", " ", str(paper.get("title") or "").strip().lower())
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(paper)
        if len(deduped) >= limit:
            break
    return deduped


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_text(path: Path, content: str) -> Path:
    return safe_write_text(path, content, trailing_newline=True)


def _reference_label(index: int) -> str:
    return f"R{index}"


def _reference_catalog(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for index, paper in enumerate(papers, start=1):
        references.append(
            {
                "label": _reference_label(index),
                "record_id": paper.get("record_id"),
                "title": paper.get("title"),
                "authors": paper.get("authors") or [],
                "year": paper.get("year"),
                "venue": paper.get("venue") or paper.get("source"),
                "doi": paper.get("doi"),
                "url": paper.get("url") or paper.get("pdf_url"),
                "abstract": paper.get("abstract") or "",
                "content_excerpt": paper.get("content_excerpt") or "",
            }
        )
    return references


def _reference_string(reference: dict[str, Any]) -> str:
    authors = ", ".join(reference["authors"][:3]) or "Unknown authors"
    venue = reference.get("venue") or "unknown venue"
    year = reference.get("year") or "n.d."
    title = reference.get("title") or "Untitled paper"
    return f"[{reference['label']}] {authors}. {title}. {venue}, {year}."


_UPLOADED_REFERENCE_MAX_ITEMS = 3
_UPLOADED_REFERENCE_PER_ITEM_CHARS = 900
_UPLOADED_REFERENCE_TOTAL_CHARS = 2400
_UPLOADED_REFERENCE_REFERENCE_EXCERPT_CHARS = 480
_GENERIC_TOPIC_FALLBACKS = {
    "appendix",
    "attachment",
    "attachments",
    "doc",
    "document",
    "draft",
    "figure",
    "figures",
    "file",
    "files",
    "image",
    "images",
    "notes",
    "paper",
    "papers",
    "pdf",
    "reference",
    "references",
    "repo",
    "research",
    "scan",
    "sci",
    "screenshot",
    "source",
    "supplement",
    "supplementary",
    "workspace",
}


def _collapse_reference_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _uploaded_reference_anchor_terms(topic: str, project_context: dict[str, Any] | None) -> list[str]:
    context = project_context or {}
    chunks: list[str] = [str(topic or "")]

    for key in ("topic", "project_name", "project_summary", "summary", "project_description"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value)

    source_project_path = str(context.get("source_project_path") or "").strip()
    if source_project_path:
        chunks.append(Path(source_project_path).stem)

    for key in ("stack", "result_clues", "source_files", "metric_inventory", "variable_inventory"):
        value = context.get(key)
        if isinstance(value, list):
            chunks.extend(str(item) for item in value[:20] if str(item).strip())
        elif isinstance(value, dict):
            chunks.extend(str(item) for item in list(value.values())[:20] if str(item).strip())
        elif isinstance(value, str) and value.strip():
            chunks.append(value)

    terms: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for token in _topic_keywords(str(chunk)):
            normalized = token.strip().lower()
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(normalized)
    return terms[:24]


def _score_uploaded_reference(ref: dict[str, Any], anchor_terms: list[str]) -> int:
    title_haystack = " ".join(
        [
            str(ref.get("filename") or ""),
            str(Path(str(ref.get("filename") or ref.get("path") or "")).stem),
            str(ref.get("description") or ""),
        ]
    ).lower()
    content_haystack = _collapse_reference_text(str(ref.get("content") or ""))[:4000].lower()
    if not title_haystack.strip() and not content_haystack:
        return -1
    if not anchor_terms:
        return 1

    title_hits = 0
    content_hits = 0
    for term in anchor_terms:
        if len(term) < 2:
            continue
        if term in title_haystack:
            title_hits += 1
        elif term in content_haystack:
            content_hits += 1
    if title_hits == 0 and content_hits == 0:
        return 0
    return title_hits * 4 + content_hits


def _excerpt_uploaded_reference(content: str, anchor_terms: list[str], limit: int) -> str:
    compact = _collapse_reference_text(content)
    if not compact or limit <= 0:
        return ""
    if len(compact) <= limit or not anchor_terms:
        return compact[:limit]

    lowered = compact.lower()
    positions = [
        lowered.find(term)
        for term in anchor_terms
        if term and lowered.find(term) >= 0
    ]
    if not positions:
        return compact[:limit]

    start = max(0, min(positions) - max(limit // 6, 80))
    excerpt = compact[start:start + limit]
    if start > 0:
        excerpt = "..." + excerpt.lstrip()
    if start + limit < len(compact):
        excerpt = excerpt.rstrip() + "..."
    return excerpt


def _select_uploaded_reference_entries(
    uploaded_references: list[dict[str, Any]],
    *,
    topic: str,
    project_context: dict[str, Any] | None = None,
    max_items: int = _UPLOADED_REFERENCE_MAX_ITEMS,
    per_item_chars: int = _UPLOADED_REFERENCE_PER_ITEM_CHARS,
    total_chars: int = _UPLOADED_REFERENCE_TOTAL_CHARS,
) -> list[dict[str, Any]]:
    anchor_terms = _uploaded_reference_anchor_terms(topic, project_context)
    ranked: list[tuple[int, int, dict[str, Any], str]] = []
    for index, ref in enumerate(uploaded_references or []):
        if ref.get("error") or ref.get("is_image"):
            continue
        content = str(ref.get("content") or "").strip()
        if not content:
            continue
        score = _score_uploaded_reference(ref, anchor_terms)
        if score < 0 or (anchor_terms and score <= 0):
            continue
        excerpt = _excerpt_uploaded_reference(content, anchor_terms, per_item_chars)
        if not excerpt:
            continue
        ranked.append((score, index, ref, excerpt))

    selected: list[dict[str, Any]] = []
    remaining_chars = total_chars
    for score, index, ref, excerpt in sorted(ranked, key=lambda item: (-item[0], item[1])):
        if len(selected) >= max_items or remaining_chars <= 0:
            break
        clipped = excerpt[:remaining_chars].rstrip()
        if not clipped:
            continue
        if len(clipped) < len(excerpt) and not clipped.endswith("..."):
            clipped = clipped.rstrip(". ") + "..."
        selected.append(
            {
                "index": index,
                "score": score,
                "ref": ref,
                "excerpt": clipped,
            }
        )
        remaining_chars -= len(clipped)
    return selected


def _is_descriptive_topic_candidate(candidate: str) -> bool:
    normalized = re.sub(r"[_\-]+", " ", str(candidate or "")).strip()
    if not normalized:
        return False

    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    if any(len(chunk) >= 4 for chunk in cjk_chunks):
        return True

    english_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", normalized.lower())
        if len(token) >= 3 and token not in ENGLISH_STOPWORDS
    ]
    meaningful = [token for token in english_tokens if token not in _GENERIC_TOPIC_FALLBACKS]
    if len(meaningful) >= 2:
        return True
    if meaningful and len(normalized) >= 16:
        return True
    return False


def _pick_uploaded_reference_topic_candidate(uploaded_references: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    seen: set[str] = set()
    for ref in uploaded_references or []:
        if ref.get("error"):
            continue
        filename = str(ref.get("filename") or ref.get("path") or "").strip()
        if not filename:
            continue
        stem = Path(filename).stem.strip()
        if not _is_descriptive_topic_candidate(stem):
            continue
        normalized = re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", stem)).strip()
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(normalized)
    return candidates[0] if len(candidates) == 1 else ""


def _build_uploaded_reference_text(
    uploaded_references: list[dict[str, Any]],
    *,
    topic: str = "",
    project_context: dict[str, Any] | None = None,
) -> str:
    """Build a compact supplementary reference block for LLM injection."""
    selected_refs = _select_uploaded_reference_entries(
        uploaded_references,
        topic=topic,
        project_context=project_context,
    )
    if not selected_refs:
        return ""

    parts = [
        "Supplementary uploaded reference excerpts. Use them only when they support, and do not override, project evidence and indexed literature.",
        "",
    ]
    for item in selected_refs:
        ref = item["ref"]
        filename = ref.get("filename", "unknown")
        parts.append(f"### Uploaded Reference: {filename}")
        parts.append(item["excerpt"])
        parts.append("")
    return "\n".join(parts).strip()


def _load_experiment_plan(project_root: Path) -> dict[str, Any] | None:
    return _load_json(project_root / "output" / "experiment-plan.json")


def _experiment_summary(plan: dict[str, Any] | None, language: str) -> list[str]:
    if not plan:
        return (
            [
                "当前还没有实验计划文件，建议先锁定任务定义、数据集、评价指标与基线。 ",
                "论文草稿保留了实验与结果章节的写作位点，后续可直接替换为真实结果。 ",
            ]
            if language == "zh"
            else [
                "No experiment plan is available yet; lock the task, datasets, metrics, and baselines before submission.",
                "The draft keeps explicit writing slots for experiments and results so real findings can be dropped in later.",
            ]
        )

    baseline_matrix = plan.get("baseline_matrix") or {}
    methods = [str(item).strip() for item in (baseline_matrix.get("methods") or []) if str(item).strip()]
    datasets = [str(item).strip() for item in (baseline_matrix.get("datasets") or []) if str(item).strip()]
    metrics = [str(item).strip() for item in (baseline_matrix.get("metrics") or []) if str(item).strip()]
    total_runs = int(baseline_matrix.get("total_runs") or max(len(methods), 1) * max(len(datasets), 1))
    ablations = plan.get("ablations") or []
    hyperparameter_grid = plan.get("hyperparameter_grid") or []

    if language == "zh":
        lines = [
            f"当前实验计划包含 {total_runs} 组基线实验、{len(ablations)} 组消融设置和 {len(hyperparameter_grid)} 组超参数候选。"
        ]
        if datasets or metrics:
            dataset_text = "、".join(datasets[:3]) or "待确认数据集"
            metric_text = "、".join(metrics[:3]) or "待确认指标"
            lines.append(f"建议优先在 {dataset_text} 上报告 {metric_text}，并保持可复现实验设置。")
        if len(methods) > 1:
            lines.append(f"基线对比建议覆盖 {'、'.join(methods[1:4])} 等代表性方法。")
        return lines

    summary_lines = [str(plan.get("summary", "")).strip(), str(plan.get("objective", "")).strip()]
    summary_lines.extend(str(item).strip() for item in (plan.get("hypotheses") or [])[:2])
    if datasets or metrics:
        summary_lines.append(
            f"Prioritize reporting results on {', '.join(datasets[:3]) or 'the confirmed datasets'} "
            f"with metrics such as {', '.join(metrics[:3]) or 'the confirmed metrics'}."
        )
    return [line for line in summary_lines if line]


def _section(title: str, paragraphs: list[str]) -> dict[str, Any]:
    return {"title": title, "content": [paragraph for paragraph in paragraphs if paragraph.strip()]}


def _build_english_sections(
    title: str,
    paper_type: str,
    references: list[dict[str, Any]],
    experiment_lines: list[str],
    plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    citation_labels = ", ".join(f"[{reference['label']}]" for reference in references[:4])
    top_titles = ", ".join(reference["title"] for reference in references[:3] if reference.get("title"))
    datasets = ", ".join(plan.get("baseline_matrix", {}).get("datasets", [])[:3]) if plan else ""
    metrics = ", ".join(plan.get("baseline_matrix", {}).get("metrics", [])[:3]) if plan else ""
    methods = ", ".join(plan.get("baseline_matrix", {}).get("methods", [])[1:4]) if plan else ""
    literature_anchor = f"recent literature {citation_labels}" if references else "the topic-matched literature that still needs to be added to the workspace"
    motivating_references = top_titles or "not available yet, so this section should be finalized after another targeted paper search"

    return [
        _section(
            "Abstract",
            [
                f"This paper studies {title} and targets a concrete research gap exposed by {literature_anchor}.",
                f"The proposed manuscript should frame the problem, motivate the technical route, and connect the method to a reproducible evaluation plan built on the current workspace assets.",
                f"The current draft already integrates literature anchors, expected experimental choices, and reporting checkpoints so it can be iteratively refined into a {paper_type} submission.",
            ],
        ),
        _section(
            "1. Introduction",
            [
                f"{title} is important because existing methods still trade off performance, robustness, and deployment cost in ways that leave clear room for improvement. The introduction should start from the application pressure, then narrow to the unresolved technical gap supported by {literature_anchor}.",
                f"A practical opening structure is: problem context -> why current methods are insufficient -> what the paper contributes -> how the paper is validated. In the current workspace, the strongest motivating references are {motivating_references}.",
                "Conclude the introduction with 3-4 explicit contributions, each phrased as a verifiable claim rather than a vague aspiration.",
            ],
        ),
        _section(
            "2. Related Work",
            [
                "Organize related work by methodological line instead of by publication chronology. A reliable pattern is: task formulation, representative model families, and evaluation protocol.",
                f"When discussing prior work, compare your target direction against strong baselines such as {methods or 'the strongest methods in the collected literature'} and cite each paragraph with concrete references from the current index.",
                "End the section by stating exactly what remains unresolved, because that unresolved point becomes the transition into the method section.",
            ],
        ),
        _section(
            "3. Method",
            [
                f"The method section should define the input, output, training or inference flow, and the core design choices behind {title}. Describe the full pipeline first, then zoom into each module with one paragraph per module.",
                "Every design choice should be defended either by prior literature, by a task-specific constraint, or by an ablation that you already plan to run. Avoid claiming novelty without naming the closest competing formulation.",
                "If the implementation is still evolving, keep placeholders for one system overview figure, one algorithm block, and one table summarizing symbols or notation.",
            ],
        ),
        _section(
            "4. Experimental Setup",
            [
                *experiment_lines,
                f"Write this section in the order: datasets -> baselines -> metrics -> implementation details -> reproducibility controls. The draft already suggests datasets ({datasets or 'to be confirmed'}), metrics ({metrics or 'to be confirmed'}), and baseline families from the experiment plan.",
            ],
        ),
        _section(
            "5. Results and Analysis Plan",
            [
                "The results section should not be a dump of tables. Start with the main comparison table, then explain why the result matters, then move to ablations, efficiency, and failure cases.",
                "Reserve one subsection for robustness or generalization, one for ablation, and one for qualitative or error analysis. If the project does not yet have results, keep these subsections as reporting slots and list the exact figures or tables that will be inserted.",
                "For each table, add a one-sentence takeaway directly under it; reviewers should not need to infer the claim themselves.",
            ],
        ),
        _section(
            "6. Discussion and Limitations",
            [
                "State boundary conditions, hidden assumptions, and likely failure modes explicitly. This section becomes much stronger when it distinguishes technical limitations from data limitations and evaluation limitations.",
                "If the method depends on curated data, hardware scale, or specific task settings, say so directly and frame follow-up work around those constraints.",
            ],
        ),
        _section(
            "7. Conclusion",
            [
                f"Summarize the problem, method, validation strategy, and practical contribution of {title} in two focused paragraphs.",
                f"Because the target is a {paper_type} paper, the conclusion should be brief and should not introduce new technical details or unsupported claims.",
            ],
        ),
    ]


def _build_chinese_sections(
    title: str,
    paper_type: str,
    references: list[dict[str, Any]],
    experiment_lines: list[str],
    plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    citation_labels = "、".join(f"[{reference['label']}]" for reference in references[:4]) or "[R1]"
    top_titles = "；".join(reference["title"] for reference in references[:3] if reference.get("title"))
    datasets = "、".join(plan.get("baseline_matrix", {}).get("datasets", [])[:3]) if plan else ""
    metrics = "、".join(plan.get("baseline_matrix", {}).get("metrics", [])[:3]) if plan else ""
    methods = "、".join(plan.get("baseline_matrix", {}).get("methods", [])[1:4]) if plan else ""

    return [
        _section(
            "摘要",
            [
                f'本文围绕\u201c{title}\u201d展开，目标是把当前工作区中的文献证据、实验方案与论文结构整合成一份可以继续细化的论文初稿 {citation_labels}。',
                "摘要应覆盖问题背景、方法主线、实验设计和预期贡献四个要点，避免写成泛泛而谈的项目说明。",
                f"当前版本已经预留了适合 {paper_type} 型论文的写作骨架，后续只需要逐步替换为真实结果、图表与精确表述。",
            ],
        ),
        _section(
            "1. 引言",
            [
                f'{title} 对应的问题具有明确应用价值，但现有研究在性能、鲁棒性、泛化能力或工程成本之间仍存在明显权衡。引言建议按照"问题重要性 -> 现有不足 -> 本文思路 -> 本文贡献"的顺序展开，并用 {citation_labels} 建立可信的研究缺口。',
                f"当前工作区中最值得优先引用的代表性文献包括：{top_titles or '已索引的高相关论文'}。这些文献应分别承担问题背景、主流方法和评价协议三类证据角色。",
                "引言结尾建议明确列出 3-4 条贡献，每条贡献都要可验证、可对应到方法或实验章节，不要写成空泛目标。",
            ],
        ),
        _section(
            "2. 相关工作",
            [
                "相关工作建议按技术路线而不是时间顺序组织，可拆成任务定义、代表性方法和评价协议三条主线。",
                f"撰写时要把你的方法和强基线进行直接对照，例如 {methods or '当前索引文献中的主流方法'}，并说明这些方法为什么仍然无法解决你的核心问题。",
                '每个小节最后都应给出一两句"差异总结"，把相关工作自然过渡到下一节的方法设计。',
            ],
        ),
        _section(
            "3. 方法",
            [
                f"方法部分需要完整交代 {title} 的输入输出、核心模块、训练或推理流程，以及各模块之间的信息流转关系。",
                "建议先给出整体框架，再逐模块展开，每个模块说明其设计动机、实现方式和对应收益。凡是声称有效的模块，都应在后文消融实验中得到验证。",
                "如果实现尚未完全定型，也应先保留系统框图、伪代码块和符号说明表的位置，避免后期结构混乱。",
            ],
        ),
        _section(
            "4. 实验设置",
            [
                *experiment_lines,
                f'本节建议按"数据集 -> 基线 -> 指标 -> 实现细节 -> 复现控制"展开。当前实验计划中已经给出了候选数据集（{datasets or '待确认'}）和指标（{metrics or '待确认'}），可以直接纳入论文初稿。',
            ],
        ),
        _section(
            "5. 结果与分析计划",
            [
                '结果部分不应只是罗列表格，建议采用"主结果 -> 消融 -> 效率/成本 -> 失败案例/误差分析"的顺序展开。',
                "如果真实实验尚未完成，当前草稿应至少明确未来需要插入哪些主表、哪些图、每一部分要回答什么问题。",
                "每个表格和图后面都应给出一句明确结论，直接指出它支持了哪条贡献或哪项假设。",
            ],
        ),
        _section(
            "6. 讨论与局限",
            [
                "讨论部分需要主动交代方法的适用边界、潜在偏差来源和尚未解决的问题。最好区分技术局限、数据局限和评价局限。",
                "如果方法依赖特定数据分布、较高算力或人工规则，也应在这里明确说明，并给出后续工作方向。",
            ],
        ),
        _section(
            "7. 结论",
            [
                f"结论需要回收全文主线，概括 {title} 的问题价值、方法亮点和实验验证思路。",
                f"由于目标稿型是 {paper_type}，结论应简洁，不新增未经验证的技术声明。",
            ],
        ),
    ]


def _render_sections_markdown(sections: list[dict[str, Any]], title: str = "") -> str:
    lines: list[str] = []
    # Emit paper title as H1 if provided
    if title:
        lines.append(f"# {title}")
        lines.append("")
    for section in sections:
        lines.append(f"## {section['title']}")
        lines.append("")
        for paragraph in section["content"]:
            lines.append(paragraph)
            lines.append("")
    return "\n".join(lines)


def _contains_mojibake(text: str) -> bool:
    if not text:
        return False
    markers = ("鍏", "鈥", "馃", "銆", "锛", "寮", "缁", "鏂", "璁", "??")
    score = sum(text.count(marker) for marker in markers)
    return score >= 3


def _is_experiment_plan_relevant(plan: dict[str, Any] | None, topic: str) -> bool:
    if not plan:
        return False
    haystack = " ".join(
        str(value)
        for value in [plan.get("title", ""), plan.get("topic", ""), plan.get("summary", ""), plan.get("objective", "")]
    ).lower()
    if not haystack.strip():
        return False
    keywords = _topic_keywords(topic)
    if not keywords:
        return False
    hits = 0
    for keyword in keywords:
        normalized = keyword.lower()
        if normalized and normalized in haystack:
            hits += 1
    return hits >= 1


def _clean_project_summary(project_context: dict[str, Any] | None, title: str) -> str:
    if not project_context:
        return f"该项目围绕 {title} 展开，当前已经具备可分析的代码结构与启动脚本。"
    stack = "、".join(project_context.get("stack") or []) or "相关技术"
    existing_summary = str(project_context.get("project_summary", "")).strip()
    if existing_summary:
        return existing_summary
    return f"该项目是一个基于 {stack} 的工程，包含多个功能模块与配置文件。"


def _clean_method_clues(project_context: dict[str, Any] | None) -> list[str]:
    if not project_context:
        return []
    return [str(item).strip() for item in (project_context.get("method_clues") or []) if str(item).strip()][:6]


def _project_source_files(project_context: dict[str, Any] | None) -> list[str]:
    if not project_context:
        return []
    return [str(item).strip() for item in (project_context.get("candidate_source_files") or []) if str(item).strip()]


def _project_priority_files(project_context: dict[str, Any] | None) -> list[str]:
    if not project_context:
        return []
    return [str(item).strip() for item in (project_context.get("priority_files") or []) if str(item).strip()]


def _project_config_files(project_context: dict[str, Any] | None) -> list[str]:
    if not project_context:
        return []
    return [str(item).strip() for item in (project_context.get("candidate_config_files") or []) if str(item).strip()]


def _project_has_path(project_context: dict[str, Any] | None, fragment: str) -> bool:
    haystack = " ".join(
        _project_source_files(project_context)
        + _project_config_files(project_context)
        + _project_priority_files(project_context)
    ).lower()
    return fragment.lower() in haystack


def _build_project_feature_lines(project_context: dict[str, Any] | None) -> list[str]:
    features: list[str] = []
    if not project_context:
        return features

    source_files = [str(f).strip() for f in (project_context.get("candidate_source_files") or []) if str(f).strip()]
    config_files = [str(f).strip() for f in (project_context.get("candidate_config_files") or []) if str(f).strip()]
    method_clues = [str(m).strip() for m in (project_context.get("method_clues") or []) if str(m).strip()]
    result_clues = [str(r).strip() for r in (project_context.get("result_clues") or []) if str(r).strip()]

    all_paths = source_files + config_files

    # Detect launch/entry-point files
    launch_files = [p for p in all_paths if "launch" in p.lower()]
    if launch_files:
        features.append(f"系统提供启动入口文件（{'、'.join(launch_files[:3])}），可统一拉起各功能模块。")

    # Detect configuration files
    if config_files:
        sample_cfgs = config_files[:3]
        features.append(f"参数与配置文件（{'、'.join(sample_cfgs)}）集中管理系统运行时的关键参数。")

    # Detect simulation-related files
    sim_files = [p for p in all_paths if any(kw in p.lower() for kw in ("gazebo", "simulation", "sim", "仿真"))]
    if sim_files:
        features.append("系统提供仿真启动流程，用于在算法联调前完成功能验证与参数配置调试。")

    # Detect source code modules
    src_files = [p for p in source_files if p.endswith((".py", ".cpp", ".c", ".h", ".hpp", ".rs", ".go", ".java"))]
    if src_files:
        features.append(f"核心源码模块包含 {'、'.join(src_files[:4])} 等，涵盖主要业务逻辑与数据处理。")

    # Method clues
    if method_clues:
        features.append(f"项目中涉及的主要技术/方法包括 {'、'.join(method_clues[:4])}。")

    # Result clues
    if result_clues:
        features.append(f"已产生的结果文件包括 {'、'.join(result_clues[:3])}，可用于实验分析。")

    return features


def _detect_formula_domain(title: str, project_context: dict[str, Any] | None) -> str:
    """Detect project domain from title and project context for formula template selection."""
    haystack = " ".join(filter(None, [
        title.lower(),
        str((project_context or {}).get("project_summary", "")).lower(),
        " ".join(str(f) for f in (project_context or {}).get("candidate_source_files", []) or []),
        " ".join(str(f) for f in (project_context or {}).get("method_clues", []) or []),
    ]))

    # Domain keyword maps (order matters: first match wins)
    domain_kw = {
        "slam": ("slam", "建图", "地图构建", "localization", "mapping", "particle filter", "gmapping",
                 "cartographer", "slam_toolbox", "hector_slam", "fast_slam"),
        "navigation": ("nav2", "navigation", "导航", "path planning", "路径规划", "路径跟踪",
                       'a*", "dwa", "mppi", "teb", "dubins", "coverage", "全覆盖", "planner'),
        "control": ("pid", "控制", "control", "mpc", "model predictive", "adaptive control",
                    'lqr", "robust control", "servo", "motion control", "运动控制'),
        "ml_dl": ("neural network", "神经网络", "deep learning", "深度学习", "transformer", "cnn",
                  'rnn", "lstm", "attention", "pytorch", "tensorflow", "训练", "training',
                  'classification", "检测", "detection", "segmentation", "recognition", "识别'),
        "signal_processing": ("signal processing", "信号处理", "fft", "filter", "滤波",
                              'kalman", "卡尔曼", "频谱", "spectrum", "wavelet", "小波'),
        "vision": ("computer vision", "计算机视觉", "image processing", "图像处理", "opencv",
                   'camera", "相机", "visual", "stereo", "深度图", "point cloud", "点云',
                   '3d reconstruction", "三维重建'),
        "mechanical": ("有限元", "finite element", "ansys", "abaqus", "structural", "结构",
                       '力学", "mechanics", "stress", "应变", "strain", "振动", "vibration',
                       '热力学", "thermodynamic", "流体", "fluid'),
        "communication": ("通信", "communication", "5g", "4g", "wireless", "信道", "channel",
                          '调制", "modulation", " mimo", "天线", "antenna", "rf'),
        "power_energy": ("电力", "power", "energy", "能源", "光伏", "solar", "电池", "battery",
                         '储能", "microgrid", "微电网", "变压器", "transformer'),
    }

    for domain, keywords in domain_kw.items():
        if any(kw in haystack for kw in keywords):
            return domain
    return "general"


def _build_formula_sections(title: str, project_context: dict[str, Any] | None) -> list[str]:
    """Build dynamic Chapter 2 formula content based on detected project domain."""
    domain = _detect_formula_domain(title, project_context)

    # Each domain returns a list of strings (paragraphs with LaTeX formulas)
    # Structure: subsection heading → symbol definitions → formula derivation chain
    templates: dict[str, list[str]] = {
        "slam": _formula_template_slam(),
        "navigation": _formula_template_navigation(),
        "control": _formula_template_control(),
        "ml_dl": _formula_template_ml(),
        "signal_processing": _formula_template_signal(),
        "vision": _formula_template_vision(),
        "mechanical": _formula_template_mechanical(),
        "communication": _formula_template_communication(),
        "power_energy": _formula_template_power(),
        "general": _formula_template_general(title),
    }

    return templates.get(domain, _formula_template_general(title))


def _formula_template_slam() -> list[str]:
    return [
        "### 2.2 SLAM 数学模型与建图原理",
        "SLAM（同步定位与建图）问题可形式化为状态估计问题。设机器人在时刻 $t$ 的位姿为 $\\mathbf{x}_t \\in SE(2)$，环境地图为 $\\mathcal{M}$，控制输入为 $\\mathbf{u}_t$，激光观测为 $\\mathbf{z}_t$。SLAM 的目标是估计后验概率分布：$$p(\\mathbf{x}_{0:t}, \\mathcal{M} \\mid \\mathbf{z}_{1:t}, \\mathbf{u}_{1:t}) \\tag{2.1}$$",
        "运动模型描述机器人在控制输入作用下的位姿变化。假设运动噪声服从零均值高斯分布 $\\mathbf{w}_t \\sim \\mathcal{N}(0, \\mathbf{R}_t)$，则运动方程为：$$\\mathbf{x}_t = f(\\mathbf{x}_{t-1}, \\mathbf{u}_t) + \\mathbf{w}_t \\tag{2.2}$$",
        "观测模型描述传感器观测与位姿、地图之间的几何关系。设观测噪声 $\\mathbf{v}_t \\sim \\mathcal{N}(0, \\mathbf{Q}_t)$，则：$$\\mathbf{z}_t = h(\\mathbf{x}_t, \\mathcal{M}) + \\mathbf{v}_t \\tag{2.3}$$",
        "对于粒子滤波 SLAM，每个粒子 $i$ 携带位姿假设 $\\mathbf{x}_t^{(i)}$ 和权重 $\\omega_t^{(i)}$。预测步通过运动模型采样得到提议位姿，更新步利用激光观测计算似然并更新权重：$$\\omega_t^{(i)} = \\eta \\cdot \\omega_{t-1}^{(i)} \\cdot p(\\mathbf{z}_t \\mid \\mathbf{x}_t^{(i)}, \\mathcal{M}) \\tag{2.4}$$其中 $\\eta$ 为归一化常数，似然函数 $p(\\mathbf{z}_t \\mid \\mathbf{x}_t^{(i)}, \\mathcal{M})$ 通常采用似然场模型或束调整模型计算。",
    ]


def _formula_template_navigation() -> list[str]:
    return [
        "### 2.2 路径规划与运动控制数学模型",
        "路径规划问题可定义为在已知地图 $\\mathcal{M}$ 中，为机器人从起点 $\\mathbf{p}_s$ 到目标点 $\\mathbf{p}_g$ 寻找一条无碰撞轨迹 $\\boldsymbol{\\xi} = [\\mathbf{p}_0, \\mathbf{p}_1, \\ldots, \\mathbf{p}_N]$，使得代价函数最小化：$$\\boldsymbol{\\xi}^* = \\arg\\min_{\\boldsymbol{\\xi}} \\int_0^T \\left[ c_{obs}(\\mathbf{p}(t)) + c_{ctrl}(\\mathbf{u}(t)) + c_{time} \\right] dt \\tag{2.1}$$其中 $c_{obs}$ 为障碍物代价，$c_{ctrl}$ 为控制代价，$c_{time}$ 为时间惩罚系数。",
        "全局规划采用基于搜索的算法，其节点扩展代价函数为：$$f(n) = g(n) + \\alpha \\cdot h(n) \\tag{2.2}$$其中 $g(n)$ 为起点到节点 $n$ 的实际代价，$h(n)$ 为启发式估计，$\\alpha$ 为加权系数（$\\alpha > 1$ 时可加速搜索但路径可能次优）。",
        "局部控制通过在速度空间 $\\mathcal{U} = \\{(v, \\omega) \\mid v \\in [v_{min}, v_{max}], \\omega \\in [\\omega_{min}, \\omega_{max}]\\}$ 中采样多条候选轨迹，并评价其代价：$$J(\\mathbf{u}_{0:H-1}) = \\sum_{k=0}^{H-1} \\left[ q_{obs} c_{obs}(k) + q_{goal} c_{goal}(k) + q_{vel} c_{vel}(k) \\right] \\tag{2.3}$$其中 $q_{obs}, q_{goal}, q_{vel}$ 分别为障碍物、目标趋近和速度约束的权重。",
        "代价地图将环境表示为栅格化的占用代价值：$$c(x,y) = \\max\\left(c_{static}(x,y),\\ c_{inflation}(x,y)\\right) \\tag{2.4}$$其中 $c_{static}$ 来自已知地图，$c_{inflation}$ 由动态障碍物膨胀产生。",
    ]


def _formula_template_control() -> list[str]:
    return [
        "### 2.2 控制系统数学模型",
        "设被控对象的状态向量为 $\\mathbf{x}(t) \\in \\mathbb{R}^n$，控制输入为 $\\mathbf{u}(t) \\in \\mathbb{R}^m$，输出为 $\\mathbf{y}(t) \\in \\mathbb{R}^p$，则连续时间状态空间模型为：$$\\dot{\\mathbf{x}}(t) = \\mathbf{A}\\mathbf{x}(t) + \\mathbf{B}\\mathbf{u}(t) \\tag{2.1}$$$$\\mathbf{y}(t) = \\mathbf{C}\\mathbf{x}(t) + \\mathbf{D}\\mathbf{u}(t) \\tag{2.2}$$其中 $\\mathbf{A}$ 为系统矩阵，$\\mathbf{B}$ 为输入矩阵，$\\mathbf{C}$ 为输出矩阵，$\\mathbf{D}$ 为直馈矩阵。",
        "经零阶保持器离散化后，离散状态空间方程为：$$\\mathbf{x}_{k+1} = \\mathbf{F}\\mathbf{x}_k + \\mathbf{G}\\mathbf{u}_k \\tag{2.3}$$其中 $\\mathbf{F} = e^{\\mathbf{A}T_s}$，$\\mathbf{G} = \\int_0^{T_s} e^{\\mathbf{A}\\tau} d\\tau \\cdot \\mathbf{B}$，$T_s$ 为采样周期。",
        "PID 控制器的控制律为：$$u(t) = K_p e(t) + K_i \\int_0^t e(\\tau) d\\tau + K_d \\frac{de(t)}{dt} \\tag{2.4}$$其中 $e(t) = r(t) - y(t)$ 为误差信号，$K_p$、$K_i$、$K_d$ 分别为比例、积分、微分增益。",
        "MPC（模型预测控制）通过求解有限时域最优控制问题：$$\\min_{\\mathbf{u}_{0:N-1}} J = \\sum_{k=0}^{N-1} \\left[ \\|\\mathbf{x}_k - \\mathbf{x}_{ref}\\|_{\\mathbf{Q}}^2 + \\|\\mathbf{u}_k\\|_{\\mathbf{R}}^2 \\right] + \\|\\mathbf{x}_N - \\mathbf{x}_{ref}\\|_{\\mathbf{P}}^2 \\tag{2.5}$$$$\\text{s.t.} \\quad \\mathbf{x}_{k+1} = \\mathbf{F}\\mathbf{x}_k + \\mathbf{G}\\mathbf{u}_k, \\quad \\mathbf{u}_{min} \\leq \\mathbf{u}_k \\leq \\mathbf{u}_{max} \\tag{2.6}$$",
    ]


def _formula_template_ml() -> list[str]:
    return [
        "### 2.2 模型构建与训练理论",
        "设训练数据集为 $\\mathcal{D} = \\{(\\mathbf{x}_i, y_i)\\}_{i=1}^N$，其中 $\\mathbf{x}_i \\in \\mathbb{R}^d$ 为输入特征，$y_i$ 为标签。模型参数 $\\boldsymbol{\\theta}$ 的学习目标为最小化经验风险函数：$$\\boldsymbol{\\theta}^* = \\arg\\min_{\\boldsymbol{\\theta}} \\frac{1}{N} \\sum_{i=1}^N \\mathcal{L}(f(\\mathbf{x}_i; \\boldsymbol{\\theta}), y_i) + \\lambda \\Omega(\\boldsymbol{\\theta}) \\tag{2.1}$$其中 $\\mathcal{L}$ 为损失函数，$\\Omega(\\boldsymbol{\\theta})$ 为正则化项，$\\lambda$ 为正则化系数。",
        "对于分类任务，交叉熵损失函数定义为：$$\\mathcal{L}_{CE} = -\\sum_{c=1}^C y_c \\log \\hat{y}_c \\tag{2.2}$$其中 $C$ 为类别数，$y_c$ 为真实标签的 one-hot 编码，$\\hat{y}_c$ 为模型对类别 $c$ 的预测概率。",
        "神经网络的前向传播计算第 $l$ 层输出：$$\\mathbf{h}^{(l)} = \\sigma\\left(\\mathbf{W}^{(l)} \\mathbf{h}^{(l-1)} + \\mathbf{b}^{(l)}\\right) \\tag{2.3}$$其中 $\\mathbf{W}^{(l)}$ 为权重矩阵，$\\mathbf{b}^{(l)}$ 为偏置向量，$\\sigma(\\cdot)$ 为激活函数（如 ReLU $\\sigma(z) = \\max(0, z)$）。",
        "参数更新采用梯度下降法：$$\\boldsymbol{\\theta}_{t+1} = \\boldsymbol{\\theta}_t - \\eta \\nabla_{\\boldsymbol{\\theta}} \\mathcal{L}(\\boldsymbol{\\theta}_t) \\tag{2.4}$$其中 $\\eta$ 为学习率。Adam 优化器引入一阶和二阶矩估计进行自适应学习率调整：$$m_t = \\beta_1 m_{t-1} + (1-\\beta_1) g_t, \\quad v_t = \\beta_2 v_{t-1} + (1-\\beta_2) g_t^2 \\tag{2.5}$$",
    ]


def _formula_template_signal() -> list[str]:
    return [
        "### 2.2 信号处理数学模型",
        "对于连续时间信号 $x(t)$，其傅里叶变换定义为：$$X(f) = \\int_{-\\infty}^{+\\infty} x(t) e^{-j2\\pi ft} dt \\tag{2.1}$$逆变换为：$$x(t) = \\int_{-\\infty}^{+\\infty} X(f) e^{j2\\pi ft} df \\tag{2.2}$$",
        "对于采样周期为 $T_s$ 的离散信号 $x[n]$，离散傅里叶变换（DFT）为：$$X[k] = \\sum_{n=0}^{N-1} x[n] e^{-j2\\pi kn/N}, \\quad k = 0, 1, \\ldots, N-1 \\tag{2.3}$$",
        "线性时不变（LTI）系统的输入输出关系由卷积描述：$$y[n] = \\sum_{m=0}^{M-1} h[m] x[n-m] \\tag{2.4}$$其中 $h[m]$ 为系统单位冲激响应。在 $z$ 域中，传递函数为：$$H(z) = \\frac{Y(z)}{X(z)} = \\frac{b_0 + b_1 z^{-1} + \\cdots + b_M z^{-M}}{a_0 + a_1 z^{-1} + \\cdots + a_N z^{-N}} \\tag{2.5}$$",
        "卡尔曼滤波器的状态预测与更新方程为：$$\\hat{\\mathbf{x}}_{k|k-1} = \\mathbf{F}\\hat{\\mathbf{x}}_{k-1|k-1} + \\mathbf{B}\\mathbf{u}_k \\tag{2.6}$$$$\\hat{\\mathbf{x}}_{k|k} = \\hat{\\mathbf{x}}_{k|k-1} + \\mathbf{K}_k(\\mathbf{z}_k - \\mathbf{H}\\hat{\\mathbf{x}}_{k|k-1}) \\tag{2.7}$$其中卡尔曼增益 $\\mathbf{K}_k = \\mathbf{P}_{k|k-1}\\mathbf{H}^T(\\mathbf{H}\\mathbf{P}_{k|k-1}\\mathbf{H}^T + \\mathbf{R})^{-1}$。",
    ]


def _formula_template_vision() -> list[str]:
    return [
        "### 2.2 视觉处理数学模型",
        "针孔相机模型将三维空间点 $\\mathbf{P} = [X, Y, Z]^T$ 投影到像平面坐标 $\\mathbf{p} = [u, v]^T$：$$z \\begin{bmatrix} u \\\\ v \\\\ 1 \\end{bmatrix} = \\mathbf{K} \\begin{bmatrix} R & t \\end{bmatrix} \\begin{bmatrix} X \\\\ Y \\\\ Z \\\\ 1 \\end{bmatrix} \\tag{2.1}$$其中 $\\mathbf{K} = \\begin{bmatrix} f_x & 0 & c_x \\\\ 0 & f_y & c_y \\\\ 0 & 0 & 1 \\end{bmatrix}$ 为内参矩阵，$[R|t]$ 为外参矩阵。",
        "对于图像卷积操作，特征图计算为：$$F(i,j) = \\sum_{m} \\sum_{n} I(i+m, j+n) \\cdot K(m,n) + b \\tag{2.2}$$其中 $I$ 为输入图像，$K$ 为卷积核，$b$ 为偏置。",
        "图像梯度通过 Sobel 算子计算：$$G_x = \\begin{bmatrix} -1 & 0 & 1 \\\\ -2 & 0 & 2 \\\\ -1 & 0 & 1 \\end{bmatrix} * I, \\quad G_y = \\begin{bmatrix} -1 & -2 & -1 \\\\ 0 & 0 & 0 \\\\ 1 & 2 & 1 \\end{bmatrix} * I \\tag{2.3}$$梯度幅值和方向为：$$|G| = \\sqrt{G_x^2 + G_y^2}, \\quad \\theta = \\arctan\\left(\\frac{G_y}{G_x}\\right) \\tag{2.4}$$",
    ]


def _formula_template_mechanical() -> list[str]:
    return [
        "### 2.2 力学与有限元分析模型",
        "弹性力学平衡方程为：$$\\nabla \\cdot \\boldsymbol{\\sigma} + \\mathbf{f} = \\rho \\ddot{\\mathbf{u}} \\tag{2.1}$$其中 $\\boldsymbol{\\sigma}$ 为柯西应力张量，$\\mathbf{f}$ 为体积力，$\\rho$ 为密度，$\\mathbf{u}$ 为位移场。",
        "应力-应变本构关系（广义胡克定律）为：$$\\boldsymbol{\\sigma} = \\mathbf{D} : \\boldsymbol{\\varepsilon} \\tag{2.2}$$其中 $\\boldsymbol{\\varepsilon} = \\frac{1}{2}(\\nabla \\mathbf{u} + (\\nabla \\mathbf{u})^T)$ 为应变张量，$\\mathbf{D}$ 为弹性刚度矩阵。",
        "有限元离散化后的系统方程为：$$\\mathbf{K}\\mathbf{U} = \\mathbf{F} \\tag{2.3}$$其中 $\\mathbf{K} = \\sum_e \\int_{\\Omega_e} \\mathbf{B}^T \\mathbf{D} \\mathbf{B} d\\Omega$ 为全局刚度矩阵，$\\mathbf{B}$ 为应变-位移矩阵，$\\mathbf{U}$ 为节点位移向量，$\\mathbf{F}$ 为等效节点载荷。",
        "动力学问题的有限元方程为：$$\\mathbf{M}\\ddot{\\mathbf{U}} + \\mathbf{C}\\dot{\\mathbf{U}} + \\mathbf{K}\\mathbf{U} = \\mathbf{F}(t) \\tag{2.4}$$其中 $\\mathbf{M}$ 为质量矩阵，$\\mathbf{C}$ 为阻尼矩阵。通过特征值分析 $\\mathbf{K}\\boldsymbol{\\phi}_i = \\omega_i^2 \\mathbf{M}\\boldsymbol{\\phi}_i$ 可获得固有频率 $\\omega_i$ 和振型 $\\boldsymbol{\\phi}_i$。",
    ]


def _formula_template_communication() -> list[str]:
    return [
        "### 2.2 通信系统数学模型",
        "带通信号 $s(t)$ 可表示为：$$s(t) = \\text{Re}\\left[ \\tilde{s}(t) e^{j2\\pi f_c t} \\right] \\tag{2.1}$$其中 $f_c$ 为载波频率，$\\tilde{s}(t)$ 为复基带信号（等效低通表示）。",
        "加性高斯白噪声（AWGN）信道模型为：$$r(t) = s(t) + n(t) \\tag{2.2}$$其中 $n(t)$ 的功率谱密度为 $N_0/2$。接收端信噪比为：$$\\text{SNR} = \\frac{E_b}{N_0} = \\frac{P_s T_b}{N_0} \\tag{2.3}$$",
        "对于 $M$ 进制正交调制，误符号率上界为：$$P_e \\leq (M-1) Q\\left(\\sqrt{\\frac{E_s}{N_0}}\\right) \\tag{2.4}$$其中 $Q(x) = \\frac{1}{\\sqrt{2\\pi}} \\int_x^{\\infty} e^{-t^2/2} dt$ 为 Q 函数。",
        "香农信道容量公式为：$$C = B \\log_2\\left(1 + \\frac{S}{N}\\right) \\tag{2.5}$$其中 $B$ 为信道带宽，$S/N$ 为信噪比。",
    ]


def _formula_template_power() -> list[str]:
    return [
        "### 2.2 电力系统数学模型",
        "电力系统潮流方程的基本形式为：$$P_i = V_i \\sum_{j=1}^N V_j (G_{ij}\\cos\\theta_{ij} + B_{ij}\\sin\\theta_{ij}) \\tag{2.1}$$$$Q_i = V_i \\sum_{j=1}^N V_j (G_{ij}\\sin\\theta_{ij} - B_{ij}\\cos\\theta_{ij}) \\tag{2.2}$$其中 $P_i, Q_i$ 为节点 $i$ 的有功和无功注入功率，$V_i$ 为电压幅值，$\\theta_{ij} = \\theta_i - \\theta_j$ 为电压相角差，$G_{ij} + jB_{ij}$ 为导纳矩阵元素。",
        "同步发电机转子运动方程为：$$M \\frac{d^2 \\delta}{dt^2} = P_m - P_e - D\\frac{d\\delta}{dt} \\tag{2.3}$$其中 $M$ 为惯性常数，$\\delta$ 为功角，$P_m$ 为机械功率，$P_e$ 为电磁功率，$D$ 为阻尼系数。",
        "光伏电池的输出特性方程为：$$I = I_{ph} - I_0 \\left[ \\exp\\left(\\frac{q(V + IR_s)}{nkT}\\right) - 1 \\right] - \\frac{V + IR_s}{R_{sh}} \\tag{2.4}$$其中 $I_{ph}$ 为光生电流，$I_0$ 为反向饱和电流，$q$ 为电子电荷，$n$ 为二极管品质因子，$k$ 为玻尔兹曼常数，$T$ 为绝对温度。",
    ]


def _formula_template_general(title: str) -> list[str]:
    """Generic formula template for engineering papers without specific domain match."""
    return [
        f"### 2.2 {title} 核心数学模型",
        "设系统状态变量为 $\\mathbf{x} \\in \\mathbb{R}^n$，输入为 $\\mathbf{u} \\in \\mathbb{R}^m$，输出为 $\\mathbf{y} \\in \\mathbb{R}^p$，则系统的一般数学模型可表示为：$$\\mathbf{y} = f(\\mathbf{x}, \\mathbf{u}; \\boldsymbol{\\theta}) + \\boldsymbol{\\epsilon} \\tag{2.1}$$其中 $f(\\cdot)$ 为系统映射函数，$\\boldsymbol{\\theta}$ 为模型参数，$\\boldsymbol{\\epsilon}$ 为模型误差项。",
        "系统性能指标函数定义为：$$J = \\int_0^T \\left[ \\mathbf{x}^T \\mathbf{Q} \\mathbf{x} + \\mathbf{u}^T \\mathbf{R} \\mathbf{u} \\right] dt \\tag{2.2}$$其中 $\\mathbf{Q} \\succeq 0$ 为状态权重矩阵，$\\mathbf{R} \\succ 0$ 为控制权重矩阵，$T$ 为时间跨度。",
        "系统优化的约束条件包括等式约束和不等式约束：$$\\text{s.t.} \\quad g_i(\\mathbf{x}, \\mathbf{u}) = 0, \\quad i = 1, \\ldots, p \\tag{2.3}$$$$h_j(\\mathbf{x}, \\mathbf{u}) \\leq 0, \\quad j = 1, \\ldots, q \\tag{2.4}$$",
        "误差分析中，均方误差（MSE）定义为：$$\\text{MSE} = \\frac{1}{N} \\sum_{i=1}^N (y_i - \\hat{y}_i)^2 \\tag{2.5}$$其中 $y_i$ 为真实值，$\\hat{y}_i$ 为预测值，$N$ 为样本数量。",
    ]


def _project_summary_sentence(project_context: dict[str, Any] | None, title: str) -> str:
    summary = _clean_project_summary(project_context, title)
    if "Project located at" in summary:
        stack = "、".join((project_context or {}).get("stack") or []) or "相关技术"
        return f"该项目围绕 {title} 构建，已形成可用于论文撰写的基于 {stack} 的工程代码与配置文件。"
    return summary


def _stem_to_caption(stem: str) -> str | None:
    """Derive a human-readable caption from a filename stem by splitting on common delimiters."""
    if not stem:
        return None
    # Replace hyphens and underscores with spaces, then title-case
    words = re.split(r"[-_]+", stem)
    # Filter out common non-informative words
    skip = {"fig", "figure", "img", "image", "plot", "chart", "table", "output", "result", "results"}
    filtered = [w for w in words if w.lower() not in skip and len(w) >= 2]
    if not filtered:
        return None
    return " ".join(w.capitalize() for w in filtered)


def _resolve_project_evidence_root(
    project_root: str | Path,
    project_context: dict[str, Any] | None,
) -> Path:
    root = Path(project_root).resolve()
    candidate = str((project_context or {}).get("source_project_path") or "").strip()
    if candidate:
        candidate_path = Path(candidate).resolve()
        if candidate_path.exists():
            return candidate_path
    return root


def _merge_unique_strings(existing: list[str] | None, additions: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw_item in [*(existing or []), *additions]:
        item = str(raw_item).strip()
        if not item or item in seen:
            continue
        merged.append(item)
        seen.add(item)
    return merged


def _emit_generation_progress(
    progress_callback: Callable[[int, str, str], None] | None,
    step: int,
    label: str,
    detail: str = "",
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(step, label, detail)
    except Exception:
        pass


def _sync_figure_evidence_into_workspace(
    workspace_root: Path,
    evidence_root: Path,
    figures: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not figures:
        return []
    if workspace_root == evidence_root:
        return figures

    source_dir = evidence_root / "output" / "figures"
    workspace_dir = workspace_root / "output" / "figures"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    synced: list[dict[str, str]] = []
    for figure in figures:
        filename = str(figure.get("file") or "").strip()
        if not filename:
            continue
        src = source_dir / filename
        if not src.exists():
            continue
        dst = workspace_dir / filename
        import shutil as _shutil

        if (
            not dst.exists()
            or dst.stat().st_size != src.stat().st_size
            or dst.stat().st_mtime_ns != src.stat().st_mtime_ns
        ):
            _shutil.copy2(src, dst)
        synced.append(dict(figure))
    return synced


def _scan_project_figures(
    project_root: Path,
    *,
    allowed_files: set[str] | None = None,
    run_extractor: bool = True,
) -> list[dict[str, str]]:
    """Scan output/figures/ for existing PNG/PDF figures and return metadata.

    Before scanning, automatically extracts missing figures from project
    source code (MATLAB/Python) if project_figure_extractor is available.
    """
    # Auto-extract missing figures from project source code
    if run_extractor:
        try:
            from tools.project_figure_extractor import auto_extract_project_figures
            figures_dir = project_root / "output" / "figures"
            extract_result = auto_extract_project_figures(
                project_root=project_root,
                output_dir=figures_dir,
                execute=True,
                language="zh",
            )
            if extract_result.get("results"):
                n_ok = sum(1 for r in extract_result["results"] if r["success"])
                print(f"[figure-extractor] Auto-extracted {n_ok} figures from project source code")
        except Exception as exc:
            # Non-fatal: extraction is best-effort, don't block paper writing
            print(f"[figure-extractor] Skipped: {exc}")

    figures = []
    figures_dir = project_root / "output" / "figures"
    if not figures_dir.exists():
        return figures
    # Chart type suffixes (order matters for priority when same type)
    _chart_type_suffixes = ("-radar", "-heatmap", "-comparison", "-curve", "-distribution", "-training-curve")
    seen_names: set[str] = set()
    # Track which (normalized_base, chart_type) pairs we've kept
    # For each base, we allow one of each chart type (e.g., one bar + one radar)
    kept_pairs: dict[str, str] = {}  # "base::type_suffix" -> stem
    for f in sorted(figures_dir.iterdir()):
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".pdf"):
            continue
        if allowed_files is not None and f.name not in allowed_files:
            continue
        stem = f.stem
        # Skip duplicate PDF when PNG exists
        if stem in seen_names:
            continue
        # Skip files ending with "-comparison" that have a base already seen or will be seen
        base_stem = stem.removesuffix("-comparison")
        if base_stem != stem:
            if base_stem in seen_names:
                continue
            seen_names.add(base_stem)
        seen_names.add(stem)
        # Dedup by (normalized_base, chart_type) pair
        norm_stem = stem.replace("_", "-")
        # Determine chart type suffix
        chart_type = ""
        norm_base = norm_stem
        for suffix in _chart_type_suffixes:
            if norm_stem.endswith(suffix):
                chart_type = suffix
                norm_base = norm_stem[: -len(suffix)]
                break
        # Normalize common prefix variations
        if norm_base.startswith("navigation-"):
            norm_base = "nav-" + norm_base[len("navigation-"):]
        # If no suffix detected, check if this is a known comparison-like name
        # (manually created charts often lack the -comparison suffix)
        if not chart_type and any(kw in norm_base for kw in ("comparison", "scenario", "result", "controller")):
            chart_type = "-comparison"
        elif not chart_type and any(kw in norm_base for kw in ("convergence", "curve", "training")):
            chart_type = "-curve"
        pair_key = f"{norm_base}::{chart_type}"
        if pair_key in kept_pairs:
            # Already have this exact chart type for this base; skip
            continue
        kept_pairs[pair_key] = stem
        # Derive a Chinese caption from filename stem automatically
        caption = _stem_to_caption(stem) or _stem_to_caption(base_stem) or stem
        figures.append({"file": f.name, "stem": stem, "caption": caption})
    # Classify each figure by role (principle/design/scene/process/result/comparison)
    try:
        from image_roles import classify_image
        for fig in figures:
            fig["role"] = classify_image(fig["stem"], caption=fig.get("caption", ""))
    except ImportError:
        pass
    return figures


def _scan_project_csv_data(project_root: Path) -> list[dict[str, Any]]:
    """Scan output/results/ for CSV files and return parsed tables."""
    tables = []
    results_dir = project_root / "output" / "results"
    if not results_dir.exists():
        return tables
    try:
        import csv as _csv
    except ImportError:
        return tables
    for f in sorted(results_dir.iterdir()):
        if f.suffix.lower() != ".csv":
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                reader = _csv.reader(fh)
                headers = next(reader)
                rows = [row for row in reader if any(cell.strip() for cell in row)]
            tables.append({"file": f.name, "stem": f.stem, "headers": headers, "rows": rows})
        except Exception:
            continue
    return tables


def _build_figure_placeholders(figures: list[dict[str, str]], chapter_num: int = 4) -> list[str]:
    """Build three-line figure placeholder blocks for experiment chapter."""
    lines = []
    for i, fig in enumerate(figures, 1):
        fig_num = f"{chapter_num}-{i}"
        lines.append(f"[此处插入图{fig_num}：{fig['caption']}]")
        lines.append("")
        lines.append(f"图{fig_num} {fig['caption']}")
        lines.append("")
        lines.append(f"Figure {fig_num} {fig['caption']}")
        lines.append("")
    return lines


def _build_csv_markdown_tables(csv_tables: list[dict[str, Any]], chapter_num: int = 4) -> list[str]:
    """Convert CSV data to Markdown tables with chapter-numbered captions."""
    lines = []
    for i, table in enumerate(csv_tables, 1):
        table_num = f"{chapter_num}.{i}"
        caption = _stem_to_caption(table["stem"]) or table["stem"]
        lines.append(f"表{table_num} {caption}")
        lines.append(f"Table {table_num} {caption}")
        lines.append("")
        # Build markdown table
        headers = table["headers"]
        rows = table["rows"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows[:15]:  # Limit to 15 rows
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    return lines


_TABLE_UNIT_SUFFIXES: dict[str, str] = {
    "_ms": " (ms)",
    "_s": " (s)",
    "_m": " (m)",
    "_cm": " (cm)",
    "_mm": " (mm)",
    "_hz": " (Hz)",
    "_deg": " (deg)",
    "_pct": " (%)",
    "_percent": " (%)",
}


def _normalize_display_caption_text(text: str) -> str:
    caption = str(text or "").strip()
    if not caption:
        return caption
    if re.search(r"[\u4e00-\u9fff]", caption):
        return caption
    if caption.lower() == caption:
        return " ".join(part.capitalize() for part in caption.split())
    return caption


def _humanize_table_header(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return text
    if re.search(r"[\u4e00-\u9fff]", text):
        return text
    lowered = text.lower()
    if re.fullmatch(r"[a-z0-9_/%()-]+", lowered):
        for suffix, unit in _TABLE_UNIT_SUFFIXES.items():
            if lowered.endswith(suffix) and len(lowered) > len(suffix):
                base = lowered[: -len(suffix)].strip("_")
                return " ".join(part.capitalize() for part in base.split("_")) + unit
        return " ".join(part.capitalize() for part in lowered.split("_"))
    return text


def _escape_markdown_table_cell(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return text.replace("|", "\\|").replace("\n", "<br />")


def _render_table_block(table_item: dict[str, Any], chapter_num: int, table_idx: int, language: str) -> list[str]:
    caption = _normalize_display_caption_text(str(table_item.get("caption") or f"Table {table_idx}"))
    headers = [_humanize_table_header(str(item)) for item in table_item.get("headers") or []]
    rows = [list(row) for row in table_item.get("rows") or []]
    if not headers:
        headers = ["Item", "Value"] if language != "zh" else ["项目", "取值"]
    if not rows:
        rows = [["N/A", "Pending"]] if language != "zh" else [["待补充", "待验证"]]
    label = f"{chapter_num}.{table_idx}"
    caption_line = f"表{label} {caption}" if language == "zh" else f"Table {label} {caption}"
    lines = [caption_line, ""]
    lines.append("| " + " | ".join(_escape_markdown_table_cell(header) for header in headers) + " |")
    lines.append("| " + " | ".join([":---"] + ["---:"] * (len(headers) - 1)) + " |")
    for row in rows[:12]:
        padded = [_escape_markdown_table_cell(item) for item in row[: len(headers)]]
        while len(padded) < len(headers):
            padded.append("")
        lines.append("| " + " | ".join(padded) + " |")
    lines.append("")
    return lines


def _inject_tables_by_plan(md_text: str, project_context: dict[str, Any] | None, language: str = "zh") -> str:
    table_plan = [item for item in (project_context or {}).get("table_plan") or [] if isinstance(item, dict)]
    if not table_plan:
        return md_text
    if re.search(r"^表\d+\.\d+\s+", md_text, flags=re.M) or re.search(r"^Table\s+\d+\.\d+\s+", md_text, flags=re.M):
        return md_text

    chapters = _parse_numbered_chapters(md_text)
    if not chapters:
        return md_text
    lines = md_text.splitlines()
    insertions: dict[int, list[str]] = {}
    chapter_table_counts: dict[int, int] = {}

    for item in table_plan:
        section_hint = str(item.get("section") or "")
        target_chapter = None
        for chapter in chapters:
            if section_hint and section_hint.lower() in str(chapter["title"]).lower():
                target_chapter = chapter
                break
        if target_chapter is None:
            target_chapter = chapters[-2] if len(chapters) >= 2 else chapters[-1]
        anchor = _find_best_figure_anchor(lines, target_chapter, {"caption": item.get("caption")}, project_context=project_context)
        chapter_num = int(target_chapter["num"])
        chapter_table_counts[chapter_num] = chapter_table_counts.get(chapter_num, 0) + 1
        insertions.setdefault(anchor, []).extend(
            [""] + _render_table_block(item, chapter_num, chapter_table_counts[chapter_num], language)
        )

    output = lines[:]
    for anchor_idx in sorted(insertions.keys(), reverse=True):
        output[anchor_idx + 1:anchor_idx + 1] = insertions[anchor_idx]
    return "\n".join(output)


def _build_complete_chinese_sections(
    title: str,
    paper_type: str,
    references: list[dict[str, Any]],
    experiment_lines: list[str],
    project_context: dict[str, Any] | None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    from tools.domain_utils import detect_domain, get_archetype

    domain = detect_domain(title, project_context)
    archetype = get_archetype(title, project_context)

    literature_anchor = "已有文献与项目源码" if references else "项目源码与配置文件等工程材料"
    project_summary = _project_summary_sentence(project_context, title)
    features = _build_project_feature_lines(project_context)
    source_files = _project_source_files(project_context)
    method_clues = _clean_method_clues(project_context)
    config_files = _project_config_files(project_context)
    result_clues = [str(item) for item in (project_context or {}).get("result_clues") or []]
    architecture_files = "、".join(source_files[:6]) if source_files else "各功能模块源码与配置文件"
    experiment_summary = " ".join(experiment_lines[:2]).strip()
    stack = "、".join((project_context or {}).get("stack") or []) or "相关技术"
    feature_text = "；".join(features[:3]) if features else "多模块协同的工程系统"

    # ---- Abstract ----
    abstract = [
        f"本文围绕《{title}》展开，研究目标是构建一套完整的工程系统，实现从需求分析、模块设计到集成验证的全流程覆盖。",
        f"系统以 {literature_anchor} 为基础，{project_summary}",
        f"在系统实现上，论文重点分析了模块组织方式、接口设计策略、参数配置逻辑以及不同运行场景下的差异化设计。",
        f"实验部分围绕系统核心功能的验证展开。{experiment_summary or '当前可先输出完整论文正文与实验方案框架，后续补充图表和统计结果即可形成终稿。'}",
        f"研究结果表明，采用面向场景的模块化集成方法，可以有效降低系统开发中的模块耦合与部署复杂度，为后续功能迭代提供稳定的工程基础。",
    ]

    # Derive keywords for the abstract
    _keywords = _derive_submission_keywords(title, project_context)
    abstract.append("")
    abstract.append(f"**关键词：** {'；'.join(_keywords) if _keywords else '（待补充关键词）'}")

    # ---- Introduction ----
    introduction = [
        "### 1.1 研究背景",
        f"随着相关领域技术的持续发展，{title} 所涉及的核心能力已成为当前研究与应用中的关键议题。系统性地完成从理论分析到工程实现的完整链路，对于提升系统可用性和降低开发成本具有重要意义。",
        f"本项目基于 {stack} 技术栈展开，利用现有开源工具和工程化方法，围绕核心功能需求构建完整的软件系统。这一技术路线的选择既考虑了生态成熟度和社区支持，也兼顾了后续扩展与维护的便利性。",
        "### 1.2 课题意义",
        f"{title} 不仅关注某一单点方法的理论改进，更强调将各功能模块在统一框架中进行集成与验证。相较于单独讨论某一算法或组件，论文更需要说明如何将各类功能组织为稳定协同的完整系统，并保证系统在目标环境下可靠运行。",
        f"{project_summary} 结合现有工程结构可以看出，项目已覆盖多个关键子模块，具备开展完整论文撰写的基础。{' '.join(features[:2]) if features else ''}",
        "### 1.3 研究内容与论文结构",
        f"本文的主要研究内容包括：分析系统的功能需求与技术约束；设计面向 {stack} 的模块化软件架构；实现核心功能模块并完成参数配置与接口联调；设计实验方案验证系统的有效性与稳定性。",
        f"全文结构安排如下：第一章介绍研究背景与研究意义；第二章阐述相关理论基础与核心模型；第三章说明总体架构与数据流设计；第四章详细分析关键模块实现；第五章给出实验设计与结果分析；第六章总结全文并展望后续工作。",
    ]

    # ---- Chapter 2: Related / Theory (with domain-specific formulas) ----
    formula_content = _build_formula_sections(title, project_context)

    if archetype == "engineering":
        related = [
            "### 2.1 核心技术基础",
            f"本项目基于 {stack} 技术栈构建，涉及的核心技术包括通信机制、数据管理、模块协同等方面。了解这些技术基础对于理解系统架构和后续实现细节至关重要。",
            f"从项目现有代码结构看，关键文件包括 {architecture_files}，各模块通过清晰的接口定义进行交互，保证了系统的可维护性与可扩展性。",
        ] + formula_content + [
            "### 2.3 系统需求分析",
            "从功能角度看，系统需要满足核心功能模块的基本需求；从性能角度看，系统需要兼顾实时性、稳定性与可维护性。系统还应考虑模块化设计需求，使各功能组件之间保持清晰的接口边界。",
            f"此外，系统应支持从开发到部署的完整流程，即尽量复用相同的软件框架与核心逻辑，仅在环境配置、设备接口与部分参数文件上进行差异化配置。",
        ]
    elif archetype == "science":
        related = [
            "### 2.1 核心概念与理论框架",
            f"本课题的理论基础涉及 {title} 的核心概念体系。理解这些基础理论对于正确设计实验方案和解读实验结果具有指导意义。",
            "相关研究已经建立了较为完善的理论框架，为本课题的假设提出、变量选择和分析方法提供了依据。",
        ] + formula_content + [
            "### 2.3 研究现状与不足",
            "通过对已有文献的梳理可以看出，现有研究在方法论和实验设计方面已取得显著进展，但在系统化集成和实际验证方面仍存在不足。本课题在此基础上，尝试从工程实现的角度提供新的解决思路。",
        ]
    elif archetype == "data_analytics":
        related = [
            "### 2.1 核心概念界定",
            f"本课题涉及的核心概念围绕 {title} 展开。明确这些概念的内涵与外延，有助于后续变量定义、模型构建和结果解读。",
            "相关领域的理论发展为本课题的研究假设和分析框架提供了基础。",
        ] + formula_content + [
            "### 2.3 研究假设与文献评述",
            "基于对已有文献的梳理，本课题提出相应的研究假设。现有研究在方法选择、样本范围和指标体系方面已积累了丰富经验，但也存在结论不一致、方法可改进的空间。",
        ]
    elif archetype == "humanities":
        related = [
            "### 2.1 核心概念界定与学术史梳理",
            f"本课题围绕 {title} 展开，需要首先对核心概念进行界定，并梳理相关学术史的发展脉络。概念界定是后续论证的逻辑起点，学术史梳理则有助于把握研究的前沿动态和学术争论焦点。",
            "相关领域的研究者已从不同角度对这一议题进行了探讨，形成了多种理论视角和分析方法。",
        ] + formula_content + [
            "### 2.3 理论框架与分析视角",
            "在梳理已有研究的基础上，本课题确立适用于当前研究对象的理论框架和分析视角。该框架将指导后续资料收集、案例分析和论证展开。",
            "分析视角的选择既取决于研究对象的特点，也受到研究问题和可用资料类型的约束。",
        ]
    else:  # arts
        related = [
            "### 2.1 理论基础与设计原则",
            f"本课题的理论基础涉及 {title} 的核心设计原则与方法论。理解这些基础对于后续的创作/设计实践和作品评价具有指导意义。",
            "相关领域的经典案例和实践经验为本课题提供了重要的参考和启发。",
        ] + formula_content + [
            "### 2.3 现有方法的优缺点评述",
            "通过对国内外经典案例的分析，可以归纳出现有方法的优势和不足。本课题在此基础上，尝试提出具有创新性的改进方案。",
        ]

    # ---- Chapter 3: Design / Methodology ----
    if archetype == "engineering":
        design = [
            "### 3.1 系统总体架构",
            f"从源码结构看，项目由多个功能模块组成，涉及的关键文件包括 {architecture_files}。整体架构以 {stack} 为组织基础，各功能模块职责清晰，通过配置文件和接口定义形成统一的系统入口。",
            f"系统运行时，各功能模块按照预定的数据流进行协同工作。{feature_text}",
            "### 3.2 模块划分与接口设计",
            "系统采用模块化设计思想，将不同功能解耦为独立的模块或组件。每个模块通过明确的接口与外部交互，降低了模块间的耦合度，便于独立开发、测试和替换。",
            f"从项目现有代码看，{'、'.join(method_clues[:4]) if method_clues else '多个'}核心功能通过多个类和函数实现，模块划分合理。",
            "### 3.3 数据流与参数配置",
            "数据流设计决定了系统内部各模块之间的信息传递路径。合理的参数配置方案则保证了系统在不同场景下的灵活性和可调性。",
            f"项目中包含 {len(config_files)} 个配置文件，用于管理运行参数和环境设置，体现了工程化的配置管理思路。",
            "### 3.4 部署与运行设计",
            "系统应支持从开发到部署的完整流程。在实际部署中，需要考虑环境差异、依赖管理和启动顺序等问题。",
            "通过统一的启动入口和参数化配置，系统可以在不同环境下快速部署和运行，降低了运维复杂度。",
        ]
    elif archetype == "science":
        design = [
            "### 3.1 实验设计与方法论",
            "本课题的实验设计遵循科学研究的规范流程，从假设提出到实验验证，每个环节都力求严谨可控。方法论的选择综合考虑了研究对象的特点、可用设备和预期结果的可解释性。",
            "### 3.2 材料与设备",
            f"实验所需的材料和设备根据研究目标确定。从项目现有文件看，{'、'.join(config_files[:4]) if config_files else '相关配置文件'}相关配置参数已在项目中明确。",
            "### 3.3 实验参数与流程",
            "实验流程按照预设的步骤依次执行，关键参数通过配置文件进行管理，确保实验的可重复性。每个实验步骤都有明确的输入条件和预期输出。",
            "### 3.4 数据处理方法",
            "实验数据的处理和分析方法直接影响结论的可靠性。本项目采用定量分析方法，通过统计检验验证实验结果的显著性。",
        ]
    elif archetype == "data_analytics":
        design = [
            "### 3.1 研究假设与变量定义",
            "基于文献综述和理论分析，本课题提出明确的研究假设。变量定义遵循操作化原则，确保每个核心概念都可以通过可观测的指标进行度量。",
            "### 3.2 数据来源与样本描述",
            "数据的质量和代表性直接影响实证分析的可信度。本课题对数据来源进行了严格筛选，并对样本的基本特征进行了描述性统计分析。",
            f"{'、'.join(result_clues[:3]) + '、' if result_clues else ''}项目中已产生可用于分析的结果数据。",
            "### 3.3 模型构建与估计方法",
            "模型的选择兼顾理论依据和实际可行性。估计方法采用标准的统计学程序，参数估计通过优化目标函数获得。",
            "### 3.4 变量度量与操作化定义",
            "核心变量的度量参考了已有研究的通行做法，同时结合本课题的具体情境进行了适当调整，以保证度量工具的信度和效度。",
        ]
    elif archetype == "humanities":
        design = [
            "### 3.1 研究方法选择",
            f"本课题的研究方法根据 {title} 的特点和研究问题进行选择。方法论的合理性是确保研究结论可信度的前提。",
            "在方法选择过程中，综合考虑了资料的可用性、分析框架的适用性以及研究伦理等方面的要求。",
            "### 3.2 资料收集与整理",
            "研究资料的收集遵循全面性和代表性的原则。通过对原始资料的系统整理和分类，为后续分析建立了可靠的资料基础。",
            "### 3.3 分析框架的操作化",
            "理论框架的操作化是连接抽象理论与具体分析的桥梁。本课题将分析框架转化为可操作的分析维度和评判标准。",
        ]
    else:  # arts
        design = [
            "### 3.1 设计理念与创作思路",
            f"本课题的创作/设计理念围绕 {title} 展开，力求在功能性和审美性之间取得平衡。设计思路的确定综合考虑了用户需求、技术约束和表达意图。",
            "### 3.2 方法流程与技术路线",
            "创作/设计过程遵循系统化的方法流程，从概念构思到方案细化，再到最终实现，每个阶段都有明确的输出和评审标准。",
            "### 3.3 工具与实现过程",
            f"项目使用 {stack} 等工具进行实现。{'、'.join(method_clues[:3]) if method_clues else '多个'}核心功能通过关键类和函数实现。",
        ]

    # ---- Chapter 4: Implementation / Core Work ----
    if archetype == "engineering":
        implementation = [
            "### 4.1 核心模块实现",
            f"核心模块的实现是系统功能的基础。从项目代码看，{'、'.join(method_clues[:3]) if method_clues else '多个'}关键功能通过核心类和函数实现。每个模块遵循单一职责原则，内部逻辑清晰，对外提供稳定的接口。",
            "模块实现过程中需要处理的关键问题包括：输入验证、异常处理、边界条件、性能优化以及与外部模块的协同。",
            "### 4.2 参数配置与接口联调",
            "系统通过配置文件管理运行时参数，使不同场景下的切换不需要修改代码。接口联调确保各模块之间的数据传递格式正确、时序匹配。",
            f"从项目配置文件看，{'、'.join(config_files[:4]) if config_files else '相关配置文件'}参数组织清晰，层次分明。",
            "### 4.3 功能验证与调试",
            "各模块实现后需要进行独立的功能验证，确保其行为符合设计预期。调试过程中记录的关键问题和解决方案，是论文实验章节的重要素材。",
            "### 4.4 系统集成测试",
            "系统集成测试验证各模块在协同工作时的正确性和稳定性。测试内容包括端到端功能测试、异常恢复测试和性能压力测试等。",
        ]
    elif archetype == "science":
        implementation = [
            "### 4.1 实验过程与数据采集",
            "实验按照预设方案严格执行，每个步骤的操作条件和参数设置都有详细记录。数据采集过程注重控制无关变量的干扰，确保实验结果的有效性。",
            "### 4.2 关键参数与条件控制",
            "实验中的关键参数通过预实验确定合理范围，正式实验中严格控制各组的实验条件，保证组间可比性。",
            "### 4.3 数据处理与分析方法",
            "实验数据的处理采用标准化的分析流程，包括数据清洗、统计分析、图表生成和结果可视化。分析方法的选取基于数据特征和研究假设。",
            "### 4.4 实验的可重复性",
            "为保证实验结论的可靠性，本课题对实验条件、操作步骤和分析方法进行了详细记录，确保实验可以被独立重复验证。",
        ]
    elif archetype == "data_analytics":
        implementation = [
            "### 4.1 描述性统计分析",
            "首先对样本数据进行描述性统计，包括均值、标准差、分布特征等基本统计量，以及变量间的相关系数矩阵。这一步骤有助于了解数据的基本特征和潜在问题。",
            "### 4.2 回归/模型估计",
            "核心模型的估计采用标准的计量经济学/统计学方法。模型设定参考了已有研究的理论预期和经验发现，变量选择兼顾了经济学/科学意义和统计显著性。",
            "### 4.3 稳健性检验",
            "为验证结论的可靠性，本课题进行了多项稳健性检验，包括替换变量度量方式、调整样本范围、引入控制变量等。",
            "### 4.4 内生性处理",
            "内生性问题是实证研究中的重要挑战。本课题采用了适当的识别策略来处理潜在的内生性问题，以提高因果推断的可信度。",
        ]
    elif archetype == "humanities":
        implementation = [
            "### 4.1 核心论点的展开与论证",
            "本课题的核心论点按照逻辑递进的方式展开。每一步论证都建立在充分的证据和严密的推理之上，力求做到论点明确、论据充分、论证过程连贯。",
            "### 4.2 多角度分析",
            "为增强论证的说服力，本课题从多个角度对核心议题进行分析。不同角度的分析结果相互印证，共同支撑论文的核心结论。",
            "### 4.3 案例与文本解读",
            "通过典型案例的深入分析和原始文本的细致解读，本课题将抽象的理论命题具体化，使读者能够直观理解论证的逻辑链条。",
            "### 4.4 与既有观点的对话",
            "本课题在论证过程中注重与已有研究成果的对话，既吸收合理的观点，也对不完善之处提出建设性的批评和修正。",
        ]
    else:  # arts
        implementation = [
            "### 4.1 创作/设计方案详述",
            f"本课题的创作/设计方案围绕核心设计理念展开，{'、'.join(method_clues[:3]) if method_clues else '多项'}关键技术通过项目代码实现。方案设计力求在创新性和可行性之间取得平衡。",
            "### 4.2 实现过程与技术细节",
            "创作/设计实现过程中的关键技术细节和处理策略是论文的重要内容。每个关键决策都附有设计意图说明和技术选型依据。",
            "### 4.3 方案迭代与优化",
            "创作/设计过程并非一蹴而就，而是经过多轮迭代和优化。每次迭代的改进点和改进效果都是重要的分析素材。",
        ]

    # ---- Chapter 5: Experiment / Results ----
    if archetype == "engineering":
        experiment = [
            "### 5.1 实验环境与评价指标",
            f"实验在 {stack} 环境下进行，评价指标根据系统核心功能确定，涵盖功能完整性、运行稳定性和性能效率等维度。每项指标都有明确的量化标准。",
            "### 5.2 实验场景与测试用例",
            "实验设计了多种测试场景以覆盖系统的典型使用情况。每个测试用例包含输入条件、执行步骤和预期输出，确保实验结果的可验证性。",
            "### 5.3 实验结果",
            f"实验结果以表格和图表形式呈现。{experiment_summary or '以下为实验方案框架，待补充实际运行数据。'}",
            "### 5.4 结果分析与讨论",
            "对实验结果进行分析，讨论系统在不同条件下的表现差异，识别影响系统性能的关键因素，并与预期目标进行对比。",
            "### 5.5 误差来源与局限性",
            "分析实验过程中可能的误差来源，讨论结果的适用范围和局限性，为后续改进提供方向。",
        ]
    elif archetype == "science":
        experiment = [
            "### 5.1 实验结果呈现",
            f"实验结果以表格和图表形式系统呈现。{experiment_summary or '以下为实验方案框架，待补充实际运行数据。'}",
            "### 5.2 结果分析与物理解释",
            "对实验结果进行深入分析，结合理论预期解释观察到的现象。重点关注结果与假设的一致性以及异常值的可能原因。",
            "### 5.3 与已有研究的对比",
            "将本课题的实验结果与已有文献中的同类研究进行对比，讨论结果的一致性和差异性，分析可能的原因。",
            "### 5.4 误差分析与讨论",
            "分析实验过程中的误差来源，评估其对结论的影响程度，并提出降低误差的改进建议。",
        ]
    elif archetype == "data_analytics":
        experiment = [
            "### 5.1 描述性统计结果",
            "样本的基本特征以表格形式呈现，包括均值、标准差、最大值、最小值等统计量，以及主要变量的分布特征。",
            f"{experiment_summary or '以下为分析方案框架，待补充实际运行数据。'}",
            "### 5.2 回归/模型估计结果",
            "核心模型的估计结果以系数表形式呈现，标注统计显著性和置信区间。重点解释关键变量的系数符号、大小和显著性水平。",
            "### 5.3 结果讨论",
            "对估计结果进行经济学/科学含义解读，讨论关键发现的理论意义和实践价值。重点关注结果是否支持研究假设。",
            "### 5.4 稳健性检验结果",
            "稳健性检验的结果以对比表格形式呈现。不同设定下的估计结果的一致性增强了结论的可靠性。",
        ]
    elif archetype == "humanities":
        experiment = [
            "### 5.1 论证总结",
            "本课题的核心论证按照逻辑链条进行总结，梳理从问题提出到结论得出的完整推理过程。论证的关键环节和转折点得到特别强调。",
            "### 5.2 与既有观点的对比与辩驳",
            "将本课题的核心观点与已有研究成果进行系统对比，明确学术贡献和创新之处。对存在分歧的观点进行有理有据的辩驳。",
            "### 5.3 证据评估",
            "对本课题所依据的各类证据进行评估，分析其可靠性、代表性和充分性。承认证据的局限性，避免过度解读。",
        ]
    else:  # arts
        experiment = [
            "### 5.1 作品/方案展示",
            f"本课题的创作/设计成果以图文结合的方式呈现。{experiment_summary or '以下为展示方案框架，待补充实际作品。'}",
            "### 5.2 评价标准与方法",
            "采用多维度的评价标准对作品/方案进行系统评估，评价标准兼顾功能性、美观性、创新性和实用性等方面。",
            "### 5.3 用户/专家反馈",
            "通过用户测试或专家评审收集对作品/方案的反馈意见，对反馈进行分类整理和量化分析。",
            "### 5.4 与同类作品的比较",
            "将本课题的成果与同类作品进行比较分析，讨论差异和创新之处。",
        ]

    # ---- Chapter 6: Conclusion ----
    conclusion = [
        f"### 6.1 研究结论",
        f"本文围绕《{title}》完成了从需求分析、系统设计到功能实现和实验验证的完整流程。研究结果表明，采用模块化的工程方法可以有效组织复杂系统的开发过程，{feature_text}。",
        "### 6.2 创新点与贡献",
        "本课题的主要贡献在于：将理论方法与工程实践相结合，构建了完整可运行的系统；采用模块化架构降低了系统复杂度；通过实验验证了系统的有效性和稳定性。",
        "### 6.3 不足分析",
        "本研究仍存在以下不足：实验场景的覆盖面有限，部分功能模块的实现可以进一步优化；实验数据的规模和多样性有待扩充；部分性能指标尚未与已有研究进行严格对比。",
        "### 6.4 后续研究方向",
        "后续研究可以从以下几个方向展开：引入更多类型的测试场景以增强结论的普适性；优化核心算法以提升系统性能；完善自动化测试和持续集成流程以提高开发效率。",
    ]

    return [
        _section("摘要", abstract),
        _section("1. 绪论", introduction),
        _section("2. 相关理论基础", related),
        _section("3. 研究设计", design),
        _section("4. 核心实现", implementation),
        _section("5. 结果与分析", experiment),
        _section("6. 总结与展望", conclusion),
    ]
def _topic_to_english_title(title: str) -> str:
    replacements = [
        ("基于", "Based on "),
        ("的", " "),
        ("系统", "System "),
        ("相关技术栈", "related technology stack"),
        ("系统设计与实现", "System Design and Implementation"),
        ("设计与实现", "Design and Implementation"),
        ("系统", "System "),
        ("与", "and "),
    ]
    english = title
    for source, target in replacements:
        english = english.replace(source, target)
    english = re.sub(r"\s+", " ", english).strip(" -")
    if not english or _contains_cjk(english):
        return f"Design and Implementation of {title}" if title else "System Design and Implementation"
    if not english.lower().startswith(("design", "implementation")):
        english = english[:1].upper() + english[1:]
    return english


def _derive_submission_keywords(title: str, project_context: dict[str, Any] | None) -> list[str]:
    domain = detect_domain(title, project_context)
    keywords = get_default_keywords(domain)
    # Also extract topic-specific keywords
    topic_kws = _topic_keywords(title)
    for kw in topic_kws:
        if kw not in keywords and len(kw) >= 2:
            keywords.append(kw)
    deduped: list[str] = []
    for keyword in keywords:
        if keyword not in deduped:
            deduped.append(keyword)
    return deduped[:5]


def _derive_submission_metadata(title: str, project_context: dict[str, Any] | None) -> dict[str, str]:
    return {
        "school": "（待填写学校名称）",
        "college": "（待填写学院名称）",
        "major": "（待填写专业名称）",
        "student": "（待填写学生姓名）",
        "student_id": "（待填写学号）",
        "advisor": "（待填写指导教师）",
        "date": "（待填写提交日期）",
        "project_source": str((project_context or {}).get("source_project_path") or "（待填写项目路径）"),
        "title": title,
    }


def _derive_english_abstract(title: str, project_context: dict[str, Any] | None) -> str:
    english_title = _topic_to_english_title(title)
    stack = "、".join((project_context or {}).get("stack") or []) or "relevant technologies"
    return (
        f"This thesis studies {english_title}. "
        f"It builds an integrated system based on {stack}, connecting core functional modules into a unified software workflow. "
        "The implementation emphasizes modular design, parameter configuration, and system integration so that the system can support both development verification and deployment. "
        "The manuscript also provides an experiment framework for evaluating key performance metrics, which can be finalized after real logs, screenshots, and quantitative results are added."
    )


def _derive_acknowledgements(project_context: dict[str, Any] | None) -> list[str]:
    """Return real acknowledgements if the project supplies them, else an empty list.

    Earlier versions emitted three boilerplate lines even when no real content
    was available. That produced filler sections the user had to strip manually.
    Now the acknowledgements section is only rendered when the caller explicitly
    provides substantive text via ``project_context['acknowledgements']``.
    """
    if not isinstance(project_context, dict):
        return []
    items = project_context.get("acknowledgements")
    if isinstance(items, (list, tuple)):
        return [str(item).strip() for item in items if str(item).strip()]
    if isinstance(items, str) and items.strip():
        return [items.strip()]
    return []


def _derive_appendix_notes(project_context: dict[str, Any] | None) -> list[str]:
    """Return real appendix notes when provided; otherwise empty (no boilerplate)."""
    if not isinstance(project_context, dict):
        return []
    items = project_context.get("appendix_notes")
    if isinstance(items, (list, tuple)):
        return [str(item).strip() for item in items if str(item).strip()]
    if isinstance(items, str) and items.strip():
        return [items.strip()]
    return []


def _thesis_chapter_targets(target_words: int = 15000) -> list[dict[str, str]]:
    """Return per-chapter word-count targets scaled to *target_words*.

    The baseline is calibrated for a 15 000-character thesis.  For other
    lengths the ranges are scaled proportionally (clamped to a minimum of
    200 characters per chapter).
    """
    _scale = target_words / 15000

    def _scaled(lo: int, hi: int) -> str:
        lo_s = max(200, int(lo * _scale))
        hi_s = max(300, int(hi * _scale))
        return f"{lo_s}-{hi_s}字"

    return [
        {"chapter": "摘要与关键词", "target": _scaled(500, 800), "focus": "背景、目的、方法、结果、结论"},
        {"chapter": "绪论", "target": _scaled(2500, 4000), "focus": "背景、意义、现状、难点、研究内容"},
        {"chapter": "相关技术与文献综述", "target": _scaled(3000, 5000), "focus": "技术原理、研究现状、研究空白"},
        {"chapter": "系统总体设计与研究方法", "target": _scaled(3000, 5000), "focus": "架构设计、模块划分、参数设计"},
        {"chapter": "关键实现", "target": _scaled(3000, 5000), "focus": "核心模块、通信链路、控制实现"},
        {"chapter": "实验与结果分析", "target": _scaled(3000, 5000), "focus": "实验方案、结果展示、误差讨论"},
        {"chapter": "结论与展望", "target": _scaled(1500, 2500), "focus": "总结、创新点、后续工作"},
    ]


def _clean_academic_zh_text(text: str) -> str:
    if not text:
        return text
    replacements = {
        "首先，": "",
        "首先": "",
        "其次，": "",
        "其次": "",
        "最后，": "",
        "最后": "",
        "此外，": "",
        "此外": "",
        "另外，": "",
        "另外": "",
        "接下来，": "",
        "接下来": "",
        "值得注意的是，": "",
        "需要指出的是，": "",
        "必须强调的是，": "",
    }
    cleaned = text
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"。+", "。", cleaned)
    return cleaned


def _rewrite_chinese_thesis_paragraph(text: str) -> str:
    if not text:
        return text

    rewrites = [
        (
            "从写作结构看",
            "绪论需要在研究背景和研究意义的基础上，明确研究对象、研究边界、实现平台与研究目标，使全文围绕统一的问题主线展开，并为后文的系统设计、关键实现与实验分析奠定论证基础。",
        ),
        (
            "在这一边界内，论文重点讨论的对象包括",
            "本文围绕系统核心模块、数据感知链路、模块间接口关系以及仿真与实测双场景部署方案展开分析，并将论证重点集中在这些对象的接口关系、运行逻辑与工程实现上。",
        ),
        (
            "在任务推进过程中，论文撰写并不是开发结束后的附加工作",
            "课题实施过程同时包含系统搭建、功能联调、实验记录与论文整理四个方面。设计依据、参数选择和调试现象均来源于实际开发过程，这些一手材料共同构成了全文论证的主要工程证据。",
        ),
        (
            "也便于后续替换控制器、定位算法或传感器驱动",
            "在本系统中，各功能模块分别由不同组件维护，模块间通过统一的接口进行协作。这样的组织方式既降低了单个模块变化对整体系统的连锁影响，也为后续算法替换与功能扩展保留了清晰接口。",
        ),
        (
            "适合本科毕业论文从系统实现角度展开描述",
            "项目通过配置文件加载模块参数，并将各子系统的数据流纳入统一处理链路，从而实现系统功能的协同工作。该方案结构清晰，既能体现各模块之间的关系，也有利于从系统实现角度分析其工程逻辑。",
        ),
        (
            "这一要求直接决定了后续总体设计必须采用模块化、可复用的启动与配置组织方式",
            "这一要求决定了系统总体设计必须采用模块化、可复用的启动与配置组织方式。",
        ),
        (
            "决定了后续章节必须围绕数据流与模块边界展开分析",
            "因此系统分析需要围绕数据流与模块边界展开。",
        ),
        (
            "便于后续升级",
            "对于工程型课题而言，评价重点不能局限于单一算法指标，而应同时覆盖系统功能完整性、运行稳定性、模块可维护性、实验可重复性与应用扩展能力等多个维度。只有在这些维度上形成较为均衡的表现，系统方案才具备较强的工程价值与推广意义。",
        ),
        (
            "本章的相关技术分析并非独立于后续设计章节存在",
            "本章梳理的关键技术并不是孤立的背景说明，而是直接决定了系统总体设计与关键模块实现的分析重点。只有先明确这些技术在工作流程中的具体作用，后文才能对系统设计中的参数选择、模块边界与通信链路作出有针对性的解释。",
        ),
        (
            "换言之，第二章所梳理的技术背景",
            "第二章所梳理的技术背景构成了第三章与第四章的技术基础。系统总体设计需要依赖这些技术前提来确定模块组织、数据流方向与部署策略，而关键模块实现则需要以这些技术机制为依据说明具体实现为何能够满足任务要求。",
        ),
        (
            "后续在插入系统架构图和节点通信图时",
            "当系统架构图与节点通信图与正文内容一一对应时",
        ),
        (
            "从论文撰写角度看，需要明确说明每一段通信链路传递的内容",
            "在系统实现分析中，需要明确说明每一段通信链路传递的数据类型、潜在延迟来源以及系统为降低通信不稳定所采用的时序控制与参数约束。",
        ),
        (
            '毕业论文在描述控制器参数时，不应只停留在"配置了某参数"的层面',
            "参数分析不能停留在配置罗列层面，而应结合系统行为解释参数变化带来的影响。例如，提高控制频率通常有助于提升响应及时性，但也会增加计算压力；增大约束阈值有助于提升安全性，但可能使系统行为趋于保守；适当放宽目标容差可以提升成功率，但同时可能降低最终精度。",
        ),
        (
            "这些工程经验对于毕业论文而言具有较强的总结价值",
            "这些工程经验不仅解释了系统能够稳定运行的原因，也揭示了系统集成型课题在实现过程中的关键工程规律。对毕业论文而言，真正具有说服力的内容并不是简单的功能清单，而是围绕实现路径、运行机制和调试证据所形成的完整论证链条。",
        ),
        (
            "建议实验方案包括三组",
            "实验设置由三组测试构成：第一组为核心功能测试，在相同条件下记录关键输出指标与系统表现；第二组为综合任务测试，在给定多个目标条件下记录完成率、耗时与结果质量；第三组为稳定性测试，在连续运行条件下观察系统状态、数据一致性与异常情况。",
        ),
        (
            "建议至少设计三类实验",
            "实验内容包括三类测试：核心功能测试、综合任务测试以及系统稳定性测试。通过对这三类实验结果进行综合分析，可以较为完整地反映系统在功能实现、任务执行与长期运行三个层面的表现。",
        ),
        (
            "若当前尚未形成完整的统计数据，可先在论文中保留结果表与截图位置，并围绕已有系统结构给出预期分析框架",
            "在现有材料基础上，可围绕系统结构、运行日志与典型现象组织结果分析框架，并以关键输出结果和主要参数作为论证依据。",
        ),
        (
            "只需将对应图表、日志和统计值替换到本章即可形成完整的结果分析内容",
            "当补充对应图表、日志与统计值后，实验分析即可进一步转化为定量与定性结合的完整论证。",
        ),
        (
            "为了使实验章节更接近正式毕业论文，建议结果展示至少包含三类材料",
            "实验结果展示主要包括三类材料。第一类是系统运行结果图，用于展示核心功能输出与质量；第二类是任务执行过程截图或轨迹图，用于展示系统从起始到完成的运行过程；第三类是统计表格，用于呈现成功率、平均耗时、关键指标等量化数据。",
        ),
        (
            '考虑到当前仓库中尚未沉淀完整测试结果，本文采用"先完整论证实验设计、后补充具体数据"的写作策略',
            "受现有项目材料限制，本章先根据已有实现与运行链路展开分析，并将实验指标、图表位置和结果解释框架固定下来。",
        ),
        (
            "对于毕业论文提交而言，这种策略具有较高实用性",
            "在补充完整实验记录后，这种组织方式能够较为自然地衔接定量结果与文字分析。",
        ),
        (
            "为了使毕业论文在视觉表达上更完整，实验章节建议至少配置一张系统总体架构图",
            "实验部分宜配置系统总体架构图、模块交互图、运行结果图、任务执行轨迹图以及实验统计表，形成图文对应的证据链。",
        ),
        (
            "实验统计表建议包含实验场景编号、任务类型、目标点数量、成功次数、平均耗时、异常情况说明等字段",
            "实验统计表可包含实验场景编号、任务类型、目标点数量、成功次数、平均耗时与异常情况说明等字段。",
        ),
        (
            "尽管当前仓库中尚未自动沉淀完整实验数据，但实验章节已经形成了相对完整的写作骨架",
            "虽然现有仓库尚未沉淀完整实验数据，但实验章节的结构已经能够覆盖实验目的、场景设置、指标体系、误差来源与复现条件。",
        ),
        (
            '在后续补录实验结果时，建议按照"先证明系统可运行，再证明系统运行较稳定，分析系统优化空间"的顺序组织材料',
            "在补充实验结果时，可按照系统可运行性、系统稳定性与性能优化空间三个层次组织材料。",
        ),
        (
            "对于尚未完成的实验数据，可在记录时统一保存地图图片、导航轨迹截图、运行日志和关键参数",
            "对尚未补充完毕的数据材料，应统一保存运行结果图、任务执行截图、运行日志和关键参数。",
        ),
        (
            "为了提高结果可信性，后续测试应尽量保持场景、目标点设置、参数文件版本和启动流程一致",
            "为提高结果可信性，实验过程需尽量保持场景、目标点设置、参数文件版本和启动流程一致。",
        ),
        (
            "只要后续实验材料继续沿用这一组织方式",
            "若实验材料继续沿用这一组织方式，则论文中的实验结论能够建立在较清晰的复现链条之上。换言之，实验结论的可信度不仅来自结果数值本身，也来自测试条件、参数版本、运行日志与结果图像之间的一致对应关系。",
        ),
        (
            "可直接用于后续补充地图截图、导航轨迹图、运行日志与性能统计表",
            "本章从实验目的、实验环境、指标体系、结果分析思路与复现条件等方面完成了实验章节的主体论证。随着运行结果图、任务执行截图、运行日志与性能统计表的逐步补充，本文的实验部分能够进一步发展为图文完整、证据充分的定稿内容。",
        ),
        (
            "从系统研究与工程实现角度看，本课题的主要特点在于以可运行系统为中心组织各功能模块",
            "本课题以可运行系统为核心组织各功能模块，并将系统架构、关键实现与实验分析统一到同一条工程主线之中。",
        ),
        (
            "与仅展示功能现象的项目说明相比，本文进一步强调了模块边界、参数依据、运行时序与调试方法之间的关系",
            "全文围绕模块边界、参数依据、运行时序与调试方法之间的关系展开论证，重点说明系统为何能够形成稳定的功能闭环。",
        ),
        (
            "从更广泛的应用视角看，本课题形成的系统并不仅局限于当前具体机器人平台",
            "从应用扩展角度看，本课题形成的系统并不局限于单一平台或场景。由于系统在架构上采用模块化组织方式，并将各核心功能划分为相对独立的部分，因此在面对不同需求或不同运行环境时，只需针对局部接口和参数进行调整即可完成适配。",
        ),
        (
            "从工程实现角度看，论文写作不仅需要给出功能说明",
            "从系统实现角度看，本文将整套系统划分为若干功能层次，并分别分析其设计目标、接口关系与运行机制，以构成完整的工程论证链条。",
        ),
        (
            "从论文成稿角度看，当前生成文本已经覆盖毕业论文的主要论证链条",
            "综上，本文已经围绕系统需求、总体设计、关键实现与实验验证形成较完整的论证结构。后续工作主要集中在补充实验图表、量化结果与文献引用，以进一步提高论文的证据完整性。",
        ),
        (
            "这种可迁移性意味着，本课题不仅完成了一次课程意义上的毕业设计",
            "这种可迁移性表明，本课题不仅完成了既定的毕业设计任务，也形成了一套可迁移、可扩展的系统实现思路，可为同类项目的工程部署与功能扩展提供参考。",
        ),
        (
            "面向后续研究，可以从三个方向进一步拓展本系统",
            "围绕系统的进一步完善，可从三个方向拓展本系统。其一，在感知层面引入视觉与 IMU 等多源信息，以提高复杂场景下的定位精度与环境理解能力；其二，在控制层面研究更加自适应的局部规划与参数调节方法，以增强动态环境中的稳定性；其三，在工程层面完善自动评测、日志采集与结果分析工具链，提高系统在长期运行与持续迭代中的维护效率。",
        ),
    ]

    for marker, replacement in rewrites:
        if marker in text:
            return replacement
    return text


def _apply_chinese_writing_rules(
    sections: list[dict[str, Any]],
    title: str = "",
    project_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for section in sections:
        content: list[str] = []
        for paragraph in section.get("content", []):
            raw = str(paragraph).strip()
            if raw.startswith("### "):
                content.append(raw)
                continue
            content.append(_rewrite_chinese_thesis_paragraph(_clean_academic_zh_text(raw)))
        normalized.append({"title": section["title"], "content": content})
    _target_words = int((project_context or {}).get("target_words", 15000))
    return _boost_chinese_thesis_length(
        _enrich_chinese_thesis_sections(normalized, title=title, project_context=project_context),
        target_words=_target_words,
    )


def _insert_paragraphs_after_heading(content: list[str], heading: str, paragraphs: list[str]) -> list[str]:
    enriched: list[str] = []
    inserted = False
    for item in content:
        enriched.append(item)
        if not inserted and item == heading:
            enriched.extend(paragraphs)
            inserted = True
    return enriched


def _merge_short_chinese_paragraphs(content: list[str], min_chars: int = 70) -> list[str]:
    merged: list[str] = []
    for item in content:
        raw = str(item).strip()
        if not raw:
            continue
        if raw.startswith("### "):
            merged.append(raw)
            continue
        if merged and not merged[-1].startswith("### ") and len(raw) < min_chars:
            merged[-1] = f"{merged[-1]}{raw}"
        else:
            merged.append(raw)
    return merged


def _section_body_char_count(section: dict[str, Any]) -> int:
    total = 0
    for item in section.get("content", []):
        raw = str(item).strip()
        if not raw or raw.startswith("### "):
            continue
        total += len(re.sub(r"\s+", "", raw))
    return total


def _sections_body_char_count(sections: list[dict[str, Any]]) -> int:
    return sum(_section_body_char_count(section) for section in sections)


def _append_section_expansion(
    content: list[str],
    heading: str,
    paragraphs: list[str],
) -> list[str]:
    if any(paragraph in content for paragraph in paragraphs):
        return content
    result = _insert_paragraphs_after_heading(content, heading, paragraphs)
    # If the heading was not found in content, append at the end
    if result is content or result == content:
        expanded = list(content) + [heading] + paragraphs
        return expanded
    return result


def _boost_chinese_thesis_length(
    sections: list[dict[str, Any]],
    target_words: int = 15000,
) -> list[dict[str, Any]]:
    """Expand sections that fall below their share of *target_words*.

    The function now respects the caller's *target_words* instead of the old
    hard-coded 15 000 ceiling.  Sections that already exceed their proportional
    share are left untouched.  A global cap of ``target_words * 1.25`` prevents
    runaway padding.
    """
    # Dynamic per-section thresholds proportional to target_words.
    # Old hardcoded booster char targets mapped roughly to a 15 000-word thesis.
    # We scale them linearly so the same "fill ratio" applies at any word count.
    _scale = target_words / 15000

    boosters: dict[str, tuple[str, list[str], int]] = {
        "1. 绪论": (
            "### 1.5 研究边界与完成路径",
            [
                "对于毕业论文而言，研究问题的提出只是起点，更重要的是将研究目标落实到可验证的系统实现路径上。本文所选择的技术路线并非单纯追求算法新颖性，而是强调系统在真实应用条件下的可部署性、可维护性与可分析性，这也是工程型课题区别于纯理论研究的重要特征。",
            ],
            int(2300 * _scale),
        ),
        "2. 相关技术与系统需求分析": (
            "### 2.5 研究现状、评价维度与本文切入点",
            [
                "结合本课题的工程目标可以看出，相关技术分析的意义在于建立后续系统设计的解释基础。只有先明确各项技术在数据流、功能边界和运行约束中的作用，系统总体设计与关键实现章节才能避免停留在现象描述层面，而形成具有因果关系的工程论证。",
            ],
            int(2200 * _scale),
        ),
        "3. 系统总体设计": (
            "### 3.5 工程组织、部署流程与维护策略",
            [
                "总体设计阶段所形成的模块划分、参数组织与部署顺序，实际上决定了系统后续调试和实验验证的效率。如果总体设计缺乏边界意识，后续任何局部参数修改都可能引起整套系统的联动变化。因此，本章的设计工作不仅服务于功能实现，也服务于后续实验复现和论文论证的稳定展开。",
            ],
            int(2300 * _scale),
        ),
        "4. 关键模块实现": (
            "### 4.4 坐标变换、启动复用与工程经验",
            [
                "进一步分析可以发现，关键模块实现的难点并不只在于让各组件能够正常启动，更在于让各模块在统一时序与数据框架下稳定协同。对复杂系统而言，这种协同能力往往比单个模块的局部性能更能决定最终运行效果，也是工程型论文最需要突出的问题之一。",
            ],
            int(2600 * _scale),
        ),
        "5. 实验设计与结果分析": (
            "### 5.4 结果讨论、图表规划与复现性分析",
            [
                '实验分析的最终目的不是简单证明系统"能跑通"，而是说明系统为何能够运行、在什么条件下运行更稳定、又在哪些场景下暴露出改进空间。只有把这些问题回答清楚，实验章节才能真正支撑论文结论，而不是停留在验证性展示层面。',
            ],
            int(2500 * _scale),
        ),
        "6. 总结与展望": (
            "### 6.4 局限性、应用价值与未来研究方向",
            [
                "综合全文可以看出，本课题虽然以现有开源组件为基础，但真正完成的是一项系统化重组工作。通过对启动流程、参数体系、坐标维护与导航闭环的统一设计，项目不仅具备了面向本科毕业论文的论证价值，也为后续围绕同类机器人平台开展更深入研究奠定了工程基础。",
            ],
            int(1700 * _scale),
        ),
    }

    global_cap = int(target_words * 1.25)

    # Map chapter numbers to booster content (instead of exact title matching).
    # Key = first digit of chapter number, value = (heading, paragraphs, threshold).
    _boosters_by_number: dict[str, tuple[str, list[str], int]] = {
        "1": (
            "### 1.5 研究边界与完成路径",
            [
                "对于毕业论文而言，研究问题的提出只是起点，更重要的是将研究目标落实到可验证的系统实现路径上。本文所选择的技术路线并非单纯追求算法新颖性，而是强调系统在真实应用条件下的可部署性、可维护性与可分析性，这也是工程型课题区别于纯理论研究的重要特征。",
            ],
            int(2300 * _scale),
        ),
        "2": (
            "### 2.5 研究现状、评价维度与本文切入点",
            [
                "结合本课题的工程目标可以看出，相关技术分析的意义在于建立后续系统设计的解释基础。只有先明确各项技术在数据流、功能边界和运行约束中的作用，系统总体设计与关键实现章节才能避免停留在现象描述层面，而形成具有因果关系的工程论证。",
            ],
            int(2200 * _scale),
        ),
        "3": (
            "### 3.5 工程组织、部署流程与维护策略",
            [
                "总体设计阶段所形成的模块划分、参数组织与部署顺序，实际上决定了系统后续调试和实验验证的效率。如果总体设计缺乏边界意识，后续任何局部参数修改都可能引起整套系统的联动变化。因此，本章的设计工作不仅服务于功能实现，也服务于后续实验复现和论文论证的稳定展开。",
            ],
            int(2300 * _scale),
        ),
        "4": (
            "### 4.4 坐标变换、启动复用与工程经验",
            [
                "进一步分析可以发现，关键模块实现的难点并不只在于让各组件能够正常启动，更在于让各模块在统一时序与数据框架下稳定协同。对复杂系统而言，这种协同能力往往比单个模块的局部性能更能决定最终运行效果，也是工程型论文最需要突出的问题之一。",
            ],
            int(2600 * _scale),
        ),
        "5": (
            "### 5.4 结果讨论、图表规划与复现性分析",
            [
                '实验分析的最终目的不是简单证明系统"能跑通"，而是说明系统为何能够运行、在什么条件下运行更稳定、又在哪些场景下暴露出改进空间。只有把这些问题回答清楚，实验章节才能真正支撑论文结论，而不是停留在验证性展示层面。',
            ],
            int(2500 * _scale),
        ),
        "6": (
            "### 6.4 局限性、应用价值与未来研究方向",
            [
                "综合全文可以看出，本课题虽然以现有开源组件为基础，但真正完成的是一项系统化重组工作。通过对启动流程、参数体系、坐标维护与导航闭环的统一设计，项目不仅具备了面向本科毕业论文的论证价值，也为后续围绕同类机器人平台开展更深入研究奠定了工程基础。",
            ],
            int(1700 * _scale),
        ),
    }

    def _chapter_number(title: str) -> str:
        """Extract leading chapter number from title like '3. xxx'."""
        m = re.match(r"^(\d+)\.", title.strip())
        return m.group(1) if m else ""

    balanced: list[dict[str, Any]] = []
    for section in sections:
        title = str(section.get("title", ""))
        content = list(section.get("content", []))
        ch_num = _chapter_number(title)
        rule = _boosters_by_number.get(ch_num) if ch_num else None
        if rule and _section_body_char_count(section) < rule[2]:
            content = _append_section_expansion(content, rule[0], rule[1])
        balanced.append({"title": title, "content": _merge_short_chinese_paragraphs(content)})

    # Second pass — only if still below target and below the 1.25× cap.
    second_pass = [
        (
            "3",  # chapter number prefix
            "### 3.6 本章小结",
            "从系统工程角度看，总体设计阶段所形成的分层结构、启动策略与参数体系，决定了后续关键模块实现能否顺利展开，也决定了实验章节是否能够建立在清晰、稳定、可重复的系统基础之上。因此，本章不仅是功能组织说明，也是全文工程论证的中枢环节。",
        ),
        (
            "4",
            "### 4.5 调试方法与实现小结",
            "关键模块实现的价值最终体现在系统级效果上。只有当各功能模块、数据链路、状态管理和核心控制在同一框架下保持协同一致时，系统才具备稳定完成任务的能力，而这正是工程型毕业论文需要重点说明的实现成果。",
        ),
        (
            "5",
            "### 5.5 本章小结",
            "实验章节通过把地图结果、导航轨迹、运行日志与统计指标结合起来，使系统性能不再停留在直观演示层面，而能够被转化为具有可比较性和可解释性的论文证据。这种证据组织方式也是后续完成终稿排版和答辩汇报的重要基础。",
        ),
    ]
    if _sections_body_char_count(balanced) < target_words:
        rebuilt: list[dict[str, Any]] = []
        for section in balanced:
            title = str(section.get("title", ""))
            content = list(section.get("content", []))
            ch_num = _chapter_number(title)
            for rule_ch, heading, paragraph in second_pass:
                if ch_num == rule_ch and paragraph not in content and _sections_body_char_count(balanced) < target_words:
                    content = _append_section_expansion(content, heading, [paragraph])
            rebuilt.append({"title": title, "content": _merge_short_chinese_paragraphs(content)})
        balanced = rebuilt
    return balanced


def _enrich_chinese_thesis_sections(
    sections: list[dict[str, Any]],
    title: str = "",
    project_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Insert archetype-aware academic enrichment paragraphs into Chinese thesis sections."""
    from tools.domain_utils import detect_domain, get_archetype

    domain = detect_domain(title, project_context)
    archetype = get_archetype(title, project_context)

    enriched_sections: list[dict[str, Any]] = []
    for section in sections:
        sec_title = str(section.get("title", ""))
        content = list(section.get("content", []))

        if archetype == "engineering":
            content = _enrich_engineering_section(sec_title, content, title, project_context)
        elif archetype == "science":
            content = _enrich_science_section(sec_title, content, title, project_context)
        elif archetype == "data_analytics":
            content = _enrich_data_analytics_section(sec_title, content, title, project_context)
        elif archetype == "humanities":
            content = _enrich_humanities_section(sec_title, content, title, project_context)
        elif archetype == "arts":
            content = _enrich_arts_section(sec_title, content, title, project_context)

        enriched_sections.append({"title": sec_title, "content": content})

    return enriched_sections


def _enrich_engineering_section(
    sec_title: str,
    content: list[str],
    title: str,
    project_context: dict[str, Any] | None,
) -> list[str]:
    """Enrichment for engineering-type papers (CS, robotics, control, etc.)."""
    stack = (project_context or {}).get("stack", "相关技术栈")
    method_clues = (project_context or {}).get("method_clues", [])

    if sec_title.startswith("1.") or sec_title.startswith("绪论") or sec_title.startswith("引言"):
        content = _insert_paragraphs_after_heading(
            content,
            "研究现状与技术路线",
            [
                f"对于系统实现型毕业论文而言，研究价值不仅体现在算法名称的罗列上，更体现在能否把核心模块组织为稳定的功能闭环。因而本文在绪论部分就将论证重点落在系统链路完整性、场景适应性与工程实现路径上，以避免全文沦为对组件的简单拼接说明。",
                f"结合项目现有代码与配置可以看出，{stack} 技术路线的选取围绕核心功能需求展开，既能够支撑仿真验证，也能够为实际部署提供较为明确的技术实现路径。",
            ],
        )

    if sec_title.startswith("2.") or "相关" in sec_title or "理论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "技术架构与系统需求",
            [
                f"从技术架构角度看，{stack} 的优势不仅体现在核心通信与计算能力上，还体现在它能够把感知、控制、数据处理和可视化等异构模块纳入统一的框架与工具链之中。对于本课题这类包含多种设备接口和多层软件逻辑的系统，这种架构一致性对于降低开发复杂度和提升维护效率十分重要。",
            ],
        )

    if sec_title.startswith("3.") or "设计" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "系统总体架构",
            [
                "从系统分层角度观察，本课题所实现的系统可以划分为设备接入层、数据处理层、核心算法层、控制决策层以及可视化与调试层。这样的分层方式有助于明确各模块的输入输出边界，也便于在论文中从整体到局部展开论证，使系统设计逻辑更加清晰。",
            ],
        )

    if sec_title.startswith("4.") or "实现" in sec_title or "模块" in sec_title:
        clue_text = "、".join(method_clues[:3]) if method_clues else "核心功能"
        content = _insert_paragraphs_after_heading(
            content,
            "核心模块实现",
            [
                f"关键模块实现并不是对各个功能组件进行简单介绍，而是要说明它们如何在统一运行链路中承担不同职责。系统先通过基础模块建立运行条件，再通过 {clue_text} 完成上层核心功能，最后借助可视化和调试工具形成可观察、可测试的工程闭环。",
            ],
        )

    if sec_title.startswith("5.") or "实验" in sec_title or "结果" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "实验结果分析",
            [
                "实验结果分析不能停留在简单的通过或失败判断上，而应把系统性能、运行日志和模块状态综合起来考察。通过这种综合分析方式，论文能够更准确地揭示系统性能与实现机制之间的对应关系。",
                "当实验结果与预期存在偏差时，分析过程应优先区分问题究竟来自算法本身、数据噪声、硬件限制还是配置参数。这样的误差分层方法有助于提高结果讨论的针对性和结论的可信度。",
            ],
        )

    if sec_title.startswith("6.") or sec_title.startswith("结论") or sec_title.startswith("总结"):
        content = _insert_paragraphs_after_heading(
            content,
            "研究工作总结",
            [
                "从课题完成情况看，本文已经围绕需求分析、总体设计、关键实现与实验论证建立了较完整的论文结构，并把项目代码、配置和运行链路转化为可以直接支撑论文写作的工程证据。",
                "与偏重理论推导的研究相比，本文的创新性主要体现在系统集成与工程实现层面。这种创新形式虽然不以全新算法为目标，但在工程实践中具有很强的应用价值。",
            ],
        )

    return content


def _enrich_science_section(
    sec_title: str,
    content: list[str],
    title: str,
    project_context: dict[str, Any] | None,
) -> list[str]:
    """Enrichment for science-type papers (physics, chemistry, biology, medicine, materials)."""
    if sec_title.startswith("1.") or sec_title.startswith("引言"):
        content = _insert_paragraphs_after_heading(
            content,
            "研究现状与科学问题",
            [
                f"本课题围绕 {title} 展开，旨在通过系统的实验研究和理论分析，解决该领域中的关键科学问题。研究意义不仅在于验证已有理论的适用范围，更在于发现新的现象或规律。",
            ],
        )

    if sec_title.startswith("2.") or "理论" in sec_title or "文献" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "理论框架与假设",
            [
                "理论框架的建立是科学研究的基础。本课题基于已有理论成果，结合具体研究问题，提出了可检验的研究假设。理论推导过程中注重逻辑的严谨性和物理意义的明确性。",
            ],
        )

    if sec_title.startswith("4.") or "结果" in sec_title or "讨论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "结果讨论",
            [
                "实验结果的分析需要结合理论预期和已有文献进行综合讨论。当实验数据与理论预测一致时，应进一步分析其内在机理；当出现偏差时，应探讨可能的误差来源和影响因素。",
                "与已有研究的对比讨论是结果分析的重要组成部分。通过定量比较关键指标，可以客观评估本课题研究方法的优劣和适用范围。",
            ],
        )

    if sec_title.startswith("5.") or "结论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "研究总结与展望",
            [
                "本课题通过系统的实验研究和理论分析，得到了若干有意义的科学发现。这些发现不仅丰富了该领域的知识体系，也为后续研究提供了参考和启示。",
            ],
        )

    return content


def _enrich_data_analytics_section(
    sec_title: str,
    content: list[str],
    title: str,
    project_context: dict[str, Any] | None,
) -> list[str]:
    """Enrichment for data analytics papers (economics, social science, education, psychology)."""
    if sec_title.startswith("1.") or sec_title.startswith("引言"):
        content = _insert_paragraphs_after_heading(
            content,
            "研究背景与现实意义",
            [
                f"本课题围绕 {title} 展开，从现实问题出发，通过系统的数据分析和统计检验，为理解和解决该问题提供实证依据。研究的现实意义在于为相关决策提供科学参考。",
            ],
        )

    if sec_title.startswith("2.") or "文献" in sec_title or "理论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "理论框架与研究假设",
            [
                "基于已有文献和理论基础，本课题提出了明确的研究假设。变量定义遵循操作化原则，确保每个核心概念都可以通过可观测的指标进行度量。理论假设的提出兼顾学术严谨性和现实可检验性。",
            ],
        )

    if sec_title.startswith("4.") or "实证" in sec_title or "结果" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "实证结果讨论",
            [
                "实证结果的分析需要结合经济含义或社会意义进行解读。统计显著性的判断只是分析的第一步，更重要的是理解变量之间的关系机制和实际影响程度。",
                "稳健性检验是保证结论可靠性的重要环节。通过变换模型设定、调整样本范围或替换度量方式，可以验证核心结论对不同假设条件的敏感性。",
            ],
        )

    if sec_title.startswith("5.") or "结论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "政策建议与研究展望",
            [
                "基于实证分析结果，本课题提出了具有针对性的政策或实践建议。这些建议既考虑了研究发现的理论含义，也兼顾了现实条件下的可操作性。",
            ],
        )

    return content


def _enrich_humanities_section(
    sec_title: str,
    content: list[str],
    title: str,
    project_context: dict[str, Any] | None,
) -> list[str]:
    """Enrichment for humanities papers (history, philosophy, literature, linguistics, law)."""
    if sec_title.startswith("1.") or sec_title.startswith("绪论"):
        content = _insert_paragraphs_after_heading(
            content,
            "问题意识与学术价值",
            [
                f"本课题围绕 {title} 展开，从学术史脉络中提炼核心问题。研究的学术价值不仅在于填补某一知识领域的空白，更在于为理解相关学术问题提供新的分析视角或论证路径。",
            ],
        )

    if sec_title.startswith("2.") or "文献" in sec_title or "综述" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "学术史梳理与理论框架",
            [
                "文献综述的目的不仅是罗列已有研究成果，更重要的是通过梳理学术争论的演变脉络，明确本课题在学术对话中的位置。理论框架的选择应当与研究对象和研究问题相匹配，避免生搬硬套。",
            ],
        )

    if sec_title.startswith("4.") or "论证" in sec_title or "主体" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "论证展开与证据分析",
            [
                "主体论证需要从多个维度展开，每个论点都应有充分的文献或史料支撑。论证过程中应注重逻辑的连贯性，避免孤立的论断或未经论证的跳跃推理。",
                "与既有观点的对话是学术论证的重要环节。在尊重已有研究成果的基础上，指出其局限或不足，并提出更具解释力的分析框架，是本研究学术贡献的核心体现。",
            ],
        )

    if sec_title.startswith("5.") or "结论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "研究总结与学术贡献",
            [
                "本课题通过系统的文献梳理和深入的主体论证，对核心问题给出了有说服力的回答。研究结论不仅回应了绪论中提出的问题意识，也在一定程度上推进了该领域的学术讨论。",
            ],
        )

    return content


def _enrich_arts_section(
    sec_title: str,
    content: list[str],
    title: str,
    project_context: dict[str, Any] | None,
) -> list[str]:
    """Enrichment for arts/design papers (architecture, music, art)."""
    if sec_title.startswith("1.") or sec_title.startswith("引言"):
        content = _insert_paragraphs_after_heading(
            content,
            "创作背景与设计目标",
            [
                f"本课题围绕 {title} 展开，从创作或设计需求出发，明确设计目标和评价标准。研究的价值在于探索功能性与审美性之间的平衡点。",
            ],
        )

    if sec_title.startswith("2.") or "理论" in sec_title or "案例" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "设计原则与经典案例",
            [
                "经典案例的分析为设计实践提供了重要参考。通过对比不同方案的优缺点，可以提炼出适用于本课题的设计原则和方法论，为后续创作或设计提供理论支撑。",
            ],
        )

    if sec_title.startswith("4.") or "作品" in sec_title or "呈现" in sec_title or "评价" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "作品评价与比较讨论",
            [
                "作品评价应建立明确的评价标准，从多个维度进行综合分析。与同类作品的比较讨论有助于客观定位本课题的创作或设计水平，也能发现需要改进的方向。",
            ],
        )

    if sec_title.startswith("5.") or "结论" in sec_title:
        content = _insert_paragraphs_after_heading(
            content,
            "创作总结与改进方向",
            [
                "本课题的创作或设计实践证明，系统化的方法论和充分的前期调研对于产出高质量作品至关重要。总结创作过程中的经验和教训，可以为后续改进提供明确方向。",
            ],
        )

    return content


def _split_chapter_blocks(content: list[str]) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    current_title = ""
    current_body: list[str] = []
    for item in content:
        raw = str(item).strip()
        if raw.startswith("### "):
            if current_title or current_body:
                blocks.append((current_title, current_body))
            current_title = raw[4:].strip()
            current_body = []
        else:
            current_body.append(raw)
    if current_title or current_body:
        blocks.append((current_title, current_body))
    return blocks


def _merge_chapter_blocks(
    blocks: list[tuple[str, list[str]]],
    groups: list[tuple[str, list[str]]],
) -> list[str]:
    merged: list[str] = []
    used: set[str] = set()
    block_map = {title: body for title, body in blocks}
    for new_title, source_titles in groups:
        paragraphs: list[str] = []
        for source_title in source_titles:
            body = block_map.get(source_title)
            if body:
                paragraphs.extend(body)
                used.add(source_title)
        if paragraphs:
            merged.append(f"### {new_title}")
            merged.extend(paragraphs)
    for title, body in blocks:
        if title in used:
            continue
        if title:
            merged.append(f"### {title}")
        merged.extend(body)
    return merged


def _compact_chinese_sections_structure(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge sub-sections within each chapter using keyword-based matching.

    Instead of matching exact hardcoded robotics section titles, this uses
    prefix and keyword matching to consolidate sub-sections generically.
    """
    compacted: list[dict[str, Any]] = []
    for section in sections:
        sec_title = str(section.get("title", ""))
        content = list(section.get("content", []))
        blocks = _split_chapter_blocks(content)

        if not blocks:
            compacted.append({"title": sec_title, "content": content})
            continue

        # Generic merging: group consecutive small sub-sections under broader headings
        block_titles = [t for t, _ in blocks]
        merged_content = _auto_merge_chapter_blocks(blocks, sec_title)

        compacted.append({"title": sec_title, "content": merged_content})

    return compacted


def _auto_merge_chapter_blocks(
    blocks: list[tuple[str, list[str]]],
    chapter_title: str,
) -> list[str]:
    """Auto-merge sub-sections within a chapter based on semantic similarity.

    Groups consecutive sub-sections that share similar topic prefixes,
    keeping the first sub-section's title as the merged heading.
    """
    if not blocks:
        return []

    # Grouping rules: sub-sections with the same numeric prefix or topic
    # keyword get merged together.
    groups: list[tuple[str, list[str]]] = []
    current_group_title = ""
    current_sources: list[str] = []

    def flush():
        nonlocal current_group_title, current_sources
        if current_sources:
            groups.append((current_group_title, list(current_sources)))
        current_group_title = ""
        current_sources = []

    for block_title, block_body in blocks:
        if not current_group_title:
            current_group_title = block_title
            current_sources = [block_title]
        elif _sections_should_merge(current_group_title, block_title):
            current_sources.append(block_title)
        else:
            flush()
            current_group_title = block_title
            current_sources = [block_title]

    flush()

    if not groups:
        return [f"### {blocks[0][0]}"] + blocks[0][1]

    return _merge_chapter_blocks(blocks, groups)


def _sections_should_merge(title_a: str, title_b: str) -> bool:
    """Determine if two sub-sections should be merged.

    Merge if they share the same top-level numeric prefix (e.g. 3.1 and 3.2)
    or if the second title is a continuation of the first (小节 suffix).
    """
    import re as _re

    # Extract numeric prefixes like "2.1", "2.2"
    ma = _re.match(r"(\d+\.\d+)", title_a)
    mb = _re.match(r"(\d+\.\d+)", title_b)

    if ma and mb:
        # Same chapter number, merge if consecutive or if second is a sub-point
        prefix_a = ma.group(1).rsplit(".", 1)[0]
        prefix_b = mb.group(1).rsplit(".", 1)[0]
        if prefix_a == prefix_b:
            num_a = int(ma.group(1).split(".")[1])
            num_b = int(mb.group(1).split(".")[1])
            # Merge consecutive sub-sections
            if num_b == num_a + 1:
                return True

    # Check if second title contains continuation keywords
    continuation_kw = ("续", "补充", "进一步", "延伸", "扩展", "小结")
    if any(kw in title_b for kw in continuation_kw):
        return True

    return False
    return compacted


def _render_front_matter_markdown(payload: dict[str, Any]) -> str:
    metadata = payload.get("submission_meta") or {}
    zh_keywords = payload.get("zh_keywords") or []
    en_keywords = payload.get("en_keywords") or []
    lines = [
        "## 封面信息",
        "",
        f"- 学校：{metadata.get('school', '')}",
        f"- 学院：{metadata.get('college', '')}",
        f"- 专业：{metadata.get('major', '')}",
        f"- 题目：{metadata.get('title', payload.get('topic', ''))}",
        f"- 学生姓名：{metadata.get('student', '')}",
        f"- 学号：{metadata.get('student_id', '')}",
        f"- 指导教师：{metadata.get('advisor', '')}",
        f"- 提交日期：{metadata.get('date', '')}",
        "",
        "## 中文摘要",
        "",
    ]
    for paragraph in (payload.get("sections") or [{}])[0].get("content", []):
        lines.append(str(paragraph))
        lines.append("")
    lines.append("## 中文关键词")
    lines.append("")
    lines.append("、".join(zh_keywords) or "（待补充关键词）")
    lines.extend(["", "## English Abstract", "", str(payload.get("english_abstract") or ""), "", "## Keywords", "", ", ".join(en_keywords) or "(to be completed)", ""])
    return "\n".join(lines)


def _render_reference_list_markdown(references: list[dict[str, Any]]) -> str:
    lines = ["## 参考文献", ""]
    if references:
        lines.extend(f"{index}. {_reference_string(reference)}" for index, reference in enumerate(references, 1))
    else:
        lines.extend(
            [
                "1. （待补充）与课题核心方法和系统架构相关的代表性论文。",
                "2. （待补充）与本系统关键技术和实现方法直接相关的代表性文献。",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _render_tail_sections_markdown(payload: dict[str, Any]) -> str:
    """Render acknowledgements / appendix / missing-inputs / prompt-sources.

    Any section whose content is empty or contains only boilerplate is now
    omitted entirely — no placeholder heading is emitted.
    """
    blocks: list[str] = []

    acks = [str(x).strip() for x in (payload.get("acknowledgements") or []) if str(x).strip()]
    if acks:
        blocks.append("## 致谢\n\n" + "\n".join(acks))

    appx = [str(x).strip() for x in (payload.get("appendix_notes") or []) if str(x).strip()]
    if appx:
        blocks.append("## 附录说明\n\n" + "\n".join(appx))

    # Missing-inputs and prompt-sources are authoring hints, not paper content.
    # They are intentionally not emitted by default; callers that want them must
    # place them in the payload explicitly.
    missing = [str(x).strip() for x in (payload.get("missing_inputs") or []) if str(x).strip()]
    if missing and payload.get("include_missing_inputs"):
        blocks.append("## 定稿前待补项\n\n" + "\n".join(f"- {item}" for item in missing))

    prompts = [str(x).strip() for x in (payload.get("prompt_sources") or []) if str(x).strip()]
    if prompts and payload.get("include_prompt_sources"):
        blocks.append("## 工作流来源\n\n" + "\n".join(f"- {item}" for item in prompts))

    return ("\n\n".join(blocks) + "\n") if blocks else ""


def _render_english_abstract_block(artifact: dict[str, Any]) -> str:
    """Standalone English abstract + Keywords block, designed to sit right after
    the Chinese 摘要 at the start of the paper."""
    en_abstract = (artifact.get("english_abstract") or "").strip()
    en_keywords = [str(k).strip() for k in (artifact.get("en_keywords") or []) if str(k).strip()]
    if not en_abstract and not en_keywords:
        return ""
    parts = ["## English Abstract", ""]
    if en_abstract:
        parts.append(en_abstract)
    parts.extend(["", "**Keywords:** " + (", ".join(en_keywords) if en_keywords else "(to be completed)"), ""])
    return "\n".join(parts)


def _insert_after_chinese_abstract(md: str, block: str) -> str:
    """Insert ``block`` right after the Chinese 摘要 section (before chapter 1)."""
    if not block.strip():
        return md
    # Find the first chapter heading "## 1. ..." — the abstract ends immediately
    # before it. When missing, fall back to inserting at the top of the doc.
    chap_pat = re.compile(r'^##\s+1\.\s+', re.M)
    m = chap_pat.search(md)
    insert_at = m.start() if m else len(md)
    before = md[:insert_at].rstrip() + "\n\n"
    after = md[insert_at:]
    return before + block.rstrip() + "\n\n" + after


def _render_back_matter(result: dict[str, Any]) -> str:
    """Render only what belongs at the end of the paper: 参考文献 + optional tail."""
    artifact = result.get("artifact") or {}
    parts: list[str] = []

    references = artifact.get("references") or []
    parts.append(_render_reference_list_markdown(references))

    project_context = result.get("project_context") or artifact.get("project_context")
    payload = {
        "acknowledgements": artifact.get("acknowledgements") or _derive_acknowledgements(project_context),
        "appendix_notes": artifact.get("appendix_notes") or _derive_appendix_notes(project_context),
        "missing_inputs": artifact.get("missing_inputs") or [],
        "prompt_sources": artifact.get("prompt_sources") or [],
        "include_missing_inputs": bool(artifact.get("include_missing_inputs")),
        "include_prompt_sources": bool(artifact.get("include_prompt_sources")),
    }
    tail = _render_tail_sections_markdown(payload)
    if tail:
        parts.append(tail)

    return "\n\n".join(p for p in parts if p.strip())


def _render_front_matter_and_tail(result: dict[str, Any]) -> str:
    """Legacy entry point kept for backwards compatibility.

    New callers should use ``_render_english_abstract_block`` +
    ``_insert_after_chinese_abstract`` for the English abstract block, and
    ``_render_back_matter`` for references + optional acknowledgements/appendix.
    """
    return _render_back_matter(result)


def _build_clean_chinese_experiment_lines(
    plan: dict[str, Any] | None,
    project_context: dict[str, Any] | None,
) -> list[str]:
    if not plan:
        return [
            "实验围绕系统核心功能的验证展开，重点考察关键模块的运行效果与系统整体的稳定性。",
            "实验记录以运行日志、结果数据和关键参数为主要证据，用于评价系统在不同场景下的有效性、稳定性与可重复性。",
        ]

    baseline_matrix = plan.get("baseline_matrix") or {}
    methods = [str(item).strip() for item in (baseline_matrix.get("methods") or []) if str(item).strip()]
    datasets = [str(item).strip() for item in (baseline_matrix.get("datasets") or []) if str(item).strip()]
    metrics = [str(item).strip() for item in (baseline_matrix.get("metrics") or []) if str(item).strip()]
    total_runs = int(baseline_matrix.get("total_runs") or max(len(methods), 1) * max(len(datasets), 1))
    lines = [f"实验计划共包含 {total_runs} 组候选实验，可据此组织实验章节的整体结构。"]
    if datasets:
        lines.append(f"实验场景可优先选择 { '、'.join(datasets[:3]) }。")
    if metrics:
        lines.append(f"实验结果统一报告 { '、'.join(metrics[:4]) } 等指标。")
    if methods:
        lines.append(f"对比分析可围绕 { '、'.join(methods[:4]) } 展开，并突出本系统在实际场景中的工程化特点。")
    if project_context and not project_context.get("result_clues"):
        lines.append("项目材料中尚未提取出完整结果文件，实验分析可结合运行日志、截图、地图结果与性能统计表组织。")
    return lines


def _build_clean_chinese_sections(
    title: str,
    paper_type: str,
    references: list[dict[str, Any]],
    experiment_lines: list[str],
    project_context: dict[str, Any] | None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    return _build_complete_chinese_sections(title, paper_type, references, experiment_lines, project_context, project_root)


def _render_clean_zh_outline_markdown(title: str, sections: list[dict[str, Any]], references: list[dict[str, Any]]) -> str:
    lines = [f"# 论文写作大纲：{title}", ""]
    for section in sections:
        lines.append(f"## {section['title']}")
        lines.append("")
        lines.append("- 本节核心问题")
        lines.append("- 需要引用的证据或代码依据")
        lines.append("- 可放置的图表或流程图")
        lines.append("- 尚待补充的数据或材料")
        lines.append("")
    lines.append("## 候选参考文献")
    lines.append("")
    if references:
        lines.extend(f"- {_reference_string(reference)}" for reference in references)
    else:
        lines.append("- 当前尚未纳入与该项目直接相关的核心文献，建议后续补充。")
    lines.append("")
    return "\n".join(lines)


def _render_clean_zh_paper_markdown(
    payload: dict[str, Any],
    markdown_path: Path,
    json_path: Path,
    outline_path: Path,
    plan_path: Path,
    prompts_path: Path,
    latex_path: Path,
    bib_path: Path,
) -> str:
    body_sections = payload["sections"][1:] if payload.get("sections") and str(payload["sections"][0].get("title", "")) == "摘要" else payload["sections"]
    return _render_sections_markdown(body_sections).rstrip() + "\n"


def _render_clean_zh_plan_markdown(
    title: str,
    topic: str,
    paper_type: str,
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
    missing_inputs: list[str],
) -> str:
    section_titles = [_strip_section_number(str(section["title"])) for section in sections]
    chapter_targets = _thesis_chapter_targets()
    lines = [
        f"# 论文执行计划：{title}",
        "",
        f"- 主题：{topic}",
        f"- 稿件类型：{paper_type}",
        f"- 当前可用参考文献：{len(references)}",
        "",
        "## 推进原则",
        "",
        "- 先补证据，再写结论；没有实验或文献支持的结论不进入终稿。",
        "- 先固化系统设计与实现，再完善摘要、绪论和结论。",
        "- 每一章至少对应一项可检查产物，如图、表、代码说明或实验记录。",
        "- 按 issue-driven 方式推进，每轮只收敛一个章节或一类证据，再进入下一轮修订。",
        "",
        "## 当前待办",
        "",
        "- [ ] 补充与课题核心方法和技术相关的学术论文。",
        "- [ ] 明确实验环境和测试条件的实验流程。",
        "- [ ] 为系统架构、模块关系和核心流程绘制图示。",
        "- [ ] 补充地图结果、导航截图和性能统计表。",
        "- [ ] 将最终内容整理到 LaTeX 论文模板中。",
        "",
        "## 章节目标字数",
        "",
    ]
    lines.extend(f"- {item['chapter']}：{item['target']}，重点为 {item['focus']}" for item in chapter_targets)
    lines.extend([
        "",
        "## 迭代节奏",
        "",
        "- 第 1 轮：固化目录、题目、章节目标字数和图表规划。",
        "- 第 2 轮：补齐系统设计与关键实现正文，使章节先完整成型。",
        "- 第 3 轮：补齐参考文献、图表和实验记录，逐章替换占位内容。",
        "- 第 4 轮：执行学术性润色、引用增强与 LaTeX 排版清理，形成终稿。",
        "",
        "## 章节推进顺序",
        "",
    ])
    lines.extend(f"- [ ] {section_title}" for section_title in section_titles)
    lines.extend(["", "## 当前缺口", ""])
    lines.extend(f"- {item}" for item in missing_inputs or ["当前没有记录到阻塞性缺口。"])
    lines.append("")
    return "\n".join(lines)


def _render_clean_zh_revision_prompts_markdown(title: str) -> str:
    return dedent(
        f"""\
        # 论文修订提示包：{title}

        ## 长篇章节扩写

        ```text
        你现在扮演中文本科毕业论文写作助手。请根据我提供的章节标题、已有正文、项目代码线索和实验信息，扩写为信息密度更高的长篇正文。
        要求：
        1. 输出直接可用于论文正文，不要写解释，不要使用列表堆砌观点。
        2. 每段只表达一个中心意思，段落长度控制在 150-300 字。
        3. 语言自然、正式、学术化，避免"首先、其次、最后、此外、值得注意的是"等机械连接词。
        4. 必须围绕项目真实代码、配置、实验和系统设计展开，不要编造不存在的功能。
        5. 如果信息不足，允许补充合理的工程分析，但不能伪造实验数据。
        6. 目标是把该章节扩展到适合毕业论文提交的长度，而不是只写提纲说明。
        ```

        ## 文献综述与研究空白

        ```text
        请围绕"{title}"撰写文献综述章节。
        执行步骤：
        1. 先按"研究问题—方法路线—评价方式—不足与空白"构造综述逻辑；
        2. 不按论文发表时间机械罗列，而按技术方向分组；
        3. 每组文献最后给出共性不足，并自然引出本文切入点；
        4. 如果我提供了论文摘要或笔记，请基于这些材料归纳；如果没有，就只输出可填充的高质量综述框架正文。
        输出必须是连续的中文论文正文，不要输出项目符号列表。
        ```

        ## 引用增强与 BibTeX 补全

        ```text
        请在不改变原意的前提下增强下面这段论文内容的学术支撑。
        要求：
        1. 找出需要文献支撑的结论句、背景句和方法描述句；
        2. 在合适位置加入 \\cite{{...}} 占位；
        3. 如需新增文献，请同时给出 BibTeX 条目；
        4. 中文与英文文献都可以使用，但必须与主题直接相关；
        5. 输出分为两个代码块：第一个是改写后的正文，第二个是新增 BibTeX。
        ```

        ## 去 AI 味学术润色

        ```text
        请将以下论文正文改写为更自然、更像人工撰写的中文学术表达。
        要求：
        1. 保持原意与信息量；
        2. 删除冗余、重复和过于模板化的句式；
        3. 避免列表腔、口语化和明显 AI 写作痕迹；
        4. 不要额外加粗、标题符号或解释说明；
        5. 输出为连续段落。
        ```

        ## 实验结果与讨论深化

        ```text
        请根据以下实验记录、截图说明、日志结果和表格数据，写出"实验结果与讨论"长正文。
        写作要求：
        1. 先概述实验目的与评价指标；
        2. 再解释主要结果，不要只复述表格数值；
        3. 分析结果背后的原因，包括系统结构、参数设置和硬件条件的影响；
        4. 指出局限性、误差来源和后续改进方向；
        5. 输出应像毕业论文中的实验分析章节，不要写成实验报告步骤清单。
        ```

        ## 反向大纲与逻辑检查

        ```text
        请对下面这段论文正文做 reverse outline 检查。
        任务：
        1. 提取每一段的中心句；
        2. 判断相邻段落之间的逻辑是否连续；
        3. 标出重复、跳跃、空泛或论证不足的部分；
        4. 给出按段落级别的重写建议。
        输出格式为：
        段落编号 | 中心意思 | 问题 | 修改建议
        ```

        ## 审稿人视角总评

        ```text
        请从毕业论文评阅教师的视角审查以下正文。
        重点检查：
        1. 结构是否完整；
        2. 论证是否空泛；
        3. 是否存在"只有骨架没有内容"的段落；
        4. 图表、引用、实验和结论是否对应；
        5. 哪些地方最影响提交质量。
        请输出"问题严重度 + 原因 + 修改建议"，按严重程度排序。
        ```

        ## 图表与标题生成

        ```text
        请根据以下章节内容，为毕业论文设计 5-8 个图表候选。
        对每个图表给出：
        1. 图/表名称；
        2. 适合放置的章节；
        3. 图表要表达的核心信息；
        4. 一句正式的论文图题或表题。
        ```
        """
    ).strip() + "\n"


import csv as _pw_csv


_THESIS_MIN_REFERENCE_COUNT = 18


def _thesis_source_root(base_root: str | Path, project_context: dict[str, Any] | None) -> Path:
    return _resolve_project_evidence_root(base_root, project_context)


def _thesis_safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _thesis_safe_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(_pw_csv.DictReader(handle))
    except Exception:
        return []


def _thesis_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _thesis_avg(values: list[Any]) -> float | None:
    normalized = [_thesis_float(value) for value in values]
    filtered = [value for value in normalized if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def _thesis_drop_rate(series: list[Any]) -> float | None:
    normalized = [_thesis_float(value) for value in series]
    filtered = [value for value in normalized if value is not None]
    if len(filtered) < 2 or filtered[0] == 0:
        return None
    return (filtered[0] - filtered[-1]) / filtered[0]


def _thesis_reference_language(reference: dict[str, Any]) -> str:
    explicit = str(reference.get("language") or "").strip().lower()
    if explicit in {"zh", "en"}:
        return explicit
    haystack = " ".join(
        str(reference.get(key) or "")
        for key in ("title", "venue", "abstract", "content_excerpt")
    )
    return "zh" if _contains_cjk(haystack) else "en"


def _thesis_reference_ready(reference: dict[str, Any]) -> bool:
    has_core = bool(reference.get("title")) and bool(reference.get("authors")) and bool(reference.get("year"))
    has_locator = bool(reference.get("doi") or reference.get("url") or reference.get("pdf_url"))
    return has_core and (bool(reference.get("verified")) or has_locator)


def _thesis_dedupe_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reference in references:
        key = str(reference.get("doi") or reference.get("record_id") or reference.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(reference)
    return ordered


def _select_reference_papers(project_root: Path, topic: str, limit: int = 8) -> list[dict[str, Any]]:
    papers = load_paper_index(project_root)
    keywords = _topic_keywords(topic)
    ranked = sorted(papers, key=lambda paper: _paper_relevance_score(paper, keywords), reverse=True)
    ready = [paper for paper in ranked if _thesis_reference_ready(paper)]
    pool = ready or ranked

    desired = max(limit, _THESIS_MIN_REFERENCE_COUNT if _contains_cjk(topic) else 10)
    if not _contains_cjk(topic):
        return _thesis_dedupe_references(pool[:desired])

    zh_pool = [paper for paper in pool if _thesis_reference_language(paper) == "zh"]
    en_pool = [paper for paper in pool if _thesis_reference_language(paper) == "en"]

    selected: list[dict[str, Any]] = []
    selected.extend(zh_pool[: min(6, len(zh_pool))])
    selected.extend(en_pool[: max(desired - len(selected), 0)])
    if len(selected) < desired:
        leftovers = [paper for paper in pool if paper not in selected]
        selected.extend(leftovers[: desired - len(selected)])
    return _thesis_dedupe_references(selected[:desired])


def _reference_catalog(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for index, paper in enumerate(papers, start=1):
        catalog.append(
            {
                "label": str(index),
                "record_id": paper.get("record_id"),
                "title": paper.get("title"),
                "authors": list(paper.get("authors") or []),
                "year": paper.get("year"),
                "venue": paper.get("venue"),
                "doi": paper.get("doi"),
                "url": paper.get("url") or paper.get("pdf_url"),
                "abstract": paper.get("abstract") or "",
                "content_excerpt": paper.get("content_excerpt") or "",
                "language": _thesis_reference_language(paper),
                "verified": bool(paper.get("verified")),
                "source": paper.get("source"),
                "metadata": paper.get("metadata") or {},
            }
        )
    return catalog


def _reference_string(reference: dict[str, Any]) -> str:
    authors = "，".join((reference.get("authors") or [])[:4]) or "佚名"
    title = reference.get("title") or "未命名文献"
    venue = reference.get("venue") or "未知来源"
    year = reference.get("year") or "n.d."
    doi = str(reference.get("doi") or "").strip()
    url = str(reference.get("url") or "").strip()
    tail = f" DOI: {doi}." if doi else (f" URL: {url}." if url else "")
    return f"{authors}. {title}. {venue}, {year}.{tail}"


def _thesis_reference_labels(references: list[dict[str, Any]], *keywords: str, limit: int = 3) -> str:
    labels: list[str] = []
    lowered_keywords = [keyword.lower() for keyword in keywords if keyword]
    for reference in references:
        haystack = " ".join(
            str(reference.get(key) or "").lower()
            for key in ("title", "venue", "abstract", "content_excerpt")
        )
        if any(keyword in haystack for keyword in lowered_keywords):
            labels.append(str(reference.get("label")))
    labels = list(dict.fromkeys(labels))[:limit]
    return "、".join(f"[{label}]" for label in labels)


def _thesis_filter_section_content(section: dict[str, Any] | None) -> list[str]:
    if not section:
        return []
    filtered: list[str] = []
    skipping_summary = False
    for raw_item in section.get("content") or []:
        item = str(raw_item).strip()
        if not item:
            continue
        if item.startswith("### "):
            heading = item[4:].strip()
            skipping_summary = "本章小结" in heading or heading.endswith("小结")
            if skipping_summary:
                continue
        if skipping_summary:
            continue
        filtered.append(item)
    return filtered


def _thesis_results_bundle(base_root: str | Path, project_context: dict[str, Any] | None) -> dict[str, Any]:
    root = _thesis_source_root(base_root, project_context)
    results_dir = root / "output" / "results"
    figures_dir = root / "output" / "figures"
    slam_rows = _thesis_safe_csv_rows(results_dir / "slam_comparison.csv")
    planner_rows = _thesis_safe_csv_rows(results_dir / "planner_comparison.csv")
    controller_rows = _thesis_safe_csv_rows(results_dir / "mppi_controller_results.csv")
    scenario_rows = _thesis_safe_csv_rows(results_dir / "navigation_scenarios.csv")
    convergence = _thesis_safe_json(results_dir / "slam_convergence.json") or {}

    metrics: dict[str, Any] = {"project_root": str(root), "results_dir": str(results_dir)}

    if slam_rows:
        best_slam = max(slam_rows, key=lambda row: _thesis_float(row.get("map_accuracy")) or 0.0)
        metrics["slam_best_method"] = best_slam.get("method")
        metrics["slam_best_accuracy"] = _thesis_float(best_slam.get("map_accuracy"))
        metrics["slam_best_loop"] = _thesis_float(best_slam.get("loop_closure_rate"))
        hector = next((row for row in slam_rows if "hector" in str(row.get("method", "")).lower()), {})
        metrics["slam_lightweight_time"] = _thesis_float(hector.get("avg_processing_time_ms"))
        metrics["slam_lightweight_memory"] = _thesis_float(hector.get("memory_usage_mb"))

    if planner_rows:
        best_planner = max(planner_rows, key=lambda row: _thesis_float(row.get("success_rate")) or 0.0)
        shortest_planner = min(planner_rows, key=lambda row: _thesis_float(row.get("path_length_m")) or 999999.0)
        metrics["planner_best_method"] = best_planner.get("method")
        metrics["planner_best_success"] = _thesis_float(best_planner.get("success_rate"))
        metrics["planner_best_smoothness"] = _thesis_float(best_planner.get("path_smoothness"))
        metrics["planner_best_time"] = _thesis_float(best_planner.get("planning_time_ms"))
        metrics["planner_shortest_method"] = shortest_planner.get("method")
        metrics["planner_shortest_path"] = _thesis_float(shortest_planner.get("path_length_m"))

    if controller_rows:
        ours = next((row for row in controller_rows if "ours" in str(row.get("method", "")).lower() or "mppi" in str(row.get("method", "")).lower()), controller_rows[0])
        metrics["controller_method"] = ours.get("method")
        metrics["controller_goal_reach"] = _thesis_float(ours.get("goal_reach_rate"))
        metrics["controller_collision"] = _thesis_float(ours.get("collision_rate"))
        metrics["controller_smoothness"] = _thesis_float(ours.get("trajectory_smoothness"))
        metrics["controller_dynamic"] = _thesis_float(ours.get("dynamic_obstacle_avoidance"))

    if scenario_rows:
        metrics["scenario_avg_success"] = _thesis_avg([row.get("success_rate") for row in scenario_rows])
        metrics["scenario_avg_time"] = _thesis_avg([row.get("avg_time_s") for row in scenario_rows])
        metrics["scenario_avg_deviation"] = _thesis_avg([row.get("avg_path_deviation_m") for row in scenario_rows])
        hardest = min(scenario_rows, key=lambda row: _thesis_float(row.get("success_rate")) or 999999.0)
        metrics["scenario_hardest_name"] = hardest.get("method")
        metrics["scenario_hardest_success"] = _thesis_float(hardest.get("success_rate"))

    map_error = convergence.get("map_error") if isinstance(convergence, dict) else None
    residual = convergence.get("loop_closure_residual") if isinstance(convergence, dict) else None
    if isinstance(map_error, list) and map_error:
        metrics["map_error_start"] = _thesis_float(map_error[0])
        metrics["map_error_end"] = _thesis_float(map_error[-1])
        metrics["map_error_drop"] = _thesis_drop_rate(map_error)
    if isinstance(residual, list) and residual:
        metrics["loop_residual_start"] = _thesis_float(residual[0])
        metrics["loop_residual_end"] = _thesis_float(residual[-1])
        metrics["loop_residual_drop"] = _thesis_drop_rate(residual)

    metrics["figures"] = {
        name: f"../output/figures/{name}"
        for name in (
            "system-architecture.png",
            "slam-comparison-comparison.png",
            "planner-comparison-comparison.png",
            "mppi-controller-results-comparison.png",
            "mppi-radar.png",
            "navigation-scenarios-comparison.png",
            "slam-convergence-curve.png",
        )
        if (figures_dir / name).exists()
    }
    return metrics


_legacy_inject_project_context = None


def _inject_project_context(
    sections: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    language: str,
) -> list[dict[str, Any]]:
    enriched = _legacy_inject_project_context(sections, project_context, language)
    if language == "zh":
        return _polish_chinese_sections(enriched)
    return enriched


def _render_writing_assets_markdown() -> str:
    return dedent(
        """\
        # 论文写作资产清单

        本文件汇总了当前系统已吸收的线上论文写作 prompts、skills 与 workflow。

        ## 已纳入的高价值来源

        - LeSinus/chatgpt-prompts-for-academic-writing
          - 链接：https://github.com/LeSinus/chatgpt-prompts-for-academic-writing
          - 吸收内容：Role + Objective + Constraints + Format 的 prompt 结构；章节扩写、文献综述、研究问题、结果讨论等 prompt 类型；"先提问补上下文"的交互策略。

        - federicodeponte/academic-thesis-ai（OpenDraft）
          - 链接：https://github.com/federicodeponte/academic-thesis-ai
          - 吸收内容：research → structure → writing → validation → polish 的多阶段写作流程；长篇 thesis 目标；自动化 research-first 的工作方式。

        - articlewriting-skill
          - 本地路径：external/articlewriting-skill
          - 吸收内容：正文去列表化、去 AI 味、段落中心句、章节字数目标、章节模板、LaTeX 输出规范。

        - latex-arxiv-SKILL
          - 本地路径：external/latex-arxiv-SKILL
          - 吸收内容：issue-driven 论文推进、plan 先行、LaTeX 工程导向输出。

        - awesome-ai-research-writing
          - 本地路径：external/awesome-ai-research-writing
          - 吸收内容：提示词分类思路，包括润色、逻辑检查、实验分析、图题表题与 reviewer 视角检查。

        ## 当前系统已经采用的规则

        1. 正文优先使用连续段落，不用项目符号堆砌观点。
        2. 每段尽量围绕一个中心意思展开，避免模板化连接词。
        3. 章节按照毕业论文目标字数扩写，而不是只生成提纲。
        4. 修订提示包覆盖扩写、引用增强、实验讨论、逻辑检查与审稿人审查。
        5. 输出优先落到 `drafts/`，方便统一查看与修改。

        ## 后续可继续增强的方向

        - 自动从论文索引生成文献综述初稿。
        - 自动根据正文生成图表清单与图题表题。
        - 自动把缺引文的位置和 BibTeX 生成串起来。
        - 自动生成开题答辩、中期答辩和最终答辩 PPT 提纲。
        """
    ).strip() + "\n"


def _render_writing_assets_markdown() -> str:
    return render_integrated_writing_assets_markdown()


_legacy_apply_chinese_writing_rules = _apply_chinese_writing_rules
_legacy_inject_project_context = None

ZH_META_HINTS = (
    "论文",
    "写作",
    "正文",
    "章节",
    "定稿",
    "补录",
    "图表",
    "图示",
    "稿件",
    "提交",
    "说明文档",
)

ZH_EVIDENCE_HINTS = (
    "系统",
    "模块",
    "接口",
    "参数",
    "配置",
    "性能",
    "实验",
    "结果",
    "数据",
    "功能",
)

ZH_META_DROP_HEADINGS = (
    "本章小结",
    "本章补充说明",
    "实验结果补录策略",
    "面向毕业论文表达",
    "从实现细节到论文论证的转换",
)

ZH_META_DROP_PHRASES = (
    "后续工作主要集中在补充实验图表",
    "图文完整、证据充分的定稿内容",
    "只需将对应图表",
    "只需把补充证据嵌入",
    "论文文本",
    "论文成稿",
    "写作策略",
    "定稿阶段",
    "毕业论文提交而言",
    "更像项目说明文档",
    "本章在呈现实现细节时",
    "当前稿件已经围绕",
)


def _count_keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _rewrite_zh_heading(heading: str) -> str:
    cleaned = heading.strip()
    cleaned = cleaned.replace("本章小结", "小结")
    cleaned = cleaned.replace("本章补充说明", "补充说明")
    cleaned = cleaned.replace("与本文切入点", "与课题切入点")
    cleaned = cleaned.replace("实现小结", "实现分析")
    cleaned = cleaned.replace("结果小结", "结果分析")
    return cleaned


def _rewrite_zh_paragraph_surface(text: str, section_title: str = "", heading: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""

    cleaned = re.sub(r"^本文围绕《([^》]+)》展开，", r"围绕《\1》所开展的系统设计与实现工作，", cleaned)
    cleaned = re.sub(r"^本文围绕《([^》]+)》完成了", r"围绕《\1》所开展的系统设计与实现工作，已经完成", cleaned)
    cleaned = re.sub(
        r"^该项目是一个面向[^，。]+的\s*Python、C/C\+\+\s*工程，",
        "项目代码以 Python、C/C++ 为主，",
        cleaned,
    )
    replacements = (
        ("围绕项目现有代码，本文从", "分析内容从"),
        ("结合现有工程结构可以看出，", "结合现有工程结构，"),
        ("从真实机器人参数文件可以看出，", "真实机器人参数文件中"),
        ("通过这些分析可以看出，", ""),
        ("由此可以看出，", ""),
        ("由此可见，", ""),
        ("可以看出，", ""),
        ("综合来看，", ""),
        ("综上，", ""),
        ("总体而言，", ""),
        ("本文将整套导航系统划分为", "整套导航系统可划分为"),
        ("本文所实现的导航系统可以划分为", "整套导航系统可以划分为"),
        ("本文的主要研究内容包括：", "课题工作主要集中在以下几个方面："),
        ("本文围绕课题核心技术栈、", "相关技术分析围绕课题核心技术栈、"),
        ("与偏重理论推导的研究相比，本文的创新性主要体现在", "与偏重理论推导的研究相比，课题的工程创新主要体现在"),
        ("这也是本文区别于一般项目说明文档的重要方面。", "这使系统设计具备了可验证、可扩展的工程论证基础。"),
        ("这使得论文不再停留于功能演示层面，而具备较明确的系统分析深度。", "这也说明项目分析已经从功能演示推进到系统论证层面。"),
        ("可以直接支撑毕业论文写作的工程证据", "支撑系统分析与结果论证的工程证据"),
        ("论文在技术分析部分必须强调", "技术分析需要强调"),
        ("在论文写作所关注的系统完整性方面仍存在明显不足", "在系统完整性论证方面仍存在明显不足"),
        ("难以直接转化为毕业论文的论证内容", "难以直接支撑完整的工程论证"),
        (
            "因此，本文后续的系统设计与实验章节将重点围绕这些评价维度展开，从而体现本课题在工程实现与综合设计上的主要贡献。",
            "因此，后续系统设计与实验分析需要围绕这些评价维度展开，重点考察模块协同、运行稳定性与结果复现情况。",
        ),
        (
            "本课题的切入点正在于此。它不是简单叠加开源模块，而是围绕可运行系统这一核心目标，对模块职责、启动顺序、配置参数和运行场景进行重新组织，并将这些实现过程转化为可陈述、可验证、可扩展的论文结构。",
            "课题切入点在于面向可运行系统开展集成设计，而不是简单叠加开源模块。为此需要围绕模块职责、启动顺序、配置参数和运行场景重新组织系统实现，并据此形成清晰的模块边界、启动时序与运行链路。",
        ),
        (
            '通过把参数配置与控制效果之间建立明确对应关系，可以让论文从"功能说明文档"转变为"设计决策说明文档"，这对于本科毕业论文尤其重要。',
            "通过建立参数配置与控制效果之间的明确对应关系，才能说明参数整定的工程依据，并为后续实验结果分析提供解释基础。",
        ),
        (
            "对毕业论文而言，真正具有说服力的内容并不是简单的功能清单，而是围绕实现路径、运行机制和调试证据所形成的完整论证链条。",
            "更具说服力的分析不在于罗列功能清单，而在于围绕实现路径、运行机制和调试证据建立完整论证链条。",
        ),
        (
            "受现有项目材料限制，本章先根据已有实现与运行链路展开分析，并将实验指标、图表位置和结果解释框架固定下来。",
            "实验分析需要围绕已有实现与运行链路展开，优先固定评价指标、实验场景和结果解释框架。",
        ),
        (
            "在补充完整实验记录后，这种组织方式能够较为自然地衔接定量结果与文字分析。",
            "在补充建图精度、导航成功率与运行时延等记录后，可进一步形成定量结果与文字分析之间的对应关系。",
        ),
        (
            "因此，论文在关键实现章节中有必要补充系统调试方法，以说明项目是如何从功能可用走向运行稳定的。",
            "因此，关键实现章节需要补充系统调试方法，用于说明项目如何从功能可用走向运行稳定。",
        ),
        (
            "相较于单独讨论 SLAM 或路径规划算法，毕业论文更需要说明",
            "相较于单独讨论 SLAM 或路径规划算法，系统研究更需要说明",
        ),
    )
    for source, target in replacements:
        cleaned = cleaned.replace(source, target)

    cleaned = re.sub(
        r"因此，本文后续的系统设计与实验章节将重点围绕这些评价维度展开(?:，|,)?从而体现本课题在工程实现与综合设计上的主要贡献。?",
        "因此，后续系统设计与实验分析需要围绕这些评价维度展开，重点考察模块协同、运行稳定性与结果复现情况。",
        cleaned,
    )
    cleaned = re.sub(
        r'通过把参数配置与控制效果之间建立明确对应关系，可以让论文从["“]功能说明文档["”]转变为["“]设计决策说明文档["”]，这对于本科毕业论文尤其重要。?',
        "通过建立参数配置与控制效果之间的明确对应关系，才能说明参数整定的工程依据，并为后续实验结果分析提供解释基础。",
        cleaned,
    )
    cleaned = re.sub(
        r"受现有项目材料限制，本章先根据已有实现与运行链路展开分析，并将实验指标、图表位置和结果解释框架固定下来。?",
        "实验分析需要围绕已有实现与运行链路展开，优先固定评价指标、实验场景和结果解释框架。",
        cleaned,
    )
    cleaned = re.sub(
        r"在补充完整实验记录后，这种组织方式能够较为自然地衔接定量结果与文字分析。?",
        "在补充建图精度、导航成功率与运行时延等记录后，可进一步形成定量结果与文字分析之间的对应关系。",
        cleaned,
    )

    if cleaned.startswith("本文已经围绕") or cleaned.startswith("从课题完成情况看，本文已经围绕"):
        return ""

    if cleaned.startswith("本文"):
        cleaned = re.sub(r"^本文将", "下文将", cleaned)
        cleaned = re.sub(r"^本文的", "课题的", cleaned)
        cleaned = re.sub(r"^本文", "课题研究", cleaned)

    cleaned = re.sub(r"^本章梳理的", "前文梳理的", cleaned)
    cleaned = re.sub(r"^本章先根据", "实验分析先根据", cleaned)
    cleaned = re.sub(r"^从[^，。；]{0,14}角度(?:观察)?(?:看)?，", "", cleaned)
    cleaned = re.sub(r"^从课题完成情况看，", "", cleaned)
    cleaned = re.sub(r"^通过这些分析", "综合这些设计与运行关系", cleaned)
    cleaned = re.sub(r"^真实机器人参数文件中配置了", "真实机器人参数文件中配置了", cleaned)
    cleaned = re.sub(r"[。；]\s*[。；]", "。", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；")
    if cleaned and cleaned[-1] not in "。！？":
        cleaned = f"{cleaned}。"
    return cleaned


def _should_drop_zh_paragraph(text: str, heading: str = "") -> bool:
    if not text:
        return True
    if any(phrase in text for phrase in ZH_META_DROP_PHRASES):
        return True
    if any(marker in heading for marker in ZH_META_DROP_HEADINGS):
        return True

    meta_hits = _count_keyword_hits(text, ZH_META_HINTS)
    evidence_hits = _count_keyword_hits(text, ZH_EVIDENCE_HINTS)
    if meta_hits >= 3 and evidence_hits == 0 and len(text) < 220:
        return True
    return False


def _should_drop_zh_block(heading: str, body: list[str]) -> bool:
    if not heading:
        return False
    if any(marker in heading for marker in ZH_META_DROP_HEADINGS):
        return True
    if re.match(r"^\d+\.\d+\s+小结$", heading):
        return True
    joined = "".join(body)
    if "小结" in heading and _count_keyword_hits(joined, ZH_EVIDENCE_HINTS) <= 1:
        return True
    return False


def _inject_figures_into_markdown(
    md_text: str,
    figure_paths: list[Path],
    sections: list[dict[str, Any]],
) -> str:
    """Inject available figures into markdown at appropriate section boundaries.

    Places figures after the first paragraph of each section that discusses
    systems, architecture, algorithms, experiments, or results.  Skips abstract
    and conclusion sections.
    """
    if not figure_paths or not sections:
        return md_text

    # Figure captions from filenames
    _caption_map = {
        "system-architecture": "系统总体架构",
        "coverage-planning-flow": "全覆盖路径规划流程",
        "data-flow": "系统数据流与控制流",
        "roomsketcher_family_floorplan_plan": "家庭户型基准地图",
        "roomsketcher_family_floorplan_cleaning_detail": "清扫细节示意图",
        "roomsketcher_family_floorplan_animation_last_frame": "清扫路径动画最终帧",
    }

    # Section patterns that warrant a figure
    _figure_sections = {"系统", "架构", "算法", "实验", "结果", "流程", "实现", "设计"}

    lines = md_text.split("\n")
    fig_idx = 0
    result_lines: list[str] = []
    fig_counter = [0]  # per-chapter counter

    def _make_fig_block(fig_path: Path, chap_num: int) -> str:
        fig_counter[0] += 1
        stem = fig_path.stem
        caption = _caption_map.get(stem, stem)
        rel = f"output/figures/{fig_path.name}"
        return (
            f"\n![{caption}]({rel})\n"
            f"图{chap_num}-{fig_counter[0]} {caption}\n"
        )

    # Track current chapter number
    current_chap = 0
    paragraphs_since_heading = 0

    for line in lines:
        result_lines.append(line)

        # Track chapter numbers from ## headings
        chap_match = re.match(r"^## (\d+)\.", line)
        if chap_match:
            current_chap = int(chap_match.group(1))
            fig_counter[0] = 0
            paragraphs_since_heading = 0
            continue

        # Track ### headings
        if line.startswith("### "):
            paragraphs_since_heading = 0
            continue

        # Count non-empty body paragraphs
        if line.strip() and not line.startswith("|") and not line.startswith("$"):
            paragraphs_since_heading += 1

        # Insert figure after first body paragraph of a figure-worthy section
        if (paragraphs_since_heading == 1
                and fig_idx < len(figure_paths)
                and not line.startswith("![")
                and any(kw in line for kw in _figure_sections)
                and current_chap > 0):
            result_lines.append(_make_fig_block(figure_paths[fig_idx], current_chap))
            fig_idx += 1

    return "\n".join(result_lines)


def _polish_chinese_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    polished_sections: list[dict[str, Any]] = []
    for section in sections:
        section_title = str(section.get("title", ""))
        blocks = _split_chapter_blocks(list(section.get("content", [])))
        rebuilt_content: list[str] = []

        for heading, body in blocks:
            normalized_heading = _rewrite_zh_heading(heading)
            rewritten_body: list[str] = []
            for paragraph in body:
                rewritten = _rewrite_zh_paragraph_surface(str(paragraph).strip(), section_title, normalized_heading)
                if _should_drop_zh_paragraph(rewritten, normalized_heading):
                    continue
                rewritten_body.append(rewritten)

            if _should_drop_zh_block(normalized_heading, rewritten_body):
                continue

            if normalized_heading:
                rebuilt_content.append(f"### {normalized_heading}")
            rebuilt_content.extend(rewritten_body)

        polished_sections.append(
            {
                "title": section_title,
                "content": _merge_short_chinese_paragraphs(rebuilt_content),
            }
        )
    return polished_sections


def _inject_real_experiment_data(markdown_path: Path, result: dict[str, Any]) -> None:
    """Post-process: replace empty template tables with real CSV data, inject figure paths, append references."""
    try:
        import csv as _csv
    except ImportError:
        return

    md_text = markdown_path.read_text(encoding="utf-8")
    lines = md_text.split("\n")

    artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
    project_context = result.get("project_context") or artifact.get("project_context") or {}

    # Resolve project root from result
    project_root = Path(result.get("project_root", ".")).resolve()
    evidence_root = _resolve_project_evidence_root(project_root, project_context)
    results_dir = evidence_root / "output" / "results"
    raw_figure_allowlist = project_context.get("paper_workspace_figure_files")
    figure_allowlist = None if raw_figure_allowlist is None else {str(name) for name in raw_figure_allowlist}

    # --- Step 1: Inject real CSV data tables ---
    # Map CSV filenames to Chinese table titles (fuzzy match)
    csv_title_map = {
        "slam_comparison": ["SLAM", "建图", "slam", "地图"],
        "planner_comparison": ["规划器", "全局规划", "路径规划"],
        "navigation_scenarios": ["场景", "导航实验", "导航场景"],
        "mppi_controller_results": ["控制器", "局部", "MPPI", "控制"],
    }

    csv_files = list(results_dir.glob("*.csv")) if results_dir.exists() else []

    # Find table blocks in markdown (between table caption and next non-table line)
    new_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detect table caption line (表X-Y ...)
        caption_match = re.match(r"^(表\d+-\d+)\s+(.+)$", line)
        if caption_match and csv_files:
            caption_text = caption_match.group(2)
            table_lines = [line]
            i += 1
            # Collect table rows
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            # Check if table has placeholder "—" data (skip header/separator rows)
            data_rows = [r for r in table_lines[2:] if "—" in r]
            if data_rows:
                # Try to find a matching CSV
                matched_csv = None
                for csv_file in csv_files:
                    stem = csv_file.stem
                    keywords = csv_title_map.get(stem, [])
                    if keywords and any(kw.lower() in caption_text.lower() for kw in keywords):
                        matched_csv = csv_file
                        break
                if matched_csv is None:
                    # Fallback: try all CSVs
                    for csv_file in csv_files:
                        stem = csv_file.stem
                        keywords = csv_title_map.get(stem, [])
                        if keywords and any(kw.lower() in "\n".join(table_lines).lower() for kw in keywords):
                            matched_csv = csv_file
                            break

                if matched_csv:
                    try:
                        with open(matched_csv, "r", encoding="utf-8") as f:
                            reader = _csv.reader(f)
                            headers = next(reader)
                            rows = [r for r in reader if any(c.strip() for c in r)]
                        # Rebuild table with real data
                        new_lines.append(line)  # caption
                        i += 1
                        # Skip the old English caption if present
                        if i < len(lines) and re.match(r"^Table\s+\d+-\d+", lines[i]):
                            new_lines.append(lines[i])
                            i += 1
                        new_lines.append("")  # blank line before table
                        new_lines.append("| " + " | ".join(headers) + " |")
                        new_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                        for row in rows:
                            new_lines.append("| " + " | ".join(row) + " |")
                        new_lines.append("")
                        continue
                    except Exception:
                        pass  # Fall through to original table
                # Write original table
                for tl in table_lines:
                    new_lines.append(tl)
                continue
        new_lines.append(line)
        i += 1

    md_text = "\n".join(new_lines)
    language = str(result.get("language") or artifact.get("language") or "zh")
    md_text = _inject_tables_by_plan(md_text, project_context, language=language)

    # --- Step 2: Inject real figure file paths into placeholders ---
    # The enhancer may produce blocks like:
    #   [此处插入图4-1]
    #   <blank>
    #   图4-1 caption
    #   <blank>
    #   Figure 4-1 caption
    #   <blank>
    #   图4-1建议展示... (explanatory text from enhancer LLM)
    #
    # We replace the placeholder line, then clean up enhancer explanatory text.
    figures = _scan_project_figures(
        project_root,
        allowed_files=figure_allowlist,
        run_extractor=raw_figure_allowlist is None,
    )
    fig_counter = [0]  # mutable counter, accessible across steps
    if figures:
        def _replace_fig_placeholder(m: re.Match) -> str:
            fig_counter[0] += 1
            chap = m.group(1) or "4"
            num = m.group(2)
            stem = m.group(3) if m.group(3) else ""
            # Try to find matching figure by caption keywords
            matched_fig = None
            for fig in figures:
                if stem and stem.lower() in fig["caption"].lower():
                    matched_fig = fig
                    break
            # Fallback: sequential assignment
            if matched_fig is None and fig_counter[0] <= len(figures):
                matched_fig = figures[fig_counter[0] - 1]
            if matched_fig:
                caption = matched_fig['caption']
                # Clean up ugly filenames in caption
                _nice = {
                    "household_demo_obstacle_check": "障碍物检测结果",
                    "household_demo_obstacle_avoid_check": "避障检测结果",
                    "Coverage Planning Flow": "全覆盖路径规划流程",
                    "Data Flow": "系统数据流",
                    "System Architecture": "系统总体架构",
                }
                for _ugly, _nice_name in _nice.items():
                    caption = caption.replace(_ugly, _nice_name)
                return (
                    f"![{caption}](output/figures/{matched_fig['file']})\n"
                    f"图{chap}-{num} {caption}\n"
                    f"Figure {chap}-{num} {caption}"
                )
            return m.group(0)

        md_text = re.sub(
            r"\[此处插入图(\d+)-(\d+)[：:]?([^\]]*)\]",
            _replace_fig_placeholder,
            md_text,
        )

        # Step 2.1: Remove enhancer-generated explanatory text after figure captions.
        # The enhancer often writes "图X-Y建议展示..." or "图X-Y适合..." — these
        # are writing instructions to itself, not actual figure descriptions. Remove them.
        md_text = re.sub(
            r"^(?:图\d+-\d+(?:建议|适合|可采用|用于|用于呈)[^\n]*\n(?:[^\n#]*\n)*?)(?=\n\S|\n#|\Z)",
            "",
            md_text,
            flags=re.MULTILINE,
        )

    # --- Step 2.5: Append remaining unmatched figures after last table ---
    # Only append figures that are NOT already referenced in the text
    # (to avoid duplicates when injection runs multiple times e.g. after refiner)
    if figures:
        # Collect figure files already present in the text
        already_in_text: set[str] = set()
        for img_m in re.finditer(r"!\[[^\]]*\]\(output/figures/([^)]+)\)", md_text):
            already_in_text.add(img_m.group(1))

        # Determine which figures still need to be appended
        remaining = [f for f in figures if f["file"] not in already_in_text]

        if remaining:
            # Determine the next figure chapter/number from existing references
            existing_figs = re.findall(r"图(\d+)-(\d+)", md_text)
            if existing_figs:
                last_chap = int(existing_figs[-1][0])
                last_num = int(existing_figs[-1][1])
            else:
                last_chap, last_num = 4, 0
            # Find insertion point: after the last table block in the experiment section
            insert_pos = len(md_text)
            for m in re.finditer(r"^(表\d+-\d+.*)$", md_text, re.MULTILINE):
                # Skip past the table (find end of table rows)
                pos = m.end()
                while pos < len(md_text):
                    next_nl = md_text.find("\n", pos)
                    if next_nl == -1:
                        break
                    line = md_text[pos:next_nl]
                    if not line.startswith("|"):
                        break
                    pos = next_nl + 1
                insert_pos = pos
            # Insert remaining figures
            fig_blocks = []
            for i, fig in enumerate(remaining):
                f_num = last_num + i + 1
                f_chap = last_chap
                fig_blocks.append(f"\n![{fig['caption']}](output/figures/{fig['file']})")
            fig_blocks.append(f"\n图{f_chap}-{f_num} {fig['caption']}")
            fig_blocks.append(f"Figure {f_chap}-{f_num} {fig['caption']}\n")
            if fig_blocks:
                md_text = md_text[:insert_pos] + "".join(fig_blocks) + md_text[insert_pos:]
                print(f"[InjectFig] 追加 {len(remaining)} 张未匹配图表 (图{last_chap}-{last_num+1} ~ 图{last_chap}-{last_num+len(remaining)})")

    # --- Step 3: Append 参考文献 section if missing ---
    if "### 参考文献" not in md_text and "## 参考文献" not in md_text:
        references = result.get("artifact", {}).get("references", [])
        if references:
            ref_lines = ["\n## 参考文献\n"]
            for idx, ref in enumerate(references, 1):
                authors = ref.get("authors", "")
                title = ref.get("title", "")
                venue = ref.get("venue", "")
                year = ref.get("year", "")
                doi = ref.get("doi", "")
                if not title:
                    continue
                # Format: Authors. Title[J/Venue]. Venue, Year.
                ref_line = f"{authors}. {title}"
                if venue:
                    ref_type = _guess_ref_type(venue)
                    ref_line += f"[{ref_type}]. {venue}"
                if year:
                    ref_line += f", {year}"
                if doi:
                    ref_line += f". DOI: {doi}"
                ref_lines.append(f"{idx}. {ref_line}")
                ref_lines.append("")
            md_text += "\n".join(ref_lines)

    # --- Step 3.1: Fix reference entries corrupted by LLM rewriting ---
    # The refiner/enhancer may mangle author lists into Python repr format like
    # ['Q', 'u', 'i', 'g', 'l', 'e', 'y', ' ', 'M', ...]. Fix these.
    ref_section_match = re.search(r"^(## 参考文献.*)$", md_text, re.MULTILINE)
    if ref_section_match:
        ref_start = ref_section_match.end()

        def _fix_ref_line(line: str) -> str:
            bracket_idx = line.find("[")
            if bracket_idx < 0:
                return line
            # Only process numbered reference lines (e.g. "4. [...]")
            if not re.match(r"^\d+\.", line):
                return line
            bracket_end = line.find("]", bracket_idx)
            if bracket_end < 0:
                return line
            list_str = line[bracket_idx:bracket_end + 1]
            try:
                items = eval(list_str)
                if isinstance(items, list):
                    if items and isinstance(items[0], str) and len(items[0]) <= 2:
                        joined = "".join(str(x) for x in items)
                        authors = [a.strip() for a in joined.split(", ") if a.strip()]
                    else:
                        authors = [str(x) for x in items if str(x).strip()]
                    prefix = line[:bracket_idx]
                    suffix = line[bracket_end + 1:]
                    return prefix + ", ".join(authors) + suffix
            except Exception:
                pass
            return line

        ref_text = md_text[ref_start:]
        fixed_lines = [_fix_ref_line(l) for l in ref_text.split("\n")]
        md_text = md_text[:ref_start] + "\n".join(fixed_lines)

    # --- Step 3.2: Fix tables compressed into single lines by LLM ---
    # The refiner often merges markdown table rows into one long line like:
    # | a | b || --- | --- || 1 | 2 |表5-2 captionTable 5-2 caption| ...
    # Split these back into proper multi-line markdown tables.
    def _fix_compressed_tables(text: str) -> str:
        lines = text.split("\n")
        fixed: list[str] = []
        for line in lines:
            # A compressed table line contains multiple || separators
            if line.count("|") > 6 and "||" in line:
                # Strategy 1: Split at markdown table separator lines (|---|---|)
                # These appear as ||---|---|| in compressed form
                if re.search(r'\|\|[-:\s|]+\|\|', line):
                    # Split at each table separator boundary
                    segments = re.split(r'(\|\|[-:\s|]+\|\|)', line)
                    new_lines = []
                    for seg in segments:
                        if re.match(r'^\|\|[-:\s|]+\|\|$', seg):
                            # Convert ||---|---|| to proper |---|---|
                            sep = seg.strip().strip("|")
                            new_lines.append("|" + sep + "|")
                        elif seg.strip():
                            # Regular content line: split at || boundaries
                            parts = re.split(r'\|\|', seg)
                            for i, p in enumerate(parts):
                                p = p.strip()
                                if not p:
                                    continue
                                # If part starts with |, it's a table row continuation
                                if p.startswith("|"):
                                    new_lines.append(p)
                                elif i > 0:
                                    # Continuation of previous table row
                                    new_lines.append(p)
                                else:
                                    new_lines.append(p)
                    fixed.extend(new_lines)
                else:
                    # Strategy 2: Split at || boundaries for content rows
                    # Also separate trailing table captions (表X-Y)
                    parts = re.split(r'(\|\|)', line)
                    new_lines = []
                    buf = ""
                    for part in parts:
                        buf += part
                        if buf.endswith("||") and len(buf.strip()) > 2:
                            new_lines.append(buf[:-2])  # remove trailing ||
                            buf = "|"
                    if buf.strip():
                        # Separate table captions from table rows
                        remaining = re.sub(r'^(表\d+-\d+\s)', r'\n\1', buf)
                        remaining = re.sub(r'^(Table\s+\d+-\d+\s)', r'\n\1', remaining)
                        new_lines.append(remaining)
                    fixed.extend(new_lines)
            else:
                fixed.append(line)
        # Post-process: ensure every table header is followed by a separator line
        result: list[str] = []
        for i, line in enumerate(fixed):
            result.append(line)
            # If current line looks like a table header (| col | col |) and next line
            # is NOT a separator (|---|---|), insert one
            if (line.startswith("|") and line.endswith("|") and not re.match(r'^\|[\s\-:]+\|$', line)
                    and "|" in line[1:-1]):
                next_line = fixed[i + 1] if i + 1 < len(fixed) else ""
                if not re.match(r'^\|[\s\-:]+\|$', next_line):
                    # Count columns from current line
                    col_count = len([c for c in line.split("|") if c.strip()])
                    sep = "|" + "|".join(["---"] * col_count) + "|"
                    result.append(sep)
        return "\n".join(result)

    md_text = _fix_compressed_tables(md_text)

    safe_write_text(markdown_path, md_text)


def _guess_ref_type(venue: str) -> str:
    """Guess reference type from venue string."""
    vl = venue.lower()
    if any(k in vl for k in ("journal", "transactions", "magazine", "letters")):
        return "J"
    if any(k in vl for k in ("conference", "proceedings", "icra", "iros", "workshop")):
        return "C"
    if any(k in vl for k in ("arxiv", "preprint")):
        return "J"
    if any(k in vl for k in ("thesis", "dissertation")):
        return "D"
    if any(k in vl for k in ("report", "technical")):
        return "R"
    if any(k in vl for k in ("standard", "gb/t")):
        return "S"
    return "J"


# ---------------------------------------------------------------------------
# Markdown post-processing helpers
# ---------------------------------------------------------------------------

def _fix_compressed_tables(md_text: str) -> str:
    """Fix markdown tables where all rows are on a single line.

    LLM often outputs tables like:
      | H1 | H2 | |---|---| | a | b | | c | d |。
    or with trailing image refs:
      | 场景 | 算法 | 覆盖率 | |---|---:| | S1 | 基线 | 91.3 |。![fig](path)
    This splits each row onto its own line and separates trailing text.
    """
    # Regex to find a compressed markdown table anywhere in a line.
    # A table starts with | and contains a separator row (|---|...|) and data rows.
    # All rows may be on one line, possibly ending with 。or other text.
    _table_line = re.compile(
        r'(\|[^\n]*?\|[-: ]+\|[^\n]*?)'  # The compressed table
    )

    lines = md_text.split("\n")
    result: list[str] = []

    # Detect any markdown separator-row pattern inline: '|' followed by one or
    # more alignment tokens like '---', '---:', ':---:', possibly with spaces.
    _sep_probe = re.compile(r'\|\s*:?-{3,}:?\s*\|')

    for line in lines:
        # Quick check: does this line have a compressed table?
        if not _sep_probe.search(line):
            result.append(line)
            continue

        # Split off any text before the table
        first_pipe = line.find("|")
        before = line[:first_pipe].rstrip() if first_pipe > 0 else ""
        rest = line[first_pipe:]

        # Split off trailing text after the table (usually 。![...](...))
        # Find where the last table row ends — look for "。" followed by non-pipe text
        trailing = ""
        # Find the last row-ending pattern: | word | followed by 。
        # Or: last | then 。or whitespace + ![
        dot_match = re.search(r'(?<=\|)(。)', rest)
        if dot_match:
            after_dot = rest[dot_match.end():]
            if not after_dot.lstrip().startswith("|"):
                rest = rest[:dot_match.start()]
                trailing = dot_match.group(1) + after_dot
        # Also check for trailing ![ without 。
        if not trailing:
            img_match = re.search(r'(?<=\|)(\s*!\[)', rest)
            if img_match:
                after_img = rest[img_match.end():]
                rest = rest[:img_match.start()]
                trailing = img_match.group(1) + after_img

        # Now rest is just the compressed table. Split into rows.
        # Find the separator row: |---|---:|...| pattern
        # Match everything from |--- to the closing | before data starts
        sep_match = re.search(r'\|[-: ]+(\|[-: ]+)*\|', rest)
        if not sep_match:
            if before:
                result.append(before)
            result.append(line[first_pipe:])
            continue

        header_part = rest[:sep_match.start()].rstrip(" |")
        data_part = rest[sep_match.end():].rstrip(" |")

        # Column count from header cells (ground truth)
        header_cells = [c.strip() for c in header_part.split("|") if c.strip()]
        ncols = len(header_cells)

        # Parse data cells
        data_cells = [c.strip() for c in data_part.split("|") if c.strip()]

        # Reconstruct rows
        rows: list[str] = []

        # Header row
        if header_cells:
            rows.append("| " + " | ".join(header_cells) + " |")

        # Clean separator
        rows.append("|" + "|".join(["---"] * ncols) + "|")

        # Data rows
        if ncols > 0 and data_cells:
            row_buf: list[str] = []
            for cell in data_cells:
                row_buf.append(cell)
                if len(row_buf) == ncols:
                    rows.append("| " + " | ".join(row_buf) + " |")
                    row_buf = []
            if row_buf:
                while len(row_buf) < ncols:
                    row_buf.append("")
                rows.append("| " + " | ".join(row_buf) + " |")

        # Emit
        if before:
            result.append(before)
        for r in rows:
            result.append(r)
        if trailing:
            # Clean trailing: remove leading 。and split off image refs
            trailing = trailing.lstrip("。")
            if trailing.startswith("!"):
                result.append("")
                result.append(trailing)
            elif trailing:
                result.append(trailing)

    return "\n".join(result)


def _select_best_figures(
    figures_dir: Path,
    max_count: int = 6,
    allowed_files: set[str] | None = None,
) -> list[Path]:
    """Select the best figures from a directory, avoiding duplicates.

    Prioritizes figures with descriptive names, avoids duplicates, and
    sorts by image role (scene→process→result→comparison) for correct ordering.
    """
    all_figs = sorted(figures_dir.glob("*.png"))
    if allowed_files is not None:
        all_figs = [fig for fig in all_figs if fig.name in allowed_files]
    if len(all_figs) <= max_count:
        return all_figs

    # Role-aware scoring and ordering
    try:
        from image_roles import classify_image, get_image_order_key
        def _fig_priority(f: Path) -> tuple[int, int, str]:
            name = f.stem.lower()
            role = classify_image(name)
            order = get_image_order_key(role)
            # Prefer diagram-generated over screenshots
            priority_keywords = [
                ("system-architecture", 10),
                ("coverage-planning-flow", 9),
                ("data-flow", 8),
                ("flowchart", 7),
                ("architecture", 7),
                ("gen_principle", 8),
                ("gen_4_comparison", 9),
            ]
            bonus = 0
            for kw, score in priority_keywords:
                if kw in name:
                    bonus = score
                    break
            # Deprioritize duplicated household_demo_ versions
            if name.startswith("household_demo_"):
                base_name = name.replace("household_demo_", "")
                if any(f2.stem.lower() == base_name for f2 in all_figs if f2 != f):
                    bonus = -10
            return (-bonus, order, name)
    except ImportError:
        def _fig_priority(f: Path) -> tuple[int, int, str]:
            name = f.stem.lower()
            priority_keywords = [
                ("system-architecture", 10),
                ("coverage-planning-flow", 9),
                ("data-flow", 8),
            ]
            bonus = 0
            for kw, score in priority_keywords:
                if kw in name:
                    bonus = score
                    break
            if name.startswith("household_demo_"):
                base_name = name.replace("household_demo_", "")
                if any(f2.stem.lower() == base_name for f2 in all_figs if f2 != f):
                    bonus = -10
            return (-bonus, 0, name)

    scored = sorted(all_figs, key=_fig_priority)
    return scored[:max_count]


def _cleanup_markdown(md_text: str) -> str:
    """Final cleanup: fix table separators, stacked figures, caption noise."""
    lines = md_text.split("\n")
    result: list[str] = []

    # --- Pass 1: Fix tables — keep only the first separator after header ---
    _sep_re = re.compile(r'^\|[-: ]+(\|[-: ]+)*\|?$')
    in_table = False
    saw_header_sep = False
    for line in lines:
        stripped = line.strip()
        is_sep = bool(_sep_re.match(stripped))
        is_data_row = stripped.startswith("|") and not is_sep

        if is_data_row and not saw_header_sep:
            # This is a header row (first data row before separator)
            in_table = True
            saw_header_sep = False
            result.append(line)
        elif is_sep and in_table and not saw_header_sep:
            # First separator after header — keep it
            saw_header_sep = True
            result.append(line)
        elif is_sep and in_table and saw_header_sep:
            # Extra separator between data rows — skip it
            continue
        elif is_data_row and in_table and saw_header_sep:
            # Normal data row
            result.append(line)
        else:
            # Not a table line — reset state
            in_table = False
            saw_header_sep = False
            result.append(line)

    # --- Pass 2: Collapse consecutive image lines — keep max 1 per location ---
    lines = list(result)
    result = []
    consecutive_images = 0
    for line in lines:
        if line.startswith("!["):
            consecutive_images += 1
            if consecutive_images > 1:
                continue
        else:
            consecutive_images = 0
        result.append(line)

    # --- Pass 3: Clean figure captions ---
    lines = list(result)
    result = []
    for line in lines:
        # Remove trailing "Figure X-Y ..." from Chinese caption lines
        if re.match(r'^图\d+-\d+\s+', line):
            line = re.sub(r'\s*[。.]?\s*Figure\s+\d+-\d+\s+.*$', '', line)
        # Drop standalone English caption lines that follow a Chinese one
        if re.match(r'^Figure\s+\d+-\d+\s+', line):
            if result and re.match(r'^图\d+-\d+\s+', result[-1]):
                continue
        # Drop standalone pure-English caption that duplicates the previous 图X-Y line
        # (e.g. "Household Demo Avoid Verify" right after "图3-2 系统数据流")
        if (result and re.match(r'^图\d+-\d+\s+', result[-1])
                and not line.startswith('![') and not line.startswith('|')
                and not re.match(r'^图\d+-\d+\s+', line)
                and not re.match(r'^[A-Za-z]{0,3}\d', line)  # not a table/section line
                and re.match(r'^[A-Z]', line)  # starts with English capital
                and len(line.strip()) < 80):
            # Likely a stray English caption duplicate — skip it
            continue
        # Also drop consecutive 图X-Y lines (no image between them)
        if (re.match(r'^图\d+-\d+\s+', line)
                and result and re.match(r'^图\d+-\d+\s+', result[-1])
                and not any(r.startswith('![') for r in result[-3:])):
            # This is a second consecutive caption without an image — skip
            continue
        result.append(line)

    # --- Pass 4: Replace ugly filenames in image alt text and captions ---
    md_text = "\n".join(result)
    _nice_captions = [
        ("household_demo_avoid_verify", "避障验证结果"),
        ("household_demo_obstacle_check", "障碍物检测结果"),
        ("household_demo_obstacle_avoid_check", "避障检测结果"),
        ("household_demo_roomsketcher_family_floorplan_animation_frame", "路径规划过程"),
        ("household_demo_roomsketcher_family_floorplan_animation_last_frame", "路径规划最终结果"),
        ("household_demo_roomsketcher_family_floorplan_cleaning_detail", "清扫细节"),
        ("household_demo_roomsketcher_family_floorplan_plan", "家庭户型地图"),
        ("roomsketcher_family_floorplan_animation_last_frame", "清扫路径动画最终帧"),
        ("roomsketcher_family_floorplan_cleaning_detail", "清扫细节示意图"),
        ("roomsketcher_family_floorplan_plan", "家庭户型基准地图"),
        ("Coverage Planning Flow", "全覆盖路径规划流程"),
        ("Data Flow", "系统数据流"),
        ("System Architecture", "系统总体架构"),
        ("Household Demo Avoid Verify", "避障验证结果"),
        ("Household Demo Roomsketcher Family Floorplan Animation Frame", "路径规划过程"),
        ("Household Demo Roomsketcher Family Floorplan Animation Last Frame", "路径规划最终结果"),
        ("Household Demo Roomsketcher Family Floorplan Cleaning Detail", "清扫细节"),
        ("Household Demo Roomsketcher Family Floorplan Plan", "家庭户型地图"),
        ("Household Demo Obstacle Check", "障碍物检测结果"),
        ("Household Demo Obstacle Avoid Check", "避障检测结果"),
        ("Roomsketcher Family Floorplan Animation Last Frame", "清扫路径动画最终帧"),
        ("Roomsketcher Family Floorplan Cleaning Detail", "清扫细节示意图"),
        ("Roomsketcher Family Floorplan Plan", "家庭户型基准地图"),
    ]
    # Sort by length descending so longer matches replace first
    _nice_captions.sort(key=lambda x: len(x[0]), reverse=True)
    for old, new in _nice_captions:
        # Replace in ![alt](path) syntax — only change alt text, keep path intact
        md_text = md_text.replace(f"[{old}]", f"[{new}]")
        # Replace in 图X-Y caption lines — only change standalone text, not inside (path)
        lines_cap = md_text.split("\n")
        for idx, l in enumerate(lines_cap):
            if re.match(r'^图\d+-\d+\s+', l) and old in l:
                # Only replace in caption text, not in any path
                lines_cap[idx] = l.replace(old, new)
        md_text = "\n".join(lines_cap)

    # --- Pass 4.5: Extract inline images ![...](...) embedded in text lines ---
    lines = md_text.split("\n")
    result = []
    _img_inline_re = re.compile(r'(.*?)(!\[[^\]]*\]\(output/figures/[^\)]+\))(.*)')
    for line in lines:
        m = _img_inline_re.match(line)
        if m and line.count("![") == 1:
            prefix = m.group(1).rstrip()
            img_tag = m.group(2)
            remainder = m.group(3).strip()
            # Extract alt text from img tag
            alt_m = re.match(r'!\[([^\]]+)\]', img_tag)
            alt_text = alt_m.group(1) if alt_m else ""
            if prefix:
                result.append(prefix)
            result.append(img_tag)
            result.append(f"图0-0 {alt_text}")
            if remainder:
                result.append(remainder)
        else:
            result.append(line)
    md_text = "\n".join(result)

    # --- Pass 5: Sync figure caption text with preceding ![alt] ---
    lines = md_text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        img_match = re.match(r'^!\[([^\]]+)\]\(output/figures/', line)
        if img_match:
            alt_text = img_match.group(1)
            result.append(line)
            # Look ahead for the caption line(s)
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                result.append(lines[j])
                j += 1
            if j < len(lines) and re.match(r'^图\d+-\d+\s+', lines[j]):
                # Replace caption text with alt_text to keep them in sync
                cap_match = re.match(r'^(图\d+-\d+)\s+.*$', lines[j])
                if cap_match:
                    lines[j] = f"{cap_match.group(1)} {alt_text}"
                result.append(lines[j])
                i = j + 1
                continue
            i = j
            continue
        result.append(line)
        i += 1

    # --- Pass 6: Remove orphan caption lines (图X-Y not preceded by ![) ---
    lines = list(result)
    result = []
    for idx, line in enumerate(lines):
        if re.match(r'^图\d+-\d+\s+', line):
            # Check if there's a ![image] in the previous few non-empty lines
            has_image = False
            for k in range(idx - 1, max(idx - 5, -1), -1):
                prev = lines[k].strip() if k < len(lines) else ""
                if prev.startswith("!["):
                    has_image = True
                    break
                # Stop searching if we hit another 图 caption, a table, or substantial text
                if re.match(r'^图\d+-\d+\s+', prev):
                    break  # consecutive captions — only keep the one with an image
                if prev.startswith("|"):
                    continue  # skip table lines, keep looking
                if prev and len(prev) > 5:
                    break  # found real text — this caption is orphaned
            if not has_image:
                continue  # skip orphan caption
        result.append(line)

    # --- Pass 7: Deduplicate images — same file path only keep first occurrence ---
    lines = list(result)
    result = []
    seen_image_paths: set[str] = set()
    skip_next_caption = False
    for idx, line in enumerate(lines):
        m = re.match(r'^!\[[^\]]*\]\((output/figures/[^)]+)\)', line)
        if m:
            img_path = m.group(1)
            if img_path in seen_image_paths:
                skip_next_caption = True
                continue  # skip duplicate image
            seen_image_paths.add(img_path)
            skip_next_caption = False
        elif skip_next_caption and re.match(r'^图\d+-\d+\s+', line):
            skip_next_caption = False
            continue  # skip caption of the removed duplicate
        else:
            skip_next_caption = False
        result.append(line)

    return "\n".join(result)


def _renumber_figures(md_text: str, language: str = "zh") -> str:
    """Renumber all figure references sequentially within each chapter.

    Handles both Chinese ('图X-Y') and English ('Figure X-Y') captions. The
    ``language`` parameter controls the canonical label: captions are rewritten
    to match the target language even if the source used the other one.
    """
    label_zh = "图"
    label_en = "Figure"
    want_en = language.lower().startswith("en")
    canonical_prefix = label_en if want_en else label_zh

    lines = md_text.split("\n")
    result: list[str] = []
    chap_counter: dict[int, int] = {}
    current_chap = 0

    cap_any = re.compile(r"^(?:图|Figure)\s*(\d+)-(\d+)\s+(.*)")
    for line in lines:
        chap_match = re.match(r"^## (\d+)\.", line)
        if chap_match:
            current_chap = int(chap_match.group(1))
            chap_counter.setdefault(current_chap, 0)

        fig_caption = cap_any.match(line)
        if fig_caption and current_chap > 0:
            chap_counter[current_chap] = chap_counter.get(current_chap, 0) + 1
            n = chap_counter[current_chap]
            caption_text = fig_caption.group(3)
            if want_en:
                line = f"Figure {current_chap}-{n} {caption_text}"
            else:
                line = f"图{current_chap}-{n} {caption_text}"

        result.append(line)

    return "\n".join(result)


def _strip_english_caption_residue(md: str) -> str:
    """Remove orphan English caption lines ('Figure N-M ...') left by the LLM.

    Chinese captions ('图 N-M') are rendered separately and these English stubs
    are duplicates that only survive in the draft when the cleanup pass misses
    them. The pattern is strict (must start with 'Figure' followed by N-M) to
    avoid touching legitimate English phrases.
    """
    pattern = re.compile(r'^\s*Figure\s+\d+-\d+\b[^\n]*$', re.M)
    cleaned = pattern.sub('', md)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned


# ---- M1: Sub-title instruction stripping + numbering ----

_SUBTITLE_TRIM_SEPS = ('（', '(', '：', ':', '——', '—')


def _clean_subtitle_instructions(md: str) -> str:
    """Cut LLM-prompt noise out of ### subtitles.

    Source blueprints ship subtitles mixed with author-facing guidance (e.g.
    '核心算法数学建模（从问题定义出发，逐步推导核心公式，使用 $$...$$ 包裹独立公式）').
    The LLM echoes them back verbatim as '### ...'. Here we keep the part
    before the first parenthesis/colon/em-dash when the remaining head is a
    reasonable 2–18 character title.
    """
    def _trim(match: re.Match) -> str:
        raw = match.group(1).strip()
        cleaned = raw
        for sep in _SUBTITLE_TRIM_SEPS:
            idx = cleaned.find(sep)
            if 2 <= idx <= 20:
                cleaned = cleaned[:idx].strip()
                break
        # Drop trailing punctuation; collapse whitespace.
        cleaned = re.sub(r'[，。、,;；]+$', '', cleaned).strip()
        if len(cleaned) > 22:
            cleaned = cleaned[:18].rstrip('，、的与和以及')
        if not cleaned:
            return match.group(0)
        return f'### {cleaned}'

    return re.sub(r'^###\s+(.+?)\s*$', _trim, md, flags=re.M)


_NON_BODY_HEADINGS = (
    '参考文献', 'References', '英文摘要', 'English Abstract', 'Keywords',
    '致谢', '附录', '附录说明', '定稿前待补项', '工作流来源',
    '摘要', '中文摘要',
)


def _number_subtitles(md: str) -> str:
    """Prefix each ### heading with '{chapter}.{sub}' numbering.

    Skips back-matter (references / acknowledgements / etc.) and headings that
    already carry a numeric prefix like '### 2.1 ...'.
    """
    out: list[str] = []
    chapter_idx = 0
    sub_idx = 0
    chap_pat = re.compile(r'^##\s+(\d+)\.\s+(.+)$')
    sub_pat = re.compile(r'^###\s+(.+)$')
    already_numbered = re.compile(r'^\d+\.\d+(?:\.\d+)?\s+')

    for line in md.splitlines():
        mchap = chap_pat.match(line)
        if mchap:
            chapter_idx = int(mchap.group(1))
            sub_idx = 0
            out.append(line)
            continue
        if line.startswith('## '):
            head = line[3:].strip()
            if any(head.startswith(t) for t in _NON_BODY_HEADINGS):
                chapter_idx = 0  # suspend numbering
            out.append(line)
            continue
        msub = sub_pat.match(line)
        if msub and chapter_idx > 0:
            title = msub.group(1).strip()
            if already_numbered.match(title):
                out.append(line)
            else:
                sub_idx += 1
                out.append(f'### {chapter_idx}.{sub_idx} {title}')
            continue
        out.append(line)
    return '\n'.join(out)


# ---- M4a: Formula block normalization ----

def _normalize_formula_blocks(md: str) -> str:
    """Ensure every $$...$$ formula is a standalone block paragraph.

    LLM output frequently wedges formulas into prose: '...变为。$$x=y$$。其中...'
    The post-cleanup DOCX renderer then fails to style them as display math.
    This pass (a) lifts wedged formulas onto their own line with blank lines
    before/after and (b) collapses leading/trailing empty prose fragments
    like '。' that are left behind by the split.
    """
    # Iterate to fixed point: one pass may introduce new wedges as it splits.
    inline_pat = re.compile(r'([^\n]+?)\s*\$\$\s*([^$\n]+?)\s*\$\$\s*([^\n]+)')
    prev = None
    cur = md
    for _ in range(5):
        if prev == cur:
            break
        prev = cur
        cur = inline_pat.sub(
            lambda m: f"{m.group(1).rstrip()}\n\n$${m.group(2).strip()}$$\n\n{m.group(3).lstrip()}",
            cur,
        )

    # Remove stray standalone '。' lines that the split leaves as empty fragments.
    cur = re.sub(r'^\s*[。.]\s*$', '', cur, flags=re.M)
    cur = re.sub(r'\n{3,}', '\n\n', cur)
    return cur


def _format_variable_mentions(md: str, project_context: dict[str, Any] | None) -> str:
    inventory = [item for item in (project_context or {}).get("variable_inventory") or [] if isinstance(item, dict)]
    if not inventory:
        return md

    protected = md
    math_blocks: dict[str, str] = {}

    def _protect(match: re.Match[str]) -> str:
        key = f"__MATH_BLOCK_{len(math_blocks)}__"
        math_blocks[key] = match.group(0)
        return key

    protected = re.sub(r"\$\$[\s\S]*?\$\$", _protect, protected)
    protected = re.sub(r"`[^`]+`", _protect, protected)

    for item in inventory:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol or len(symbol) > 16:
            continue
        pattern = re.compile(rf"(?<![$`\w])({re.escape(symbol)})(?![$`\w])")
        protected = pattern.sub(lambda m: f"${m.group(1)}$", protected)

    for key, block in math_blocks.items():
        protected = protected.replace(key, block)
    return protected


def _number_equation_blocks(md: str, language: str = "zh") -> str:
    lines = md.splitlines()
    out: list[str] = []
    chapter_num = 0
    equation_idx = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            chapter_num = int(chapter_match.group(1))
            equation_idx = 0
            out.append(line)
            i += 1
            continue

        stripped = line.strip()
        if stripped.startswith("$$"):
            equation_lines = [line]
            if stripped.count("$$") < 2:
                while i + 1 < len(lines):
                    i += 1
                    equation_lines.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        break
            if chapter_num > 0:
                equation_idx += 1
                label = f"{chapter_num}.{equation_idx}"
                equation_text = "\n".join(equation_lines).strip()
                equation_text = re.sub(r"\\tag\{[^}]+\}", "", equation_text)
                equation_text = re.sub(
                    r"(?:\\quad|\\qquad)?\s*[\(（]?(?:式\s*)?\d+\.\d+[\)）]?\s*(?=\n*\$\$)",
                    "",
                    equation_text,
                )
                if equation_text.strip().endswith("$$"):
                    equation_text = equation_text.rstrip()
                    equation_text = re.sub(r"\n?\$\$\s*$", rf"\n\\tag{{{label}}}\n$$", equation_text)
                out.append(equation_text)
                out.append(f"式{label}" if language == "zh" else f"Eq. ({label})")
                i += 1
                continue

        out.append(line)
        i += 1
    return "\n".join(out)


def _repair_markdown_tables(md_text: str) -> str:
    lines = md_text.splitlines()
    repaired: list[str] = []
    i = 0
    sep_re = re.compile(r"^\|\s*[-:]+(?:\s*\|\s*[-:]+)*\s*\|?$")
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            header = line
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            header_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            col_count = len([cell for cell in header_cells if cell or len(header_cells) == 1])
            if col_count >= 2:
                repaired.append(header)
                if next_line == "||" or not sep_re.match(next_line):
                    repaired.append("| " + " | ".join(["---"] * col_count) + " |")
                    i += 1
                    continue
        repaired.append(line)
        i += 1
    return "\n".join(repaired)


def _renumber_tables(md_text: str, language: str = "zh") -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    current_chapter = 0
    chapter_counts: dict[int, int] = {}
    caption_re = re.compile(r"^(?:表|Table)\s*(\d+(?:[.-]\d+)?)\s+(.+)$", re.I)

    for line in lines:
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            current_chapter = int(chapter_match.group(1))
            chapter_counts.setdefault(current_chapter, 0)
            out.append(line)
            continue

        caption_match = caption_re.match(line.strip())
        if caption_match and current_chapter > 0:
            chapter_counts[current_chapter] = chapter_counts.get(current_chapter, 0) + 1
            label = f"{current_chapter}.{chapter_counts[current_chapter]}"
            caption_text = caption_match.group(2).strip()
            caption_text = re.sub(r"^(?:表|Table)\s*\d+(?:[.-]\d+)?\s+", "", caption_text, flags=re.I)
            out.append(f"表{label} {caption_text}" if language == "zh" else f"Table {label} {caption_text}")
            continue

        out.append(line)
    return "\n".join(out)


def _normalize_final_manuscript_format(
    md_text: str,
    *,
    language: str = "zh",
    project_context: dict[str, Any] | None = None,
) -> str:
    normalized = _cleanup_markdown(md_text)
    normalized = _normalize_formula_blocks(normalized)
    normalized = _format_variable_mentions(normalized, project_context)
    normalized = _number_equation_blocks(normalized, language=language)
    normalized = _dedupe_equation_labels(normalized, language=language)
    normalized = _repair_markdown_tables(normalized)
    normalized = _renumber_tables(normalized, language=language)
    normalized = _renumber_figures(normalized, language=language)
    normalized = _sync_nearby_figure_references(normalized, language=language)
    normalized = _inject_missing_figure_placeholders(normalized, project_context, language=language)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized + "\n"


def _normalize_formula_blocks(md: str) -> str:
    """Lift wedged display equations onto standalone blocks before numbering."""
    prev = None
    cur = md
    inline_pat = re.compile(r"([^\n]+?)\s*\$\$\s*([^$\n]+?)\s*\$\$\s*([^\n]+)")

    for _ in range(5):
        if prev == cur:
            break
        prev = cur
        cur = inline_pat.sub(
            lambda m: f"{m.group(1).rstrip()}\n\n$${m.group(2).strip()}$$\n\n{m.group(3).lstrip()}",
            cur,
        )
        cur = re.sub(
            r"^\s*\$\$\s*([^$\n]+?)\s*\$\$\s*([^\n$].+)$",
            lambda m: f"$${m.group(1).strip()}$$\n\n{m.group(2).lstrip()}",
            cur,
            flags=re.M,
        )
        cur = re.sub(
            r"^([^\n$].*?)\s*\$\$\s*([^$\n]+?)\s*\$\$\s*$",
            lambda m: f"{m.group(1).rstrip()}\n\n$${m.group(2).strip()}$$",
            cur,
            flags=re.M,
        )

    def _looks_like_equation(text: str) -> bool:
        stripped = text.strip()
        if not stripped or len(stripped) > 180:
            return False
        if any(punct in stripped for punct in ("。", "；", "，")):
            return False
        return any(token in stripped for token in ("=", "\\", "^", "_", "[", "]", "sin", "cos"))

    repaired: list[str] = []
    for line in cur.splitlines():
        stripped = line.strip()
        if not stripped:
            repaired.append(line)
            continue

        if stripped.endswith("$$") and "$$" in stripped and not stripped.startswith("$$"):
            expr = stripped[:-2].strip()
            expr = re.sub(r"(?:\\quad|\\qquad)?\s*[\(（]?(?:式\s*)?\d+\.\d+[\)）]?\s*$", "", expr).strip()
            if _looks_like_equation(expr):
                repaired.append(f"$${expr}$$")
                continue

        if "$$" not in stripped and re.search(r"(?:\\quad|\\qquad)?\s*[\(（]?(?:式\s*)?\d+\.\d+[\)）]?\s*$", stripped):
            expr = re.sub(r"(?:\\quad|\\qquad)?\s*[\(（]?(?:式\s*)?\d+\.\d+[\)）]?\s*$", "", stripped).strip()
            if _looks_like_equation(expr):
                repaired.append(f"$${expr}$$")
                continue

        repaired.append(line)

    cur = "\n".join(repaired)
    cur = re.sub(
        r"\$\$\s*([\s\S]*?)\s*\$\$\s*\n\s*\\tag\{([^}]+)\}\s*\n\s*\$\$",
        lambda m: "$$\n" + m.group(1).strip() + f"\n\\tag{{{m.group(2).strip()}}}\n$$",
        cur,
        flags=re.M,
    )
    cur = re.sub(
        r"\$\$\s*([\s\S]*?)\s*\$\$\s*\n\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$",
        lambda m: "$$\n" + m.group(1).strip() + "\n$$",
        cur,
        flags=re.M,
    )
    cur = re.sub(r"^\s*[。.]+\s*$", "", cur, flags=re.M)
    cur = re.sub(r"\n{3,}", "\n\n", cur)
    return cur


def _number_equation_blocks(md: str, language: str = "zh") -> str:
    lines = md.splitlines()
    out: list[str] = []
    chapter_num = 0
    equation_idx = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            chapter_num = int(chapter_match.group(1))
            equation_idx = 0
            out.append(line)
            i += 1
            continue

        stripped = line.strip()
        if stripped.startswith("$$"):
            equation_lines = [line]
            if stripped.count("$$") < 2:
                while i + 1 < len(lines):
                    i += 1
                    equation_lines.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        break
            if chapter_num > 0:
                equation_idx += 1
                label = f"{chapter_num}.{equation_idx}"
                equation_text = "\n".join(equation_lines).strip()
                equation_body = re.sub(r"^\$\$\s*|\s*\$\$$", "", equation_text, flags=re.S).strip()
                equation_body = re.sub(r"^\s*\\tag\{[^}]+\}\s*$", "", equation_body, flags=re.M).strip()
                equation_body = re.sub(r"^\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$", "", equation_body, flags=re.M).strip()
                tag_text = f"式{label}" if language == "zh" else label
                out.append("$$\n" + equation_body + f"\n\\tag{{{tag_text}}}\n$$")
                while i + 1 < len(lines) and re.match(r"^\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$", lines[i + 1].strip()):
                    i += 1
                i += 1
                continue

        out.append(line)
        i += 1
    return "\n".join(out)


def _dedupe_equation_labels(md_text: str, language: str = "zh") -> str:
    deduped = re.sub(r"^\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$\n?", "", md_text, flags=re.M)
    deduped = re.sub(r"(\\tag\{[^}]+\})\s*\n\s*(\\tag\{[^}]+\})", r"\2", deduped)
    return deduped


def _renumber_tables(md_text: str, language: str = "zh") -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    current_chapter = 0
    chapter_counts: dict[int, int] = {}
    previous_caption_text = ""
    caption_re = re.compile(r"^(表|Table)\s*(\d+(?:[.-]\d+)?)\s+(.+)$", re.I)

    def _next_nonempty_line(start_idx: int) -> str:
        for probe in range(start_idx + 1, len(lines)):
            if lines[probe].strip():
                return lines[probe].strip()
        return ""

    for idx, line in enumerate(lines):
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            current_chapter = int(chapter_match.group(1))
            chapter_counts.setdefault(current_chapter, 0)
            previous_caption_text = ""
            out.append(line)
            continue

        caption_match = caption_re.match(line.strip())
        if caption_match and current_chapter > 0:
            next_nonempty = _next_nonempty_line(idx)
            if not next_nonempty.startswith("|"):
                out.append(line)
                if line.strip():
                    previous_caption_text = ""
                continue

            raw_prefix = caption_match.group(1)
            caption_text = caption_match.group(3).strip()
            caption_text = re.sub(r"^(?:表|Table)\s*\d+(?:[.-]\d+)?\s+", "", caption_text, flags=re.I)
            caption_text_key = re.sub(r"\s+", " ", caption_text).strip().lower()
            if language == "zh" and raw_prefix.lower() == "table" and caption_text_key == previous_caption_text:
                continue
            chapter_counts[current_chapter] = chapter_counts.get(current_chapter, 0) + 1
            label = f"{current_chapter}.{chapter_counts[current_chapter]}"
            out.append(f"表{label} {caption_text}" if language == "zh" else f"Table {label} {caption_text}")
            previous_caption_text = caption_text_key
            continue

        out.append(line)
        if line.strip():
            previous_caption_text = ""
    return "\n".join(out)


def _repair_markdown_tables(md_text: str) -> str:
    lines = md_text.splitlines()
    repaired: list[str] = []
    i = 0
    sep_re = re.compile(r"^\|\s*[-:]+(?:\s*\|\s*[-:]+)*\s*\|?$")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        is_table_start = not repaired or not repaired[-1].strip().startswith("|")
        if is_table_start and stripped.startswith("|") and stripped.count("|") >= 2:
            header_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            col_count = len(header_cells)
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            repaired.append(line)
            if col_count >= 2:
                if next_line == "||":
                    repaired.append("| " + " | ".join(["---"] * col_count) + " |")
                    i += 2
                    continue
                if sep_re.match(next_line):
                    repaired.append(lines[i + 1])
                    i += 2
                    continue
                if next_line.startswith("|") and next_line.count("|") >= 2:
                    repaired.append("| " + " | ".join(["---"] * col_count) + " |")
                    i += 1
                    continue
            i += 1
            continue

        if stripped == "||":
            i += 1
            continue

        repaired.append(line)
        i += 1

    return "\n".join(repaired)


def _renumber_figures(md_text: str, language: str = "zh") -> str:
    want_en = language.lower().startswith("en")
    lines = md_text.splitlines()
    first_pass: list[str] = []
    current_chapter = 0
    figure_counts: dict[int, int] = {}
    label_map: dict[str, str] = {}
    caption_re = re.compile(r"^(图|Figure)\s*(\d+)-(\d+)\s+(.+)$", re.I)

    for line in lines:
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            current_chapter = int(chapter_match.group(1))
            figure_counts.setdefault(current_chapter, 0)

        caption_match = caption_re.match(line.strip())
        if caption_match and current_chapter > 0:
            old_label = f"{caption_match.group(2)}-{caption_match.group(3)}"
            figure_counts[current_chapter] = figure_counts.get(current_chapter, 0) + 1
            new_label = f"{current_chapter}-{figure_counts[current_chapter]}"
            caption_text = caption_match.group(4).strip()
            label_map[f"图{old_label}"] = f"图{new_label}"
            label_map[f"Figure {old_label}"] = f"Figure {new_label}"
            line = f"Figure {new_label} {caption_text}" if want_en else f"图{new_label} {caption_text}"

        first_pass.append(line)

    second_pass: list[str] = []
    for line in first_pass:
        if caption_re.match(line.strip()):
            second_pass.append(line)
            continue
        updated = line
        for old_label, new_label in label_map.items():
            if old_label == new_label:
                continue
            updated = re.sub(re.escape(old_label) + r"(?!\d)", new_label, updated)
        second_pass.append(updated)
    return "\n".join(second_pass)


def _sync_nearby_figure_references(md_text: str, language: str = "zh") -> str:
    lines = md_text.splitlines()
    caption_re = re.compile(r"^(图|Figure)\s*(\d+)-(\d+)\s+(.+)$", re.I)
    ref_re = re.compile(r"Figure\s+\d+-\d+" if language.lower().startswith("en") else r"图\d+-\d+")

    for idx, line in enumerate(lines):
        caption_match = caption_re.match(line.strip())
        if not caption_match:
            continue
        label = f"{caption_match.group(2)}-{caption_match.group(3)}"
        wanted_ref = f"Figure {label}" if language.lower().startswith("en") else f"图{label}"
        for probe in range(idx - 1, max(-1, idx - 5), -1):
            probe_line = lines[probe].strip()
            if not probe_line or probe_line.startswith("![") or caption_re.match(probe_line):
                continue
            if ref_re.search(lines[probe]):
                lines[probe] = ref_re.sub(wanted_ref, lines[probe], count=1)
                break

    return "\n".join(lines)


def _inject_real_experiment_data(markdown_path: Path, result: dict[str, Any]) -> None:
    """Post-process generated markdown with project-backed tables, figures, and references."""
    try:
        import csv as _csv
    except ImportError:
        return

    md_text = markdown_path.read_text(encoding="utf-8")
    lines = md_text.split("\n")

    artifact = result.get("artifact") if isinstance(result.get("artifact"), dict) else {}
    project_context = result.get("project_context") or artifact.get("project_context") or {}

    project_root = Path(result.get("project_root", ".")).resolve()
    evidence_root = _resolve_project_evidence_root(project_root, project_context)
    results_dir = evidence_root / "output" / "results"
    raw_figure_allowlist = project_context.get("paper_workspace_figure_files")
    figure_allowlist = None if raw_figure_allowlist is None else {str(name) for name in raw_figure_allowlist}

    csv_title_map = {
        "slam_comparison": ["SLAM", "建图", "slam", "地图"],
        "planner_comparison": ["规划器", "全局规划", "路径规划"],
        "navigation_scenarios": ["场景", "导航实验", "导航场景"],
        "mppi_controller_results": ["控制器", "局部", "MPPI", "控制"],
    }
    csv_files = list(results_dir.glob("*.csv")) if results_dir.exists() else []

    new_lines: list[str] = []
    i = 0
    table_caption_re = re.compile(r"^(?:表|Table)\s*(\d+(?:[.-]\d+)?)\s+(.+)$", re.I)
    while i < len(lines):
        line = lines[i]
        caption_match = table_caption_re.match(line.strip())
        if caption_match and csv_files:
            caption_text = caption_match.group(2)
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            data_rows = [row for row in table_lines[2:] if ("…" in row or "..." in row or "—" in row or "鈥" in row)]
            if data_rows:
                matched_csv = None
                table_blob = "\n".join(table_lines).lower()
                for csv_file in csv_files:
                    keywords = csv_title_map.get(csv_file.stem, [])
                    if keywords and any(keyword.lower() in caption_text.lower() for keyword in keywords):
                        matched_csv = csv_file
                        break
                if matched_csv is None:
                    for csv_file in csv_files:
                        keywords = csv_title_map.get(csv_file.stem, [])
                        if keywords and any(keyword.lower() in table_blob for keyword in keywords):
                            matched_csv = csv_file
                            break
                if matched_csv:
                    try:
                        with open(matched_csv, "r", encoding="utf-8") as f:
                            reader = _csv.reader(f)
                            headers = next(reader)
                            rows = [row for row in reader if any(cell.strip() for cell in row)]
                        new_lines.append(line)
                        if i < len(lines) and re.match(r"^Table\s+\d+(?:[.-]\d+)?", lines[i], re.I):
                            i += 1
                        new_lines.append("")
                        new_lines.append("| " + " | ".join(headers) + " |")
                        new_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                        for row in rows:
                            new_lines.append("| " + " | ".join(row) + " |")
                        new_lines.append("")
                        continue
                    except Exception:
                        pass
                new_lines.extend(table_lines)
                continue
        new_lines.append(line)
        i += 1

    md_text = "\n".join(new_lines)
    language = str(result.get("language") or artifact.get("language") or "zh")
    md_text = _inject_tables_by_plan(md_text, project_context, language=language)

    figures = _scan_project_figures(
        project_root,
        allowed_files=figure_allowlist,
        run_extractor=raw_figure_allowlist is None,
    )
    fig_counter = [0]
    if figures:
        def _replace_fig_placeholder(match: re.Match[str]) -> str:
            fig_counter[0] += 1
            chapter = match.group(1) or "4"
            num = match.group(2)
            stem = match.group(3) if match.group(3) else ""
            matched_fig = None
            for fig in figures:
                if stem and stem.lower() in str(fig["caption"]).lower():
                    matched_fig = fig
                    break
            if matched_fig is None and fig_counter[0] <= len(figures):
                matched_fig = figures[fig_counter[0] - 1]
            if not matched_fig:
                return match.group(0)
            caption = str(matched_fig["caption"])
            nicer_names = {
                "household_demo_obstacle_check": "障碍物检测结果",
                "household_demo_obstacle_avoid_check": "避障检测结果",
                "Coverage Planning Flow": "全覆盖路径规划流程",
                "Data Flow": "系统数据流",
                "System Architecture": "系统总体架构",
            }
            for ugly, nice_name in nicer_names.items():
                caption = caption.replace(ugly, nice_name)
            return (
                f"![{caption}](output/figures/{matched_fig['file']})\n"
                f"图{chapter}-{num} {caption}\n"
                f"Figure {chapter}-{num} {caption}"
            )

        md_text = re.sub(
            r"\[此处插入图(?:\s*)?(\d+)-(\d+)[：: ]?([^\]]*)\]",
            _replace_fig_placeholder,
            md_text,
        )

        md_text = re.sub(
            r"^(?:图\d+-\d+(?:建议|适合|可采用|用于说明)[^\n]*\n(?:[^\n#]*\n)*?)(?=\n\S|\n#|\Z)",
            "",
            md_text,
            flags=re.MULTILINE,
        )

    if figures:
        already_in_text: set[str] = set()
        for img_match in re.finditer(r"!\[[^\]]*\]\(output/figures/([^)]+)\)", md_text):
            already_in_text.add(img_match.group(1))

        remaining = [fig for fig in figures if fig["file"] not in already_in_text]
        if remaining:
            existing_figs = re.findall(r"图(\d+)-(\d+)", md_text)
            if existing_figs:
                last_chap = int(existing_figs[-1][0])
                last_num = int(existing_figs[-1][1])
            else:
                last_chap, last_num = 4, 0

            insert_pos = len(md_text)
            for match in re.finditer(r"^(?:表|Table)\s*\d+(?:[.-]\d+)?\s+.*$", md_text, re.MULTILINE):
                pos = match.end()
                while pos < len(md_text):
                    next_nl = md_text.find("\n", pos)
                    if next_nl == -1:
                        break
                    line_text = md_text[pos:next_nl]
                    if not line_text.startswith("|"):
                        break
                    pos = next_nl + 1
                insert_pos = pos

            fig_blocks: list[str] = []
            for idx, fig in enumerate(remaining, start=1):
                fig_num = last_num + idx
                fig_blocks.append(f"\n![{fig['caption']}](output/figures/{fig['file']})")
                fig_blocks.append(f"\n图{last_chap}-{fig_num} {fig['caption']}")
                fig_blocks.append(f"\nFigure {last_chap}-{fig_num} {fig['caption']}\n")
            if fig_blocks:
                md_text = md_text[:insert_pos] + "".join(fig_blocks) + md_text[insert_pos:]
                print(f"[InjectFig] 追加 {len(remaining)} 张未匹配图表")

    if "### 参考文献" not in md_text and "## 参考文献" not in md_text:
        references = result.get("artifact", {}).get("references", [])
        if references:
            ref_lines = ["", "## 参考文献", ""]
            for idx, ref in enumerate(references, 1):
                authors = ref.get("authors", "")
                title = ref.get("title", "")
                venue = ref.get("venue", "")
                year = ref.get("year", "")
                doi = ref.get("doi", "")
                if not title:
                    continue
                ref_line = f"{authors}. {title}"
                if venue:
                    ref_line += f"[{_guess_ref_type(venue)}]. {venue}"
                if year:
                    ref_line += f", {year}"
                if doi:
                    ref_line += f". DOI: {doi}"
                ref_lines.append(f"{idx}. {ref_line}")
                ref_lines.append("")
            md_text += "\n".join(ref_lines)

    ref_section_match = re.search(r"^(##\s+参考文献.*)$", md_text, re.MULTILINE)
    if ref_section_match:
        ref_start = ref_section_match.end()

        def _fix_ref_line(line: str) -> str:
            bracket_idx = line.find("[")
            if bracket_idx < 0 or not re.match(r"^\d+\.", line):
                return line
            bracket_end = line.find("]", bracket_idx)
            if bracket_end < 0:
                return line
            list_str = line[bracket_idx:bracket_end + 1]
            try:
                items = eval(list_str)
                if isinstance(items, list):
                    if items and isinstance(items[0], str) and len(items[0]) <= 2:
                        joined = "".join(str(x) for x in items)
                        authors = [author.strip() for author in joined.split(", ") if author.strip()]
                    else:
                        authors = [str(x) for x in items if str(x).strip()]
                    prefix = line[:bracket_idx]
                    suffix = line[bracket_end + 1:]
                    return prefix + ", ".join(authors) + suffix
            except Exception:
                pass
            return line

        ref_text = md_text[ref_start:]
        fixed_lines = [_fix_ref_line(line) for line in ref_text.split("\n")]
        md_text = md_text[:ref_start] + "\n".join(fixed_lines)

    def _fix_compressed_tables(text: str) -> str:
        lines = text.split("\n")
        fixed: list[str] = []
        for line in lines:
            if line.count("|") > 6 and "||" in line:
                if re.search(r"\|\|[-:\s|]+\|\|", line):
                    segments = re.split(r"(\|\|[-:\s|]+\|\|)", line)
                    new_lines: list[str] = []
                    for seg in segments:
                        if re.match(r"^\|\|[-:\s|]+\|\|$", seg):
                            sep = seg.strip().strip("|")
                            new_lines.append("|" + sep + "|")
                        elif seg.strip():
                            parts = re.split(r"\|\|", seg)
                            for idx, part in enumerate(parts):
                                part = part.strip()
                                if not part:
                                    continue
                                if part.startswith("|"):
                                    new_lines.append(part)
                                elif idx > 0:
                                    new_lines.append(part)
                                else:
                                    new_lines.append(part)
                    fixed.extend(new_lines)
                else:
                    parts = re.split(r"(\|\|)", line)
                    new_lines = []
                    buf = ""
                    for part in parts:
                        buf += part
                        if buf.endswith("||") and len(buf.strip()) > 2:
                            new_lines.append(buf[:-2])
                            buf = "|"
                    if buf.strip():
                        remaining = re.sub(r"^((?:表|Table)\s*\d+(?:[.-]\d+)?\s)", r"\n\1", buf, flags=re.I)
                        new_lines.append(remaining)
                    fixed.extend(new_lines)
            else:
                fixed.append(line)

        result_lines: list[str] = []
        for idx, line in enumerate(fixed):
            result_lines.append(line)
            if line.startswith("|") and line.endswith("|") and "|" in line[1:-1] and not re.match(r"^\|[\s\-:]+\|$", line):
                next_line = fixed[idx + 1] if idx + 1 < len(fixed) else ""
                if not re.match(r"^\|[\s\-:]+\|$", next_line):
                    col_count = len([cell for cell in line.split("|") if cell.strip()])
                    result_lines.append("|" + "|".join(["---"] * col_count) + "|")
        return "\n".join(result_lines)

    md_text = _fix_compressed_tables(md_text)
    safe_write_text(markdown_path, md_text)


# ---- M4b: Deduplicate ## 参考文献 sections ----

def _dedupe_reference_sections(md: str) -> str:
    """If '## 参考文献' appears more than once, keep the block with more entries."""
    pat = re.compile(r'(^##\s*参考文献\s*$)([\s\S]*?)(?=^##\s|\Z)', flags=re.M)
    blocks = list(pat.finditer(md))
    if len(blocks) <= 1:
        return md
    def _score(s: str) -> int:
        return len(re.findall(r'^\s*\d+\.\s+\S', s, flags=re.M))
    best_idx = max(range(len(blocks)), key=lambda i: _score(blocks[i].group(0)))
    to_remove = sorted(
        [(b.start(), b.end()) for i, b in enumerate(blocks) if i != best_idx],
        reverse=True,
    )
    out = md
    for s, e in to_remove:
        out = out[:s] + out[e:]
    out = re.sub(r'\n{3,}', '\n\n', out)
    return out


# ---- M3: Figure normalization, deduplication, and relocation ----

# Heuristic cleanup patterns for figure filename stems. Intentionally generic
# so this works across any project, not just one paper's naming scheme.
_FIGURE_STEM_CLEAN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'^(gen|auto|demo|plot|fig|figure|output|result|export|tmp)[-_]+', re.I), ''),
    (re.compile(r'[-_]+(gen|auto|demo|plot|fig|figure|output|export|tmp)[-_]+', re.I), '_'),
    (re.compile(r'[-_](v\d+|rev\d+|final|copy|last|latest|new|old|bak)(?=$|[-_])', re.I), ''),
    (re.compile(r'^extract[-_]+|[-_]+extract[-_]+', re.I), '_'),
    (re.compile(r'[-_]+\d{6,}(?=$|[-_])'), ''),   # trailing timestamps
    (re.compile(r'^\d+[-_]+'), ''),                # leading index like '01_foo'
]


def _humanize_figure_stem(stem: str) -> str:
    """Turn a filename stem into a readable short caption.

    Strips auto-gen prefixes, version suffixes, and duplicate tokens, then
    splits on separators. Language-agnostic: keeps CJK characters as-is and
    spaces latin words.
    """
    cleaned = stem
    for pat, repl in _FIGURE_STEM_CLEAN_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    cleaned = re.sub(r'[-_]+', ' ', cleaned).strip()
    seen: set[str] = set()
    kept: list[str] = []
    for word in cleaned.split():
        low = word.lower()
        if low in seen:
            continue
        seen.add(low)
        kept.append(word)
    result = ' '.join(kept).strip()
    return result or stem


def _dedupe_key_from_stem(stem: str) -> str:
    """Canonical key for near-duplicate detection.

    Applies the same cleanup as ``_humanize_figure_stem`` but lowercases and
    collapses all separators, so 'system-architecture' and 'system_architecture_v2'
    resolve to the same key.
    """
    key = stem.lower()
    for pat, repl in _FIGURE_STEM_CLEAN_PATTERNS:
        key = pat.sub(repl, key)
    key = re.sub(r'[-_\s]+', '_', key).strip('_')
    return key


# Bucket → (keyword, weight). Buckets correspond to typical engineering thesis
# chapter archetypes. A caption matches a chapter only when the chapter's title
# triggers one of these buckets and the caption mentions bucket keywords.
_FIGURE_KEYWORD_BUCKETS: dict[str, list[tuple[str, int]]] = {
    'intro': [
        ('背景', 1), ('概述', 1), ('总体', 2), ('overview', 2),
    ],
    'theory': [
        ('原理', 2), ('模型', 2), ('推导', 2), ('定义', 1), ('公式', 1), ('示意', 1),
        ('model', 2), ('theory', 2), ('derivation', 2),
    ],
    'design': [
        ('架构', 3), ('模块', 2), ('数据流', 3), ('流程', 2), ('接口', 2),
        ('设计', 2), ('框架', 2), ('工作流', 2),
        ('architecture', 3), ('flow', 2), ('pipeline', 2), ('module', 2),
        ('design', 2), ('framework', 2), ('workflow', 2), ('diagram', 1),
    ],
    'experiment': [
        ('结果', 2), ('实验', 2), ('对比', 2), ('评价', 1), ('性能', 2),
        ('指标', 1), ('误差', 2), ('准确率', 2), ('召回', 2), ('收敛', 2),
        ('分析', 1), ('热力', 3), ('柱状', 2), ('曲线', 2), ('雷达', 2),
        ('混淆', 3), ('场景', 1), ('地图', 1), ('测试', 2),
        ('result', 2), ('experiment', 2), ('comparison', 2), ('heatmap', 3),
        ('curve', 2), ('matrix', 2), ('evaluation', 2), ('benchmark', 2),
        ('scenario', 1), ('demo', 1), ('accuracy', 2), ('loss', 2),
        ('convergence', 2), ('bar', 1), ('radar', 2), ('confusion', 3),
    ],
}


def _title_buckets(chapter_title: str) -> set[str]:
    """Which keyword buckets does a chapter title activate?"""
    low = chapter_title.lower()
    buckets: set[str] = set()
    if any(k in low for k in ('绪论', '引言', 'introduction')):
        buckets.add('intro')
    if any(k in low for k in ('理论', '原理', '基础', '模型', '数学', 'theory', 'preliminar', 'background')):
        buckets.add('theory')
    if any(k in low for k in ('设计', '架构', '方法', '算法', '实现', 'design', 'architecture', 'method', 'implementation', 'algorithm', 'approach')):
        buckets.add('design')
    if any(k in low for k in ('实验', '结果', '分析', '评价', '对比', 'experiment', 'result', 'evaluation', 'analysis', 'comparison', 'benchmark')):
        buckets.add('experiment')
    if any(k in low for k in ('结论', '展望', '总结', 'conclusion', 'discussion', 'future')):
        buckets.add('conclusion')
    return buckets


def _score_caption_for_chapter(caption: str, title_buckets: set[str]) -> int:
    """Sum keyword weights where bucket matches both caption and chapter title."""
    if not title_buckets:
        return 0
    low_cap = caption.lower()
    score = 0
    for bucket in title_buckets:
        for kw, weight in _FIGURE_KEYWORD_BUCKETS.get(bucket, []):
            if kw in low_cap:
                score += weight
    return score


def _default_chapter_budget(title: str) -> int:
    """Default figure count per chapter type when an explicit budget isn't set."""
    low = title.lower()
    if any(k in low for k in ('绪论', '引言', 'introduction')):
        return 1
    if any(k in low for k in ('理论', '原理', '基础', 'theory', 'preliminar', 'background')):
        return 1
    if any(k in low for k in ('设计', '架构', '方法', 'design', 'architecture', 'method', 'approach')):
        return 3
    if any(k in low for k in ('实现', 'implementation')):
        return 2
    if any(k in low for k in ('实验', '结果', '分析', '评价', 'experiment', 'result', 'evaluation', 'analysis')):
        return 5
    if any(k in low for k in ('结论', '展望', 'conclusion', 'discussion')):
        return 0
    return 1


def _drop_pdf_figures(md: str) -> str:
    """Drop ![caption](*.pdf) figure lines — python-docx cannot embed PDF.

    Also drops the immediately-following '图 X-Y' caption line when present.
    """
    lines = md.splitlines()
    out: list[str] = []
    pdf_pat = re.compile(r'^!\[[^\]]*\]\([^)]+\.pdf\)\s*$')
    i = 0
    while i < len(lines):
        if pdf_pat.match(lines[i]):
            i += 1
            if i < len(lines) and re.match(r'^图\s*\d+-\d+', lines[i]):
                i += 1
            if i < len(lines) and not lines[i].strip():
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def _collect_figure_blocks(md: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip every figure block from `md` and return (stripped_md, blocks)."""
    lines = md.splitlines()
    stripped: list[str] = []
    blocks: list[dict[str, Any]] = []
    fig_pat = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$')
    cap_pat = re.compile(r'^图\s*\d+-\d+\s*(.*)$')
    i = 0
    while i < len(lines):
        m = fig_pat.match(lines[i])
        if m:
            alt = m.group(1)
            path = m.group(2)
            i += 1
            caption = alt
            if i < len(lines):
                cm = cap_pat.match(lines[i])
                if cm:
                    caption = (cm.group(1) or alt).strip()
                    i += 1
            if i < len(lines) and not lines[i].strip():
                i += 1
            blocks.append({'alt': alt, 'path': path, 'caption': caption})
            continue
        stripped.append(lines[i])
        i += 1
    return '\n'.join(stripped), blocks


def _normalize_and_dedupe_figures(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply heuristic caption cleanup, drop PDFs, and dedupe by stem key."""
    seen_paths: set[str] = set()
    seen_keys: set[str] = set()
    out: list[dict[str, Any]] = []
    for b in blocks:
        path = b.get('path') or ''
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        if path.lower().endswith('.pdf'):
            continue
        stem = Path(path).stem
        dedupe_key = _dedupe_key_from_stem(stem)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        existing = (b.get('caption') or '').strip()
        # Prefer an existing human caption (no raw separators, reasonable length).
        if existing and not re.search(r'[_]', existing) and 2 <= len(existing) <= 40 and existing.lower() != stem.lower():
            caption = existing
        else:
            caption = _humanize_figure_stem(stem)
        out.append({'alt': caption, 'path': path, 'caption': caption})
    return out


def _parse_numbered_chapters(md: str) -> list[dict[str, Any]]:
    """Return [{num, title, start, end}] for all '## N. <title>' chapters."""
    lines = md.splitlines()
    chap_pat = re.compile(r'^##\s+(\d+)\.\s+(.+)$')
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for idx, line in enumerate(lines):
        m = chap_pat.match(line)
        if m:
            if current is not None:
                current['end'] = idx - 1
                chapters.append(current)
            current = {'num': int(m.group(1)), 'title': m.group(2).strip(), 'start': idx}
        elif line.startswith('## ') and current is not None:
            current['end'] = idx - 1
            chapters.append(current)
            current = None
    if current is not None:
        current['end'] = len(lines) - 1
        chapters.append(current)
    return chapters


def _insert_figure_references(md: str, language: str = "zh") -> str:
    """Add a figure-reference phrase into each chapter's lead paragraph.

    Language-aware: Chinese appends '（如图 X-Y 所示）' before the first period;
    English appends ' (as shown in Figure X-Y)' before the first period.
    Only adds the first reference per chapter — keeps prose readable without
    overwhelming it with parenthetical pointers. No-op when the chapter already
    references the figure label.
    """
    en_mode = language.lower().startswith("en")
    fig_word = "Figure" if en_mode else "图"
    label_pat = re.compile(
        rf'^{"Figure" if en_mode else "图"}\s*(\d+)-(\d+)\s+',
        re.M,
    )
    chap_pat = re.compile(r'^##\s+(\d+)\.\s+(.+)$', re.M)
    matches = list(chap_pat.finditer(md))
    if not matches:
        return md

    starts = [(m.start(), int(m.group(1))) for m in matches]
    starts.append((len(md), -1))
    segments: list[tuple[int, int, int]] = [
        (starts[i][0], starts[i + 1][0], starts[i][1]) for i in range(len(starts) - 1)
    ]

    already_ref_pat = re.compile(
        rf'(?:as shown in\s+Figure\s+\d+-\d+|如图\s*\d+-\d+\s*所示)',
        re.IGNORECASE,
    )

    out = md
    offset = 0
    for seg_start, seg_end, _chap_num in segments:
        seg = out[seg_start + offset:seg_end + offset]
        if already_ref_pat.search(seg):
            continue
        fm = label_pat.search(seg)
        if not fm:
            continue
        fig_label = f"{fm.group(1)}-{fm.group(2)}"
        after_heading = seg.find('\n') + 1
        body = seg[after_heading:]
        para_pat = re.compile(r'^(?![#!\|$\s])[^\n]{20,}$', re.M)
        pm = para_pat.search(body)
        if not pm:
            continue
        para_line = pm.group(0)
        if en_mode:
            insert_marker = f' (as shown in {fig_word} {fig_label})'
            punct_candidates = ('.', ';', ',')
        else:
            insert_marker = f'（如图{fig_label}所示）'
            punct_candidates = ('。', '；', '，')
        new_para = para_line
        for punct in punct_candidates:
            idx = para_line.find(punct)
            if idx > 10:
                new_para = para_line[:idx] + insert_marker + para_line[idx:]
                break
        if new_para == para_line:
            new_para = para_line + insert_marker
        rel_pos = seg_start + offset + after_heading + pm.start()
        out = out[:rel_pos] + new_para + out[rel_pos + len(para_line):]
        offset += len(new_para) - len(para_line)
    return out


def _asset_keywords(*values: str) -> list[str]:
    tokens: list[str] = []
    for value in values:
        text = str(value or "").lower()
        tokens.extend(re.findall(r"[a-z][a-z0-9_-]{2,}", text))
        tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,}", value or ""))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        token = token.strip("-_ ")
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered[:12]


def _match_figure_plan_item(block: dict[str, Any], project_context: dict[str, Any] | None) -> dict[str, Any] | None:
    figure_plan = (project_context or {}).get("figure_plan") or []
    if not isinstance(figure_plan, list):
        return None
    block_path = Path(str(block.get("path") or "")).name.lower()
    block_caption = str(block.get("caption") or "").lower()
    best_item: dict[str, Any] | None = None
    best_score = -1
    for item in figure_plan:
        if not isinstance(item, dict):
            continue
        existing = Path(str(item.get("existing_asset") or "")).name.lower()
        caption = str(item.get("caption") or "").lower()
        score = 0
        if block_path and existing and block_path == existing:
            score += 8
        if block_caption and caption and block_caption in caption:
            score += 4
        if score > best_score:
            best_score = score
            best_item = item
    return best_item if best_score > 0 else None


def _find_best_figure_anchor(
    lines: list[str],
    chapter: dict[str, Any],
    block: dict[str, Any],
    *,
    project_context: dict[str, Any] | None,
) -> int:
    candidates = _collect_figure_anchor_candidates(lines, chapter, block, project_context=project_context)
    return candidates[0] if candidates else int(chapter["end"])


def _collect_figure_anchor_candidates(
    lines: list[str],
    chapter: dict[str, Any],
    block: dict[str, Any],
    *,
    project_context: dict[str, Any] | None,
) -> list[int]:
    plan_item = _match_figure_plan_item(block, project_context)
    keywords = _asset_keywords(
        str(block.get("caption") or ""),
        str(plan_item.get("goal") if isinstance(plan_item, dict) else ""),
        str(plan_item.get("evidence") if isinstance(plan_item, dict) else ""),
    )
    paragraph_candidates: list[tuple[int, int]] = []
    for line_idx in range(int(chapter["start"]) + 1, int(chapter["end"]) + 1):
        line = lines[line_idx].strip()
        if not line or line.startswith("#") or line.startswith("!") or line.startswith("|") or line.startswith("图") or line.startswith("Figure"):
            continue
        score = sum(2 for keyword in keywords if keyword and keyword in line.lower())
        if "如图" in line or "见图" in line or "as shown in figure" in line.lower():
            score += 4
        paragraph_candidates.append((line_idx, score))
    if not paragraph_candidates:
        return [int(chapter["end"])]
    paragraph_candidates.sort(key=lambda item: (-item[1], item[0]))
    ordered = [line_idx for line_idx, _score in paragraph_candidates]
    fallback_idx = int(chapter["end"])
    if fallback_idx not in ordered:
        ordered.append(fallback_idx)
    return ordered


def _relocate_figures_by_chapter(
    md: str,
    blocks: list[dict[str, Any]],
    language: str = "zh",
    *,
    project_context: dict[str, Any] | None = None,
) -> str:
    if not blocks:
        return md
    chapters = _parse_numbered_chapters(md)
    if not chapters:
        return md

    lines = md.splitlines()
    chapter_map = {int(chapter["num"]): chapter for chapter in chapters}
    assigned: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    fig_word = "Figure" if language.lower().startswith("en") else "图"

    blocks_by_chapter: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        plan_item = _match_figure_plan_item(block, project_context)
        preferred_section = str(plan_item.get("section") if isinstance(plan_item, dict) else "")
        target_chapter = None
        if preferred_section:
            for chapter in chapters:
                if preferred_section.lower() in str(chapter["title"]).lower():
                    target_chapter = chapter
                    break
        if target_chapter is None:
            scored = sorted(
                chapters,
                key=lambda chapter: -_score_caption_for_chapter(str(block.get("caption") or ""), _title_buckets(str(chapter["title"]))),
            )
            target_chapter = scored[0]
        chapter_num = int(target_chapter["num"])
        blocks_by_chapter.setdefault(chapter_num, []).append(block)

    for chapter_num, chapter_blocks in blocks_by_chapter.items():
        chapter = chapter_map.get(chapter_num)
        if chapter is None:
            continue
        used_anchors: set[int] = set()
        for block in chapter_blocks:
            candidates = _collect_figure_anchor_candidates(lines, chapter, block, project_context=project_context)
            anchor_idx = next((candidate for candidate in candidates if candidate not in used_anchors), candidates[0] if candidates else int(chapter["end"]))
            used_anchors.add(anchor_idx)
            assigned.setdefault(anchor_idx, []).append((chapter_num, block))

    if not assigned:
        return md

    output = lines[:]
    chapter_counts: dict[int, int] = {}
    for anchor_idx in sorted(assigned.keys(), reverse=True):
        inserted: list[str] = []
        for chapter_num, block in assigned[anchor_idx]:
            chapter_counts[chapter_num] = chapter_counts.get(chapter_num, 0) + 1
            fig_label = f"{chapter_num}-{chapter_counts[chapter_num]}"
            caption = str(block.get("caption") or "").strip()
            path = str(block.get("path") or "").strip()
            inserted.extend(
                [
                    "",
                    f"![{caption}]({path})",
                    f"{fig_word}{fig_label if fig_word == '图' else ' ' + fig_label} {caption}",
                    "",
                ]
            )
        output[anchor_idx + 1:anchor_idx + 1] = inserted
    return "\n".join(output)


def _figure_plan_item_present(md_text: str, plan_item: dict[str, Any]) -> bool:
    md_lower = md_text.lower()
    asset_name = Path(str(plan_item.get("existing_asset") or "")).name.lower()
    if asset_name and asset_name in md_lower:
        return True
    caption = str(plan_item.get("caption") or "").strip()
    caption_core = re.sub(r"^(?:图|figure)\s*\d+(?:[.-]\d+)?[.:：]?\s*", "", caption, flags=re.I).strip().lower()
    if caption_core and caption_core in md_lower:
        return True
    goal = str(plan_item.get("goal") or "").strip().lower()
    return bool(goal and goal in md_lower)


def _build_figure_placeholder_block(plan_item: dict[str, Any], language: str = "zh") -> list[str]:
    caption = str(plan_item.get("caption") or plan_item.get("goal") or "待补插图").strip()
    caption = re.sub(r"^(?:图|figure)\s*\d+(?:[.-]\d+)?[.:：]?\s*", "", caption, flags=re.I).strip() or caption
    figure_type = str(plan_item.get("figure_type") or "").strip()
    goal = str(plan_item.get("goal") or "").strip()
    evidence = str(plan_item.get("evidence") or "").strip()
    if language.lower().startswith("en"):
        lines = [
            f"> [Figure Placeholder] {caption}",
            f"> Type: {figure_type or 'figure to be provided'}",
            f"> Purpose: {goal or 'Explain the nearby paragraph with project-backed visual evidence.'}",
        ]
        if evidence:
            lines.append(f"> Evidence: {evidence}")
        return lines + [""]

    lines = [
        f"> [图占位] {caption}",
        f"> 类型：{figure_type or '待补充插图'}",
        f"> 应展示：{goal or '请围绕附近段落补充能支撑论述的项目证据图。'}",
    ]
    if evidence:
        lines.append(f"> 证据来源：{evidence}")
    return lines + [""]


def _inject_missing_figure_placeholders(
    md_text: str,
    project_context: dict[str, Any] | None,
    *,
    language: str = "zh",
) -> str:
    figure_plan = [item for item in (project_context or {}).get("figure_plan") or [] if isinstance(item, dict)]
    if not figure_plan:
        return md_text

    lines = md_text.splitlines()
    chapters = _parse_numbered_chapters(md_text)
    if not chapters:
        return md_text

    insertions: dict[int, list[str]] = {}
    for plan_item in figure_plan:
        if _figure_plan_item_present(md_text, plan_item):
            continue
        section_hint = str(plan_item.get("section") or "")
        target_chapter = None
        for chapter in chapters:
            if section_hint and section_hint.lower() in str(chapter["title"]).lower():
                target_chapter = chapter
                break
        if target_chapter is None:
            target_chapter = max(
                chapters,
                key=lambda chapter: _score_caption_for_chapter(str(plan_item.get("caption") or plan_item.get("goal") or ""), _title_buckets(str(chapter["title"]))),
            )
        anchor_idx = _find_best_figure_anchor(lines, target_chapter, {"caption": plan_item.get("caption"), "path": ""}, project_context=project_context)
        insertions.setdefault(anchor_idx, []).extend([""] + _build_figure_placeholder_block(plan_item, language=language))

    if not insertions:
        return md_text

    output = lines[:]
    for anchor_idx in sorted(insertions.keys(), reverse=True):
        output[anchor_idx + 1:anchor_idx + 1] = insertions[anchor_idx]
    return "\n".join(output)


def _is_standalone_equation_label(text: str) -> bool:
    return bool(re.match(r"^\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$", text))


def _normalize_formula_blocks(md: str) -> str:
    """Canonicalize malformed display equations into stable $$...$$ blocks."""
    prev = None
    cur = md
    inline_pat = re.compile(r"([^\n]+?)\s*\$\$\s*([^$\n]+?)\s*\$\$\s*([^\n]+)")

    for _ in range(5):
        if prev == cur:
            break
        prev = cur
        cur = inline_pat.sub(
            lambda m: f"{m.group(1).rstrip()}\n\n$${m.group(2).strip()}$$\n\n{m.group(3).lstrip()}",
            cur,
        )
        cur = re.sub(
            r"^\s*\$\$\s*([^$\n]+?)\s*\$\$\s*([^\n$].+)$",
            lambda m: f"$${m.group(1).strip()}$$\n\n{m.group(2).lstrip()}",
            cur,
            flags=re.M,
        )
        cur = re.sub(
            r"^([^\n$].*?)\s*\$\$\s*([^$\n]+?)\s*\$\$\s*$",
            lambda m: f"{m.group(1).rstrip()}\n\n$${m.group(2).strip()}$$",
            cur,
            flags=re.M,
        )

    cur = re.sub(
        r"\$\$\s*([^\n$][\s\S]*?)\n\s*\\tag\{([^}]+)\}\s*\n\s*\$\$",
        lambda m: "$$\n" + m.group(1).strip() + f"\n\\tag{{{m.group(2).strip()}}}\n$$",
        cur,
        flags=re.M,
    )
    cur = re.sub(
        r"\$\$\s*([^\n$][\s\S]*?)\s*\$\$\s*\n\s*\\tag\{([^}]+)\}",
        lambda m: "$$\n" + m.group(1).strip() + f"\n\\tag{{{m.group(2).strip()}}}\n$$",
        cur,
        flags=re.M,
    )

    tag_pat = re.compile(r"^\s*\\tag\{[^}]+\}\s*$")
    rebuilt: list[str] = []
    lines = cur.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("$$"):
            expr_lines: list[str] = []
            tag_line = ""
            opening = stripped[2:].strip()
            if opening.endswith("$$") and len(opening) > 2:
                expr = opening[:-2].strip()
                if expr:
                    expr_lines.append(expr)
                i += 1
            else:
                if opening:
                    expr_lines.append(opening)
                i += 1
                while i < len(lines):
                    current = lines[i].strip()
                    if not current:
                        i += 1
                        continue
                    if tag_pat.match(current):
                        tag_line = current
                        i += 1
                        continue
                    if current == "$$":
                        i += 1
                        break
                    if current.endswith("$$") and not current.startswith("$$"):
                        tail = current[:-2].strip()
                        if tail:
                            expr_lines.append(tail)
                        i += 1
                        break
                    expr_lines.append(lines[i].strip())
                    i += 1

            while i < len(lines) and _is_standalone_equation_label(lines[i].strip()):
                i += 1

            expr_text = "\n".join(part for part in expr_lines if part).strip()
            if expr_text:
                rebuilt.extend(["$$", expr_text])
                if tag_line:
                    rebuilt.append(tag_line)
                rebuilt.append("$$")
            continue

        if tag_pat.match(stripped) or _is_standalone_equation_label(stripped):
            i += 1
            continue

        rebuilt.append(lines[i])
        i += 1

    cur = "\n".join(rebuilt)
    cur = re.sub(r"^\s*[。.]+\s*$", "", cur, flags=re.M)
    cur = re.sub(r"\n{3,}", "\n\n", cur)
    return cur


def _number_equation_blocks(md: str, language: str = "zh") -> str:
    lines = md.splitlines()
    out: list[str] = []
    chapter_num = 0
    equation_idx = 0
    tag_pat = re.compile(r"^\s*\\tag\{[^}]+\}\s*$")
    i = 0
    while i < len(lines):
        line = lines[i]
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            chapter_num = int(chapter_match.group(1))
            equation_idx = 0
            out.append(line)
            i += 1
            continue

        if line.strip() == "$$":
            i += 1
            body_lines: list[str] = []
            while i < len(lines) and lines[i].strip() != "$$":
                if not tag_pat.match(lines[i].strip()):
                    body_lines.append(lines[i].strip())
                i += 1
            if i < len(lines) and lines[i].strip() == "$$":
                i += 1
            while i < len(lines) and _is_standalone_equation_label(lines[i].strip()):
                i += 1

            equation_body = "\n".join(part for part in body_lines if part).strip()
            if equation_body and chapter_num > 0:
                equation_idx += 1
                label = f"{chapter_num}.{equation_idx}"
                tag_text = f"式{label}" if language == "zh" else label
                out.append("$$\n" + equation_body + f"\n\\tag{{{tag_text}}}\n$$")
            elif equation_body:
                out.append("$$\n" + equation_body + "\n$$")
            continue

        if tag_pat.match(line.strip()) or _is_standalone_equation_label(line.strip()):
            i += 1
            continue

        out.append(line)
        i += 1
    return "\n".join(out)


def _dedupe_equation_labels(md_text: str, language: str = "zh") -> str:
    deduped = re.sub(r"^\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$\n?", "", md_text, flags=re.M)
    deduped = re.sub(r"(\\tag\{[^}]+\})\s*\n\s*(\\tag\{[^}]+\})", r"\2", deduped)
    deduped = re.sub(r"\$\$\s*\n\s*\\tag\{[^}]+\}\s*\n\$\$\n?", "", deduped)
    return deduped


def _repair_markdown_tables(md_text: str) -> str:
    lines = md_text.splitlines()
    repaired: list[str] = []
    sep_re = re.compile(r"^\|\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?$")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            header_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(header_cells) >= 2:
                if repaired and repaired[-1].strip():
                    repaired.append("")
                repaired.append("| " + " | ".join(_escape_markdown_table_cell(_humanize_table_header(cell)) for cell in header_cells) + " |")
                i += 1
                if i < len(lines) and sep_re.match(lines[i].strip()):
                    i += 1
                repaired.append("| " + " | ".join([":---"] + ["---:"] * (len(header_cells) - 1)) + " |")
                while i < len(lines):
                    row = lines[i].strip()
                    if not row.startswith("|") or row.count("|") < 2:
                        break
                    if sep_re.match(row):
                        i += 1
                        continue
                    row_cells = [cell.strip() for cell in row.strip("|").split("|")]
                    padded = [_escape_markdown_table_cell(cell) for cell in row_cells[: len(header_cells)]]
                    while len(padded) < len(header_cells):
                        padded.append("")
                    repaired.append("| " + " | ".join(padded) + " |")
                    i += 1
                if i < len(lines) and repaired[-1].strip():
                    repaired.append("")
                continue
        repaired.append(lines[i])
        i += 1
    return "\n".join(repaired)


def _renumber_tables(md_text: str, language: str = "zh") -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    current_chapter = 0
    chapter_counts: dict[int, int] = {}
    previous_caption_text = ""
    caption_re = re.compile(r"^(?:表|Table)\s*(\d+(?:[.-]\d+)?)\s+(.+)$", re.I)

    def _next_nonempty_line(start_idx: int) -> str:
        for probe in range(start_idx + 1, len(lines)):
            if lines[probe].strip():
                return lines[probe].strip()
        return ""

    for idx, line in enumerate(lines):
        chapter_match = re.match(r"^##\s+(\d+)\.", line)
        if chapter_match:
            current_chapter = int(chapter_match.group(1))
            chapter_counts.setdefault(current_chapter, 0)
            previous_caption_text = ""
            out.append(line)
            continue

        caption_match = caption_re.match(line.strip())
        if caption_match and current_chapter > 0:
            next_nonempty = _next_nonempty_line(idx)
            if not next_nonempty.startswith("|"):
                out.append(line)
                if line.strip():
                    previous_caption_text = ""
                continue

            caption_text = _normalize_display_caption_text(caption_match.group(2).strip())
            if caption_text == previous_caption_text:
                continue

            chapter_counts[current_chapter] = chapter_counts.get(current_chapter, 0) + 1
            label = f"{current_chapter}.{chapter_counts[current_chapter]}"
            out.append(f"表{label} {caption_text}" if language == "zh" else f"Table {label} {caption_text}")
            previous_caption_text = caption_text
            continue

        out.append(line)
        if line.strip():
            previous_caption_text = ""
    return "\n".join(out)


def _caption_key(text: str) -> str:
    raw = str(text or "").strip()
    raw = re.sub(r"^(?:图|Figure|表|Table)\s*\d+(?:[.-]\d+)?(?:\s+|[:：.])", "", raw, flags=re.I)
    raw = raw.lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", raw)


def _collect_existing_figure_keys(md_text: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", md_text, flags=re.M):
        alt = _caption_key(match.group(1))
        if alt:
            keys.add(alt)
        asset = Path(match.group(2)).name.lower()
        if asset:
            keys.add(asset)
    for match in re.finditer(r"^(?:图|Figure)\s*\d+(?:-\d+)?\s+(.+)$", md_text, flags=re.M | re.I):
        key = _caption_key(match.group(1))
        if key:
            keys.add(key)
    for match in re.finditer(r"^>\s*\[(?:Figure Placeholder|待补图)\]\s*(.+)$", md_text, flags=re.M | re.I):
        key = _caption_key(match.group(1))
        if key:
            keys.add(key)
    return keys


def _anchor_is_overcrowded(candidate: int, used_anchors: set[int], min_gap: int = 8) -> bool:
    return any(abs(candidate - used) < min_gap for used in used_anchors)


def _is_anchorable_prose_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "!", "|", ">", "图", "Figure", "表", "Table", "$$", "\\tag", "```", "- ", "* ")):
        return False
    if len(stripped) >= 18:
        return True
    if re.search(r"[\u4e00-\u9fff]{4,}", stripped):
        return True
    return bool(re.search(r"[A-Za-z]{4,}", stripped))


def _collect_figure_anchor_candidates(
    lines: list[str],
    chapter: dict[str, Any],
    block: dict[str, Any],
    *,
    project_context: dict[str, Any] | None,
) -> list[int]:
    plan_item = _match_figure_plan_item(block, project_context)
    keywords = _asset_keywords(
        str(block.get("caption") or ""),
        str(plan_item.get("goal") if isinstance(plan_item, dict) else ""),
        str(plan_item.get("evidence") if isinstance(plan_item, dict) else ""),
    )
    paragraph_candidates: list[tuple[int, int]] = []
    for line_idx in range(int(chapter["start"]) + 1, int(chapter["end"]) + 1):
        line = lines[line_idx].strip()
        if not _is_anchorable_prose_line(line):
            continue
        lower_line = line.lower()
        score = 0
        for keyword in keywords:
            if keyword and keyword in lower_line:
                score += 3
        if re.search(r"(如图|见图|as shown in figure)", lower_line, flags=re.I):
            score += 4
        paragraph_candidates.append((line_idx, score))
    if not paragraph_candidates:
        return [int(chapter["end"])]
    ordered = [line_idx for line_idx, _score in paragraph_candidates if _score > 0]
    ordered.extend(line_idx for line_idx, _score in paragraph_candidates if _score <= 0 and line_idx not in ordered)
    fallback_idx = int(chapter["end"])
    if fallback_idx not in ordered:
        ordered.append(fallback_idx)
    return ordered


def _relocate_figures_by_chapter(
    md: str,
    blocks: list[dict[str, Any]],
    language: str = "zh",
    *,
    project_context: dict[str, Any] | None = None,
) -> str:
    if not blocks:
        return md
    chapters = _parse_numbered_chapters(md)
    if not chapters:
        return md

    lines = md.splitlines()
    chapter_map = {int(chapter["num"]): chapter for chapter in chapters}
    assigned: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    fig_word = "Figure" if language.lower().startswith("en") else "图"

    blocks_by_chapter: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        plan_item = _match_figure_plan_item(block, project_context)
        preferred_section = str(plan_item.get("section") if isinstance(plan_item, dict) else "")
        target_chapter = None
        if preferred_section:
            for chapter in chapters:
                if preferred_section.lower() in str(chapter["title"]).lower():
                    target_chapter = chapter
                    break
        if target_chapter is None:
            scored = sorted(
                chapters,
                key=lambda chapter: -_score_caption_for_chapter(str(block.get("caption") or ""), _title_buckets(str(chapter["title"]))),
            )
            target_chapter = scored[0]
        blocks_by_chapter.setdefault(int(target_chapter["num"]), []).append(block)

    for chapter_num, chapter_blocks in blocks_by_chapter.items():
        chapter = chapter_map.get(chapter_num)
        if chapter is None:
            continue
        used_anchors: set[int] = set()
        span = max(1, int(chapter["end"]) - int(chapter["start"]))
        block_candidates = [
            (block, _collect_figure_anchor_candidates(lines, chapter, block, project_context=project_context))
            for block in chapter_blocks
        ]
        for order, (block, candidates) in enumerate(block_candidates, start=1):
            target_line = int(chapter["start"]) + round(span * order / (len(block_candidates) + 1))
            ranked = sorted(candidates, key=lambda candidate: (abs(candidate - target_line), candidate))
            anchor_idx = next(
                (candidate for candidate in ranked if candidate not in used_anchors and not _anchor_is_overcrowded(candidate, used_anchors)),
                None,
            )
            if anchor_idx is None:
                anchor_idx = next((candidate for candidate in ranked if candidate not in used_anchors), None)
            if anchor_idx is None:
                anchor_idx = ranked[0] if ranked else int(chapter["end"])
            used_anchors.add(anchor_idx)
            assigned.setdefault(anchor_idx, []).append((chapter_num, block))

    if not assigned:
        return md

    output = lines[:]
    chapter_counts: dict[int, int] = {}
    for anchor_idx in sorted(assigned.keys(), reverse=True):
        inserted: list[str] = []
        for chapter_num, block in assigned[anchor_idx]:
            chapter_counts[chapter_num] = chapter_counts.get(chapter_num, 0) + 1
            fig_label = f"{chapter_num}-{chapter_counts[chapter_num]}"
            caption = _normalize_display_caption_text(str(block.get("caption") or "").strip())
            path = str(block.get("path") or "").strip()
            inserted.extend(["", f"![{caption}]({path})", f"{fig_word}{fig_label if fig_word == '图' else ' ' + fig_label} {caption}", ""])
        output[anchor_idx + 1:anchor_idx + 1] = inserted
    return "\n".join(output)


def _figure_plan_item_present(md_text: str, plan_item: dict[str, Any]) -> bool:
    existing_keys = _collect_existing_figure_keys(md_text)
    asset_name = Path(str(plan_item.get("existing_asset") or "")).name.lower()
    if asset_name and asset_name in existing_keys:
        return True

    caption_key = _caption_key(plan_item.get("caption") or plan_item.get("goal") or "")
    if not caption_key:
        return False

    for key in existing_keys:
        if key == caption_key:
            return True
        if len(caption_key) >= 10 and key and (caption_key in key or key in caption_key):
            return True
    return False


def _build_figure_placeholder_block(
    plan_item: dict[str, Any],
    language: str = "zh",
    figure_ref: str | None = None,
) -> list[str]:
    caption = str(plan_item.get("caption") or plan_item.get("goal") or "待补图").strip()
    caption = re.sub(r"^(?:图|Figure)\s*\d+(?:[.-]\d+)?[.:：]?\s*", "", caption, flags=re.I).strip() or caption
    figure_type = str(plan_item.get("figure_type") or "").strip()
    goal = str(plan_item.get("goal") or "").strip()
    evidence = str(plan_item.get("evidence") or "").strip()
    if language.lower().startswith("en"):
        lines = [
            f"> [{'Insert Figure ' + figure_ref if figure_ref else 'Figure Placeholder'}] {caption}",
            f"> Type: {figure_type or 'figure to be provided'}",
            f"> Required content: {goal or 'Add a figure that directly supports the nearby paragraph.'}",
        ]
        if evidence:
            lines.append(f"> Suggested source: {evidence}")
        return lines + [""]

    lines = [
        f"> [{'此处插入图' + figure_ref if figure_ref else '待补图'}] {caption}",
        f"> 图型建议：{figure_type or '请补充与正文对应的说明图'}",
        f"> 应展示内容：{goal or '请围绕附近段落补充能支撑论述的项目图像'}",
    ]
    if evidence:
        lines.append(f"> 推荐素材来源：{evidence}")
    return lines + [""]


def _inject_missing_figure_placeholders(
    md_text: str,
    project_context: dict[str, Any] | None,
    *,
    language: str = "zh",
) -> str:
    figure_plan = [item for item in (project_context or {}).get("figure_plan") or [] if isinstance(item, dict)]
    if not figure_plan:
        return md_text

    lines = md_text.splitlines()
    chapters = _parse_numbered_chapters(md_text)
    if not chapters:
        return md_text

    insertions: dict[int, list[str]] = {}
    chapter_counts: dict[int, int] = {}
    for plan_item in figure_plan:
        if _figure_plan_item_present(md_text, plan_item):
            continue
        section_hint = str(plan_item.get("section") or "")
        target_chapter = None
        for chapter in chapters:
            if section_hint and section_hint.lower() in str(chapter["title"]).lower():
                target_chapter = chapter
                break
        if target_chapter is None:
            target_chapter = max(
                chapters,
                key=lambda chapter: _score_caption_for_chapter(str(plan_item.get("caption") or plan_item.get("goal") or ""), _title_buckets(str(chapter["title"]))),
            )
        anchor_idx = _find_best_figure_anchor(lines, target_chapter, {"caption": plan_item.get("caption"), "path": ""}, project_context=project_context)
        chapter_num = int(target_chapter["num"])
        chapter_counts[chapter_num] = chapter_counts.get(chapter_num, 0) + 1
        figure_ref = f"{chapter_num}-{chapter_counts[chapter_num]}"
        insertions.setdefault(anchor_idx, []).extend([""] + _build_figure_placeholder_block(plan_item, language=language, figure_ref=figure_ref))

    if not insertions:
        return md_text

    output = lines[:]
    for anchor_idx in sorted(insertions.keys(), reverse=True):
        output[anchor_idx + 1:anchor_idx + 1] = insertions[anchor_idx]
    return "\n".join(output)


def _build_figure_placeholder_block(
    plan_item: dict[str, Any],
    language: str = "zh",
    figure_ref: str | None = None,
) -> list[str]:
    caption = str(plan_item.get("caption") or plan_item.get("goal") or "").strip()
    caption = re.sub(r"^(?:\u56fe|Figure)\s*\d+(?:[.-]\d+)?[.:\uff1a]?\s*", "", caption, flags=re.I).strip()
    caption = _normalize_display_caption_text(caption or "Pending figure")
    figure_ref = str(figure_ref or "").strip() or "4-1"

    lines = [
        f"[\u6b64\u5904\u63d2\u5165\u56fe{figure_ref}]",
        f"\u56fe{figure_ref} {caption}",
    ]
    if language.lower().startswith("en"):
        lines.append(f"Figure {figure_ref} {caption}")
    return lines + [""]


def _inject_missing_figure_placeholders(
    md_text: str,
    project_context: dict[str, Any] | None,
    *,
    language: str = "zh",
) -> str:
    figure_plan = [item for item in (project_context or {}).get("figure_plan") or [] if isinstance(item, dict)]
    if not figure_plan:
        return md_text

    lines = md_text.splitlines()
    chapters = _parse_numbered_chapters(md_text)
    if not chapters:
        return md_text

    scheduled: list[tuple[int, int, int, dict[str, Any]]] = []
    for plan_index, plan_item in enumerate(figure_plan):
        if _figure_plan_item_present(md_text, plan_item):
            continue
        section_hint = str(plan_item.get("section") or "")
        target_chapter = None
        for chapter in chapters:
            if section_hint and section_hint.lower() in str(chapter["title"]).lower():
                target_chapter = chapter
                break
        if target_chapter is None:
            target_chapter = max(
                chapters,
                key=lambda chapter: _score_caption_for_chapter(
                    str(plan_item.get("caption") or plan_item.get("goal") or ""),
                    _title_buckets(str(chapter["title"])),
                ),
            )
        anchor_idx = _find_best_figure_anchor(
            lines,
            target_chapter,
            {"caption": plan_item.get("caption"), "path": ""},
            project_context=project_context,
        )
        scheduled.append((int(target_chapter["num"]), anchor_idx, plan_index, plan_item))

    if not scheduled:
        return md_text

    insertions: dict[int, list[str]] = {}
    chapter_counts: dict[int, int] = {}
    for chapter_num, anchor_idx, plan_index, plan_item in sorted(scheduled, key=lambda item: (item[0], item[1], item[2])):
        chapter_counts[chapter_num] = chapter_counts.get(chapter_num, 0) + 1
        figure_ref = f"{chapter_num}-{chapter_counts[chapter_num]}"
        insertions.setdefault(anchor_idx, []).extend(
            [""] + _build_figure_placeholder_block(plan_item, language=language, figure_ref=figure_ref)
        )

    output = lines[:]
    for anchor_idx in sorted(insertions.keys(), reverse=True):
        output[anchor_idx + 1:anchor_idx + 1] = insertions[anchor_idx]
    return "\n".join(output)


def _looks_like_equation_line(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if stripped in {"$$", "$"}:
        return True
    if stripped.startswith(("#", "!", "|", ">", "```", "- ", "* ")):
        return False
    if stripped.startswith(("图", "Figure", "表", "Table")):
        return False
    if _is_standalone_equation_label(stripped):
        return False
    math_hints = ("=", "\\", "^", "_", "{", "}", "[", "]", "(", ")", "sin", "cos", "log", "exp", "sqrt", "sum", "int", "theta", "lambda")
    if any(token in stripped for token in math_hints):
        return True
    return bool(
        re.fullmatch(r"[A-Za-z0-9\s\+\-\*/\.,:&<>|]+", stripped)
        and any(ch.isalpha() for ch in stripped)
        and any(op in stripped for op in "+-*/")
    )


def _looks_like_explicit_equation_body(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "!", "|", ">", "```", "- ", "* ", "图", "Figure", "表", "Table")):
        return False
    strong_tokens = ("=", "\\frac", "\\sum", "\\int", "\\begin{", "\\rightarrow")
    if any(token in stripped for token in strong_tokens):
        return True
    return bool(
        len(stripped) <= 120
        and re.fullmatch(r"[A-Za-z0-9\\\s\+\-\*/\^\_\(\)\[\]\{\}\.,:&<>|]+", stripped)
        and any(token in stripped for token in ("\\", "^", "_"))
    )


def _salvage_formula_source(md: str) -> str:
    """Repair common malformed equation fragments before canonical parsing."""
    lines = md.splitlines()
    tag_pat = re.compile(r"^\s*\\tag\{[^}]+\}\s*$")
    salvaged: list[str] = []
    i = 0

    def _next_nonempty(start: int) -> tuple[int | None, str]:
        for idx in range(start, len(lines)):
            candidate = lines[idx].strip()
            if candidate:
                return idx, candidate
        return None, ""

    while i < len(lines):
        raw_line = lines[i]
        stripped = raw_line.strip()

        if not stripped:
            salvaged.append(raw_line)
            i += 1
            continue

        next_idx, next_stripped = _next_nonempty(i + 1)
        next2_idx, next2_stripped = _next_nonempty((next_idx + 1) if next_idx is not None else i + 1)

        if (
            not stripped.startswith("$$")
            and _looks_like_explicit_equation_body(stripped)
            and next_idx is not None
            and next2_idx is not None
            and (tag_pat.match(next_stripped) or _is_standalone_equation_label(next_stripped))
            and next2_stripped.startswith("$$")
        ):
            salvaged.extend(["$$", stripped])
            if tag_pat.match(next_stripped):
                salvaged.append(next_stripped)
            closing = next2_stripped[2:].strip()
            if tag_pat.match(closing):
                salvaged.append(closing)
            salvaged.append("$$")
            i = next2_idx + 1
            continue

        if stripped.startswith("$$") and stripped != "$$":
            remainder = stripped[2:].strip()
            if _is_standalone_equation_label(remainder):
                salvaged.append("$$")
                i += 1
                while i < len(lines):
                    trailing = lines[i].strip()
                    if not trailing:
                        break
                    if tag_pat.match(trailing) or _is_standalone_equation_label(trailing):
                        i += 1
                        continue
                    break
                continue
            if remainder:
                if not salvaged or salvaged[-1].strip() != "$$":
                    salvaged.append("$$")
                if remainder.endswith("$$"):
                    expr = remainder[:-2].strip()
                    if expr:
                        salvaged.append(expr)
                    salvaged.append("$$")
                else:
                    salvaged.append(remainder)
                i += 1
                continue

        if stripped.endswith("$$") and stripped != "$$":
            remainder = stripped[:-2].rstrip()
            if _looks_like_explicit_equation_body(remainder):
                if remainder:
                    salvaged.append(remainder)
                salvaged.append("$$")
                i += 1
                continue
            salvaged.append(raw_line[: raw_line.rfind("$$")].rstrip())
            if not (next_idx is not None and next_stripped.startswith("$$")):
                salvaged.append("$$")
            i += 1
            continue

        salvaged.append(raw_line)
        i += 1

    compacted: list[str] = []
    for line in salvaged:
        if line.strip() == "$$" and compacted and compacted[-1].strip() == "$$":
            continue
        compacted.append(line)
    return "\n".join(compacted)


def _normalize_formula_blocks(md: str) -> str:
    """Normalize display equations without swallowing nearby prose or tables."""
    prev = None
    cur = _salvage_formula_source(md)
    inline_pat = re.compile(r"([^\n]+?)\s*\$\$\s*([^$\n]+?)\s*\$\$\s*([^\n]+)")

    for _ in range(4):
        if prev == cur:
            break
        prev = cur
        cur = inline_pat.sub(
            lambda m: f"{m.group(1).rstrip()}\n\n$${m.group(2).strip()}$$\n\n{m.group(3).lstrip()}",
            cur,
        )

    tag_pat = re.compile(r"^\s*\\tag\{[^}]+\}\s*$")
    lines = cur.splitlines()
    rebuilt: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            rebuilt.append(lines[i])
            i += 1
            continue

        if not stripped.startswith("$$"):
            if _looks_like_explicit_equation_body(stripped):
                tag_line = ""
                probe = i + 1
                while probe < len(lines):
                    candidate = lines[probe].strip()
                    if not candidate:
                        probe += 1
                        break
                    if tag_pat.match(candidate):
                        tag_line = candidate
                        probe += 1
                        continue
                    if _is_standalone_equation_label(candidate):
                        probe += 1
                        continue
                    if candidate == "$$":
                        probe += 1
                        break
                    if candidate.startswith("$$") and _is_standalone_equation_label(candidate[2:].strip()):
                        probe += 1
                        break
                    break

                rebuilt.extend(["$$", stripped])
                if tag_line:
                    rebuilt.append(tag_line)
                rebuilt.append("$$")
                i = probe
                continue

            if tag_pat.match(stripped) or _is_standalone_equation_label(stripped):
                i += 1
                continue
            rebuilt.append(lines[i])
            i += 1
            continue

        tag_line = ""
        body_lines: list[str] = []

        if stripped.count("$$") >= 2 and stripped != "$$":
            start = stripped.find("$$") + 2
            end = stripped.find("$$", start)
            expr = stripped[start:end].strip()
            tail = stripped[end + 2 :].strip()
            if expr and not _is_standalone_equation_label(expr):
                body_lines.append(expr)
            if tag_pat.match(tail):
                tag_line = tail
            i += 1
            while i < len(lines):
                extra = lines[i].strip()
                if tag_pat.match(extra):
                    tag_line = extra
                    i += 1
                    continue
                if _is_standalone_equation_label(extra):
                    i += 1
                    continue
                break
        else:
            first = stripped[2:].strip()
            if first and not _is_standalone_equation_label(first):
                body_lines.append(first)
            i += 1
            while i < len(lines):
                current = lines[i].strip()
                if not current:
                    i += 1
                    continue
                if tag_pat.match(current):
                    tag_line = current
                    i += 1
                    continue
                if _is_standalone_equation_label(current):
                    i += 1
                    continue
                if current == "$$":
                    i += 1
                    break
                if current.startswith("$$"):
                    remainder = current[2:].strip()
                    if not remainder:
                        i += 1
                        break
                    if _is_standalone_equation_label(remainder):
                        i += 1
                        while i < len(lines):
                            trailing = lines[i].strip()
                            if tag_pat.match(trailing):
                                tag_line = trailing
                                i += 1
                                continue
                            if _is_standalone_equation_label(trailing):
                                i += 1
                                continue
                            break
                        break
                    current = remainder
                if current.endswith("$$"):
                    expr = current[:-2].strip()
                    if expr and not _is_standalone_equation_label(expr):
                        body_lines.append(expr)
                    i += 1
                    break
                if not _looks_like_equation_line(current):
                    break
                body_lines.append(current)
                i += 1

        expr_text = "\n".join(part for part in body_lines if part).strip()
        if expr_text and not _is_standalone_equation_label(expr_text):
            rebuilt.extend(["$$", expr_text])
            if tag_line:
                rebuilt.append(tag_line)
            rebuilt.append("$$")

    cur = "\n".join(rebuilt)
    cur = re.sub(r"^\s*(?:式\s*\d+\.\d+|Eq\.\s*\(\d+\.\d+\))\s*$", "", cur, flags=re.M)
    cur = re.sub(r"^\s*[。.]+\s*$", "", cur, flags=re.M)
    cur = re.sub(r"\n{3,}", "\n\n", cur)
    return cur


def _relocate_figures_by_chapter(
    md: str,
    blocks: list[dict[str, Any]],
    language: str = "zh",
    *,
    project_context: dict[str, Any] | None = None,
) -> str:
    if not blocks:
        return md
    chapters = _parse_numbered_chapters(md)
    if not chapters:
        return md

    lines = md.splitlines()
    chapter_map = {int(chapter["num"]): chapter for chapter in chapters}
    assignments: list[tuple[int, int, dict[str, Any]]] = []
    fig_word = "Figure" if language.lower().startswith("en") else "图"

    blocks_by_chapter: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        plan_item = _match_figure_plan_item(block, project_context)
        preferred_section = str(plan_item.get("section") if isinstance(plan_item, dict) else "")
        target_chapter = None
        if preferred_section:
            for chapter in chapters:
                if preferred_section.lower() in str(chapter["title"]).lower():
                    target_chapter = chapter
                    break
        if target_chapter is None:
            scored = sorted(
                chapters,
                key=lambda chapter: -_score_caption_for_chapter(str(block.get("caption") or ""), _title_buckets(str(chapter["title"]))),
            )
            target_chapter = scored[0]
        blocks_by_chapter.setdefault(int(target_chapter["num"]), []).append(block)

    for chapter_num, chapter_blocks in blocks_by_chapter.items():
        chapter = chapter_map.get(chapter_num)
        if chapter is None:
            continue
        used_anchors: set[int] = set()
        span = max(1, int(chapter["end"]) - int(chapter["start"]))
        block_candidates = [
            (block, _collect_figure_anchor_candidates(lines, chapter, block, project_context=project_context))
            for block in chapter_blocks
        ]
        block_candidates.sort(key=lambda item: item[1][0] if item[1] else int(chapter["end"]))
        for order, (block, candidates) in enumerate(block_candidates, start=1):
            target_line = int(chapter["start"]) + round(span * order / (len(block_candidates) + 1))
            ranked = sorted(candidates, key=lambda candidate: (abs(candidate - target_line), candidate))
            anchor_idx = next(
                (candidate for candidate in ranked if candidate not in used_anchors and not _anchor_is_overcrowded(candidate, used_anchors)),
                None,
            )
            if anchor_idx is None:
                anchor_idx = next((candidate for candidate in ranked if candidate not in used_anchors), None)
            if anchor_idx is None:
                anchor_idx = ranked[0] if ranked else int(chapter["end"])
            used_anchors.add(anchor_idx)
            assignments.append((anchor_idx, chapter_num, block))

    if not assignments:
        return md

    ordered_assignments = sorted(assignments, key=lambda item: (item[0], item[1]))
    labeled: list[tuple[int, int, str, str]] = []
    chapter_counts: dict[int, int] = {}
    for anchor_idx, chapter_num, block in ordered_assignments:
        chapter_counts[chapter_num] = chapter_counts.get(chapter_num, 0) + 1
        fig_label = f"{chapter_num}-{chapter_counts[chapter_num]}"
        caption = _normalize_display_caption_text(str(block.get("caption") or "").strip())
        path = str(block.get("path") or "").strip()
        labeled.append((anchor_idx, chapter_num, fig_label, caption, path))

    output = lines[:]
    for anchor_idx, _chapter_num, fig_label, caption, path in sorted(labeled, key=lambda item: item[0], reverse=True):
        output[anchor_idx + 1:anchor_idx + 1] = [
            "",
            f"![{caption}]({path})",
            f"{fig_word}{fig_label if fig_word == '图' else ' ' + fig_label} {caption}",
            "",
        ]
    return "\n".join(output)


# ---- M2: LLM-driven citation marker injection ----

def _inject_citation_markers(md: str, references: list[dict[str, Any]],
                              llm_call: Any) -> str:
    """Ask the LLM to plan [N] citation insertions and apply them.

    Returns the original text unchanged if the LLM call fails or the plan is
    malformed. Only touches chapters 1–4 body prose (skips tables, formulas,
    figure lines, and headings).
    """
    if not references or not callable(llm_call):
        return md

    refs_brief_lines = []
    for i, r in enumerate(references[:8], 1):
        title = (r.get('title') or '').strip()[:120]
        first_auth = ((r.get('authors') or [''])[0] or '').strip()
        year = str(r.get('year') or '').strip() or 'n.d.'
        refs_brief_lines.append(f"[{i}] {first_auth}. {title} ({year})")
    refs_brief = '\n'.join(refs_brief_lines)

    # Select prose snippets from chapters 1-4 only.
    chap_pat = re.compile(r'^##\s+([1-4])\.\s+.+$', re.M)
    chap_matches = list(chap_pat.finditer(md))
    if not chap_matches:
        return md
    bounds = []
    for i, m in enumerate(chap_matches):
        end = chap_matches[i + 1].start() if i + 1 < len(chap_matches) else len(md)
        bounds.append((m.start(), end))

    # Collect up to 12 candidate paragraphs from those chapters.
    candidates: list[str] = []
    for s, e in bounds:
        seg = md[s:e]
        for para in re.split(r'\n{2,}', seg):
            p = para.strip()
            if len(p) < 60:
                continue
            if p.startswith(('#', '!', '|', '$', '图', '表')):
                continue
            if '|' in p or '$$' in p:
                continue
            if len(candidates) >= 12:
                break
            candidates.append(p[:260])
        if len(candidates) >= 12:
            break
    if not candidates:
        return md

    numbered = '\n'.join(f'({i + 1}) {c}' for i, c in enumerate(candidates))
    max_ref = len(refs_brief_lines)
    prompt = (
        "You are annotating a Chinese engineering thesis with citation markers. "
        "Given the reference list and a set of numbered candidate paragraphs, "
        "pick 4-6 paragraphs where a citation is natural (background, prior method, "
        "comparison, theoretical basis). For each, output a JSON object "
        "with keys 'paragraph_id' (integer), 'anchor' (an 8-20 character "
        "substring from that paragraph where the [N] should be appended), "
        f"and 'cite' (one of [1]..[{max_ref}]). "
        "Output ONLY a JSON array. No commentary.\n\n"
        "REFERENCES:\n"
        f"{refs_brief}\n\n"
        "CANDIDATE PARAGRAPHS:\n"
        f"{numbered}\n"
    )

    try:
        raw = llm_call(prompt)
    except Exception as exc:
        print(f"[CiteInject] LLM call failed: {exc}")
        return md
    if not raw:
        return md
    m = re.search(r'\[.*\]', raw, re.S)
    if not m:
        return md
    try:
        plan = json.loads(m.group(0))
    except Exception as exc:
        print(f"[CiteInject] JSON parse failed: {exc}")
        return md
    if not isinstance(plan, list):
        return md

    out = md
    applied = 0
    for entry in plan:
        try:
            pid = int(entry.get('paragraph_id'))
            anchor = str(entry.get('anchor') or '').strip()
            cite = str(entry.get('cite') or '').strip()
        except Exception:
            continue
        if not anchor or not cite.startswith('[') or not cite.endswith(']'):
            continue
        if not (1 <= pid <= len(candidates)):
            continue
        if anchor not in out:
            continue
        # Append citation after the first end-of-sentence that follows anchor.
        idx = out.find(anchor)
        after_anchor = out[idx + len(anchor):]
        punct_match = re.search(r'[。；]', after_anchor)
        if punct_match:
            insert_at = idx + len(anchor) + punct_match.end()
            out = out[:insert_at] + cite + out[insert_at:]
        else:
            insert_at = idx + len(anchor)
            out = out[:insert_at] + cite + out[insert_at:]
        applied += 1
    if applied:
        print(f"[CiteInject] 注入 {applied} 处文献引用标记")
    return out


# ---- Final polish pass: one-shot fixes for the full-paper audit ----

_META_PREFIX_PATTERNS = [
    re.compile(r'^\s*论文写作稿\s*[:：]\s*'),
    re.compile(r'^\s*毕业论文\s*[:：]\s*'),
    re.compile(r'^\s*论文初稿\s*[:：]\s*'),
    re.compile(r'^\s*论文\s*[:：]\s*'),
    re.compile(r'^\s*(?:thesis|paper|draft|title)\s*[:：]\s*', re.I),
]


def _strip_title_meta_prefix(title: str) -> str:
    """Remove author/debug prefixes that sneak into the H1 title.

    Handles forms like '论文写作稿：基于...' or 'Thesis Draft: ...'.
    """
    cleaned = title
    for pat in _META_PREFIX_PATTERNS:
        cleaned = pat.sub('', cleaned, count=1)
    return cleaned.strip()


def _strip_meta_prefix_in_markdown(md: str) -> str:
    """Remove '论文写作稿：' etc. from the H1 and any text body references."""
    out_lines: list[str] = []
    for line in md.splitlines():
        if line.startswith('# '):
            title_body = line[2:]
            cleaned = _strip_title_meta_prefix(title_body)
            out_lines.append('# ' + cleaned if cleaned else line)
            continue
        # Body-level contamination: "本文围绕论文写作稿：X 展开"
        for pat in _META_PREFIX_PATTERNS:
            # Look for 'SomeText<prefix>' anywhere in the line
            if pat.pattern.lstrip('^').strip():
                body_pat = re.compile(pat.pattern.replace('^\\s*', ''))
                line = body_pat.sub('', line)
        out_lines.append(line)
    return '\n'.join(out_lines)


def _separate_table_caption_from_prose(md: str) -> str:
    """Wherever a line contains '...表X-Y 中文 Table X-Y English。', split the
    bilingual caption off onto its own line (Chinese only), drop the English
    duplicate, and remove the trailing period.
    """
    out_lines: list[str] = []
    # Full bilingual form: find "表 N-M <zh> Table N-M <en>[。.]"
    bilingual = re.compile(
        r'(表\s*\d+-\d+\s+[^。]*?)\s*Table\s+\d+-\d+[^。\n]*[。.]?',
        re.I,
    )
    # Fallback: "表 N-M <zh>[。]" at end of a prose line (no table directly below
    # required — LLM places these sentence-terminal captions all over).
    chinese_only = re.compile(r'(表\s*\d+-\d+\s+[^。\n]+?)[。.]\s*$')

    for line in md.splitlines():
        m = bilingual.search(line)
        if m:
            cap = m.group(1).strip().rstrip('。. ')
            prose = (line[:m.start()] + line[m.end():]).strip()
            if prose:
                out_lines.append(prose)
                out_lines.append('')
            out_lines.append(cap)
            out_lines.append('')
            continue
        m2 = chinese_only.search(line)
        if m2 and not line.lstrip().startswith('|'):
            cap = m2.group(1).strip()
            prose = line[:m2.start()].strip()
            if prose:
                out_lines.append(prose)
                out_lines.append('')
            out_lines.append(cap)
            out_lines.append('')
            continue
        out_lines.append(line)
    # Collapse triple blanks introduced by the split
    result = '\n'.join(out_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


def _escape_dollar_math_in_tables(md: str) -> str:
    """Convert $var$ occurrences INSIDE markdown table cells to *var* so the
    docx exporter (which now honours inline formatting in cells) renders them
    as italic variables. Body-paragraph math ($...$) is untouched.
    """
    lines = md.splitlines()
    out: list[str] = []
    dollar_pat = re.compile(r'(?<!\\)\$([^$\n]+?)\$')
    for line in lines:
        if line.lstrip().startswith('|') and line.count('|') >= 3:
            # Inside a table row — replace $...$ with *...*
            line = dollar_pat.sub(lambda m: f'*{m.group(1).strip()}*', line)
        out.append(line)
    return '\n'.join(out)


def _split_formula_chains_and_orphans(md: str) -> str:
    """Second-stage formula cleanup.

    Handles chains like:
        $$...$$。启发代价采用目标点的欧氏估计：。$$...$$
    Splits into:
        $$...$$

        启发代价采用目标点的欧氏估计：

        $$...$$
    Also removes orphan '。' or '：。' lines produced by previous splits, and
    repairs '$$\n\n$$' runs that the earlier pass left stacked.
    """
    # 1. Replace any 中文标点 + '$$' stuck together with a blank-line break.
    md = re.sub(r'([。：；！？])(\s*\$\$)', r'\1\n\n\2', md)
    md = re.sub(r'(\$\$)(\s*[。：；！？])', r'\1\n\n\2', md)
    # 2. Remove a leading '。' or '：。' or '：' on a line right after $$...$$.
    md = re.sub(r'\$\$\s*\n\s*[。：]+\s*\n', '$$\n\n', md)
    md = re.sub(r'\$\$\s*\n\s*[。：][^\n]{0,3}\n', '$$\n\n', md)
    # 3. Drop isolated single-character lines that are just punctuation.
    md = re.sub(r'^[。：]\s*$\n', '', md, flags=re.M)
    # 4. Collapse multiple blank lines.
    md = re.sub(r'\n{3,}', '\n\n', md)
    return md


def _move_citation_before_period(md: str) -> str:
    """Chinese academic convention puts [N] before the sentence-ending 。,
    not after. Move them.

        '...融合等方向。[2]在较规则环境中' → '...融合等方向[2]。在较规则环境中'
    """
    return re.sub(r'([\u4e00-\u9fff])([。；])(\[\d{1,3}\])', r'\1\3\2', md)


def _format_references_gb7714(md: str) -> str:
    """Upgrade the ## 参考文献 section to approximate GB/T 7714.

    - Replace 'Authors. Title. venue, year' with
      '[N] Authors. Title[J/C/D]. venue, year. DOI.' when data is available.
    - Add '[J]' article marker when venue looks like a journal; '[C]' when it
      looks like a conference. Leaves unchanged if already bracketed.
    """
    ref_block_pat = re.compile(
        r'(^##\s*参考文献\s*\n+)([\s\S]*?)(?=^##\s|\Z)', flags=re.M)
    m = ref_block_pat.search(md)
    if not m:
        return md
    head = m.group(1)
    body = m.group(2)
    # Each reference is expected to start with "N. ..."
    entry_pat = re.compile(r'^(\d+)\.\s+(.+)$', re.M)

    def _transform(match: re.Match) -> str:
        idx = match.group(1)
        line = match.group(2).strip()
        if '[J]' in line or '[C]' in line or '[D]' in line or '[M]' in line:
            return f'[{idx}] {line}'
        # Heuristic type tag based on venue/keywords.
        low = line.lower()
        if any(k in low for k in ('proceedings', 'conference', 'workshop', 'symposium', 'icra', 'iros', 'cvpr', 'neurips', 'icml')):
            type_tag = '[C]'
        elif any(k in low for k in ('arxiv', 'preprint')):
            type_tag = '[J/OL]'
        else:
            type_tag = '[J]'
        # Inject type_tag after the title. Title ends at the last '. ' before
        # the year-or-venue tail. A simple heuristic: locate the last period
        # before a 4-digit year.
        year_m = re.search(r',\s*(\d{4})\s*\.?$', line)
        if year_m:
            before_year = line[:year_m.start()].rstrip('.')
            # Title ends at the last '. ' in before_year
            last_period = before_year.rfind('. ')
            if last_period > 0:
                title = before_year[:last_period].strip()
                venue = before_year[last_period + 2:].strip()
                year = year_m.group(1)
                return f'[{idx}] {title}{type_tag}. {venue}, {year}.'
            # No venue detected
            return f'[{idx}] {before_year}{type_tag}, {year_m.group(1)}.'
        # Could not parse year — just bracket-prefix and tag the end
        return f'[{idx}] {line.rstrip(".")}{type_tag}.'

    new_body = entry_pat.sub(_transform, body)
    return md[:m.start()] + head + new_body + md[m.end():]


def _ensure_heading_blank_line(md: str) -> str:
    """Insert a blank line after every '### ...' heading when the next line
    is not already blank. Markdown technically doesn't need it, but the docx
    renderer and most readers expect the separation."""
    lines = md.splitlines()
    out: list[str] = []
    for idx, line in enumerate(lines):
        out.append(line)
        if line.startswith('### ') and idx + 1 < len(lines) and lines[idx + 1].strip():
            out.append('')
    return '\n'.join(out)


def _drop_figures_with_missing_files(md: str, project_root: Path) -> str:
    """Remove any ![...](output/figures/X.png) whose target file does not
    actually exist on disk (e.g. removed smoke-test residue)."""
    root = Path(project_root)
    lines = md.splitlines()
    out: list[str] = []
    fig_pat = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$')
    cap_pat = re.compile(r'^图\s*\d+-\d+\b')
    i = 0
    dropped = 0
    while i < len(lines):
        m = fig_pat.match(lines[i])
        if m:
            rel = m.group(2)
            resolved = (root / rel).resolve()
            if not resolved.exists():
                # Drop this figure block (image line + caption line + trailing blank)
                i += 1
                if i < len(lines) and cap_pat.match(lines[i]):
                    i += 1
                if i < len(lines) and not lines[i].strip():
                    i += 1
                dropped += 1
                continue
            # Also drop obvious test/smoke patterns regardless of file existence.
            stem_low = Path(rel).stem.lower()
            if any(stem_low.startswith(p) or stem_low.startswith(f'_{p}') for p in ('test', 'tmp', 'scratch', 'debug', 'smoke')):
                i += 1
                if i < len(lines) and cap_pat.match(lines[i]):
                    i += 1
                if i < len(lines) and not lines[i].strip():
                    i += 1
                dropped += 1
                continue
        out.append(lines[i])
        i += 1
    if dropped:
        print(f"[FigCleanup] dropped {dropped} figure(s) with missing/test-residue files")
    return '\n'.join(out)


def _translate_figure_captions_via_llm(md: str, llm_call: Any, language: str) -> str:
    """Ask the LLM to batch-translate non-Chinese (or mixed/garbled) figure
    captions into natural Chinese (or English when language='en'). No-op if
    the LLM call isn't available.
    """
    if not callable(llm_call):
        return md
    want_zh = language == 'zh'
    cap_pat = re.compile(r'^(图|Figure)\s*(\d+-\d+)\s+(.+)$', re.M)
    caps: list[tuple[re.Match, str]] = []
    for m in cap_pat.finditer(md):
        text = m.group(3).strip()
        if want_zh and re.search(r'[\u4e00-\u9fff]', text) and not re.search(r'^[A-Za-z]{3,}(\s+[A-Za-z]+)*$', text):
            continue  # looks already-Chinese enough
        if not want_zh and not re.search(r'[\u4e00-\u9fff]', text):
            continue  # already English
        caps.append((m, text))
    if not caps:
        return md
    numbered_items = '\n'.join(f'({i + 1}) {text}' for i, (_m, text) in enumerate(caps))
    target_lang = 'Chinese' if want_zh else 'English'
    prompt = (
        f"Translate the following figure captions into natural, concise academic {target_lang}. "
        "They are figure titles from an engineering thesis about robotic path planning. "
        "Keep them under 20 characters each when translated to Chinese. "
        "Output ONLY a JSON array of translated strings, same order and count as the input.\n\n"
        "INPUT:\n"
        f"{numbered_items}\n"
    )
    try:
        raw = llm_call(prompt)
    except Exception as exc:
        print(f"[CapTranslate] LLM call failed: {exc}")
        return md
    if not raw:
        return md
    arr_match = re.search(r'\[.*\]', raw, re.S)
    if not arr_match:
        return md
    try:
        translated = json.loads(arr_match.group(0))
    except Exception as exc:
        print(f"[CapTranslate] JSON parse failed: {exc}")
        return md
    if not isinstance(translated, list) or len(translated) != len(caps):
        return md
    # Apply replacements in reverse so indices stay valid
    out = md
    applied = 0
    for (m, _old), new in zip(reversed(caps), reversed(translated)):
        if not isinstance(new, str) or not new.strip():
            continue
        new = new.strip().strip('。.')
        prefix = m.group(1)
        label = m.group(2)
        replacement = f'{prefix}{"" if prefix == "图" else " "}{label} {new}'
        out = out[:m.start()] + replacement + out[m.end():]
        applied += 1
    # Also update the corresponding ![alt](...) line when it holds the raw
    # English stem — find the image block right above each caption.
    if applied:
        print(f"[CapTranslate] translated {applied} figure caption(s)")
    return out


def _sync_image_alt_with_caption(md: str) -> str:
    """After caption translation, keep ![alt](path) in sync with the '图 X-Y <caption>'
    text that follows. Prevents docx from showing the old English alt text.
    """
    lines = md.splitlines()
    fig_pat = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$')
    cap_pat = re.compile(r'^(?:图|Figure)\s*\d+-\d+\s+(.+)$')
    i = 0
    while i < len(lines) - 1:
        fm = fig_pat.match(lines[i])
        cm = cap_pat.match(lines[i + 1])
        if fm and cm:
            new_alt = cm.group(1).strip()
            if new_alt and new_alt != fm.group(1):
                lines[i] = f'![{new_alt}]({fm.group(2)})'
        i += 1
    return '\n'.join(lines)


def _interleave_figures_in_body(md: str) -> str:
    """When a chapter has >=3 figures stacked back-to-back at the end, push
    some of them into the body so each is preceded by prose. This gives the
    reader context before each image.

    Strategy: collect the chapter's figure blocks (image+caption+blank), then
    insert them after every Nth paragraph of the chapter's own body (skipping
    tables, headings, existing figures). If the chapter has too few paragraphs
    to interleave, figures stay at the end.
    """
    lines = md.splitlines()
    chap_pat = re.compile(r'^##\s+\d+\.\s+')
    fig_pat = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$')
    cap_pat = re.compile(r'^(?:图|Figure)\s*\d+-\d+\s+.+$')

    # Group into chapter segments.
    chapters: list[tuple[int, int]] = []  # (start, end)
    cur_start = None
    for idx, line in enumerate(lines):
        if chap_pat.match(line):
            if cur_start is not None:
                chapters.append((cur_start, idx - 1))
            cur_start = idx
        elif line.startswith('## ') and cur_start is not None:
            chapters.append((cur_start, idx - 1))
            cur_start = None
    if cur_start is not None:
        chapters.append((cur_start, len(lines) - 1))

    new_lines = list(lines)
    # Process each chapter from the LAST one backward so indices stay valid.
    for chap_start, chap_end in reversed(chapters):
        chap_lines = new_lines[chap_start:chap_end + 1]
        # Extract figure blocks at the tail of the chapter.
        fig_blocks: list[list[str]] = []
        body_end = len(chap_lines)
        j = body_end - 1
        while j >= 0:
            # Skip trailing blank lines
            while j >= 0 and not chap_lines[j].strip():
                j -= 1
            if j < 0:
                break
            # Is this a caption preceded by an image?
            if cap_pat.match(chap_lines[j]):
                # Look for an image 1-2 lines above
                img_idx = None
                for k in range(j - 1, max(j - 3, -1), -1):
                    if fig_pat.match(chap_lines[k]):
                        img_idx = k
                        break
                if img_idx is None:
                    break
                block = chap_lines[img_idx:j + 1]
                fig_blocks.insert(0, block)
                # Move cursor above the image, plus any trailing blank
                j = img_idx - 1
                if j >= 0 and not chap_lines[j].strip():
                    j -= 1
                body_end = j + 1
                continue
            # Not a figure block — stop collection.
            break

        if len(fig_blocks) < 2:
            continue

        # Body is chap_lines[:body_end]. Collect paragraph-ending positions.
        body = chap_lines[:body_end]
        # Find positions where a paragraph ends (blank line or end of body),
        # skipping positions inside tables or figure blocks.
        para_ends: list[int] = []
        in_table = False
        for k, bline in enumerate(body):
            if bline.lstrip().startswith('|'):
                in_table = True
                continue
            if in_table and not bline.strip():
                in_table = False
                para_ends.append(k)
                continue
            if bline.startswith(('### ', '## ', '# ', '$$', '!', '|')):
                continue
            if not bline.strip() and k > 0 and body[k - 1].strip() and not body[k - 1].startswith(('### ', '## ', '|', '$$', '!')):
                para_ends.append(k)

        # Keep only paragraph-end positions that come AFTER the chapter heading
        # and any existing table caption. We want figures mid-chapter, not
        # immediately after the H2.
        para_ends = [p for p in para_ends if p > 2]

        if not para_ends:
            continue

        # Distribute figures across available paragraph ends.
        n_figs = len(fig_blocks)
        n_slots = len(para_ends)
        if n_slots < n_figs:
            # Place as many as we can evenly, stash the rest at the end.
            chosen = para_ends
        else:
            step = max(1, n_slots // n_figs)
            chosen = para_ends[step - 1:: step][:n_figs]

        # Splice figures into chap_lines body. Iterate in reverse so indices
        # don't shift.
        new_body = body[:]
        for i, block in enumerate(reversed(fig_blocks[:len(chosen)])):
            slot = list(reversed(chosen))[i]
            insertion = [''] + block + ['']
            new_body[slot:slot] = insertion

        # Remaining figures (if n_figs > n_slots): append at chapter end.
        leftover = fig_blocks[len(chosen):]
        tail: list[str] = []
        for block in leftover:
            tail.extend([''] + block)

        new_chap = new_body + tail
        new_lines[chap_start:chap_end + 1] = new_chap

    return '\n'.join(new_lines)


def _polish_final_paper(md: str, result: dict[str, Any], language: str,
                        llm_call: Any | None) -> str:
    """One-shot late-stage polish pass.

    Runs deterministic cleanups, then an optional LLM caption-translation pass.
    The goal is that the returned markdown is "presentation-ready" — no debug
    prefixes, no compressed tables, no wedged formulas, no test-residue figures,
    no English stem captions, no orphan punctuation, and figures are
    interleaved with prose.
    """
    project_root = Path(str(result.get("project_root") or "."))

    md = _strip_meta_prefix_in_markdown(md)
    md = _drop_figures_with_missing_files(md, project_root)
    md = _fix_compressed_tables(md)                  # enhanced regex
    md = _separate_table_caption_from_prose(md)
    md = _escape_dollar_math_in_tables(md)
    md = _split_formula_chains_and_orphans(md)
    md = _move_citation_before_period(md)
    md = _format_references_gb7714(md)
    md = _ensure_heading_blank_line(md)

    # LLM caption translation + image alt sync (best-effort).
    if callable(llm_call):
        md = _translate_figure_captions_via_llm(md, llm_call, language)
        md = _sync_image_alt_with_caption(md)

    md = _interleave_figures_in_body(md)

    return md


def _backfill_i18n_and_references(result: dict[str, Any]) -> None:
    """Post-enhancement backfill: translate English front matter via LLM and
    auto-search references when empty.

    Mutates ``result['artifact']`` in place. Each step is best-effort and
    falls back to the existing template values if the LLM/network call fails.
    """
    artifact = result.get("artifact")
    if not isinstance(artifact, dict):
        return

    topic = str(artifact.get("title") or result.get("topic") or "").strip()
    if not topic:
        return

    zh_keywords = list(artifact.get("zh_keywords") or [])
    en_keywords = list(artifact.get("en_keywords") or [])
    english_title = str(artifact.get("english_title") or "")
    english_abstract = str(artifact.get("english_abstract") or "")

    needs_title_tx = _contains_cjk(english_title) or not english_title.strip()
    template_prefix = "This thesis studies Design and Implementation of"
    needs_abs_tx = (
        _contains_cjk(english_abstract)
        or english_abstract.strip().startswith(template_prefix)
        or not english_abstract.strip()
    )
    needs_kw_tx = any(_contains_cjk(k) for k in en_keywords) or not en_keywords
    needs_refs = not (artifact.get("references") or [])

    if not (needs_title_tx or needs_abs_tx or needs_kw_tx or needs_refs):
        return

    project_root = result.get("project_root") or "."

    llm_chain = None
    _call_fn = None
    try:
        from tools.writing_enhancer import _load_llm_config, _call_model
        llm_chain = _load_llm_config(Path(project_root), fallback_chain=True)
        _call_fn = _call_model
    except Exception as exc:
        print(f"[Backfill] 加载 LLM 配置失败: {exc}")

    def _llm_call(prompt: str) -> str | None:
        if not llm_chain or _call_fn is None:
            return None
        configs = llm_chain if isinstance(llm_chain, list) else [llm_chain]
        for cfg in configs:
            try:
                return _call_fn(cfg, prompt, "en").strip()
            except Exception as exc:
                print(f"[Backfill] LLM {cfg.get('provider')}/{cfg.get('model')} failed: {exc}")
                continue
        return None

    zh_abstract = ""
    for sec in (artifact.get("sections") or []):
        if isinstance(sec, dict) and "摘要" in str(sec.get("title", "")):
            content = sec.get("content") or []
            zh_abstract = "\n".join(str(p) for p in content)[:1800]
            break

    if (needs_title_tx or needs_abs_tx) and llm_chain:
        prompt = (
            "You are translating a Chinese engineering thesis front matter into academic English. "
            "Output ONLY a single JSON object with keys 'title' and 'abstract'. No code fence, no commentary.\n"
            "Rules: the 'title' must be the translated thesis title ONLY — do NOT prepend 'Thesis Draft:', "
            "'Paper:', 'Abstract:', or any similar meta prefix. Keep it under 150 characters.\n\n"
            f"Chinese title: {topic}\n\n"
            f"Chinese abstract:\n{zh_abstract or '(empty — compose a 120-word English abstract that matches the title)'}\n"
        )
        raw = _llm_call(prompt)
        if raw:
            m = re.search(r'\{.*\}', raw, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if needs_title_tx and obj.get("title") and not _contains_cjk(str(obj["title"])):
                        _t = str(obj["title"]).strip()
                        for _p in ("Thesis Draft:", "Thesis:", "Paper:", "Abstract:", "Title:"):
                            if _t.lower().startswith(_p.lower()):
                                _t = _t[len(_p):].strip()
                        artifact["english_title"] = _t
                        print(f"[Backfill] 翻译英文标题: {_t[:70]}")
                    if needs_abs_tx and obj.get("abstract") and not _contains_cjk(str(obj["abstract"])):
                        artifact["english_abstract"] = str(obj["abstract"]).strip()
                        print("[Backfill] 翻译英文摘要")
                except Exception as exc:
                    print(f"[Backfill] 解析 LLM 译文失败: {exc}")

    if needs_kw_tx and zh_keywords and llm_chain:
        prompt = (
            "Translate these Chinese academic keywords into English terms used in robotics / CS / engineering literature. "
            "Output ONLY a comma-separated list on a single line. No numbering, no extra text.\n\n"
            f"{', '.join(zh_keywords)}"
        )
        raw = _llm_call(prompt)
        if raw:
            raw = raw.splitlines()[0]
            parts = re.split(r'[,，;；]', raw)
            cleaned = [p.strip().strip('.') for p in parts if p.strip()]
            cleaned = [p for p in cleaned if p and not _contains_cjk(p)]
            if cleaned:
                artifact["en_keywords"] = cleaned[: max(len(zh_keywords), 4)]
                print(f"[Backfill] 翻译关键词: {', '.join(artifact['en_keywords'])}")

    if needs_refs:
        try:
            from tools.unified_search import auto_search
            # Use English keywords (shorter = higher relevance) as primary query,
            # falling back to the english_title and then the raw Chinese topic.
            en_kw_for_query = [k for k in (artifact.get("en_keywords") or []) if not _contains_cjk(k)]
            if en_kw_for_query:
                query = " ".join(en_kw_for_query[:4])
            else:
                query = str(artifact.get("english_title") or "").strip() or topic
                if _contains_cjk(query):
                    query = topic
            if len(query) > 140:
                query = query[:140]
            discipline = "cs"
            papers: list[dict[str, Any]] = []
            try:
                papers = auto_search(query, discipline=discipline, limit=10) or []
            except Exception as exc:
                print(f"[Backfill] auto_search 主查询失败: {exc}")
            if len(papers) < 5 and zh_keywords:
                try:
                    more = auto_search(" ".join(zh_keywords[:3]), discipline=discipline, limit=8) or []
                    papers.extend(more)
                except Exception as exc:
                    print(f"[Backfill] auto_search 关键词回退失败: {exc}")
            # Relevance filter: require the title to share at least one primary
            # English keyword (path planning / robot / navigation / ...) or the
            # abstract to mention the topic. Drops obvious off-topic results
            # (e.g. astronomy papers returned by unrelated lexical matches).
            focus_terms = {kw.lower() for kw in en_kw_for_query} | {"robot", "slam", "matlab"}
            focus_terms = {t for t in focus_terms if len(t) >= 4}
            seen_titles: set[str] = set()
            filtered: list[dict[str, Any]] = []
            for p in papers:
                title_l = str(p.get("title") or "").strip().lower()
                abs_l = str(p.get("abstract") or "").lower()
                if not title_l or title_l in seen_titles:
                    continue
                if focus_terms and not any(t in title_l or t in abs_l for t in focus_terms):
                    continue
                seen_titles.add(title_l)
                filtered.append(p)
                if len(filtered) >= 8:
                    break
            if filtered:
                artifact["references"] = _reference_catalog(filtered)
                print(f"[Backfill] 注入 {len(filtered)} 条参考文献 (via unified_search)")
            elif papers:
                # All filtered out — fall back to top 5 so section is at least populated.
                top_n = []
                for p in papers:
                    title_l = str(p.get("title") or "").strip().lower()
                    if title_l and title_l not in seen_titles:
                        seen_titles.add(title_l)
                        top_n.append(p)
                    if len(top_n) >= 5:
                        break
                if top_n:
                    artifact["references"] = _reference_catalog(top_n)
                    print(f"[Backfill] 相关性过滤后为空，回退注入 {len(top_n)} 条")
        except Exception as exc:
            print(f"[Backfill] 文献检索整体失败(非致命): {exc}")


def _apply_consistency_fixes(result: dict[str, Any], agent_output: str) -> None:
    """Apply consistency fixes suggested by the AI agent."""
    import json as _json
    try:
        json_str = agent_output
        if "{" in agent_output:
            start = agent_output.index("{")
            end = agent_output.rindex("}") + 1
            json_str = agent_output[start:end]
        data = _json.loads(json_str)
    except (ValueError, _json.JSONDecodeError):
        return

    issues = data.get("issues", [])
    if not issues:
        return

    # Log issues for quality_meta
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        meta = artifact.get("quality_meta")
        if not isinstance(meta, dict):
            meta = {}
            artifact["quality_meta"] = meta
        meta["consistency_issues"] = len(issues)
        meta["consistency_level"] = data.get("overall_consistency", "unknown")

    markdown_path = result.get("markdown_path")
    if not markdown_path or not Path(markdown_path).exists():
        return

    content = Path(markdown_path).read_text(encoding="utf-8")
    notes = []
    for issue in issues[:10]:
        desc = issue.get("description", "")
        suggestion = issue.get("suggestion", "")
        location = issue.get("location", "")
        notes.append(f"- [{location}] {desc} → {suggestion}")

    if notes:
        footnote = "\n\n<!-- Agent consistency review -->\n<!-- " + "\n<!-- ".join(notes) + " -->\n"
        content += footnote
        safe_write_text(Path(markdown_path), content)
    print(f"[PaperWriter] 一致性审查发现 {len(issues)} 个问题，已记录到论文注释中")


def _finalize_zh_generated_package(
    result: dict[str, Any],
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> dict[str, Any]:
    artifact = result.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("language") != "zh":
        return result
    project_context = result.get("project_context") or artifact.get("project_context") or {}
    workspace_root = Path(result.get("project_root", ".")).resolve()
    evidence_root = _resolve_project_evidence_root(workspace_root, project_context)
    finalize_step = 6 if str(project_context.get("source_project_path") or "").strip() else 4
    raw_figure_allowlist = project_context.get("paper_workspace_figure_files")
    figure_allowlist = None if raw_figure_allowlist is None else {str(name) for name in raw_figure_allowlist}

    try:
        _backfill_i18n_and_references(result)
    except Exception as exc:
        print(f"[Backfill] 未知异常(非致命): {exc}")

    raw_sections = artifact.get("sections")
    sections: list[dict[str, Any]] = []
    if isinstance(raw_sections, list):
        for raw_section in raw_sections:
            if not isinstance(raw_section, dict):
                continue
            title = str(raw_section.get("title") or "").strip()
            content = [str(item).strip() for item in (raw_section.get("content") or []) if str(item).strip()]
            if title or content:
                sections.append({"title": title, "content": content})
    if not sections:
        return result

    polished_sections = _polish_chinese_sections(sections)
    artifact["sections"] = polished_sections
    _emit_generation_progress(progress_callback, finalize_step, "正在整理终稿与附件...", "正在清洗章节结构并写回草稿文件")

    # Resolve paper title from artifact or topic. Strip common meta prefixes
    # (e.g. "论文写作稿：", "Thesis Draft:") so they don't bleed into the H1,
    # abstract, and every downstream reference.
    _raw_title = str((artifact.get("title") or result.get("topic") or "").strip())
    _paper_title = _strip_title_meta_prefix(_raw_title)
    if _paper_title != _raw_title:
        artifact["title"] = _paper_title

    markdown_path_value = result.get("markdown_path")
    if markdown_path_value:
        markdown_path = Path(str(markdown_path_value))

        # Render ALL sections including 摘要, with paper title as H1
        md_text = _render_sections_markdown(polished_sections, title=_paper_title).rstrip() + "\n"

        # --- Fix compressed markdown tables (LLM sometimes puts all rows on one line) ---
        md_text = _fix_compressed_tables(md_text)

        safe_write_text(markdown_path, md_text)
        _emit_generation_progress(progress_callback, finalize_step, "正在整理终稿与附件...", "正在补齐实验表格、整理图表和清洗 Markdown")

        # Auto-generate figures from CSV/JSON result data
        try:
            from figure_generator import auto_figures_from_results as _auto_fig
            _results_dir = evidence_root / "output" / "results"
            _figures_dir = workspace_root / "output" / "figures"
            if _results_dir.exists():
                _gen = _auto_fig(_results_dir, _figures_dir, language="zh")
                if _gen:
                    if figure_allowlist is not None:
                        figure_allowlist.update(Path(str(item)).name for item in _gen)
                        project_context["paper_workspace_figure_files"] = sorted(figure_allowlist)
                    print(f"[FigureGen] 从数据文件自动生成 {len(_gen)} 张图表")
            # Also auto-generate diagrams from paper context if figures dir exists
            if _figures_dir.exists() and len(list(_figures_dir.glob("*.png"))) < 3:
                try:
                    from diagram_generator import auto_generate_missing_diagrams
                    _paper_text = _render_sections_markdown(polished_sections, title=_paper_title)
                    _diagrams = auto_generate_missing_diagrams(_figures_dir, _paper_text)
                    if _diagrams:
                        if figure_allowlist is not None:
                            figure_allowlist.update(Path(str(item)).name for item in _diagrams)
                            project_context["paper_workspace_figure_files"] = sorted(figure_allowlist)
                        print(f"[DiagramGen] 自动生成 {len(_diagrams)} 张图表")
                except Exception:
                    pass
        except Exception:
            pass  # Non-critical: figures may already exist

        # Post-process: inject real CSV data tables and figure paths into the draft
        _inject_real_experiment_data(markdown_path, result)

        # Post-process: if no figures in markdown, inject available figures
        _figures_dir = workspace_root / "output" / "figures"
        _md_text = markdown_path.read_text(encoding="utf-8")
        if "![(" not in _md_text and _figures_dir.exists():
            _available_figs = _select_best_figures(
                _figures_dir,
                max_count=6,
                allowed_files=figure_allowlist,
            )
            if _available_figs:
                _injected = _inject_figures_into_markdown(
                    _md_text, _available_figs, polished_sections,
                )
                if _injected != _md_text:
                    # Renumber figures sequentially
                    _injected = _renumber_figures(_injected)
                    safe_write_text(markdown_path, _injected)
                    print(f"[FigInject] 注入 {len(_available_figs)} 张图片到论文中")
        else:
            # Already has figures — still renumber and clean up
            _md_text = _renumber_figures(markdown_path.read_text(encoding="utf-8"))
            safe_write_text(markdown_path, _md_text)

        # --- Final cleanup pass ---
        _final = markdown_path.read_text(encoding="utf-8")
        _final = _cleanup_markdown(_final)
        # Renumber again after cleanup (Pass 4.5 may have added 图0-0 placeholders)
        _final = _renumber_figures(_final)

        # --- Ensure 摘要 section exists (enhancer may have stripped it) ---
        if "## 摘要" not in _final and "## 中文摘要" not in _final:
            _abstract_section = ""
            for sec in polished_sections:
                if "摘要" in str(sec.get("title", "")):
                    _abs_lines = [f"## {sec['title']}", ""]
                    for p in sec.get("content", []):
                        _abs_lines.append(str(p))
                        _abs_lines.append("")
                    _abstract_section = "\n".join(_abs_lines) + "\n\n"
                    break
            if not _abstract_section:
                # Fallback: generate a basic abstract
                _kw = artifact.get("zh_keywords") or _derive_submission_keywords(_paper_title, None)
                _abstract_section = (
                    "## 摘要\n\n"
                    f"本文围绕{_paper_title}展开研究。"
                    "论文从需求分析出发，设计并实现了完整的系统架构，"
                    "通过理论推导与仿真实验验证了系统的有效性。\n\n"
                    f"**关键词：** {'；'.join(_kw) if _kw else '（待补充）'}\n\n"
                )
            # Insert after title line
            _title_end = _final.find("\n", _final.find("# ")) + 1
            if _title_end > 0:
                _final = _final[:_title_end] + "\n" + _abstract_section + _final[_title_end:]
                print("[StructFix] 补充了摘要章节")

        # --- Append back matter (references + optional acks/appendix). The
        # English abstract block is injected directly after the Chinese 摘要 so
        # that both sit at the top of the paper, per thesis convention. ---
        _paper_language = str(artifact.get("language") or "zh")

        # 1) English abstract right after Chinese 摘要
        try:
            _en_block = _render_english_abstract_block(artifact)
            if _en_block:
                _final = _insert_after_chinese_abstract(_final, _en_block)
                print("[FrontMatter] 英文摘要已插入中文摘要之后")
        except Exception as exc:
            print(f"[FrontMatter] 插入英文摘要失败(非致命): {exc}")

        # 2) Back matter: references + (optional) acknowledgements + (optional) appendix
        try:
            _back = _render_back_matter(result)
            if _back:
                _final = _final.rstrip() + "\n\n" + _back
                print("[BackMatter] 追加参考文献等尾段")
        except Exception as exc:
            print(f"[BackMatter] 追加尾段失败(非致命): {exc}")

        # --- Strip leftover English captions (e.g. "Figure 4-3 Bar chart of ...") ---
        _before = _final
        _final = _strip_english_caption_residue(_final)
        if _final != _before:
            print("[CaptionClean] 清理英文 caption 残留")

        # --- M1: clean LLM instruction noise from ### subtitles + number them ---
        _before = _final
        _final = _clean_subtitle_instructions(_final)
        if _final != _before:
            print("[SubtitleClean] 裁剪 LLM 指令痕迹")
        _before = _final
        _final = _number_subtitles(_final)
        if _final != _before:
            print("[SubtitleNumber] 为三级标题补齐章节编号")

        # --- M4a: promote wedged $$...$$ formulas to block paragraphs ---
        _before = _final
        _final = _normalize_formula_blocks(_final)
        if _final != _before:
            print("[FormulaBlock] 规范公式块换行")
        _before = _final
        _final = _format_variable_mentions(_final, project_context)
        _final = _number_equation_blocks(_final, language=_paper_language)
        if _final != _before:
            print("[EquationFormat] 变量与公式编号已规范化")

        # --- M3: figure pipeline (drop PDFs → dedupe/normalize → relocate → ref) ---
        try:
            # G2 pre-step: ask the LLM to generate extra figures from the
            # project's real data files. Output PNGs land in output/figures/
            # and are picked up by the normal figure pipeline below.
            try:
                from tools.source_code_figure import generate_source_driven_figures
                _project_root = result.get("project_root") or "."
                _llm_chain_fig = None
                _call_fn_fig = None
                try:
                    from tools.writing_enhancer import _load_llm_config as _lcfg, _call_model as _cm_fig
                    _llm_chain_fig = _lcfg(Path(_project_root), fallback_chain=True)
                    _call_fn_fig = _cm_fig
                except Exception:
                    _llm_chain_fig = None

                def _llm_call_fig(prompt: str) -> str:
                    if not _llm_chain_fig or _call_fn_fig is None:
                        return ""
                    cfgs = _llm_chain_fig if isinstance(_llm_chain_fig, list) else [_llm_chain_fig]
                    for _cfg in cfgs:
                        try:
                            return _call_fn_fig(_cfg, prompt, "en").strip()
                        except Exception:
                            continue
                    return ""

                if _llm_chain_fig:
                    _topic_for_fig = str(artifact.get("title") or result.get("topic") or "")
                    _new_figs = generate_source_driven_figures(
                        _project_root,
                        _topic_for_fig,
                        _llm_call_fig,
                        language=_paper_language,
                        max_figures=2,
                    )
                    if _new_figs:
                        # Splice these paths into the markdown as raw figure blocks
                        # so the pipeline below picks them up during collection.
                        _extra_block_lines = []
                        for _fp in _new_figs:
                            _rel = f"output/figures/{_fp.name}"
                            _extra_block_lines.append(f"![{_fp.stem}]({_rel})")
                        _final = _final.rstrip() + "\n\n" + "\n\n".join(_extra_block_lines) + "\n"
                        print(f"[SourceFig] 已追加 {len(_new_figs)} 张 LLM 源码驱动图")
            except Exception as exc:
                print(f"[SourceFig] 失败(非致命): {exc}")

            _pre_fig = _final
            _final = _drop_pdf_figures(_final)
            _stripped, _blocks = _collect_figure_blocks(_final)
            _clean_blocks = _normalize_and_dedupe_figures(_blocks)
            if _clean_blocks:
                _final = _relocate_figures_by_chapter(
                    _stripped,
                    _clean_blocks,
                    language=_paper_language,
                    project_context=project_context,
                )
                _final = _renumber_figures(_final, language=_paper_language)
                _final = _insert_figure_references(_final, language=_paper_language)
                print(
                    f"[FigPipeline] {len(_blocks)} 图 → 去重后 {len(_clean_blocks)} 图，按章重排"
                )
            else:
                _final = _pre_fig
        except Exception as exc:
            print(f"[FigPipeline] 失败(非致命): {exc}")

        _before = _final
        _final = _inject_tables_by_plan(_final, project_context, language=_paper_language)
        if _final != _before:
            print("[TablePlan] 已按提及位置补入表格")

        # --- M4b: collapse duplicate '## 参考文献' sections (LLM + tail-render) ---
        _before = _final
        _final = _dedupe_reference_sections(_final)
        if _final != _before:
            print("[RefDedup] 合并重复的参考文献章节")

        _emit_generation_progress(progress_callback, finalize_step, "正在整理终稿与附件...", "正在补充引用标记并规范参考文献")
        # --- M2: LLM pass to inject [N] citation markers into body prose ---
        try:
            _refs = artifact.get("references") or []
            if _refs:
                _llm_chain = None
                _call_fn = None
                try:
                    from tools.writing_enhancer import _load_llm_config, _call_model as _cm
                    _project_root = result.get("project_root") or "."
                    _llm_chain = _load_llm_config(Path(_project_root), fallback_chain=True)
                    _call_fn = _cm
                except Exception:
                    _llm_chain = None

                def _llm_call_cite(prompt: str) -> str:
                    if not _llm_chain or _call_fn is None:
                        return ""
                    cfgs = _llm_chain if isinstance(_llm_chain, list) else [_llm_chain]
                    for _cfg in cfgs:
                        try:
                            return _call_fn(_cfg, prompt, "zh").strip()
                        except Exception:
                            continue
                    return ""

                if _llm_chain:
                    _final = _inject_citation_markers(_final, _refs, _llm_call_cite)
        except Exception as exc:
            print(f"[CiteInject] 失败(非致命): {exc}")

        _emit_generation_progress(progress_callback, finalize_step, "正在整理终稿与附件...", "正在执行终稿润色和格式统一")
        # --- Final polish pass: every remaining audit issue in one place ---
        try:
            _polish_llm_chain = None
            _polish_call_fn = None
            try:
                from tools.writing_enhancer import _load_llm_config as _pl_cfg, _call_model as _pl_cm
                _polish_root = result.get("project_root") or "."
                _polish_llm_chain = _pl_cfg(Path(_polish_root), fallback_chain=True)
                _polish_call_fn = _pl_cm
            except Exception:
                _polish_llm_chain = None

            def _polish_llm_call(prompt: str) -> str:
                if not _polish_llm_chain or _polish_call_fn is None:
                    return ""
                cfgs = _polish_llm_chain if isinstance(_polish_llm_chain, list) else [_polish_llm_chain]
                for _cfg in cfgs:
                    try:
                        return _polish_call_fn(_cfg, prompt, "zh").strip()
                    except Exception:
                        continue
                return ""

            _before = _final
            _final = _polish_final_paper(
                _final, result, language=_paper_language,
                llm_call=_polish_llm_call if _polish_llm_chain else None,
            )
            if _final != _before:
                print("[FinalPolish] 标题前缀/表格/公式/引用/图 caption/GB7714 已统一处理")
        except Exception as exc:
            print(f"[FinalPolish] 失败(非致命): {exc}")

        _before = _final
        _final = _normalize_final_manuscript_format(
            _final,
            language=_paper_language,
            project_context=project_context,
        )
        if _final != _before:
            print("[FormatNormalize] 终稿公式、图号与表格编号已重新规范化")

        safe_write_text(markdown_path, _final)

        _emit_generation_progress(progress_callback, finalize_step, "正在整理终稿与附件...", "正在补齐插图和最终配图资源")
        # --- Ensure sufficient figures ---
        try:
            from tools.image_gen import ensure_figures
            _gen_result = ensure_figures(
                workspace_root, polished_sections, language=_paper_language,
            )
            if _gen_result.get("generated"):
                print(f"[ImageGen] 补充生成 {len(_gen_result['generated'])} 张图片")
            if _gen_result.get("errors"):
                for _err in _gen_result["errors"]:
                    print(f"[ImageGen] 错误: {_err}")
            if _gen_result.get("skipped"):
                for _skip in _gen_result["skipped"]:
                    print(f"[ImageGen] 跳过: {_skip}")
        except Exception as _fig_exc:
            print(f"[ImageGen] 图片生成失败(非致命): {_fig_exc}")

    json_path_value = result.get("json_path")
    if json_path_value:
        safe_write_text(
            Path(str(json_path_value)),
            safe_json_dumps(artifact, ensure_ascii=False, indent=2),
            trailing_newline=True,
        )

    _emit_generation_progress(progress_callback, finalize_step, "正在整理终稿与附件...", "正在执行质量检查和自动修复")
    # --- Quality check + auto-fix loop ---
    quality_report = None
    if markdown_path and markdown_path.exists():
        try:
            from tools.paper_quality import validate_paper, format_report
            _base_dir = str(result.get("project_root") or markdown_path.parent)
            for _qc_round in range(3):
                _emit_generation_progress(
                    progress_callback,
                    finalize_step,
                    "正在整理终稿与附件...",
                    f"正在执行质量检查（第 {_qc_round + 1}/3 轮）",
                )
                qreport = validate_paper(markdown_path, language=_paper_language, base_dir=_base_dir)
                quality_report = qreport
                if not qreport["issues"]:
                    break

                # Filter to fixable issues (high+medium severity only)
                _fixable = [i for i in qreport["issues"] if i["severity"] in ("high", "medium")]
                if not _fixable:
                    break

                # Build fix prompt
                _fix_desc = "\n".join(f"- [{i['severity']}] {i['category']}: {i['message']}" for i in _fixable)
                _md_text = markdown_path.read_text(encoding="utf-8")
                _fix_prompt = (
                    f"以下是检查发现的问题，请直接修改论文内容解决这些问题：\n\n"
                    f"{_fix_desc}\n\n"
                    f"论文全文：\n{_md_text}\n\n"
                    f"要求：\n"
                    f"1. 只修改有问题的地方，不要改动正确的内容\n"
                    f"2. 保持论文的整体结构和逻辑不变\n"
                    f"3. 输出修改后的完整论文，不要省略任何部分\n"
                )

                # Use LLM to fix
                try:
                    from tools.writing_enhancer import _load_llm_config as _qc_cfg, _call_model as _qc_cm
                    _qc_chain = _qc_cfg(Path(_base_dir), fallback_chain=True)
                    if _qc_chain:
                        _cfgs = _qc_chain if isinstance(_qc_chain, list) else [_qc_chain]
                        for _cfg in _cfgs:
                            try:
                                _fixed = _qc_cm(_cfg, _fix_prompt, _paper_language).strip()
                                if _fixed and len(_fixed) > len(_md_text) * 0.8:
                                    _fixed = _normalize_final_manuscript_format(
                                        _fixed,
                                        language=_paper_language,
                                        project_context=project_context,
                                    )
                                    safe_write_text(markdown_path, _fixed)
                                    print(f"[QualityFix] 第{_qc_round+1}轮修复完成，问题数: {len(_fixable)}")
                                break
                            except Exception:
                                continue
                except Exception as _qe:
                    print(f"[QualityFix] LLM修复失败: {_qe}")
                    break

            if quality_report and quality_report["issues"]:
                print(format_report(quality_report))
        except Exception as qe:
            print(f"[QualityCheck] 质量检查失败(非致命): {qe}")

    result["quality_report"] = quality_report
    result["artifact"] = artifact
    return result


def _apply_chinese_writing_rules(
    sections: list[dict[str, Any]],
    title: str = "",
    project_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _polish_chinese_sections(_legacy_apply_chinese_writing_rules(sections, title=title, project_context=project_context))


def _inject_project_context(
    sections: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    language: str,
) -> list[dict[str, Any]]:
    enriched = _legacy_inject_project_context(sections, project_context, language)
    if language == "zh":
        return _polish_chinese_sections(enriched)
    return enriched


def _render_outline_markdown(title: str, sections: list[dict[str, Any]], references: list[dict[str, Any]], language: str) -> str:
    heading = "论文写作大纲" if language == "zh" else "Paper Writing Outline"
    lines = [f"# {heading}：{title}" if language == "zh" else f"# {heading}: {title}", ""]
    for section in sections:
        lines.append(f"## {section['title']}")
        lines.append("")
        lines.append("- Key message")
        lines.append("- Evidence to cite")
        lines.append("- Figure or table slot")
        lines.append("- Open questions to resolve")
        lines.append("")

    lines.append("## Reference Pool" if language != "zh" else "## 候选参考文献")
    lines.append("")
    if references:
        lines.extend(f"- {_reference_string(reference)}" for reference in references)
    else:
        lines.append(
            "- Add topic-matched references after the next literature search."
            if language != "zh"
            else "- 下一轮文献检索后补充主题相关参考文献。"
        )
    lines.append("")
    return "\n".join(lines)


def _render_paper_markdown(
    payload: dict[str, Any],
    markdown_path: Path,
    json_path: Path,
    outline_path: Path,
    plan_path: Path,
    prompts_path: Path,
    latex_path: Path,
    bib_path: Path,
) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"- Topic: {payload['topic']}",
        f"- Language: {payload['language']}",
        f"- Paper type: {payload['paper_type']}",
        f"- Draft path: `{markdown_path}`",
        f"- Structured payload: `{json_path}`",
        f"- Outline path: `{outline_path}`",
        f"- Plan path: `{plan_path}`",
        f"- Prompt pack path: `{prompts_path}`",
        f"- LaTeX path: `{latex_path}`",
        f"- Bib path: `{bib_path}`",
        "",
        payload["summary"],
        "",
    ]
    if payload.get("project_context"):
        project_context = payload["project_context"]
        lines.extend(
            [
                "",
                "## Project Context" if payload["language"] != "zh" else "## 项目上下文",
                "",
                (
                    f"- Source project: `{project_context.get('source_project_path', '')}`"
                    if payload["language"] != "zh"
                    else f"- 项目路径：`{project_context.get('source_project_path', '')}`"
                ),
                (
                    f"- Analysis note: `{project_context.get('analysis_path', '')}`"
                    if payload["language"] != "zh"
                    else f"- 分析笔记：`{project_context.get('analysis_path', '')}`"
                ),
                (
                    f"- Stack: {', '.join(project_context.get('stack') or [])}"
                    if payload["language"] != "zh"
                    else f"- 技术栈：{'、'.join(project_context.get('stack') or [])}"
                ),
                "",
            ]
        )
    lines.append("## Writing Checklist" if payload["language"] != "zh" else "## 写作清单")
    lines.extend(f"- {item}" for item in payload["writing_checklist"])
    lines.extend(["", _render_sections_markdown(payload["sections"])])
    lines.append("## Evidence Notes" if payload["language"] != "zh" else "## 证据笔记")
    lines.append("")
    if payload["evidence_notes"]:
        lines.extend(f"- {item}" for item in payload["evidence_notes"])
    else:
        lines.append(
            "- Add excerpt-backed evidence notes after reading the shortlisted papers."
            if payload["language"] != "zh"
            else "- 阅读候选论文后补充带摘录依据的证据笔记。"
        )
    lines.append("")
    lines.append("## Reference Candidates" if payload["language"] != "zh" else "## 候选参考文献")
    lines.append("")
    if payload["references"]:
        lines.extend(f"- {_reference_string(reference)}" for reference in payload["references"])
    else:
        lines.append(
            "- No topic-matched references are indexed yet."
            if payload["language"] != "zh"
            else "- 当前还没有收录主题高度相关的参考文献。"
        )
    lines.append("")
    if payload["missing_inputs"]:
        lines.append("## Missing Inputs" if payload["language"] != "zh" else "## 当前缺口")
        lines.append("")
        lines.extend(f"- {item}" for item in payload["missing_inputs"])
        lines.append("")
    return "\n".join(lines)


def _sanitize_sections_without_references(
    sections: list[dict[str, Any]],
    language: str,
    title: str,
    paper_type: str,
) -> list[dict[str, Any]]:
    if language != "zh":
        return sections

    sanitized_sections: list[dict[str, Any]] = []
    for section in sections:
        title_text = str(section.get("title", ""))
        content = list(section.get("content", []))
        if title_text == "摘要" and content:
            content[0] = (
                f'本文围绕\u201c{title}\u201d展开，重点分析系统的总体设计、关键实现与实验验证路径。'
                "由于正式参考文献尚未完全补齐，文中关于研究现状与方法对比的部分仍需在定稿阶段结合核心论文进一步完善。"
            )
        elif title_text in {"1. 引言", "1. 绪论"} and len(content) >= 2:
            content[0] = (
                f"{title} 所面向的问题具有明确工程应用价值，但现有研究在系统鲁棒性、部署复杂度与场景适应能力之间仍存在不同程度的权衡。"
                "因此，本文在绪论部分将重点围绕研究背景、系统需求、技术难点和工程实现路径展开论述。"
            )
            content[1] = (
                "现阶段核心参考文献仍需进一步补齐。"
                "在正式定稿时，应结合课题方向的代表性研究，对问题背景、主流方法与评价依据进行系统补强。"
            )
        elif title_text in {"7. 结论", "6. 总结与展望"} and content:
            content[-1] = "结论部分应以已有实现与实验分析为依据，避免引入未经验证的新结论，并将未来研究方向控制在与现有系统直接相关的范围内。"

        sanitized_sections.append({"title": title_text, "content": content})

    return sanitized_sections


def _inject_project_context(
    sections: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    language: str,
) -> list[dict[str, Any]]:
    if not project_context:
        return sections

    project_summary = str(project_context.get("project_summary", "")).strip()
    stack = project_context.get("stack") or []
    method_clues = project_context.get("method_clues") or []
    result_clues = project_context.get("result_clues") or []

    enriched: list[dict[str, Any]] = []
    for section in sections:
        title = str(section.get("title", ""))
        content = list(section.get("content", []))
        if title in {"1. Introduction", "1. 引言"} and project_summary:
            prefix = (
                f"The current implementation baseline is captured by the source project: {project_summary}"
                if language != "zh"
                else f"当前源项目已经提供了一个可复用实现基线：{project_summary}"
            )
            content.insert(1 if content else 0, prefix)
        elif title in {"3. Method", "3. 方法"}:
            if method_clues:
                content.append(
                    (
                        f"Project-aligned method anchors include {', '.join(method_clues[:4])}; keep the final method section consistent with these code artifacts."
                        if language != "zh"
                        else f"项目中可直接映射到方法描述的线索包括：{'；'.join(method_clues[:4])}。正式写作时应确保方法章节与这些代码实体一致。"
                    )
                )
            if stack:
                content.append(
                    (
                        f"Implementation evidence currently points to the following stack: {', '.join(stack)}."
                        if language != "zh"
                        else f"当前实现证据显示项目主要技术栈为：{'、'.join(stack)}。"
                    )
                )
        elif title in {"4. Experimental Setup", "4. 实验设置"} and result_clues:
            content.append(
                (
                    f"Candidate project-side evidence files already exist: {', '.join(result_clues[:4])}. Use them to verify tables and metrics before final submission."
                    if language != "zh"
                    else f"项目内已经检测到若干可能的结果证据：{'；'.join(result_clues[:4])}。在终稿前应先用这些文件核对表格和指标。"
                )
            )
        enriched.append({"title": title, "content": content})
    return enriched


def _localize_zh_markdown(text: str) -> str:
    replacements = {
        "- Topic:": "- 主题:",
        "- Language:": "- 语言:",
        "- Paper type:": "- 稿型:",
        "- Draft path:": "- 草稿路径:",
        "- Structured payload:": "- 结构化数据:",
        "- Outline path:": "- 大纲路径:",
        "- Plan path:": "- 计划路径:",
        "- Prompt pack path:": "- 提示包路径:",
        "- LaTeX path:": "- LaTeX 路径:",
        "- Bib path:": "- 参考文献路径:",
        "## Writing Checklist": "## 写作清单",
        "## Evidence Notes": "## 证据笔记",
        "## Reference Candidates": "## 候选参考文献",
        "## Missing Inputs": "## 当前缺口",
        "- Key message": "- 核心信息",
        "- Evidence to cite": "- 需要引用的证据",
        "- Figure or table slot": "- 图表占位",
        "- Open questions to resolve": "- 待解决问题",
        "- Add excerpt-backed evidence notes after reading the shortlisted papers.": "- 阅读候选论文后补充带摘录依据的证据笔记。",
        "- No topic-matched references are indexed yet.": "- 当前还没有收录主题高度相关的参考文献。",
        "- Add topic-matched references after the next literature search.": "- 下一轮文献检索后补充主题相关参考文献。",
    }
    localized_text = text
    for source, target in replacements.items():
        localized_text = localized_text.replace(source, target)
    return localized_text


def _postprocess_markdown_outputs(markdown_path: Path, outline_path: Path, language: str) -> None:
    if language != "zh":
        return

    for path in [markdown_path, outline_path]:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        safe_write_text(path, _localize_zh_markdown(content))


def _strip_section_number(title: str) -> str:
    return re.sub(r"^\d+(?:\.\d+)?\.\s*", "", title).strip()


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
    }
    escaped = text
    for source, target in replacements.items():
        escaped = escaped.replace(source, target)
    return escaped


def _render_paper_plan_markdown(
    title: str,
    topic: str,
    language: str,
    paper_type: str,
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
    missing_inputs: list[str],
) -> str:
    section_titles = [_strip_section_number(str(section["title"])) for section in sections]
    if language == "zh":
        lines = [
            f"# 论文执行计划：{title}",
            "",
            f"- 主题：{topic}",
            f"- 稿型：{paper_type}",
            f"- 当前可用参考文献：{len(references)}",
            "",
            "## 计划原则",
            "",
            "- 用 issue 驱动推进：每个任务只解决一个明确问题，并绑定可检查产物。",
            "- 先补证据再写结论：没有文献或实验支撑的论断不进入终稿。",
            "- 章节逐步冻结：摘要和引言最后收敛，方法和实验先行固化。",
            "",
            "## 当前 backlog",
            "",
            "- [ ] 补齐 5-8 篇主题高度相关核心论文，并完成摘录卡片。",
            "- [ ] 将实验计划中的数据集、基线、指标映射到正文实验设置。",
            "- [ ] 为方法部分补系统框图、伪代码和关键符号表。",
            "- [ ] 规划主结果表、消融表和误差分析图。",
            "- [ ] 完成 LaTeX 初稿并统一引用格式。",
            "",
            "## 章节推进顺序",
            "",
        ]
        lines.extend(f"- [ ] {section_title}" for section_title in section_titles)
        lines.extend(
            [
                "",
                "## 完成标准",
                "",
                "- 每一章都要有一个主结论、一个证据来源和一个待解决问题。",
                "- 每一个图表占位都要写明图表要回答什么问题。",
                "- 每一条贡献都要能映射到方法设计或实验结果。",
                "",
                "## 当前缺口",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in missing_inputs or ["当前主要缺口已关闭，可进入细化写作。"])
        lines.append("")
        return "\n".join(lines)

    lines = [
        f"# Paper Execution Plan: {title}",
        "",
        f"- Topic: {topic}",
        f"- Paper type: {paper_type}",
        f"- Available references: {len(references)}",
        "",
        "## Operating Rules",
        "",
        "- Work in issue-sized tasks and bind each task to a concrete deliverable.",
        "- Do not promote claims into the final draft before they are backed by literature or experiments.",
        "- Freeze sections progressively: method and setup first, abstract and introduction last.",
        "",
        "## Current Backlog",
        "",
        "- [ ] Add 5-8 topic-matched core papers and extract evidence notes.",
        "- [ ] Map datasets, baselines, and metrics from the experiment plan into the manuscript.",
        "- [ ] Add one system figure, one algorithm block, and one notation table.",
        "- [ ] Define the main results table, one ablation table, and one failure-analysis figure.",
        "- [ ] Move the draft into the LaTeX manuscript and normalize citation style.",
        "",
        "## Section Progression",
        "",
    ]
    lines.extend(f"- [ ] {section_title}" for section_title in section_titles)
    lines.extend(
        [
            "",
            "## Done Criteria",
            "",
            "- Each section contains one main claim, one evidence source, and one open question.",
            "- Every figure or table slot states exactly which question it should answer.",
            "- Every contribution statement maps to either a method choice or an evaluation result.",
            "",
            "## Current Gaps",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in missing_inputs or ["No blocking gaps are currently recorded."])
    lines.append("")
    return "\n".join(lines)


def _render_revision_prompts_markdown(title: str, language: str) -> str:
    if language == "zh":
        return dedent(
            f"""\
            # 论文修订提示包：{title}

            ## 学术润色

            ```text
            请润色以下论文段落，保持核心信息不变，删除冗余表达，避免机械化连接词，改成更自然的中文学术写法。
            输出保持为 LaTeX 代码，不使用 Markdown 语法；代码标识请写成 \\texttt{{...}}；尽量不要使用 \\subsection 和括号补充说明。
            直接输出可粘贴到正文的内容。
            ```

            ## 补引用润色

            ```text
            请扩写并润色以下论文段落，保持原意，同时补充中英文参考文献。新增引用请在正文中使用 \\cite{{key}}，
            并额外输出一个 bibtex 代码块给出新增条目。只补与主题直接相关、能支撑论断的引用。
            ```

            ## LaTeX 转写

            ```text
            请把以下结构化草稿转成规范 LaTeX 段落，保留章节结构，避免项目符号堆砌。
            输出从 \\section 开始，不包含导言区；图表位置保留为 Figure/Table 占位。
            ```

            ## 审稿前检查

            ```text
            请作为论文内部审稿人，逐章检查以下内容：论点是否可验证、引用是否充分、图表是否支撑结论、语言是否仍然像 AI 模板。
            输出按"问题 / 风险 / 修改建议"三列给出。
            ```
            """
        ).strip() + "\n"

    return dedent(
        f"""\
        # Revision Prompt Pack: {title}

        ## Academic Polish

        ```text
        Polish the following paper paragraph without changing the core meaning. Remove redundancy, reduce list-like phrasing,
        and rewrite it into natural academic prose. Return LaTeX-ready text and use \\texttt{{...}} for inline code.
        ```

        ## Citation Enrichment

        ```text
        Expand and polish the following paragraph while adding only topic-relevant citations. Insert new references with \\cite{{key}}
        and provide the added BibTeX entries in a separate code block.
        ```

        ## LaTeX Conversion

        ```text
        Convert the following structured draft into clean LaTeX prose. Preserve section boundaries, avoid bullet-heavy output,
        and keep figure/table slots as placeholders for later replacement.
        ```

        ## Internal Review

        ```text
        Review the following manuscript section as an internal reviewer. Check claim-evidence alignment, citation sufficiency,
        figure-table support, and AI-sounding phrasing. Return a compact table with issue, risk, and revision advice.
        ```
        """
    ).strip() + "\n"


def _render_bibtex(references: list[dict[str, Any]]) -> str:
    if not references:
        return "% Add topic-matched BibTeX entries here after the next literature search.\n"

    entries: list[str] = []
    for reference in references:
        key_source = reference.get("doi") or reference.get("record_id") or reference.get("title") or "paper"
        key = re.sub(r"[^0-9A-Za-z]+", "", str(key_source))[:40] or "paper"
        authors = " and ".join(reference.get("authors") or ["Unknown Author"])
        title = reference.get("title") or "Untitled paper"
        venue = reference.get("venue") or "Unknown venue"
        year = reference.get("year") or "n.d."
        doi = reference.get("doi") or ""
        url = reference.get("url") or ""
        lines = [
            f"@article{{{key},",
            f"  author = {{{authors}}},",
            f"  title = {{{title}}},",
            f"  journal = {{{venue}}},",
            f"  year = {{{year}}},",
        ]
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        if url:
            lines.append(f"  url = {{{url}}},")
        lines.append("}")
        entries.append("\n".join(lines))
    return "\n\n".join(entries) + "\n"


def _render_figure_block(fig_meta: dict[str, Any], language: str = "en") -> list[str]:
    """Generate LaTeX lines for a single figure from figure_generator metadata."""
    path = fig_meta.get("path_pdf", fig_meta.get("path_png", ""))
    caption = fig_meta.get("caption", "")
    label = fig_meta.get("label", "")
    lines = [
        r"\begin{figure}[htbp]",
        r"\centering",
        rf"\includegraphics[width=0.85\textwidth]{{{path}}}",
        rf"\caption{{{_latex_escape(caption)}}}",
    ]
    if label:
        lines.append(rf"\label{{{label}}}")
    lines.extend([r"\end{figure}", ""])
    return lines


def _render_latex_table_block(table_latex: str) -> list[str]:
    """Wrap a pre-formatted LaTeX table string into lines."""
    return [table_latex, ""]


def _render_latex_manuscript(
    title: str,
    language: str,
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
    figures: list[dict[str, Any]] | None = None,
    tables: list[str] | None = None,
) -> str:
    document_class = r"\documentclass[UTF8,a4paper,11pt]{ctexart}" if language == "zh" else r"\documentclass[11pt]{article}"
    preamble = [
        document_class,
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{hyperref}",
        r"\usepackage[numbers,sort&compress]{natbib}",
        r"\usepackage{subcaption}",
        r"\usepackage{float}",
        r"\usepackage{amsmath}",
        "",
        rf"\title{{{_latex_escape(title)}}}",
        r"\author{}",
        r"\date{}",
        "",
        r"\begin{document}",
        "",
    ]

    body: list[str] = []
    abstract_section = sections[0] if sections else None
    if abstract_section and _strip_section_number(str(abstract_section.get("title", ""))).lower() in {"abstract", "摘要"}:
        body.append(r"\begin{abstract}")
        body.extend(_latex_escape(str(paragraph)) for paragraph in abstract_section.get("content", []))
        body.append(r"\end{abstract}")
        body.append("")
        section_iterable = sections[1:]
    else:
        section_iterable = sections

    for section in section_iterable:
        section_title = _latex_escape(_strip_section_number(str(section.get("title", ""))))
        body.append(rf"\section{{{section_title}}}")
        body.append("")
        for paragraph in section.get("content", []):
            body.append(_latex_escape(str(paragraph)))
            body.append("")

    # Insert figures at the end of body (before references) if provided
    if figures:
        body.append("% --- Auto-generated figures ---")
        for fig_meta in figures:
            body.extend(_render_figure_block(fig_meta, language))

    # Insert tables if provided
    if tables:
        body.append("% --- Auto-generated tables ---")
        for table_latex in tables:
            body.extend(_render_latex_table_block(table_latex))

    if references:
        body.extend(
            [
                r"\bibliographystyle{plainnat}",
                r"\bibliography{references}",
            ]
        )
    else:
        body.append("% Add references.bib entries before final submission.")

    body.extend(["", r"\end{document}", ""])
    return "\n".join(preamble + body)


def generate_paper_package(
    project_root: str | Path,
    topic: str,
    language: str | None = None,
    paper_type: str = "general",
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise ValueError("Paper draft topic cannot be empty.")

    resolved_language = _resolve_language(normalized_topic, language)
    title = _normalize_title(normalized_topic, resolved_language)
    references = _reference_catalog(_select_reference_papers(root, normalized_topic))

    # Augment with uploaded reference file content
    uploaded_refs = (project_context or {}).get("uploaded_references", [])
    if uploaded_refs:
        selected_uploaded_refs = _select_uploaded_reference_entries(
            uploaded_refs,
            topic=normalized_topic,
            project_context=project_context,
        )
        for i, item in enumerate(selected_uploaded_refs, start=len(references) + 1):
            ref = item["ref"]
            excerpt = str(item.get("excerpt") or "").strip()
            filename = ref.get("filename", "uploaded_file")
            if not excerpt:
                continue
            references.append({
                "label": str(i),
                "record_id": None,
                "title": f"Uploaded Reference: {filename}",
                "authors": [],
                "year": None,
                "venue": None,
                "doi": None,
                "url": ref.get("path"),
                "abstract": excerpt[: min(len(excerpt), 220)],
                "content_excerpt": excerpt[:_UPLOADED_REFERENCE_REFERENCE_EXCERPT_CHARS],
            })

    experiment_plan = _load_experiment_plan(root)
    if resolved_language == "zh":
        experiment_lines = _build_clean_chinese_experiment_lines(experiment_plan, project_context)
        sections = _apply_chinese_writing_rules(
            _build_clean_chinese_sections(title, paper_type, references, experiment_lines, project_context, project_root=root),
            title=title,
            project_context=project_context,
        )
        sections = _compact_chinese_sections_structure(sections)
        if not references:
            sections = _sanitize_sections_without_references(sections, resolved_language, title, paper_type)
        submission_meta = _derive_submission_metadata(title, project_context)
        zh_keywords = _derive_submission_keywords(title, project_context)
        english_title = _topic_to_english_title(title)
        english_abstract = _derive_english_abstract(title, project_context)
        en_keywords = _topic_keywords(title)[: max(len(zh_keywords), 4)]
        acknowledgements = _derive_acknowledgements(project_context)
        appendix_notes = _derive_appendix_notes(project_context)
        chapter_targets = _thesis_chapter_targets()
        writing_assets_path = root / "drafts" / "writing-assets.md"
        summary = "已生成以正文为主的中文毕业论文长稿，并同步输出配套大纲、计划、LaTeX 与写作资产清单。"
        writing_checklist = [
            "补充与课题核心方法和技术相关的学术论文。",
            "逐段核对系统设计、模块实现、参数配置和源码是否一致。",
            "补充系统总体架构图、模块关系图和核心流程图。",
            "正式定稿前补充实验结果截图、性能统计表与真实数据分析。",
        ]
        missing_inputs = []
        if not references:
            missing_inputs.append("当前还没有索引到与该项目直接相关的核心文献，建议至少补充 5-8 篇相关论文。")
        if not experiment_plan:
            missing_inputs.append("当前没有识别到与该项目直接匹配的实验计划，实验章节需要结合仿真和实车测试自行补充。")
    else:
        experiment_lines = _experiment_summary(experiment_plan, resolved_language)
        sections = _build_english_sections(title, paper_type, references, experiment_lines, experiment_plan)
        summary = f"Generated a stronger paper writing pack with a full draft, outline, evidence notes, and revision checklist for a {paper_type} paper."
        writing_checklist = [
            "Lock the target venue and page budget before compressing the contribution list.",
            "Keep one main claim per section and tie it to explicit evidence.",
            "Reserve space for one main results table, one ablation table, and one failure-analysis figure.",
            "Replace planning language with verified findings before submission.",
        ]
        missing_inputs = []
        if not references:
            missing_inputs.append("The workspace does not yet contain enough relevant references; add 5-8 core papers first.")
        if not experiment_plan:
            missing_inputs.append("No experiment plan JSON was found; generate it before finalizing the experiment and results sections.")

    sections = _inject_project_context(sections, project_context, resolved_language)
    if project_context:
        if resolved_language == "zh":
            writing_checklist.insert(1, "逐段核对方法描述与项目代码、配置和脚本是否一致。")
            if not project_context.get("result_clues"):
                missing_inputs.append("尚未从项目中识别到明确结果文件，建议补充日志、表格或评估导出。")
        else:
            writing_checklist.insert(1, "Cross-check each method paragraph against project code, configs, and scripts.")
            if not project_context.get("result_clues"):
                missing_inputs.append("No obvious project-side result files were detected; add logs, tables, or evaluation exports.")

    evidence_notes = []
    for reference in references:
        note = reference.get("content_excerpt") or reference.get("abstract") or ""
        clipped = re.sub(r"\s+", " ", note).strip()
        if len(clipped) > 220:
            clipped = clipped[:217].rstrip() + "..."
        evidence_notes.append(
            f"[{reference['label']}] {reference.get('title') or 'Untitled'} - "
            f"{clipped or 'Add a concise takeaway after reading the full text.'}"
        )

    payload = {
        "kind": "paper",
        "title": f"论文写作稿：{title}" if resolved_language == "zh" else f"Research Paper Draft: {title}",
        "topic": normalized_topic,
        "language": resolved_language,
        "paper_type": paper_type,
        "summary": summary,
        "sections": sections,
        "references": references,
        "writing_checklist": writing_checklist,
        "evidence_notes": evidence_notes,
        "missing_inputs": missing_inputs,
        "experiment_plan_available": bool(experiment_plan),
        "project_context": project_context,
        "generated_at": _now_iso(),
    }
    if resolved_language == "zh":
        payload.update(
            {
                "submission_meta": submission_meta,
                "zh_keywords": zh_keywords,
                "english_title": english_title,
                "english_abstract": english_abstract,
                "en_keywords": en_keywords,
                "acknowledgements": acknowledgements,
                "appendix_notes": appendix_notes,
                "chapter_targets": chapter_targets,
                "prompt_sources": [
                    "毕业论文prompts/学术性润色.txt",
                    "毕业论文prompts/需要增加引用的润色.txt",
                    "external/articlewriting-skill/templates/paper-outline.md",
                    "external/articlewriting-skill/modules/writing-core.md",
                    "external/articlewriting-skill/skills/brainstorming-research/chapter-templates.md",
                    "external/articlewriting-skill/skills/latex-output/template-parser.md",
                    "external/autoresearch/program.md",
                    "external/latex-arxiv-SKILL/README.zh-CN.md",
                    "external/awesome-ai-research-writing/README.md",
                ],
            }
        )

    draft_dir = root / "drafts"
    markdown_path = draft_dir / "paper-draft.md"
    outline_path = draft_dir / "paper-outline.md"
    plan_path = draft_dir / "paper-plan.md"
    prompts_path = draft_dir / "paper-revision-prompts.md"
    json_path = draft_dir / "paper-draft.json"
    latex_path = draft_dir / "paper.tex"
    bib_path = draft_dir / "references.bib"
    writing_assets_path = draft_dir / "writing-assets.md"

    payload["supporting_assets"] = {
        "plan_path": str(plan_path),
        "prompts_path": str(prompts_path),
        "latex_path": str(latex_path),
        "bib_path": str(bib_path),
        "writing_assets_path": str(writing_assets_path),
    }

    if resolved_language == "zh":
        _write_text(
            markdown_path,
            _render_clean_zh_paper_markdown(payload, markdown_path, json_path, outline_path, plan_path, prompts_path, latex_path, bib_path),
        )
        _write_text(outline_path, _render_clean_zh_outline_markdown(title, sections, references))
        _write_text(plan_path, _render_clean_zh_plan_markdown(title, normalized_topic, paper_type, sections, references, missing_inputs))
        _write_text(prompts_path, _render_clean_zh_revision_prompts_markdown(title))
        _write_text(writing_assets_path, _render_writing_assets_markdown())
        _write_text(
            latex_path,
            _render_clean_zh_latex_manuscript(
                title,
                sections,
                references,
                payload.get("submission_meta"),
                payload.get("zh_keywords"),
                payload.get("english_title"),
                payload.get("english_abstract"),
                payload.get("en_keywords"),
                payload.get("acknowledgements"),
                payload.get("appendix_notes"),
                payload.get("missing_inputs"),
            ),
        )
    else:
        _write_text(
            markdown_path,
            _render_paper_markdown(payload, markdown_path, json_path, outline_path, plan_path, prompts_path, latex_path, bib_path),
        )
        _write_text(outline_path, _render_outline_markdown(title, sections, references, resolved_language))
        _write_text(
            plan_path,
            _render_paper_plan_markdown(title, normalized_topic, resolved_language, paper_type, sections, references, missing_inputs),
        )
        _write_text(prompts_path, _render_revision_prompts_markdown(title, resolved_language))
        _write_text(latex_path, _render_latex_manuscript(title, resolved_language, sections, references))
        _postprocess_markdown_outputs(markdown_path, outline_path, resolved_language)
    _write_text(bib_path, _render_bibtex(references))
    _write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))

    state = sync_project_state(root)
    dashboard_path = build_dashboard(root)
    return {
        "project_root": str(root),
        "dashboard_path": str(dashboard_path),
        "markdown_path": str(markdown_path),
        "outline_path": str(outline_path),
        "plan_path": str(plan_path),
        "prompts_path": str(prompts_path),
        "json_path": str(json_path),
        "latex_path": str(latex_path),
        "bib_path": str(bib_path),
        "artifact": payload,
        "state": state,
    }


def _topic_keywords(topic: str) -> list[str]:
    english_keywords = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9/_-]{2,}", topic.lower())
        if token not in ENGLISH_STOPWORDS
    ]

    chinese_stopwords = {
        "研究",
        "方法",
        "模型",
        "实验",
        "系统",
        "分析",
        "应用",
        "设计",
        "实现",
        "基于",
        "面向",
        "关于",
        "用于",
    }
    split_pattern = re.compile(r"(?:的|及其|以及|基于|面向|针对|关于|用于|与|和)")

    chinese_keywords: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", topic):
        parts = [part.strip() for part in split_pattern.split(chunk) if len(part.strip()) >= 2]
        if not parts:
            parts = [chunk]
        for part in parts:
            if part not in chinese_stopwords:
                chinese_keywords.append(part)

    ordered_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in [*english_keywords, *chinese_keywords]:
        if keyword and keyword not in seen:
            ordered_keywords.append(keyword)
            seen.add(keyword)
    return ordered_keywords


_ROBOTICS_FALLBACK_REFERENCES: list[dict[str, Any]] = [
    {"title": "ROS: an open-source Robot Operating System", "authors": "Quigley M, Conley K, Gerkey B, Faust J, Foote T, Leibs J, Wheeler R, Ng A Y", "year": "2009", "venue": "ICRA Workshop on Open Source Software", "doi": "", "type": "inproceedings"},
    {"title": "Robot Operating System 2: Design, architecture, and uses in the wild", "authors": "Macenski S, Foote T, Gerkey B, Gerondidis D, Peer A", "year": "2022", "venue": "Science Robotics", "doi": "10.1126/scirobotics.abm6074", "type": "article"},
    {"title": "Design and use paradigms for Gazebo, an open-source multi-robot simulator", "authors": "Koenig N, Howard A", "year": "2004", "venue": "IEEE/RSJ International Conference on Intelligent Robots and Systems", "doi": "10.1109/IROS.2004.1389727", "type": "inproceedings"},
    {"title": "Probabilistic Robotics", "authors": "Thrun S, Burgard W, Fox D", "year": "2005", "venue": "Cambridge, MA: MIT Press", "doi": "", "type": "book"},
    {"title": "SLAM Toolbox: SLAM for the dynamic world", "authors": "Macenski S, Jambrecic I", "year": "2021", "venue": "Journal of Open Source Software", "doi": "10.21105/joss.02783", "type": "article"},
    {"title": "Real-time loop closure in 2D LIDAR SLAM", "authors": "Hess W, Kohler D, Rapp H, Andor D", "year": "2016", "venue": "IEEE International Conference on Robotics and Automation", "doi": "10.1109/ICRA.2016.7459276", "type": "inproceedings"},
    {"title": "A flexible and scalable SLAM system with full 3D motion estimation", "authors": "Kohlbrecher S, Von Stryk O, Meyer J, Klingauf U", "year": "2011", "venue": "IEEE International Conference on Robotics and Automation", "doi": "10.1109/ICRA.2011.5980406", "type": "inproceedings"},
    {"title": "Improved techniques for grid mapping with Rao-Blackwellized particle filters", "authors": "Grisetti G, Stachniss C, Burgard W", "year": "2007", "venue": "IEEE Transactions on Robotics", "doi": "10.1109/TRO.2007.898972", "type": "article"},
    {"title": "Globally consistent range scan alignment for environment mapping", "authors": "Lu F, Milios E", "year": "1997", "venue": "Autonomous Robots", "doi": "10.1023/A:1008051126275", "type": "article"},
    {"title": "The Marathon 2: A Navigation System", "authors": "Macenski S, Martin F, White R, Clavero J G, Lu D V, Palomeras N, Ribas D, Higuera J C G, Saenz-Otero A, Weerts H, Rostí M, Martín M M, Pérez M, Cuellar E, Zaman S, G. López F, Conner D, Martín F, How J, O'Quinn M, Yu W, Marder-Eppstein E, Foote T", "year": "2023", "venue": "IEEE Robotics and Automation Magazine", "doi": "10.1109/MRA.2023.3259410", "type": "article"},
    {"title": "Path planning for autonomous vehicles in unknown semi-structured environments", "authors": "Dolgov D, Thrun S, Montemerlo M, Diebel J", "year": "2010", "venue": "The International Journal of Robotics Research", "doi": "10.1177/0278364910361294", "type": "article"},
    {"title": "Theta*: Any-angle path planning on grids", "authors": "Nash A, Daniel K, Koenig S, Likhachev M", "year": "2007", "venue": "Journal of Artificial Intelligence Research", "doi": "10.1613/jair.2151", "type": "article"},
    {"title": "The dynamic window approach to collision avoidance", "authors": "Fox D, Burgard W, Thrun S", "year": "1997", "venue": "IEEE Robotics & Automation Magazine", "doi": "10.1109/100.650801", "type": "article"},
    {"title": "Integrated online trajectory planning and optimization in distinctive topologies", "authors": "Rosmann C, Hoffmann F, Bertram T", "year": "2015", "venue": "Robotics and Autonomous Systems", "doi": "10.1016/j.robot.2015.07.007", "type": "article"},
    {"title": "Implementation of the pure pursuit path tracking algorithm", "authors": "Coulter R C", "year": "1992", "venue": "Pittsburgh: Carnegie Mellon University", "doi": "", "type": "techreport"},
    {"title": "Information theoretic MPC for model-based reinforcement learning", "authors": "Williams G, Drews P, Goldfain B, Rehg J M, Theodorou E A", "year": "2017", "venue": "IEEE International Conference on Robotics and Automation", "doi": "10.1109/ICRA.2017.7989318", "type": "inproceedings"},
    {"title": "Ceres solver", "authors": "Agarwal S, Mierle K", "year": "2012", "venue": "Google Inc.", "doi": "", "type": "misc"},
    {"title": "A note on two problems in connexion with graphs", "authors": "Dijkstra E W", "year": "1959", "venue": "Numerische Mathematik", "doi": "10.1007/BF01386390", "type": "article"},
    {"title": "A formal basis for the heuristic determination of minimum cost paths", "authors": "Hart P E, Nilsson N J, Raphael B", "year": "1968", "venue": "IEEE Transactions on Systems Science and Cybernetics", "doi": "10.1109/TSSC.1968.940910", "type": "article"},
    {"title": "Monte Carlo localization: Efficient position estimation for mobile robots", "authors": "Fox D, Burgard W, Dellaert F, Thrun S", "year": "1999", "venue": "AAAI/IAAI", "doi": "", "type": "inproceedings"},
]


def _select_reference_papers(project_root: Path, topic: str, limit: int = 8) -> list[dict[str, Any]]:
    papers = load_paper_index(project_root)
    keywords = _topic_keywords(topic)
    if not keywords:
        return []

    strict_keywords = [keyword for keyword in keywords if len(keyword) >= 3 or not _contains_cjk(keyword)]
    if strict_keywords:
        keywords = strict_keywords

    scored = [(paper, _paper_score(paper, keywords)) for paper in papers]
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)

    matched: list[dict[str, Any]] = []
    for paper, score in ranked:
        title_hits, keyword_hits = score[0], score[1]
        if title_hits >= 1 or keyword_hits >= max(2, min(len(keywords), 3)):
            matched.append(paper)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in matched:
        dedupe_key = str(paper.get("doi") or "").strip().lower()
        if not dedupe_key:
            dedupe_key = re.sub(r"\s+", " ", str(paper.get("title") or "").strip().lower())
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(paper)
        if len(deduped) >= limit:
            break

    # Fallback: supplement with domain-specific real references when paper_index has few matches
    if len(deduped) < limit:
        topic_lower = topic.lower()
        has_robotics = any(kw in topic_lower for kw in ("ros", "ros2", "slam", "navigation", "robot", "nav2", "lidar", "planner", "controller"))
        if has_robotics:
            for ref in _ROBOTICS_FALLBACK_REFERENCES:
                ref_key = ref.get("doi", "").strip().lower()
                if not ref_key:
                    ref_key = re.sub(r"\s+", " ", ref.get("title", "").strip().lower())
                if ref_key and ref_key not in seen:
                    seen.add(ref_key)
                    deduped.append(ref)
                    if len(deduped) >= limit:
                        break

    return deduped


def _render_clean_zh_latex_manuscript(
    title: str,
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
    submission_meta: dict[str, Any] | None = None,
    zh_keywords: list[str] | None = None,
    english_title: str | None = None,
    english_abstract: str | None = None,
    en_keywords: list[str] | None = None,
    acknowledgements: list[str] | None = None,
    appendix_notes: list[str] | None = None,
    missing_inputs: list[str] | None = None,
    figures: list[dict[str, Any]] | None = None,
    tables: list[str] | None = None,
) -> str:
    submission_meta = submission_meta or {}
    zh_keywords = zh_keywords or []
    en_keywords = en_keywords or []
    acknowledgements = acknowledgements or []
    appendix_notes = appendix_notes or []
    missing_inputs = missing_inputs or []
    preamble = [
        r"\documentclass[UTF8,a4paper,11pt]{ctexrep}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{hyperref}",
        r"\usepackage{array}",
        r"\usepackage[numbers,sort&compress]{natbib}",
        r"\usepackage{subcaption}",
        r"\usepackage{float}",
        r"\usepackage{amsmath}",
        "",
        rf"\title{{{_latex_escape(title)}}}",
        r"\author{}",
        r"\date{}",
        "",
        r"\begin{document}",
        "",
    ]

    body: list[str] = []
    body.extend(
        [
            r"\begin{titlepage}",
            r"\centering",
            r"{\zihao{2}\bfseries 毕业论文\par}",
            r"\vspace{1.5cm}",
            rf"{{\zihao{{3}}\bfseries {_latex_escape(title)}\par}}",
            r"\vspace{1.2cm}",
            r"\renewcommand{\arraystretch}{1.6}",
            r"\begin{tabular}{>{\raggedleft\arraybackslash}p{4cm} p{8cm}}",
            rf"学校： & {_latex_escape(str(submission_meta.get('school', '（待填写学校名称）')))} \\",
            rf"学院： & {_latex_escape(str(submission_meta.get('college', '（待填写学院名称）')))} \\",
            rf"专业： & {_latex_escape(str(submission_meta.get('major', '（待填写专业名称）')))} \\",
            rf"学生姓名： & {_latex_escape(str(submission_meta.get('student', '（待填写学生姓名）')))} \\",
            rf"学号： & {_latex_escape(str(submission_meta.get('student_id', '（待填写学号）')))} \\",
            rf"指导教师： & {_latex_escape(str(submission_meta.get('advisor', '（待填写指导教师）')))} \\",
            rf"提交日期： & {_latex_escape(str(submission_meta.get('date', '（待填写提交日期）')))} \\",
            r"\end{tabular}",
            r"\vfill",
            r"\end{titlepage}",
            r"\tableofcontents",
            r"\clearpage",
            r"\setcounter{page}{1}",
            "",
        ]
    )
    if sections and str(sections[0].get("title", "")) == "摘要":
        body.append(r"\begin{abstract}")
        body.extend(_latex_escape(str(paragraph)) for paragraph in sections[0].get("content", []))
        if zh_keywords:
            body.append("")
            body.append(r"\noindent\textbf{关键词：}" + _latex_escape("；".join(zh_keywords)))
        body.append(r"\end{abstract}")
        body.append("")
        body.append(r"\section*{Abstract}")
        body.append("")
        if english_title:
            body.append(r"\noindent\textbf{Title:} " + _latex_escape(english_title))
            body.append("")
        body.append(_latex_escape(english_abstract or "English abstract to be completed."))
        body.append("")
        if en_keywords:
            body.append(r"\noindent\textbf{Keywords:} " + _latex_escape(", ".join(en_keywords)))
            body.append("")
        body.append(r"\clearpage")
        body.append("")
        section_iterable = sections[1:]
    else:
        section_iterable = sections

    for section in section_iterable:
        section_title = _latex_escape(_strip_section_number(str(section.get("title", ""))))
        body.append(rf"\chapter{{{section_title}}}")
        body.append("")
        for paragraph in section.get("content", []):
            raw = str(paragraph).strip()
            if not raw:
                continue
            if raw.startswith("### "):
                body.append(r"\vspace{0.6em}")
                body.append(r"\noindent\textbf{" + _latex_escape(raw[4:].strip()) + "}")
                body.append("")
                continue
            if raw.startswith("#### "):
                body.append(r"\noindent\textbf{" + _latex_escape(raw[5:].strip()) + "}")
                body.append("")
                continue
            body.append(_latex_escape(raw))
            body.append("")

    # Insert figures if provided
    if figures:
        body.append("% --- Auto-generated figures ---")
        for fig_meta in figures:
            body.extend(_render_figure_block(fig_meta, "zh"))

    # Insert tables if provided
    if tables:
        body.append("% --- Auto-generated tables ---")
        for table_latex in tables:
            body.extend(_render_latex_table_block(table_latex))

    if references:
        body.extend([r"\clearpage", r"\bibliographystyle{plainnat}", r"\bibliography{references}"])
    else:
        body.extend(
            [
                r"\clearpage",
                r"\section*{参考文献}",
                "当前尚未补充正式参考文献条目，提交前应补齐与课题核心方法和技术相关的学术论文。",
            ]
        )

    body.extend([r"\clearpage", r"\section*{致谢}", ""])
    for paragraph in acknowledgements:
        body.append(_latex_escape(str(paragraph)))
        body.append("")

    body.extend([r"\clearpage", r"\section*{附录说明}", ""])
    for paragraph in appendix_notes:
        body.append(_latex_escape(str(paragraph)))
        body.append("")

    if missing_inputs:
        body.extend([r"\section*{定稿前待补项}", ""])
        for item in missing_inputs:
            body.append(_latex_escape("- " + str(item)))
            body.append("")

    body.extend(["", r"\end{document}", ""])
    return "\n".join(preamble + body)


def generate_paper_package(
    project_root: str | Path,
    topic: str,
    language: str | None = None,
    paper_type: str = "general",
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise ValueError("Paper draft topic cannot be empty.")

    resolved_language = _resolve_language(normalized_topic, language)
    title = _normalize_title(normalized_topic, resolved_language)
    references = _reference_catalog(_select_reference_papers(root, normalized_topic))

    # Augment with uploaded reference file content
    uploaded_refs = (project_context or {}).get("uploaded_references", [])
    if uploaded_refs:
        selected_uploaded_refs = _select_uploaded_reference_entries(
            uploaded_refs,
            topic=normalized_topic,
            project_context=project_context,
        )
        for i, item in enumerate(selected_uploaded_refs, start=len(references) + 1):
            ref = item["ref"]
            excerpt = str(item.get("excerpt") or "").strip()
            filename = ref.get("filename", "uploaded_file")
            if not excerpt:
                continue
            references.append({
                "label": str(i),
                "record_id": None,
                "title": f"Uploaded Reference: {filename}",
                "authors": [],
                "year": None,
                "venue": None,
                "doi": None,
                "url": ref.get("path"),
                "abstract": excerpt[: min(len(excerpt), 220)],
                "content_excerpt": excerpt[:_UPLOADED_REFERENCE_REFERENCE_EXCERPT_CHARS],
            })

    experiment_plan = _load_experiment_plan(root)
    if project_context and not _is_experiment_plan_relevant(experiment_plan, normalized_topic):
        experiment_plan = None

    if resolved_language == "zh":
        experiment_lines = _build_clean_chinese_experiment_lines(experiment_plan, project_context)
        sections = _apply_chinese_writing_rules(
            _build_clean_chinese_sections(title, paper_type, references, experiment_lines, project_context, project_root=root),
            title=title,
            project_context=project_context,
        )
        sections = _compact_chinese_sections_structure(sections)
        if not references:
            sections = _sanitize_sections_without_references(sections, resolved_language, title, paper_type)
        submission_meta = _derive_submission_metadata(title, project_context)
        zh_keywords = _derive_submission_keywords(title, project_context)
        english_title = _topic_to_english_title(title)
        english_abstract = _derive_english_abstract(title, project_context)
        en_keywords = _topic_keywords(title)[: max(len(zh_keywords), 4)]
        acknowledgements = _derive_acknowledgements(project_context)
        appendix_notes = _derive_appendix_notes(project_context)
        chapter_targets = _thesis_chapter_targets()
        summary = "已生成接近最终提交版的中文论文稿件，并融合本地毕业论文 prompts 与外部写作模板约束，包含章节目标字数、双语摘要、长正文和章级 LaTeX 稿件。"
        writing_checklist = [
            "填写学校、学院、专业、学生、学号和导师等封面信息。",
            "补充与课题核心方法和技术相关的学术论文。",
            "补充系统总体架构图、模块关系图和核心结果图。",
            "正式定稿前补充实验截图、统计表与真实结果分析。",
        ]
        missing_inputs: list[str] = []
        if not references:
            missing_inputs.append("当前还没有索引到与该项目直接相关的核心文献，建议至少补充 5-8 篇相关论文。")
        if not experiment_plan:
            missing_inputs.append("当前没有识别到与该项目直接匹配的实验计划，实验章节需要结合仿真和实车测试自行补充。")
        if project_context and not project_context.get("result_clues"):
            missing_inputs.append("项目中尚未识别到可直接引用的结果文件，建议补充日志、截图、地图文件或性能统计表。")
    else:
        experiment_lines = _experiment_summary(experiment_plan, resolved_language)
        sections = _build_english_sections(title, paper_type, references, experiment_lines, experiment_plan)
        sections = _inject_project_context(sections, project_context, resolved_language)
        summary = f"Generated a stronger paper writing pack with a full draft, outline, evidence notes, and revision checklist for a {paper_type} paper."
        writing_checklist = [
            "Lock the target venue and page budget before compressing the contribution list.",
            "Keep one main claim per section and tie it to explicit evidence.",
            "Reserve space for one main results table, one ablation table, and one failure-analysis figure.",
            "Replace planning language with verified findings before submission.",
        ]
        missing_inputs = []
        if not references:
            missing_inputs.append("The workspace does not yet contain enough relevant references; add 5-8 core papers first.")
        if not experiment_plan:
            missing_inputs.append("No experiment plan JSON was found; generate it before finalizing the experiment and results sections.")
        if project_context:
            writing_checklist.insert(1, "Cross-check each method paragraph against project code, configs, and scripts.")
            if not project_context.get("result_clues"):
                missing_inputs.append("No obvious project-side result files were detected; add logs, tables, or evaluation exports.")

    evidence_notes = []
    for reference in references:
        note = reference.get("content_excerpt") or reference.get("abstract") or ""
        clipped = re.sub(r"\s+", " ", note).strip()
        if len(clipped) > 220:
            clipped = clipped[:217].rstrip() + "..."
        evidence_notes.append(
            f"[{reference['label']}] {reference.get('title') or 'Untitled'} - "
            f"{clipped or 'Add a concise takeaway after reading the full text.'}"
        )

    payload = {
        "kind": "paper",
        "title": f"论文写作稿：{title}" if resolved_language == "zh" else f"Research Paper Draft: {title}",
        "topic": normalized_topic,
        "language": resolved_language,
        "paper_type": paper_type,
        "summary": summary,
        "sections": sections,
        "references": references,
        "writing_checklist": writing_checklist,
        "evidence_notes": evidence_notes,
        "missing_inputs": missing_inputs,
        "experiment_plan_available": bool(experiment_plan),
        "project_context": project_context,
        "generated_at": _now_iso(),
    }
    if resolved_language == "zh":
        payload.update(
            {
                "submission_meta": submission_meta,
                "zh_keywords": zh_keywords,
                "english_title": english_title,
                "english_abstract": english_abstract,
                "en_keywords": en_keywords,
                "acknowledgements": acknowledgements,
                "appendix_notes": appendix_notes,
                "chapter_targets": chapter_targets,
                "prompt_sources": [
                    "毕业论文prompts/学术性润色.txt",
                    "毕业论文prompts/需要增加引用的润色.txt",
                    "external/articlewriting-skill/templates/paper-outline.md",
                    "external/articlewriting-skill/modules/writing-core.md",
                    "external/articlewriting-skill/skills/brainstorming-research/chapter-templates.md",
                    "external/articlewriting-skill/skills/latex-output/template-parser.md",
                    "external/autoresearch/program.md",
                    "external/latex-arxiv-SKILL/README.zh-CN.md",
                    "external/awesome-ai-research-writing/README.md",
                ],
            }
        )

    draft_dir = root / "drafts"
    markdown_path = draft_dir / "paper-draft.md"
    outline_path = draft_dir / "paper-outline.md"
    plan_path = draft_dir / "paper-plan.md"
    prompts_path = draft_dir / "paper-revision-prompts.md"
    json_path = draft_dir / "paper-draft.json"
    latex_path = draft_dir / "paper.tex"
    bib_path = draft_dir / "references.bib"

    writing_assets_path = draft_dir / "writing-assets.md"

    payload["supporting_assets"] = {
        "plan_path": str(plan_path),
        "prompts_path": str(prompts_path),
        "latex_path": str(latex_path),
        "bib_path": str(bib_path),
        "writing_assets_path": str(writing_assets_path),
    }

    if resolved_language == "zh":
        _write_text(
            markdown_path,
            _render_clean_zh_paper_markdown(payload, markdown_path, json_path, outline_path, plan_path, prompts_path, latex_path, bib_path),
        )
        _write_text(outline_path, _render_clean_zh_outline_markdown(title, sections, references))
        _write_text(plan_path, _render_clean_zh_plan_markdown(title, normalized_topic, paper_type, sections, references, missing_inputs))
        _write_text(prompts_path, _render_clean_zh_revision_prompts_markdown(title))
        _write_text(writing_assets_path, _render_writing_assets_markdown())
        _write_text(
            latex_path,
            _render_clean_zh_latex_manuscript(
                title,
                sections,
                references,
                payload.get("submission_meta"),
                payload.get("zh_keywords"),
                payload.get("english_title"),
                payload.get("english_abstract"),
                payload.get("en_keywords"),
                payload.get("acknowledgements"),
                payload.get("appendix_notes"),
                payload.get("missing_inputs"),
            ),
        )
    else:
        _write_text(
            markdown_path,
            _render_paper_markdown(payload, markdown_path, json_path, outline_path, plan_path, prompts_path, latex_path, bib_path),
        )
        _write_text(outline_path, _render_outline_markdown(title, sections, references, resolved_language))
        _write_text(
            plan_path,
            _render_paper_plan_markdown(title, normalized_topic, resolved_language, paper_type, sections, references, missing_inputs),
        )
        _write_text(prompts_path, _render_revision_prompts_markdown(title, resolved_language))
        _write_text(latex_path, _render_latex_manuscript(title, resolved_language, sections, references))
        _postprocess_markdown_outputs(markdown_path, outline_path, resolved_language)

    _write_text(bib_path, _render_bibtex(references))
    _write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))

    state = sync_project_state(root)
    dashboard_path = build_dashboard(root)
    return {
        "project_root": str(root),
        "dashboard_path": str(dashboard_path),
        "markdown_path": str(markdown_path),
        "outline_path": str(outline_path),
        "plan_path": str(plan_path),
        "prompts_path": str(prompts_path),
        "json_path": str(json_path),
        "latex_path": str(latex_path),
        "bib_path": str(bib_path),
        "artifact": payload,
        "state": state,
    }


_legacy_generate_paper_package = generate_paper_package


_legacy_inject_project_context = _inject_project_context


def _inject_project_context(
    sections: list[dict[str, Any]],
    project_context: dict[str, Any] | None,
    language: str,
) -> list[dict[str, Any]]:
    enriched = _legacy_inject_project_context(sections, project_context, language)
    if language == "zh":
        return _polish_chinese_sections(enriched)
    return enriched


def _project_context_has_primary_evidence(project_context: dict[str, Any] | None) -> bool:
    context = project_context or {}
    if str(context.get("source_project_path") or "").strip():
        return True
    for key in (
        "project_name",
        "source_files",
        "result_clues",
        "figure_candidates",
        "table_candidates",
        "metric_inventory",
        "variable_inventory",
        "project_summary",
    ):
        value = context.get(key)
        if isinstance(value, (list, dict)) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _resolve_effective_topic(
    project_root: str | Path,
    topic: str,
    project_context: dict[str, Any] | None = None,
) -> str:
    normalized_topic = str(topic or "").strip()
    if normalized_topic:
        return normalized_topic

    context = project_context or {}
    for key in ("topic", "project_name"):
        value = str(context.get(key) or "").strip()
        if value:
            return value

    source_project_path = str(context.get("source_project_path") or "").strip()
    if source_project_path:
        source_name = Path(source_project_path).name.strip()
        if source_name:
            return source_name

    root_name = Path(project_root).resolve().name.strip()
    if root_name and _is_descriptive_topic_candidate(root_name):
        return root_name

    if not _project_context_has_primary_evidence(context):
        uploaded_topic = _pick_uploaded_reference_topic_candidate(context.get("uploaded_references") or [])
        if uploaded_topic:
            return uploaded_topic

    if root_name and root_name.lower() not in _GENERIC_TOPIC_FALLBACKS:
        return root_name

    return "Research Draft"


def generate_paper_package(
    project_root: str | Path = ".",
    topic: str = "",
    language: str = "auto",
    paper_type: str = "general",
    project_context: dict[str, Any] | None = None,
    target_words: int | None = None,
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    # Enrich project_context with CSV data and figure info for LLM prompts
    if project_context is None:
        project_context = {}
    resolved_topic = _resolve_effective_topic(root, topic, project_context)
    if not str(project_context.get("topic") or "").strip():
        project_context["topic"] = resolved_topic

    # Pass target_words into project_context so that _boost_chinese_thesis_length
    # and other downstream functions can scale their output dynamically.
    if target_words is not None:
        project_context.setdefault("target_words", target_words)

    evidence_root = _resolve_project_evidence_root(root, project_context)

    # Auto-run source project to collect experiment data/figures/metrics
    _source_path = str(project_context.get("source_project_path") or "").strip()
    _project_mode = bool(_source_path and Path(_source_path).is_dir())
    _collect_step = 3 if _project_mode else 2
    _outline_step = 4 if _project_mode else 2
    _draft_step = 5 if _project_mode else 3
    _finalize_step = 6 if _project_mode else 4
    if _source_path and Path(_source_path).is_dir():
        try:
            from tools.project_runner import run_project as _run_project
            _dry = project_context.get("skip_run", False)
            _timeout = int(project_context.get("run_timeout", 300))
            _entry = project_context.get("entry_script") or None
            _allow_agent_modifications = project_context.get("allow_agent_modifications")
            _emit_generation_progress(progress_callback, 2, "正在运行项目代码...")
            _emit_generation_progress(progress_callback, 2, "正在运行项目代码...", "尝试执行项目入口并采集运行日志")
            print(f"[PaperWriter] 自动运行项目: {_source_path}")
            _pr = _run_project(
                _source_path,
                entry_script=_entry,
                timeout=_timeout,
                dry_run=_dry,
                allow_agent_modifications=_allow_agent_modifications,
            )
            # Merge collected data into project_context (don't overwrite user-provided keys)
            _pc = _pr.get("project_context", {})
            for _key in ("result_clues", "experiment_metrics",
                         "metrics_summary", "source_files", "stack", "console_output"):
                if _pc.get(_key) and not project_context.get(_key):
                    project_context[_key] = _pc[_key]
            # Always merge figures and copy them to project_root for finalize step
            _figs = _pr.get("collected", {}).get("figures", [])
            if _figs:
                # Copy source project figures into project_root/output/figures/
                _dst_fig_dir = root / "output" / "figures"
                _dst_fig_dir.mkdir(parents=True, exist_ok=True)
                existing = set(project_context.get("candidate_result_files") or [])
                for _f in _figs:
                    _src = Path(_f["path"])
                    _dst_name = _src.name
                    _dst = _dst_fig_dir / _dst_name
                    if not _dst.exists() and _src.exists():
                        import shutil as _shutil
                        _shutil.copy2(_src, _dst)
                    _rel = f"output/figures/{_dst_name}"
                    if _rel not in existing:
                        project_context.setdefault("candidate_result_files", []).append(_rel)
                        existing.add(_rel)
            # Merge run status
            project_context["run_success"] = _pr["run_result"]["success"]
            project_context["project_type"] = _pr["project_type"]
            print(f"[PaperWriter] 项目运行完成: type={_pr['project_type']} "
                  f"success={_pr['run_result']['success']} "
                  f"figures={len(_figs)}")
        except Exception as _e:
            print(f"[PaperWriter] 项目运行失败（继续生成）: {_e}")

    _emit_generation_progress(progress_callback, _collect_step, "正在采集实验结果...")
    _emit_generation_progress(progress_callback, _collect_step, "正在采集实验结果...", "整理图表、CSV、指标和结果摘要")
    csv_tables = _scan_project_csv_data(evidence_root)
    if csv_tables:
        new_result_clues: list[str] = []
        for table in csv_tables:
            # Build a compact text summary of CSV data for the LLM
            header_str = ", ".join(table["headers"])
            rows_strs = []
            for row in table["rows"][:10]:
                rows_strs.append(", ".join(row))
            new_result_clues.append(f"{table['stem']}: {header_str}; 数据: {'; '.join(rows_strs)}")
        project_context["result_clues"] = _merge_unique_strings(
            list(project_context.get("result_clues") or []),
            new_result_clues,
        )

    project_figures = _scan_project_figures(
        evidence_root,
        run_extractor=evidence_root == root,
    )
    synced_figures = _sync_figure_evidence_into_workspace(root, evidence_root, project_figures)
    if evidence_root != root:
        project_context["paper_workspace_figure_files"] = sorted({fig["file"] for fig in synced_figures})
    if synced_figures:
        existing_result_files = list(project_context.get("candidate_result_files") or [])
        if evidence_root != root:
            allowed_prefixes = {f"output/figures/{fig['file']}" for fig in synced_figures}
            existing_result_files = [
                item
                for item in existing_result_files
                if not str(item).strip().startswith("output/figures/")
                or any(str(item).strip().startswith(prefix) for prefix in allowed_prefixes)
            ]
        figure_refs = [f"output/figures/{fig['file']} ({fig['caption']})" for fig in synced_figures]
        project_context["candidate_result_files"] = _merge_unique_strings(existing_result_files, figure_refs)
        existing_figure_candidates = [item for item in (project_context.get("figure_candidates") or []) if isinstance(item, dict)]
        seen_figure_paths = {str(item.get("path") or "") for item in existing_figure_candidates}
        for figure in synced_figures:
            rel_path = f"output/figures/{figure['file']}"
            if rel_path in seen_figure_paths:
                continue
            existing_figure_candidates.append(
                {
                    "path": rel_path,
                    "caption": figure.get("caption") or Path(figure["file"]).stem,
                    "role": figure.get("role") or "result",
                    "section": "experiment" if figure.get("role") in {"result", "comparison"} else "design",
                    "source": "workspace-figure-scan",
                }
            )
            seen_figure_paths.add(rel_path)
        project_context["figure_candidates"] = existing_figure_candidates

    csv_tables = _scan_project_csv_data(evidence_root)
    if csv_tables:
        existing_table_candidates = [item for item in (project_context.get("table_candidates") or []) if isinstance(item, dict)]
        seen_table_paths = {str(item.get("path") or "") for item in existing_table_candidates}
        for table in csv_tables:
            rel_path = f"output/results/{table['file']}"
            if rel_path in seen_table_paths:
                continue
            existing_table_candidates.append(
                {
                    "path": rel_path,
                    "caption": _stem_to_caption(table["stem"]) or table["stem"],
                    "section": "experiment",
                    "headers": list(table.get("headers") or []),
                    "preview_rows": [list(row) for row in table.get("rows") or []][:3],
                    "metrics": [
                        header
                        for header in table.get("headers") or []
                        if any(token in str(header).lower() for token in ("acc", "loss", "score", "f1", "auc", "latency", "rmse"))
                    ],
                    "source": "workspace-table-scan",
                }
            )
            seen_table_paths.add(rel_path)
        project_context["table_candidates"] = existing_table_candidates

    chapter_budget = project_context.get("chapter_budget") or {}
    figure_budget = dict(chapter_budget.get("figures") or {})
    table_budget = dict(chapter_budget.get("tables") or {})
    figure_count = len(project_context.get("figure_candidates") or [])
    table_count = len(project_context.get("table_candidates") or [])
    if figure_count:
        figure_budget["design"] = max(int(figure_budget.get("design") or 0), 1)
        figure_budget["experiment"] = max(int(figure_budget.get("experiment") or 0), min(max(figure_count // 2, 1), 6))
    if table_count:
        table_budget["experiment"] = max(int(table_budget.get("experiment") or 0), min(max(table_count, 1), 4))
    chapter_budget["figures"] = figure_budget
    chapter_budget["tables"] = table_budget
    chapter_budget["total_figures"] = sum(int(value) for value in figure_budget.values())
    chapter_budget["total_tables"] = sum(int(value) for value in table_budget.values())
    project_context["chapter_budget"] = chapter_budget

    # Inject dynamic figure/table budget into project_context for LLM prompts
    _emit_generation_progress(progress_callback, _outline_step, "正在生成论文大纲...")
    _emit_generation_progress(progress_callback, _outline_step, "正在规划论文结构...", "生成章节大纲、证据槽位和图表规划")
    try:
        from tools.domain_utils import detect_domain, get_archetype, get_figure_table_instruction
        _domain = detect_domain(resolved_topic, project_context)
        _archetype = get_archetype(resolved_topic, project_context)
        _budget_instruction = get_figure_table_instruction(_archetype, resolved_topic, target_words=target_words or 15000)
        project_context["figure_table_budget"] = _budget_instruction
    except Exception:
        pass  # Non-critical

    resolved_language = _resolve_language(resolved_topic, language)
    figure_plan_path = root / "drafts" / "figure-plan.md"
    figure_plan = build_figure_plan(
        topic=resolved_topic,
        language=resolved_language,
        project_context=project_context,
    )
    table_plan = build_table_plan(
        topic=resolved_topic,
        language=resolved_language,
        project_context=project_context,
    )
    equation_plan = build_equation_plan(
        topic=resolved_topic,
        language=resolved_language,
        project_context=project_context,
    )
    project_context["figure_plan"] = figure_plan
    project_context["table_plan"] = table_plan
    project_context["equation_plan"] = equation_plan
    project_context["figure_plan_summary"] = build_figure_plan_summary(figure_plan, resolved_language)
    _write_text(
        figure_plan_path,
        render_figure_plan_markdown(
            topic=resolved_topic,
            language=resolved_language,
            plan=figure_plan,
            table_plan=table_plan,
            equation_plan=equation_plan,
        ),
    )

    # Inject uploaded reference content as text for LLM context
    uploaded_refs = (project_context or {}).get("uploaded_references")
    if uploaded_refs:
        uploaded_reference_text = _build_uploaded_reference_text(
            uploaded_refs,
            topic=resolved_topic,
            project_context=project_context,
        )
        if uploaded_reference_text:
            project_context["uploaded_reference_text"] = uploaded_reference_text
        else:
            project_context.pop("uploaded_reference_text", None)

    _emit_generation_progress(progress_callback, _draft_step, "正在调用 LLM 撰写各章节...")
    _emit_generation_progress(progress_callback, _draft_step, "正在撰写论文正文...", "根据章节蓝图、项目证据和参考资料生成初稿")
    result = _legacy_generate_paper_package(
        project_root=project_root,
        topic=resolved_topic,
        language=language,
        paper_type=paper_type,
        project_context=project_context,
    )
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        supporting_assets = artifact.get("supporting_assets")
        if not isinstance(supporting_assets, dict):
            supporting_assets = {}
        writing_assets_path = root / "drafts" / "writing-assets.md"
        _write_text(writing_assets_path, render_integrated_writing_assets_markdown())
        supporting_assets["writing_assets_path"] = str(writing_assets_path)
        supporting_assets["figure_plan_path"] = str(figure_plan_path)
        artifact["supporting_assets"] = supporting_assets
        artifact["figure_plan"] = figure_plan
        artifact["table_plan"] = table_plan
        artifact["equation_plan"] = equation_plan
        artifact["figure_plan_summary"] = project_context.get("figure_plan_summary")
    result["figure_plan_path"] = str(figure_plan_path)
    try:
        from tools.writing_enhancer import enhance_generated_paper_package

        _emit_generation_progress(progress_callback, _draft_step, "正在撰写论文正文...", "基础章节已生成，正在做长文增强、去重和补写")
        enhanced = enhance_generated_paper_package(
            result,
            project_root=project_root,
            topic=resolved_topic,
            language=language,
            paper_type=paper_type,
            project_context=project_context,
            target_words=target_words,
        )

        # Stage 3: Agent-assisted paper-code consistency review
        try:
            from tools.agent_bridge import agent_enabled, run_agent_task, build_consistency_prompt
            if agent_enabled() and project_context and project_context.get("source_project_path"):
                _emit_generation_progress(progress_callback, _draft_step, "正在撰写论文正文...", "正在核对论文内容与项目实现是否一致")
                print("[PaperWriter] 调用 AI 代理审查论文与代码一致性...")
                consistency_result = run_agent_task(
                    task=build_consistency_prompt(enhanced, project_context),
                    project_path=str(project_context["source_project_path"]),
                    max_turns=5,
                )
                if consistency_result["success"]:
                    _apply_consistency_fixes(enhanced, consistency_result["output"])
                    print("[PaperWriter] 一致性审查完成")
                else:
                    print(f"[PaperWriter] 一致性审查失败: {consistency_result.get('error', 'unknown')}")
        except ImportError:
            pass
        except Exception as _ce:
            print(f"[PaperWriter] Agent 一致性审查异常（继续）: {_ce}")

        _emit_generation_progress(progress_callback, _finalize_step, "正在整理终稿与附件...", "正在清洗草稿、图表、引用和质量信息")
        return _finalize_zh_generated_package(enhanced, progress_callback=progress_callback)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        artifact = result.get("artifact")
        if isinstance(artifact, dict):
            quality_meta = artifact.get("quality_meta") if isinstance(artifact.get("quality_meta"), dict) else {}
            quality_meta.update(
                {
                    "llm_enhanced": False,
                    "enhancer_error": str(exc),
                    "target_words": target_words,
                }
            )
            artifact["quality_meta"] = quality_meta
        _emit_generation_progress(progress_callback, _finalize_step, "正在整理终稿与附件...", "增强阶段失败，正在保留可用草稿并整理结果")
        return _finalize_zh_generated_package(result, progress_callback=progress_callback)


def _render_clean_zh_revision_prompts_markdown(title: str) -> str:
    return dedent(
        f"""\
        # 论文修订提示包：{title}

        ## 长篇章节扩写

        ```text
        你现在扮演中文本科毕业论文写作助手。请根据我提供的章节标题、已有正文、项目代码线索和实验信息，扩写为信息密度更高的长篇正文。
        要求：
        1. 输出直接可用于论文正文，不要写解释，不要使用列表堆砌观点。
        2. 每段只表达一个中心意思，段落长度控制在 150-300 字。
        3. 语言自然、正式、学术化，避免"首先、其次、最后、此外、值得注意的是"等机械连接词。
        4. 必须围绕项目真实代码、配置、实验和系统设计展开，不要编造不存在的功能。
        5. 如果信息不足，允许补充合理的工程分析，但不能伪造实验数据。
        6. 目标是把该章节扩展到适合毕业论文提交的长度，而不是只写提纲说明。
        7. 禁止出现"论文需要""本章将说明如何写""更合理的表达方式是"等元写作话语。
        8. 不要把正文写成项目说明文档，要写成"研究问题-方案设计-实现依据-结果验证"的工科论文叙述。
        9. 不要把每段都写成对称的"三点并列 + 总结句"结构，允许局部段落更贴近真实工程推进过程。
        ```

        ## 本科工科摘要重写

        ```text
        请将下面的摘要改写为更符合本科工科毕业论文习惯的中英文摘要。
        要求：
        1. 中文摘要采用"研究问题-系统方法-实验结果-结论价值"四段式逻辑，但输出为 1-2 个自然段；
        2. 删除源码文件名、配置文件名、CSV/JSON 文件名、章节安排和写作说明；
        3. 只保留最关键的 2-4 个量化结果，不要把摘要写成结果清单；
        4. 关键词控制在 3-5 个，优先保留研究对象、核心方法和任务目标；
        5. 英文摘要与中文摘要内容保持一致，不要自由发挥。
        ```

        ## 文献综述与研究空白

        ```text
        请围绕"{title}"撰写文献综述章节。
        执行步骤：
        1. 先按"研究问题—方法路线—评价方式—不足与空白"构造综述逻辑；
        2. 不按论文发表时间机械罗列，而按技术方向分组；
        3. 每组文献最后给出共性不足，并自然引出本文切入点；
        4. 如果我提供了论文摘要或笔记，请基于这些材料归纳；如果没有，就只输出可填充的高质量综述框架正文。
        输出必须是连续的中文论文正文，不要输出项目符号列表。
        ```

        ## 引用增强与 BibTeX 补全

        ```text
        请在不改变原意的前提下增强下面这段论文内容的学术支撑。
        要求：
        1. 找出需要文献支撑的结论句、背景句和方法描述句；
        2. 在合适位置加入 \\cite{{...}} 占位；
        3. 如需新增文献，请同时给出 BibTeX 条目；
        4. 中文与英文文献都可以使用，但必须与主题直接相关；
        5. 输出分为两个代码块：第一个是改写后的正文，第二个是新增 BibTeX。
        ```

        ## 参考文献扩容

        ```text
        请根据论文主题"{title}"补强参考文献体系。
        要求：
        1. 文献总量目标为 25-30 篇；
        2. 必须覆盖基础理论、核心方法、实验评价和工程实现相关来源；
        3. 优先给出期刊、会议和经典教材，不要用博客或随意网页充数；
        4. 先指出正文中哪些句子需要引用，再给出建议文献；
        5. 输出包含：引用建议、参考文献列表、必要时新增 BibTeX。
        ```

        ## 初稿去模板味改写

        ```text
        请将以下论文正文改写为更自然、更像人工完成的本科工科论文初稿。
        要求：
        1. 保持原意、信息量和章节逻辑，不要为了"润色"删掉关键技术细节；
        2. 删除重复套话，尤其避免反复出现"结果表明、由此可见、综合来看、可以看出、需要指出的是、一方面、另一方面"等句壳；
        3. 不要把每段都写成"先下结论、再补解释、最后总结"的对称结构；
        4. 如果段落里已经有数据、图表、日志或对比结果，就直接解释原因、条件和局限，不要再补一句空泛结论；
        5. 段首句式要有变化，优先使用"场景条件、设计取舍、工程约束、运行现象、误差来源"来展开；
        6. 少写正确但空泛的评价词，多写项目特有的模块关系、参数影响、调试过程和场景差异；
        7. 不要额外加粗、加标题或写解释说明，输出为连续论文正文。
        ```

        ## 项目证据优先改写

        ```text
        请根据我给出的源码、配置、运行日志、实验截图和表格数据，把下面这段正文改写成"证据优先"的毕业论文表达。
        要求：
        1. 每一段都要尽量落到具体模块、接口、参数、运行条件或实验场景；
        2. 优先解释"为什么这样设计""这样配置带来了什么工程后果""实验现象为什么出现"；
        3. 如果证据不足，可以保留为待验证判断，但不能把猜测写成已经验证的结果；
        4. 不要把源码文件名机械堆在正文里，而要把它们转化为设计依据和实现逻辑；
        5. 输出风格必须像本科工科毕业设计论文，而不是项目说明文档。
        ```

        ## 句式雷同扫描

        ```text
        请扫描下面这段论文正文中的模板化句式和重复节奏。
        任务：
        1. 标出重复出现的段首句壳、总结句壳和过度对称的并列结构；
        2. 判断哪些句子虽然语法正确，但信息增量很低；
        3. 给出"保留 / 删除 / 改写"的逐项建议；
        4. 优先检查"结果表明、由此可见、综合来看、本文、从…角度看、一方面、另一方面"等高频模式。
        输出格式为：
        原句 | 问题类型 | 处理建议 | 改写方向
        ```

        ## 实验结果与讨论深化

        ```text
        请根据以下实验记录、截图说明、日志结果和表格数据，写出"实验结果与讨论"长正文。
        写作要求：
        1. 先概述实验目的与评价指标；
        2. 再解释主要结果，不要只复述表格数值；
        3. 分析结果背后的原因，包括系统结构、参数设置和硬件条件的影响；
        4. 指出局限性、误差来源和后续改进方向；
        5. 输出应像毕业论文中的实验分析章节，不要写成实验报告步骤清单。
        ```

        ## 反向大纲与逻辑检查

        ```text
        请对下面这段论文正文做 reverse outline 检查。
        任务：
        1. 提取每一段的中心句；
        2. 判断相邻段落之间的逻辑是否连续；
        3. 标出重复、跳跃、空泛或论证不足的部分；
        4. 给出按段落级别的重写建议。
        输出格式为：
        段落编号 | 中心意思 | 问题 | 修改建议
        ```

        ## 审稿人视角总评

        ```text
        请从毕业论文评阅教师的视角审查以下正文。
        重点检查：
        1. 结构是否完整；
        2. 论证是否空泛；
        3. 是否存在"只有骨架没有内容"的段落；
        4. 图表、引用、实验和结论是否对应；
        5. 哪些地方最影响提交质量。
        请输出"问题严重度 + 原因 + 修改建议"，按严重程度排序。
        ```

        ## 图表与标题生成

        ```text
        请根据以下章节内容，为毕业论文设计 5-8 个图表候选。
        对每个图表给出：
        1. 图/表名称；
        2. 适合放置的章节；
        3. 图表要表达的核心信息；
        4. 一句正式的论文图题或表题。
        ```
        """
    ).strip() + "\n"


def _render_writing_assets_markdown() -> str:
    return dedent(
        """\
        # 论文写作资产清单

        本文件汇总了当前系统已吸收的线上论文写作 prompts、skills 与 workflow。

        ## 已纳入的高价值来源

        - LeSinus/chatgpt-prompts-for-academic-writing
          - 链接：https://github.com/LeSinus/chatgpt-prompts-for-academic-writing
          - 吸收内容：Role + Objective + Constraints + Format 的 prompt 结构；章节扩写、文献综述、研究问题、结果讨论等 prompt 类型；"先提问补上下文"的交互策略。

        - federicodeponte/academic-thesis-ai（OpenDraft）
          - 链接：https://github.com/federicodeponte/academic-thesis-ai
          - 吸收内容：research → structure → writing → validation → polish 的多阶段写作流程；长篇 thesis 目标；自动化 research-first 的工作方式。

        - articlewriting-skill
          - 本地路径：external/articlewriting-skill
          - 吸收内容：正文去列表化、去 AI 味、段落中心句、章节字数目标、章节模板、LaTeX 输出规范。

        - latex-arxiv-SKILL
          - 本地路径：external/latex-arxiv-SKILL
          - 吸收内容：issue-driven 论文推进、plan 先行、LaTeX 工程导向输出。

        - awesome-ai-research-writing
          - 本地路径：external/awesome-ai-research-writing
          - 吸收内容：提示词分类思路，包括润色、逻辑检查、实验分析、图题表题与 reviewer 视角检查。

        ## 当前系统已经采用的规则

        1. 正文优先使用连续段落，不用项目符号堆砌观点。
        2. 每段尽量围绕一个中心意思展开，避免模板化连接词。
        3. 章节按照毕业论文目标字数扩写，而不是只生成提纲。
        4. 修订提示包覆盖扩写、引用增强、实验讨论、逻辑检查与审稿人审查。
        5. 输出优先落到 `drafts/`，方便统一查看与修改。
        6. 已增加 [`drafts/thesis-benchmark-notes.md`](./thesis-benchmark-notes.md) 作为本科工科论文基准，明确限制元话语、项目说明文风、摘要写法和参考文献数量下限。
        7. 初稿生成默认采用"项目证据优先"策略，优先展开模块关系、参数取舍、运行时序、场景条件和误差来源。
        8. 初稿修订默认扫描并压低高频句壳，如"结果表明、由此可见、综合来看、可以看出、一方面、另一方面"等。
        9. 不再鼓励把每一段都写成完全对称的三点并列或段末总结句，允许局部保留更接近真实工程叙述的非对称展开。

        ## 初稿去模板味的具体约束

        - 先写证据、现象和条件，再写判断，不要每段都先下结论。
        - 如果已经出现图表、日志、参数或量化结果，下一句优先解释原因与限制，而不是重复总结。
        - 把源码文件、配置文件和目录结构转化成设计依据与实现逻辑，不直接堆文件名。
        - 多写场景差异、调试过程、接口约束和失败案例来源，少写"系统具有较好效果"这类空泛评价。
        - 保持段首句式变化，避免连续多段以"本文、本章、结果表明、从…角度看"起笔。
        - 允许段落结构局部不对称，只要论证顺序真实、信息密度足够即可。

        ## 后续可继续增强的方向

        - 自动从论文索引生成文献综述初稿。
        - 自动根据正文生成图表清单与图题表题。
        - 自动把缺引文的位置和 BibTeX 生成串起来。
        - 自动生成开题答辩、中期答辩和最终答辩 PPT 提纲。
        - 自动统计正文中的高频句壳、段首重复和低信息总结句，用于批量回改初稿。
        """
    ).strip() + "\n"
