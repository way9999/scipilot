from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from tools.experiment_design import (
    ablation_study,
    baseline_comparison,
    clinical_groups,
    dose_response,
    hyperparameter_grid,
    list_tdc_benchmarks,
    optuna_search_template,
    screening_plate,
)
from tools.literature_review import generate_literature_review
from tools.paper_dashboard import build_dashboard
from tools.paper_content_crawler import crawl_paper_content
from tools.project_paper_context import analyze_project_for_paper
from tools.paper_writer import generate_paper_package
from tools.research_capability_audit import analyze_research_capabilities
from tools.research_export import export_markdown_to_docx, export_presentation_to_pptx
from tools.research_qa import answer_research_question
from tools.project_state import load_paper_index, register_search_results, save_paper_index, sync_project_state
from tools.unified_search import auto_download, auto_search


def _json_default(value: Any):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _data_analyzer_api():
    from tools.data_analyzer import (
        compute_metrics_summary,
        find_result_files,
        format_results_table,
        load_results,
    )

    return find_result_files, load_results, compute_metrics_summary, format_results_table


def _figure_generator_api():
    from tools.figure_generator import auto_figures_from_results, generate_figure_inventory

    return auto_figures_from_results, generate_figure_inventory


def _resolve_project_root(project_root: str | None) -> Path:
    if project_root:
        return Path(project_root).resolve()
    return PROJECT_ROOT


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def _csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _render_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _render_markdown_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _render_named_configs(items: list[dict[str, Any]], limit: int = 6) -> str:
    lines: list[str] = []
    for index, item in enumerate(items[:limit], start=1):
        label = item.get("name") or f"Config {index}"
        config = item.get("config", item)
        lines.append(f"{index}. {label}: `{json.dumps(config, ensure_ascii=False, sort_keys=True)}`")
    return "\n".join(lines)


def _infer_experiment_domain(topic: str, requested_domain: str | None) -> str:
    if requested_domain and requested_domain != "auto":
        return requested_domain

    normalized = topic.lower()
    biomedical_keywords = [
        "drug",
        "molecule",
        "compound",
        "protein",
        "admet",
        "cell",
        "clinical",
        "tumor",
        "omics",
        "bio",
        "chem",
    ]
    cs_keywords = [
        "llm",
        "transformer",
        "graph",
        "gnn",
        "vision",
        "retrieval",
        "rag",
        "agent",
        "diffusion",
        "benchmark",
        "classification",
        "segmentation",
    ]

    if any(keyword in normalized for keyword in biomedical_keywords):
        return "biomedicine"
    if any(keyword in normalized for keyword in cs_keywords):
        return "cs_ai"
    return "general"


def _suggest_cs_assets(topic: str) -> tuple[list[str], list[str], list[str]]:
    normalized = topic.lower()
    if "graph" in normalized or "molecule" in normalized or "drug" in normalized:
        return (
            ["GCN", "GAT", "GraphSAGE"],
            ["OGB-MolHIV", "QM9", "ZINC"],
            ["ROC-AUC", "PR-AUC", "MAE"],
        )
    if "vision" in normalized or "image" in normalized or "segmentation" in normalized:
        return (
            ["ResNet", "ViT", "Swin Transformer"],
            ["ImageNet-1K", "CIFAR-100", "COCO"],
            ["Top-1 Acc", "mAP", "FLOPs"],
        )
    if "retrieval" in normalized or "rag" in normalized or "question answering" in normalized:
        return (
            ["BM25", "DPR", "ColBERT"],
            ["MS MARCO", "Natural Questions", "BEIR"],
            ["MRR@10", "Recall@20", "nDCG@10"],
        )
    return (
        ["Strong Baseline A", "Strong Baseline B", "Open Source Replica"],
        ["Public Benchmark A", "Public Benchmark B", "Internal Validation Set"],
        ["Primary Metric", "Latency", "Memory"],
    )


def _build_cs_plan(topic: str, method_name: str, paper_count: int) -> dict[str, Any]:
    baselines, datasets, metrics = _suggest_cs_assets(topic)
    ablations = ablation_study(
        {
            "core_module": ["enabled", "none"],
            "feature_fusion": ["enabled", "none"],
            "auxiliary_loss": ["enabled", "none"],
        }
    )
    grid = hyperparameter_grid(
        {
            "learning_rate": [1e-3, 5e-4, 1e-4],
            "batch_size": [16, 32],
            "dropout": [0.1, 0.3],
        }
    )
    baseline_matrix = baseline_comparison(method_name, baselines, datasets, metrics)
    optuna_template = optuna_search_template(
        {
            "learning_rate": ("log_float", 1e-5, 1e-2),
            "dropout": ("float", 0.0, 0.5),
            "batch_size": ("categorical", [16, 32, 64]),
        },
        n_trials=40,
    )

    hypotheses = [
        f"{method_name} should improve the primary metric on at least one public benchmark.",
        "Each core module should have a measurable contribution under ablation.",
        "The final report should include mean/std across at least three random seeds.",
    ]
    checklist = [
        "Lock train/valid/test splits before tuning.",
        "Track seed, latency, peak memory, and hardware for every run.",
        "Report both best checkpoint and average over repeated runs.",
        "Archive plots and raw metrics under output/ for later writing.",
    ]
    summary = (
        f"Prepared {baseline_matrix['total_runs']} baseline runs, "
        f"{len(ablations)} ablation settings, and {len(grid)} grid search candidates "
        f"from a literature base of {paper_count} indexed papers."
    )

    return {
        "domain": "cs_ai",
        "title": f"Experiment plan for {topic}",
        "topic": topic,
        "summary": summary,
        "objective": f"Validate {method_name} against strong baselines with reproducible evaluation.",
        "hypotheses": hypotheses,
        "baseline_matrix": baseline_matrix,
        "ablations": ablations,
        "hyperparameter_grid": grid,
        "optuna_template": optuna_template,
        "checklist": checklist,
    }


def _build_biomedicine_plan(topic: str, method_name: str, paper_count: int) -> dict[str, Any]:
    concentrations = [0.01, 0.1, 1, 10, 100]
    screening = screening_plate(
        compounds=["Lead_A", "Lead_B", "Lead_C"],
        concentrations=[0.1, 1, 10, 100],
        replicates=2,
        plate_size=96,
    )
    dose_groups = dose_response(concentrations, replicates=3, include_control=True)
    animal_groups = clinical_groups(
        arms=["Vehicle", f"{method_name}_Low", f"{method_name}_High", "Positive_Control"],
        n_per_arm=8,
        stratify_by=["sex", "baseline burden"],
    )
    tdc_benchmarks = list_tdc_benchmarks()
    checklist = [
        "Freeze hit criteria before running confirmatory assays.",
        "Include vehicle and positive controls on every plate.",
        "Record replicate, batch, and operator metadata for every wet-lab run.",
        "Mirror wet-lab endpoints with one public TDC benchmark where applicable.",
    ]
    hypotheses = [
        f"{method_name} should show a dose-dependent response with a clean control separation.",
        "Screening hits should remain stable across technical and biological replicates.",
        "The computational benchmark should align with the wet-lab ranking direction.",
    ]
    summary = (
        f"Prepared a 96-well screening layout, {len(dose_groups)} dose-response groups, "
        f"and {animal_groups['total_n']} subjects for confirmatory grouping from "
        f"{paper_count} indexed papers."
    )

    return {
        "domain": "biomedicine",
        "title": f"Experiment plan for {topic}",
        "topic": topic,
        "summary": summary,
        "objective": f"Evaluate {method_name} with a screening-to-confirmation workflow.",
        "hypotheses": hypotheses,
        "screening": screening,
        "dose_response": dose_groups,
        "animal_groups": animal_groups,
        "tdc_benchmarks": tdc_benchmarks,
        "checklist": checklist,
    }


def _build_general_plan(topic: str, method_name: str, paper_count: int) -> dict[str, Any]:
    baseline_matrix = baseline_comparison(
        method_name,
        ["Reference Workflow", "Heuristic Baseline", "Human Expert"],
        ["Primary Dataset", "Stress Test Set"],
        ["Primary Metric", "Cost", "Turnaround Time"],
    )
    grid = hyperparameter_grid(
        {
            "budget_level": ["low", "medium", "high"],
            "sample_size": [50, 100, 200],
        }
    )
    hypotheses = [
        f"{method_name} should outperform the reference workflow on the primary metric.",
        "Performance should remain stable under the stress-test condition.",
        "The chosen operating point should balance cost and quality.",
    ]
    checklist = [
        "Define the decision threshold before looking at final results.",
        "Separate development feedback from final evaluation data.",
        "Capture failure cases for the final report and presentation.",
    ]
    summary = (
        f"Prepared {baseline_matrix['total_runs']} comparison runs and {len(grid)} operating points "
        f"from {paper_count} indexed papers."
    )

    return {
        "domain": "general",
        "title": f"Experiment plan for {topic}",
        "topic": topic,
        "summary": summary,
        "objective": f"Validate {method_name} with a compact baseline and stress-test matrix.",
        "hypotheses": hypotheses,
        "baseline_matrix": baseline_matrix,
        "grid": grid,
        "checklist": checklist,
    }


def _render_experiment_markdown(plan: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    lines = [
        f"# {plan['title']}",
        "",
        f"- Domain: {plan['domain']}",
        f"- Topic: {plan['topic']}",
        f"- Objective: {plan['objective']}",
        f"- Summary: {plan['summary']}",
        "",
        "## Hypotheses",
        _render_markdown_list(plan["hypotheses"]),
        "",
    ]

    if plan["domain"] in {"cs_ai", "general"}:
        baseline_matrix = plan["baseline_matrix"]
        lines.extend(
            [
                "## Baseline Matrix",
                f"- Total runs: {baseline_matrix['total_runs']}",
                f"- Methods: {', '.join(baseline_matrix['methods'])}",
                f"- Datasets: {', '.join(baseline_matrix['datasets'])}",
                f"- Metrics: {', '.join(baseline_matrix['metrics'])}",
                "",
                "```markdown",
                baseline_matrix["table_template"],
                "```",
                "",
            ]
        )

    if plan["domain"] == "cs_ai":
        lines.extend(
            [
                "## Ablation Study",
                _render_named_configs(plan["ablations"]),
                "",
                "## Hyperparameter Sweep",
                _render_named_configs(plan["hyperparameter_grid"]),
                "",
                "## Optuna Starter",
                "```python",
                plan["optuna_template"].rstrip(),
                "```",
                "",
            ]
        )

    if plan["domain"] == "biomedicine":
        lines.extend(
            [
                "## Screening Layout",
                f"- Plate size: {plan['screening']['plate_size']}",
                f"- Plates needed: {plan['screening']['plates_needed']}",
                f"- Total wells: {plan['screening']['total_wells']}",
                "",
                "## Dose Response",
                _render_named_configs(plan["dose_response"]),
                "",
                "## Confirmatory Groups",
                f"- Arms: {', '.join(plan['animal_groups']['arms'])}",
                f"- N per arm: {plan['animal_groups']['n_per_arm']}",
                f"- Total N: {plan['animal_groups']['total_n']}",
                "",
                "## Public Benchmarks",
                _render_markdown_list(
                    [
                        f"{name}: {details.get('description', '')}"
                        for name, details in plan["tdc_benchmarks"].items()
                    ]
                ),
                "",
            ]
        )

    if plan["domain"] == "general":
        lines.extend(
            [
                "## Operating Points",
                _render_named_configs(plan["grid"]),
                "",
            ]
        )

    lines.extend(
        [
            "## Execution Checklist",
            _render_markdown_list(plan["checklist"]),
            "",
            "## Generated Assets",
            f"- Markdown plan: `{markdown_path}`",
            f"- Structured payload: `{json_path}`",
            "",
        ]
    )

    return "\n".join(lines)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _resolve_language(topic: str, requested_language: str | None) -> str:
    if requested_language and requested_language != "auto":
        return requested_language
    return "zh" if _contains_cjk(topic) else "en"


def _normalize_topic_title(topic: str, language: str) -> str:
    topic = topic.strip()
    if not topic:
        return "Untitled Topic"
    if language == "zh" or _contains_cjk(topic):
        return topic
    return re.sub(r"\s+", " ", topic).strip().title()


def _topic_keywords(topic: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9/_-]{2,}", topic.lower())


def _paper_relevance_score(paper: dict[str, Any], keywords: list[str]) -> tuple[int, int, int, int, int]:
    haystack = " ".join(
        str(value)
        for value in [
            paper.get("title", ""),
            paper.get("abstract", ""),
            paper.get("venue", ""),
            paper.get("discipline", ""),
            paper.get("doi", ""),
        ]
    ).lower()
    keyword_hits = sum(1 for keyword in keywords if keyword and keyword in haystack)
    return (
        keyword_hits,
        int(bool(paper.get("verified"))),
        int(bool(paper.get("downloaded") or paper.get("local_path"))),
        int(paper.get("citation_count") or 0),
        int(paper.get("year") or 0),
    )


def _select_reference_papers(project_root: Path, topic: str, limit: int = 8) -> list[dict[str, Any]]:
    papers = load_paper_index(project_root)
    keywords = _topic_keywords(topic)
    ranked = sorted(papers, key=lambda paper: _paper_relevance_score(paper, keywords), reverse=True)
    title_matched = [
        paper
        for paper in ranked
        if any(keyword in str(paper.get("title", "")).lower() for keyword in keywords)
    ]
    if title_matched:
        return title_matched[:limit]

    matched = [paper for paper in ranked if _paper_relevance_score(paper, keywords)[0] >= 2]
    if matched:
        return matched[:limit]
    return []


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_experiment_plan(project_root: Path) -> dict[str, Any] | None:
    return _load_json_if_exists(project_root / "output" / "experiment-plan.json")


def _reference_label(index: int) -> str:
    return f"R{index}"


def _format_reference_entry(index: int, paper: dict[str, Any], language: str) -> str:
    authors = ", ".join((paper.get("authors") or [])[:3]) or ("未知作者" if language == "zh" else "Unknown authors")
    year = paper.get("year") or ("未标注年份" if language == "zh" else "n.d.")
    venue = paper.get("venue") or paper.get("source") or ("未知来源" if language == "zh" else "unknown venue")
    title = paper.get("title") or ("未命名论文" if language == "zh" else "Untitled paper")
    return f"[{_reference_label(index)}] {authors}. {title}. {venue}, {year}."


def _reference_catalog(papers: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for index, paper in enumerate(papers, start=1):
        catalog.append(
            {
                "label": _reference_label(index),
                "record_id": paper.get("record_id"),
                "title": paper.get("title"),
                "year": paper.get("year"),
                "source": paper.get("source"),
                "doi": paper.get("doi"),
                "url": paper.get("url") or paper.get("pdf_url"),
                "formatted": _format_reference_entry(index, paper, language),
            }
        )
    return catalog


def _reference_lines(references: list[dict[str, Any]]) -> list[str]:
    return [f"- {reference['formatted']}" for reference in references]


def _experiment_plan_snapshot(plan: dict[str, Any] | None, language: str) -> list[str]:
    if not plan:
        if language == "zh":
            return ["尚未生成实验方案，当前文档使用通用实验结构占位。"]
        return ["No experiment plan was found, so this draft uses a generic evaluation scaffold."]

    lines: list[str] = []
    for value in [plan.get("summary"), plan.get("objective")]:
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
    lines.extend((plan.get("hypotheses") or [])[:2])
    lines.extend((plan.get("checklist") or [])[:2])
    return lines


def _build_workspace_context(project_root: Path, topic: str, language: str) -> dict[str, Any]:
    references = _select_reference_papers(project_root, topic)
    experiment_plan = _load_experiment_plan(project_root)
    state = sync_project_state(project_root)
    return {
        "project_root": project_root,
        "topic": topic,
        "language": language,
        "title": _normalize_topic_title(topic, language),
        "papers": references,
        "references": _reference_catalog(references, language),
        "experiment_plan": experiment_plan,
        "experiment_snapshot": _experiment_plan_snapshot(experiment_plan, language),
        "state": state,
    }


def _proposal_sections(context: dict[str, Any]) -> list[dict[str, Any]]:
    title = context["title"]
    language = context["language"]
    reference_labels = [reference["label"] for reference in context["references"][:4]]
    evidence = "、".join(f"[{label}]" for label in reference_labels) if language == "zh" else ", ".join(
        f"[{label}]" for label in reference_labels
    )
    evidence_hint = evidence or (
        "在下一轮定向检索后补充已核验参考文献" if language == "zh" else "add verified references after a targeted literature search"
    )
    experiment_snapshot = context["experiment_snapshot"]

    if language == "zh":
        return [
            {
                "title": "一、研究背景与意义",
                "bullets": [
                    f"{title} 面向一个已有明确应用牵引但仍存在方法缺口的问题。",
                    f"当前知识库已沉淀 {len(context['papers'])} 篇高相关论文，可用于开题论证与技术路线对齐。",
                    f"本课题的必要性主要体现在研究价值、工程可落地性与后续成果转化空间 {evidence or '[R1]'}。",
                ],
            },
            {
                "title": "二、国内外研究现状",
                "bullets": [
                    "现有工作通常从基线性能、数据条件与部署成本三个维度展开比较。",
                    "建议在开题答辩中重点说明已有方法的边界条件，而不是只罗列名称。",
                    f"优先引用已核验或已下载论文建立文献主线 {evidence or '[R1], [R2]'}。",
                ],
            },
            {
                "title": "三、研究目标与核心问题",
                "bullets": [
                    f"目标 1：明确 {title} 的研究对象、输入输出与评价口径。",
                    "目标 2：构建可复现实验方案，并形成基线、消融与风险控制闭环。",
                    "目标 3：把可汇报、可答辩、可论文写作的材料统一到一套资产里。",
                ],
            },
            {
                "title": "四、研究方法与技术路线",
                "bullets": [
                    "技术路线建议按“问题定义 → 方法设计 → 实验验证 → 结果分析”组织。",
                    *experiment_snapshot,
                ],
            },
            {
                "title": "五、可行性分析与风险控制",
                "bullets": [
                    "数据与文献条件：已有知识库和论文索引可以支撑快速进入验证阶段。",
                    "实现条件：桌面端已具备论文检索、下载、实验规划与文档生成能力。",
                    "主要风险包括数据不足、基线不稳和实验成本超预算，需准备替代数据集与降级方案。",
                ],
            },
            {
                "title": "六、预期成果与创新点",
                "bullets": [
                    "形成完整开题报告、实验计划、阶段汇报材料与论文初稿。",
                    "创新点应落在问题建模、方法组合或验证方式，而不是泛化表述。",
                    "最终成果建议同时覆盖学术表达与工程落地两个层面。",
                ],
            },
            {
                "title": "七、进度安排",
                "bullets": [
                    "第 1-2 月：补齐文献与问题定义，冻结研究范围。",
                    "第 3-4 月：完成方法实现、基线与消融配置。",
                    "第 5-6 月：产出实验结果、汇报材料与论文初稿。",
                ],
            },
        ]

    return [
        {
            "title": "1. Background and Motivation",
            "bullets": [
                f"{title} targets a problem with clear practical demand and unresolved methodological gaps.",
                f"The workspace already contains {len(context['papers'])} highly relevant references for evidence-backed planning.",
                f"The proposal should justify novelty, feasibility, and downstream impact with explicit citations, or clearly note that you still need to {evidence_hint}.",
            ],
        },
        {
            "title": "2. Related Literature Snapshot",
            "bullets": [
                "Position prior work by baseline strength, data assumptions, and deployment cost.",
                "Highlight where current methods underperform instead of listing papers without synthesis.",
                f"Use verified or downloaded references as the primary citation chain, or clearly mark that you need to {evidence_hint}.",
            ],
        },
        {
            "title": "3. Objectives and Research Questions",
            "bullets": [
                f"Objective 1: define the scope, inputs, outputs, and evaluation protocol for {title}.",
                "Objective 2: lock a reproducible experimental workflow with baselines, ablations, and guardrails.",
                "Objective 3: keep proposal, presentation, and paper assets aligned in one workspace.",
            ],
        },
        {
            "title": "4. Methodology and Technical Route",
            "bullets": [
                "Organize the workflow as problem formulation → method design → experimental validation → analysis.",
                *experiment_snapshot,
            ],
        },
        {
            "title": "5. Feasibility and Risk Control",
            "bullets": [
                "Literature and data coverage already support an initial validation cycle.",
                "The desktop workflow now provides paper search, download, experiment planning, and draft generation.",
                "Main risks are missing data, unstable baselines, and budget overruns; prepare fallback datasets and simplified plans.",
            ],
        },
        {
            "title": "6. Expected Contributions",
            "bullets": [
                "Deliver a proposal draft, experiment plan, reporting deck, and paper starter in one workspace.",
                "Frame contributions around modeling, method integration, or validation rigor rather than broad claims.",
                "Keep academic and engineering value visible in the final narrative.",
            ],
        },
        {
            "title": "7. Timeline",
            "bullets": [
                "Months 1-2: complete literature consolidation and freeze the scope.",
                "Months 3-4: implement the method and run baseline plus ablation studies.",
                "Months 5-6: finalize results, presentation assets, and the paper draft.",
            ],
        },
    ]


def _render_proposal_markdown(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    lines = [f"# {payload['title']}", ""]
    lines.extend(
        [
            f"- Topic: {payload['topic']}",
            f"- Language: {payload['language']}",
            f"- Generated asset: `{markdown_path}`",
            f"- Structured payload: `{json_path}`",
            "",
            payload["summary"],
            "",
        ]
    )

    for section in payload["sections"]:
        lines.append(f"## {section['title']}")
        lines.append(_render_markdown_list(section["bullets"]))
        lines.append("")

    lines.extend(["## Reference Candidates", *_reference_lines(payload["references"]), ""])
    return "\n".join(lines)


def _build_proposal_payload(context: dict[str, Any]) -> dict[str, Any]:
    language = context["language"]
    summary = (
        f"围绕 {context['title']} 自动生成开题报告草稿，已联动论文证据、实验规划和阶段安排。"
        if language == "zh"
        else f"Auto-generated proposal starter for {context['title']} with linked literature, experiment planning, and milestones."
    )
    return {
        "kind": "proposal",
        "title": f"{'开题报告草稿' if language == 'zh' else 'Research Proposal Draft'}：{context['title']}",
        "topic": context["topic"],
        "language": language,
        "summary": summary,
        "sections": _proposal_sections(context),
        "references": context["references"],
        "experiment_plan_available": bool(context["experiment_plan"]),
    }


def _generate_proposal(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    topic = args.topic.strip()
    if not topic:
        raise ValueError("Proposal topic cannot be empty.")

    language = _resolve_language(topic, args.language)
    context = _build_workspace_context(project_root, topic, language)
    payload = _build_proposal_payload(context)
    markdown_path = project_root / "drafts" / "proposal-draft.md"
    json_path = project_root / "output" / "proposal-draft.json"

    _write_text(markdown_path, _render_proposal_markdown(payload, markdown_path, json_path))
    _write_text(json_path, _render_json_block(payload))

    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "artifact": payload,
        "state": state,
    }


_SCENARIO_DESCRIPTIONS = {
    "proposal_review": {
        "zh": "开题答辩/论文评审场景，面向导师和评审委员会，重点展示研究动机、方法创新性和可行性",
        "en": "Proposal defense / thesis review, targeting advisors and review committee, emphasizing research motivation, methodological novelty, and feasibility",
    },
    "lab_update": {
        "zh": "组会/阶段汇报场景，面向课题组成员，重点展示进展、遇到的困难和下一步计划",
        "en": "Lab meeting / progress update, targeting research group members, emphasizing progress, blockers, and next steps",
    },
    "conference": {
        "zh": "学术会议报告场景，面向同行研究者，重点展示方法细节、实验结果和贡献",
        "en": "Academic conference talk, targeting peer researchers, emphasizing method details, experimental results, and contributions",
    },
}


def _parse_json_response(raw: str) -> Any | None:
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    brace = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if brace:
        text = brace.group(0)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(text)
    except Exception:
        return None


def _make_llm_caller(project_root: Path) -> Callable[[str], str | None] | None:
    try:
        from tools.writing_enhancer import _load_llm_config, _call_model
        llm_chain = _load_llm_config(project_root, fallback_chain=True)
    except Exception:
        return None
    if not llm_chain:
        return None

    def _call(prompt: str) -> str | None:
        configs = llm_chain if isinstance(llm_chain, list) else [llm_chain]
        for cfg in configs:
            try:
                result = _call_model(cfg, prompt, "en")
                if result:
                    return result.strip()
            except Exception:
                continue
        return None
    return _call


def _generate_outline_llm(context: dict, deck_type: str, llm_call: Callable[[str], str | None]) -> list[dict] | None:
    lang = context["language"]
    scenario = _SCENARIO_DESCRIPTIONS.get(deck_type, _SCENARIO_DESCRIPTIONS["proposal_review"])
    scenario_text = scenario.get(lang, scenario["en"])
    paper_count = len(context.get("papers") or [])
    experiment_snapshot = "\n".join(context.get("experiment_snapshot") or [])[:600]
    references = context.get("references") or []
    ref_brief = "\n".join(f"- {r['label']}: {r.get('formatted', '')}" for r in references[:6])[:500]

    prompt = f"""You are an expert academic presentation designer. Generate a slide deck outline.

Topic: {context['title']}
Scenario: {scenario_text}
Language: {lang}
Reference papers available: {paper_count}
Key references:
{ref_brief or '(none yet)'}

Experiment plan summary:
{experiment_snapshot or '(no experiment plan yet)'}

Generate 8-15 slides as a JSON array. Each element must have:
- "title": slide title (in {"Chinese" if lang == "zh" else "English"})
- "layout": one of "titleOnly", "titleAndContent", "twoColumn", "sectionDivider", "imageCaption", "blank"
- "bullet_hints": 3-6 brief hints for bullet points
- "notes_hint": brief speaker notes hint

For "twoColumn" slides, also add "left_hint" and "right_hint" (arrays of 2-3 hints each).
For "imageCaption" slides, add "image_description" describing what visual to show.

The first slide should be "titleOnly" (cover). Include at least one "sectionDivider" to break the flow.
Tailor the structure to the scenario described above.

Output ONLY the JSON array. No code fences, no commentary."""

    raw = llm_call(prompt)
    if not raw:
        return None
    parsed = _parse_json_response(raw)
    if isinstance(parsed, list) and len(parsed) >= 5:
        return parsed
    return None


def _generate_design_guide(context: dict, deck_type: str, outline: list[dict], llm_call: Callable[[str], str | None]) -> str:
    scenario = _SCENARIO_DESCRIPTIONS.get(deck_type, _SCENARIO_DESCRIPTIONS["proposal_review"])
    titles = ", ".join(s.get("title", "") for s in outline[:6])
    prompt = f"""You are a presentation design advisor. Provide a concise design guide (3-5 bullet points) for this deck.

Scenario: {scenario.get(context['language'], scenario['en'])}
Topic: {context['title']}
Slide titles: {titles}

Cover: narrative arc, tone/formality, key emphasis areas, consistent terminology, what to repeat.
Keep under 150 words. Output plain text only."""

    result = llm_call(prompt)
    return result or "Academic tone. Consistent terminology. Progressive depth. Clear transitions."


def _generate_slide_content_llm(
    context: dict, slide_outline: dict, index: int, total: int,
    design_guide: str, llm_call: Callable[[str], str | None],
) -> dict | None:
    lang = context["language"]
    references = context.get("references") or []
    ref_brief = ", ".join(r["label"] for r in references[:5])[:300]
    experiment_snapshot = "\n".join(context.get("experiment_snapshot") or [])[:400]

    prompt = f"""Generate detailed content for slide {index + 1} of {total} in a research presentation.

Deck topic: {context['title']}
Design guide: {design_guide}
Language: {"Chinese" if lang == "zh" else "English"}

Slide outline:
{json.dumps(slide_outline, ensure_ascii=False)}

Available references: {ref_brief or '(none)'}
Experiment: {experiment_snapshot or '(none)'}

Return a JSON object with:
- "title": refined slide title (in {"Chinese" if lang == "zh" else "English"})
- "layout": same as input
- "bullets": 3-6 detailed bullet points (each under 120 chars)
- "left_column": (for twoColumn) 2-4 left bullets
- "right_column": (for twoColumn) 2-4 right bullets
- "notes": detailed speaker notes (200-500 chars, actionable guidance)
- "image_caption": (for imageCaption) brief caption

Output ONLY the JSON object. No code fences."""

    raw = llm_call(prompt)
    if not raw:
        return None
    parsed = _parse_json_response(raw)
    if isinstance(parsed, dict) and parsed.get("title"):
        for key in ("bullets", "left_column", "right_column"):
            if key not in parsed or not isinstance(parsed.get(key), list):
                parsed[key] = []
        if "notes" not in parsed:
            parsed["notes"] = ""
        if "layout" not in parsed:
            parsed["layout"] = "titleAndContent"
        return parsed
    return None


def _presentation_slides(context: dict[str, Any], deck_type: str) -> list[dict[str, Any]]:
    project_root = context.get("project_root")
    if isinstance(project_root, Path):
        llm_call = _make_llm_caller(project_root)
    else:
        llm_call = None

    if llm_call:
        try:
            print("[Presentation] Generating outline via LLM...")
            outline = _generate_outline_llm(context, deck_type, llm_call)
            if outline:
                print(f"[Presentation] Outline: {len(outline)} slides, generating content...")
                design_guide = _generate_design_guide(context, deck_type, outline, llm_call)
                slides = []
                for i, slide_outline in enumerate(outline):
                    content = _generate_slide_content_llm(
                        context, slide_outline, i, len(outline), design_guide, llm_call,
                    )
                    if content:
                        slides.append(content)
                if len(slides) >= len(outline) * 0.7:
                    print(f"[Presentation] LLM generated {len(slides)} slides successfully")
                    return slides
                print(f"[Presentation] Only {len(slides)}/{len(outline)} slides generated, falling back")
        except Exception as exc:
            print(f"[Presentation] LLM generation failed: {exc}")

    return _presentation_template_slides(context, deck_type)


def _presentation_template_slides(context: dict[str, Any], deck_type: str) -> list[dict[str, Any]]:
    language = context["language"]
    title = context["title"]
    reference_labels = [reference["label"] for reference in context["references"][:3]]
    citation_hint = "、".join(f"[{label}]" for label in reference_labels) if language == "zh" else ", ".join(
        f"[{label}]" for label in reference_labels
    )
    citation_hint = citation_hint or (
        "在下一轮定向检索后补充已核验参考文献" if language == "zh" else "add verified references after a targeted literature search"
    )
    experiment_snapshot = context["experiment_snapshot"][:3]

    if language == "zh":
        return [
            {
                "title": f"{title}：研究汇报",
                "layout": "titleOnly",
                "bullets": ["研究主题", "核心问题", f"汇报场景：{deck_type}"],
                "left_column": [], "right_column": [],
                "notes": "先用一句话说明问题价值，再交代本次汇报要解决什么决策。",
            },
            {
                "title": "问题背景与动机",
                "layout": "titleAndContent",
                "bullets": ["应用痛点", "现有方案不足", "为什么现在值得做"],
                "left_column": [], "right_column": [],
                "notes": "避免泛背景，直接落到任务、数据或场景约束。",
            },
            {
                "title": "文献与现状",
                "layout": "titleAndContent",
                "bullets": [
                    f"已选取 {len(context['papers'])} 篇高相关论文建立证据链",
                    "重点比较代表性方法、数据条件与评价指标",
                    f"文献线索：{citation_hint or '[R1], [R2]'}",
                ],
                "left_column": [], "right_column": [],
                "notes": "强调文献差距，而不是只做论文罗列。",
            },
            {
                "title": "研究缺口与切入点",
                "layout": "titleAndContent",
                "bullets": ["现有方法的瓶颈", "本研究的切入假设", "预期改进点"],
                "left_column": [], "right_column": [],
                "notes": "把创新点说成可验证命题，而不是口号。",
            },
            {
                "title": "方法与技术路线",
                "layout": "titleAndContent",
                "bullets": ["问题定义", "核心方法模块", "实现与评估链路"],
                "left_column": [], "right_column": [],
                "notes": "如果已经有方法图，后续可以替换成图示版本。",
            },
            {
                "title": "实验设计",
                "layout": "titleAndContent",
                "bullets": experiment_snapshot,
                "left_column": [], "right_column": [],
                "notes": "汇报时先讲评价口径，再讲基线和消融。",
            },
            {
                "title": "阶段产出与里程碑",
                "layout": "titleAndContent",
                "bullets": ["阶段 1：文献与问题冻结", "阶段 2：实现与验证", "阶段 3：汇报与论文"],
                "left_column": [], "right_column": [],
                "notes": "里程碑要能和导师/组会节奏对齐。",
            },
            {
                "title": "风险与备选方案",
                "layout": "titleAndContent",
                "bullets": ["数据风险", "实验风险", "时间与资源风险"],
                "left_column": [], "right_column": [],
                "notes": "给出至少一个降级方案，体现计划稳健性。",
            },
            {
                "title": "预期贡献",
                "layout": "titleAndContent",
                "bullets": ["方法贡献", "实验贡献", "应用或工程贡献"],
                "left_column": [], "right_column": [],
                "notes": "如果用于开题答辩，贡献一定要和可验证结果绑定。",
            },
            {
                "title": "需要的反馈",
                "layout": "titleAndContent",
                "bullets": ["问题定义是否聚焦", "实验路线是否合理", "优先级如何调整"],
                "left_column": [], "right_column": [],
                "notes": "最后一页明确希望导师或评审给什么反馈。",
            },
        ]

    return [
        {
            "title": f"{title}: research update",
            "layout": "titleOnly",
            "bullets": ["Topic", "Core question", f"Deck mode: {deck_type}"],
            "left_column": [], "right_column": [],
            "notes": "Start with the decision this talk should unblock.",
        },
        {
            "title": "Motivation",
            "layout": "titleAndContent",
            "bullets": ["Pain point", "Limitations of current practice", "Why this matters now"],
            "left_column": [], "right_column": [],
            "notes": "Keep this concrete and scenario-driven.",
        },
        {
            "title": "Literature Snapshot",
            "layout": "titleAndContent",
            "bullets": [
                f"Selected {len(context['papers'])} high-relevance papers for the evidence chain",
                "Compare methods, data assumptions, and metrics",
                f"Primary citation thread: {citation_hint}",
            ],
            "left_column": [], "right_column": [],
            "notes": "Synthesize the gap; do not just list papers.",
        },
        {
            "title": "Research Gap",
            "layout": "titleAndContent",
            "bullets": ["Observed bottlenecks", "Working hypothesis", "Why the gap is actionable"],
            "left_column": [], "right_column": [],
            "notes": "Turn novelty into a testable claim.",
        },
        {
            "title": "Method Overview",
            "layout": "titleAndContent",
            "bullets": ["Problem formulation", "Core modules", "Evaluation workflow"],
            "left_column": [], "right_column": [],
            "notes": "A diagram can replace this bullet slide later.",
        },
        {
            "title": "Experiment Plan",
            "layout": "titleAndContent",
            "bullets": experiment_snapshot,
            "left_column": [], "right_column": [],
            "notes": "Lead with evaluation protocol, then baselines and ablations.",
        },
        {
            "title": "Milestones",
            "layout": "titleAndContent",
            "bullets": ["Phase 1: freeze scope", "Phase 2: implement and validate", "Phase 3: report and write"],
            "left_column": [], "right_column": [],
            "notes": "Map milestones to advisor or lab review cadence.",
        },
        {
            "title": "Risks and Fallbacks",
            "layout": "titleAndContent",
            "bullets": ["Data risk", "Validation risk", "Time and compute risk"],
            "left_column": [], "right_column": [],
            "notes": "Show at least one credible fallback path.",
        },
        {
            "title": "Expected Contributions",
            "layout": "titleAndContent",
            "bullets": ["Method contribution", "Experimental contribution", "Engineering or application value"],
            "left_column": [], "right_column": [],
            "notes": "Tie each claim to evidence you can actually produce.",
        },
        {
            "title": "Feedback Needed",
            "layout": "titleAndContent",
            "bullets": ["Scope check", "Evaluation check", "Priority alignment"],
            "left_column": [], "right_column": [],
            "notes": "End by asking for precise review input.",
        },
    ]


def _render_presentation_markdown(payload: dict[str, Any], markdown_path: Path, json_path: Path, html_path: Path) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"- Topic: {payload['topic']}",
        f"- Language: {payload['language']}",
        f"- Deck mode: {payload['deck_type']}",
        f"- Slide markdown: `{markdown_path}`",
        f"- Slide HTML: `{html_path}`",
        f"- Structured payload: `{json_path}`",
        "",
        payload["summary"],
        "",
    ]
    for index, slide in enumerate(payload["slides"], start=1):
        lines.extend(
            [
                f"## Slide {index}: {slide['title']}",
                _render_markdown_list(slide["bullets"]),
                "",
                f"**Speaker notes**: {slide['notes']}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_presentation_html(payload: dict[str, Any]) -> str:
    slide_sections = []
    for index, slide in enumerate(payload["slides"], start=1):
        layout = slide.get("layout", "titleAndContent")
        bullets = "".join(f"<li>{html.escape(item)}</li>" for item in slide.get("bullets") or [])
        left_col = slide.get("left_column") or []
        right_col = slide.get("right_column") or []
        notes_text = html.escape(slide.get("notes") or "")

        if layout == "twoColumn" and (left_col or right_col):
            left_html = "".join(f"<li>{html.escape(item)}</li>" for item in left_col)
            right_html = "".join(f"<li>{html.escape(item)}</li>" for item in right_col)
            body_html = f'<div class="two-col"><div class="col"><ul>{left_html}</ul></div><div class="col"><ul>{right_html}</ul></div></div>'
        elif layout == "sectionDivider":
            body_html = f'<div class="section-subtitle">{html.escape(", ".join(slide.get("bullets", [])[:2]))}</div>'
        elif layout == "titleOnly":
            body_html = f'<div class="cover-subtitle">{html.escape(", ".join(slide.get("bullets", [])[:3]))}</div>'
        else:
            body_html = f"<ul>{bullets}</ul>"

        img_caption = slide.get("image_caption")
        if img_caption:
            body_html += f'<div class="img-caption">{html.escape(img_caption)}</div>'

        layout_class = f"slide layout-{layout}"
        slide_sections.append(
            f"""
            <section class="{layout_class}">
              <div class="slide-index">{index:02d}</div>
              <h2>{html.escape(slide['title'])}</h2>
              {body_html}
              <div class="notes"><strong>Notes:</strong> {notes_text}</div>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="{payload['language']}">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(payload['title'])}</title>
    <style>
      :root {{
        color-scheme: light;
        font-family: "Segoe UI", Arial, sans-serif;
        background: #0f172a;
        color: #e2e8f0;
      }}
      body {{
        margin: 0;
        padding: 32px;
        background:
          radial-gradient(circle at top right, rgba(59,130,246,0.22), transparent 30%),
          linear-gradient(180deg, #111827, #020617);
      }}
      .deck {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        gap: 20px;
      }}
      .slide {{
        min-height: 420px;
        border-radius: 20px;
        padding: 24px;
        background: rgba(15, 23, 42, 0.88);
        border: 1px solid rgba(148, 163, 184, 0.2);
        box-shadow: 0 20px 45px rgba(0, 0, 0, 0.28);
      }}
      .slide-index {{
        display: inline-flex;
        width: 42px;
        height: 42px;
        border-radius: 999px;
        align-items: center;
        justify-content: center;
        background: rgba(59, 130, 246, 0.16);
        color: #93c5fd;
        font-weight: 700;
      }}
      h1 {{
        margin: 0 0 10px;
        font-size: 32px;
      }}
      h2 {{
        margin: 16px 0 12px;
        font-size: 24px;
      }}
      .summary {{
        margin: 0 0 28px;
        color: #cbd5e1;
        max-width: 960px;
        line-height: 1.7;
      }}
      ul {{
        margin: 0;
        padding-left: 22px;
        line-height: 1.8;
      }}
      .notes {{
        margin-top: 18px;
        padding: 14px 16px;
        border-radius: 14px;
        background: rgba(148, 163, 184, 0.08);
        color: #cbd5e1;
        line-height: 1.6;
      }}
      .two-col {{ display: flex; gap: 24px; }}
      .two-col .col {{ flex: 1; }}
      .layout-sectionDivider {{
        background: rgba(37, 99, 235, 0.15);
        border-color: rgba(59, 130, 246, 0.4);
      }}
      .layout-sectionDivider h2 {{ text-align: center; font-size: 32px; }}
      .layout-titleOnly h2 {{ text-align: center; font-size: 32px; margin-top: 40px; }}
      .section-subtitle {{ text-align: center; color: #93c5fd; margin-top: 16px; font-size: 18px; }}
      .cover-subtitle {{ text-align: center; color: #cbd5e1; margin-top: 24px; font-size: 18px; }}
      .img-caption {{ text-align: center; color: #94a3b8; font-style: italic; margin-top: 12px; }}
    </style>
  </head>
  <body>
    <h1>{html.escape(payload['title'])}</h1>
    <p class="summary">{html.escape(payload['summary'])}</p>
    <main class="deck">
      {''.join(slide_sections)}
    </main>
  </body>
</html>
"""


def _build_presentation_payload(context: dict[str, Any], deck_type: str) -> dict[str, Any]:
    language = context["language"]
    summary = (
        f"已生成面向 {deck_type} 场景的 10 页汇报 deck，可直接用于组会、开题或阶段答辩准备。"
        if language == "zh"
        else f"Generated a 10-slide reporting deck for {deck_type}, ready for advisor review, lab updates, or proposal defense."
    )
    return {
        "kind": "presentation",
        "title": f"{'汇报PPT大纲' if language == 'zh' else 'Research Presentation Deck'}：{context['title']}",
        "topic": context["topic"],
        "language": language,
        "deck_type": deck_type,
        "summary": summary,
        "slides": _presentation_slides(context, deck_type),
        "references": context["references"],
    }


def _generate_presentation(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    topic = args.topic.strip()
    if not topic:
        raise ValueError("Presentation topic cannot be empty.")

    language = _resolve_language(topic, args.language)
    context = _build_workspace_context(project_root, topic, language)
    payload = _build_presentation_payload(context, args.deck_type)
    markdown_path = project_root / "output" / "research-presentation.md"
    json_path = project_root / "output" / "research-presentation.json"
    html_path = project_root / "output" / "research-presentation.html"

    _write_text(markdown_path, _render_presentation_markdown(payload, markdown_path, json_path, html_path))
    _write_text(json_path, _render_json_block(payload))
    _write_text(html_path, _render_presentation_html(payload))

    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "html_path": str(html_path),
        "artifact": payload,
        "state": state,
    }


def _paper_sections(context: dict[str, Any], paper_type: str) -> list[dict[str, Any]]:
    language = context["language"]
    title = context["title"]
    reference_labels = [reference["label"] for reference in context["references"][:4]]
    citations = "、".join(f"[{label}]" for label in reference_labels) if language == "zh" else ", ".join(
        f"[{label}]" for label in reference_labels
    )
    citations = citations or (
        "待通过定向检索补充已核验参考文献" if language == "zh" else "verified references to be added after targeted search"
    )
    experiment_snapshot = context["experiment_snapshot"]

    if language == "zh":
        return [
            {
                "title": "摘要",
                "content": [
                    f"本文围绕 {title} 展开，目标是在明确问题定义的基础上提出可验证的方法路线。",
                    "当前版本为论文初稿骨架，重点锁定研究动机、方法结构和实验验证逻辑。",
                ],
            },
            {
                "title": "1. 引言",
                "content": [
                    f"{title} 对应的问题具有明确应用需求，但现有方案在泛化、效率或鲁棒性方面仍有不足。",
                    f"引言应使用经过筛选的文献建立问题背景与研究缺口 {citations or '[R1], [R2]'}。",
                ],
            },
            {
                "title": "2. 相关工作",
                "content": [
                    "建议分成任务定义、代表性方法和评估协议三条主线组织相关工作。",
                    "每个小节最后给出与本文方案的差异点，而不是停留在罗列式综述。",
                ],
            },
            {
                "title": "3. 方法",
                "content": [
                    f"方法部分应完整描述 {title} 的输入输出、核心模块和训练/推理流程。",
                    "如果已有系统框图，后续可替换本节中的文字占位内容。",
                ],
            },
            {
                "title": "4. 实验设计",
                "content": experiment_snapshot,
            },
            {
                "title": "5. 预期结果与分析框架",
                "content": [
                    "本节当前保留为预期结果模板，后续应替换为真实表格、图形和误差分析。",
                    "至少准备主结果、消融结果、成本对比和失败案例分析四类材料。",
                ],
            },
            {
                "title": "6. 讨论与局限",
                "content": [
                    "明确方法适用边界、潜在偏差来源与后续扩展方向。",
                    "避免把尚未验证的假设写成既成事实。",
                ],
            },
            {
                "title": "7. 结论",
                "content": [
                    f"总结 {title} 的问题价值、方法亮点与实验验证计划。",
                    f"如果目标是 {paper_type} 型论文，需要在篇幅与贡献密度之间做好取舍。",
                ],
            },
        ]

    return [
        {
            "title": "Abstract",
            "content": [
                f"This draft studies {title} and frames a paper-ready narrative around the problem, method, and validation plan.",
                "The current version is a scaffold: it prioritizes structure and evidence placeholders over final prose.",
            ],
        },
        {
            "title": "1. Introduction",
            "content": [
                f"{title} addresses a relevant problem where current methods still face gaps in generalization, efficiency, or robustness.",
                f"Use {citations} to motivate the gap and research question.",
            ],
        },
        {
            "title": "2. Related Work",
            "content": [
                "Organize prior work by task definition, representative methods, and evaluation protocol.",
                "End each subsection by clarifying how the present approach differs from existing work.",
            ],
        },
        {
            "title": "3. Method",
            "content": [
                f"Describe the inputs, outputs, core modules, and training or inference pipeline for {title}.",
                "Replace the placeholder narrative with figures or pseudocode once the implementation stabilizes.",
            ],
        },
        {
            "title": "4. Experimental Design",
            "content": experiment_snapshot,
        },
        {
            "title": "5. Expected Results and Analysis Plan",
            "content": [
                "This section intentionally remains a reporting template until real results are available.",
                "Prepare tables for main results, ablations, cost trade-offs, and failure cases.",
            ],
        },
        {
            "title": "6. Discussion and Limitations",
            "content": [
                "State boundary conditions, likely bias sources, and follow-up directions explicitly.",
                "Do not turn unverified assumptions into claims.",
            ],
        },
        {
            "title": "7. Conclusion",
            "content": [
                f"Summarize the value, method, and evaluation plan for {title}.",
                f"If the target is a {paper_type} paper, balance contribution density against page budget.",
            ],
        },
    ]


def _render_paper_markdown(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"- Topic: {payload['topic']}",
        f"- Language: {payload['language']}",
        f"- Paper type: {payload['paper_type']}",
        f"- Draft path: `{markdown_path}`",
        f"- Structured payload: `{json_path}`",
        "",
        payload["summary"],
        "",
    ]
    for section in payload["sections"]:
        lines.append(f"## {section['title']}")
        lines.append("")
        for paragraph in section["content"]:
            lines.append(paragraph)
            lines.append("")

    lines.extend(["## Reference Candidates", *_reference_lines(payload["references"]), ""])
    return "\n".join(lines)


def _build_paper_payload(context: dict[str, Any], paper_type: str) -> dict[str, Any]:
    language = context["language"]
    summary = (
        f"已生成 {paper_type} 风格的论文初稿骨架，可继续替换为真实结果、图表和引用细节。"
        if language == "zh"
        else f"Generated a {paper_type} paper starter that can now be refined with real results, figures, and precise citations."
    )
    return {
        "kind": "paper",
        "title": f"{'论文初稿' if language == 'zh' else 'Research Paper Draft'}：{context['title']}",
        "topic": context["topic"],
        "language": language,
        "paper_type": paper_type,
        "summary": summary,
        "sections": _paper_sections(context, paper_type),
        "references": context["references"],
        "experiment_plan_available": bool(context["experiment_plan"]),
    }


def _generate_paper_draft(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    return generate_paper_package(
        project_root=project_root,
        topic=args.topic,
        language=args.language,
        paper_type=args.paper_type,
        target_words=getattr(args, "target_words", None),
    )


def _generate_paper_from_project(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    project_context = analyze_project_for_paper(
        workspace_root=project_root,
        source_project_path=args.source_project,
        topic=args.topic,
    )
    result = generate_paper_package(
        project_root=project_root,
        topic=args.topic,
        language=args.language,
        paper_type=args.paper_type,
        project_context=project_context,
        target_words=getattr(args, "target_words", None),
    )
    result["project_analysis_path"] = project_context["analysis_path"]
    result["project_analysis_json_path"] = project_context["analysis_json_path"]
    return result


def _generate_literature_review(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    return generate_literature_review(
        topic=args.topic,
        project_root=project_root,
        language=args.language,
    )


def _analyze_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    return analyze_research_capabilities(project_root=project_root)


def _answer_research_question(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    return answer_research_question(
        question=args.question,
        project_root=project_root,
        language=args.language,
    )


def _ensure_export_source(args: argparse.Namespace, project_root: Path) -> None:
    if args.source:
        return

    artifact = args.artifact
    if artifact == "paper" and args.topic:
        generate_paper_package(
            project_root=project_root,
            topic=args.topic,
            language=args.language,
            paper_type=args.paper_type,
            target_words=getattr(args, "target_words", None),
        )
    elif artifact == "proposal" and args.topic:
        _generate_proposal(
            argparse.Namespace(
                project_root=str(project_root),
                topic=args.topic,
                language=args.language,
            )
        )
    elif artifact == "literature_review" and args.topic:
        generate_literature_review(
            topic=args.topic,
            project_root=project_root,
            language=args.language,
        )
    elif artifact == "research_answer" and args.question:
        answer_research_question(
            question=args.question,
            project_root=project_root,
            language=args.language,
        )
    elif artifact == "presentation" and args.topic:
        _generate_presentation(
            argparse.Namespace(
                project_root=str(project_root),
                topic=args.topic,
                language=args.language,
                deck_type=args.deck_type,
            )
        )


def _export_docx(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    _ensure_export_source(args, project_root)
    export_payload = export_markdown_to_docx(
        project_root=project_root,
        artifact=args.artifact,
        source=args.source,
        output_path=args.output,
        style=getattr(args, "docx_style", "default"),
    )
    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "artifact": export_payload,
        "state": state,
    }


def _export_pptx(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    if not args.source and args.topic:
        _generate_presentation(
            argparse.Namespace(
                project_root=str(project_root),
                topic=args.topic,
                language=args.language,
                deck_type=args.deck_type,
            )
        )
    export_payload = export_presentation_to_pptx(
        project_root=project_root,
        source=args.source,
        output_path=args.output,
    )
    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "artifact": export_payload,
        "state": state,
    }


def _analyze_results(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    results_dir = Path(args.results_dir).resolve() if args.results_dir else project_root
    find_result_files, load_results, compute_metrics_summary, format_results_table = _data_analyzer_api()

    discovered = find_result_files(results_dir)
    if not discovered:
        return {
            "project_root": str(project_root),
            "results_dir": str(results_dir),
            "files_found": 0,
            "summaries": {},
            "tables": [],
        }

    summaries = {}
    tables = []
    for result_path in discovered:
        try:
            df = load_results(result_path)
        except Exception:
            continue
        key = str(result_path.relative_to(results_dir))
        summary = compute_metrics_summary(df, group_by=args.group_by)
        summaries[key] = summary
        table_str = format_results_table(df, fmt=args.table_format, bold_best=True,
                                          caption=f"Results from {result_path.stem}",
                                          label=f"tab:{result_path.stem}")
        tables.append({"source": key, "table": table_str})

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps({"summaries": summaries, "tables": tables}, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

    return {
        "project_root": str(project_root),
        "results_dir": str(results_dir),
        "files_found": len(discovered),
        "summaries": summaries,
        "tables": tables,
    }


def _generate_figures(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    results_dir = Path(args.results_dir).resolve() if args.results_dir else project_root
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (project_root / "output" / "figures")
    language = getattr(args, "language", "auto")
    resolved_language = "zh" if language == "auto" else language
    auto_figures_from_results, generate_figure_inventory = _figure_generator_api()

    figures = auto_figures_from_results(results_dir, output_dir, language=resolved_language)
    inventory_path = project_root / "drafts" / "figure-inventory.md"
    generate_figure_inventory(figures, inventory_path, language=resolved_language)

    return {
        "project_root": str(project_root),
        "results_dir": str(results_dir),
        "output_dir": str(output_dir),
        "language": resolved_language,
        "figures_generated": len(figures),
        "inventory_path": str(inventory_path),
        "figures": figures,
    }


def _plan_experiment(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    topic = args.topic.strip()
    if not topic:
        raise ValueError("Experiment topic cannot be empty.")

    domain = _infer_experiment_domain(topic, args.domain)
    method_name = (args.method_name or "Proposed Method").strip() or "Proposed Method"
    paper_count = len(load_paper_index(project_root))

    if domain == "cs_ai":
        plan = _build_cs_plan(topic, method_name, paper_count)
    elif domain == "biomedicine":
        plan = _build_biomedicine_plan(topic, method_name, paper_count)
    else:
        plan = _build_general_plan(topic, method_name, paper_count)

    markdown_path = project_root / "drafts" / "experiment-plan.md"
    json_path = project_root / "output" / "experiment-plan.json"
    markdown = _render_experiment_markdown(plan, markdown_path, json_path)

    _write_text(markdown_path, markdown)
    _write_text(json_path, _render_json_block(plan))

    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "plan": plan,
        "state": state,
    }


def _search(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    results = auto_search(args.query, discipline=args.discipline, limit=args.limit)

    downloaded_count = 0
    if args.download:
        papers_dir = project_root / "papers"
        papers_dir.mkdir(parents=True, exist_ok=True)
        for paper in results:
            local_path = auto_download(paper, output_dir=str(papers_dir))
            if local_path:
                paper["local_path"] = local_path
                paper["downloaded"] = True
                downloaded_count += 1

    merged_records = register_search_results(
        results,
        project_root=project_root,
        discipline=args.discipline,
        query=args.query,
    )
    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)

    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "query": args.query,
        "discipline": args.discipline,
        "result_count": len(results),
        "downloaded_count": downloaded_count,
        "indexed_count": len(merged_records),
        "results": results,
        "state": state,
    }


def _download(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    papers = load_paper_index(project_root)
    target = next((paper for paper in papers if paper.get("record_id") == args.record_id), None)
    if not target:
        raise ValueError(f"Paper with record_id '{args.record_id}' was not found in the paper index.")

    papers_dir = project_root / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    local_path = auto_download(target, output_dir=str(papers_dir))
    if not local_path:
        raise RuntimeError("Unable to download a PDF for the selected paper.")

    target["local_path"] = local_path
    target["downloaded"] = True
    save_paper_index(papers, project_root)

    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "paper": target,
        "state": state,
    }


def _crawl_paper_content(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    return crawl_paper_content(args.record_id, project_root=project_root)


def _refresh(args: argparse.Namespace) -> dict[str, Any]:
    project_root = _resolve_project_root(args.project_root)
    state = sync_project_state(project_root)
    dashboard_path = build_dashboard(project_root)
    papers = load_paper_index(project_root)
    return {
        "project_root": str(project_root),
        "dashboard_path": str(dashboard_path),
        "paper_count": len(papers),
        "state": state,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research workspace bridge for SciPilot.")
    parser.add_argument("--project-root", dest="project_root", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search papers and update local research artifacts.")
    search_parser.add_argument("--project-root", dest="project_root", default=None)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--discipline", default="generic")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--download", action="store_true")
    search_parser.set_defaults(handler=_search)

    download_parser = subparsers.add_parser("download", help="Download a paper from the local paper index.")
    download_parser.add_argument("--project-root", dest="project_root", default=None)
    download_parser.add_argument("--record-id", required=True)
    download_parser.set_defaults(handler=_download)

    crawl_parser = subparsers.add_parser(
        "crawl-paper-content",
        help="Extract paper content from a local PDF or remote source and persist it into the workspace.",
    )
    crawl_parser.add_argument("--project-root", dest="project_root", default=None)
    crawl_parser.add_argument("--record-id", required=True)
    crawl_parser.set_defaults(handler=_crawl_paper_content)

    refresh_parser = subparsers.add_parser("refresh", help="Sync state and rebuild the paper dashboard.")
    refresh_parser.add_argument("--project-root", dest="project_root", default=None)
    refresh_parser.set_defaults(handler=_refresh)

    experiment_parser = subparsers.add_parser(
        "plan-experiment",
        help="Generate a structured experiment plan and save it into the workspace.",
    )
    experiment_parser.add_argument("--project-root", dest="project_root", default=None)
    experiment_parser.add_argument("--topic", required=True)
    experiment_parser.add_argument("--domain", choices=["auto", "cs_ai", "biomedicine", "general"], default="auto")
    experiment_parser.add_argument("--method-name", default="Proposed Method")
    experiment_parser.set_defaults(handler=_plan_experiment)

    proposal_parser = subparsers.add_parser(
        "generate-proposal",
        help="Generate a proposal draft from the current research workspace.",
    )
    proposal_parser.add_argument("--project-root", dest="project_root", default=None)
    proposal_parser.add_argument("--topic", required=True)
    proposal_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    proposal_parser.set_defaults(handler=_generate_proposal)

    presentation_parser = subparsers.add_parser(
        "generate-presentation",
        help="Generate a reporting deck outline and HTML slide deck.",
    )
    presentation_parser.add_argument("--project-root", dest="project_root", default=None)
    presentation_parser.add_argument("--topic", required=True)
    presentation_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    presentation_parser.add_argument(
        "--deck-type",
        choices=["proposal_review", "lab_update", "conference"],
        default="proposal_review",
    )
    presentation_parser.set_defaults(handler=_generate_presentation)

    paper_parser = subparsers.add_parser(
        "generate-paper-draft",
        help="Generate a paper draft scaffold from the current research workspace.",
    )
    paper_parser.add_argument("--project-root", dest="project_root", default=None)
    paper_parser.add_argument("--topic", required=True)
    paper_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    paper_parser.add_argument("--paper-type", choices=["general", "conference", "journal"], default="general")
    paper_parser.add_argument("--target-words", type=int, default=None)
    paper_parser.set_defaults(handler=_generate_paper_draft)

    project_paper_parser = subparsers.add_parser(
        "generate-paper-from-project",
        help="Analyze a local project and generate a paper package from project evidence plus workspace assets.",
    )
    project_paper_parser.add_argument("--project-root", dest="project_root", default=None)
    project_paper_parser.add_argument("--source-project", required=True)
    project_paper_parser.add_argument("--topic", required=True)
    project_paper_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    project_paper_parser.add_argument("--paper-type", choices=["general", "conference", "journal"], default="general")
    project_paper_parser.add_argument("--target-words", type=int, default=None)
    project_paper_parser.set_defaults(handler=_generate_paper_from_project)

    review_parser = subparsers.add_parser(
        "generate-literature-review",
        help="Generate a literature review draft and evidence matrix from the local paper index.",
    )
    review_parser.add_argument("--project-root", dest="project_root", default=None)
    review_parser.add_argument("--topic", required=True)
    review_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    review_parser.set_defaults(handler=_generate_literature_review)

    capability_parser = subparsers.add_parser(
        "analyze-capabilities",
        help="Audit whether the current workspace covers a research-assistant workflow.",
    )
    capability_parser.add_argument("--project-root", dest="project_root", default=None)
    capability_parser.set_defaults(handler=_analyze_capabilities)

    qa_parser = subparsers.add_parser(
        "answer-research-question",
        help="Answer a research question from indexed papers and crawled evidence.",
    )
    qa_parser.add_argument("--project-root", dest="project_root", default=None)
    qa_parser.add_argument("--question", required=True)
    qa_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    qa_parser.set_defaults(handler=_answer_research_question)

    export_docx_parser = subparsers.add_parser(
        "export-docx",
        help="Export a generated Markdown artifact to DOCX.",
    )
    export_docx_parser.add_argument("--project-root", dest="project_root", default=None)
    export_docx_parser.add_argument(
        "--artifact",
        choices=["paper", "proposal", "literature_review", "research_answer", "presentation"],
        default="paper",
    )
    export_docx_parser.add_argument("--source", default=None)
    export_docx_parser.add_argument("--output", default=None)
    export_docx_parser.add_argument("--topic", default=None)
    export_docx_parser.add_argument("--question", default=None)
    export_docx_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    export_docx_parser.add_argument("--paper-type", choices=["general", "conference", "journal"], default="general")
    export_docx_parser.add_argument("--target-words", type=int, default=None)
    export_docx_parser.add_argument("--docx-style", choices=["default", "thesis", "journal"], default="default")
    export_docx_parser.add_argument(
        "--deck-type",
        choices=["proposal_review", "lab_update", "conference"],
        default="proposal_review",
    )
    export_docx_parser.set_defaults(handler=_export_docx)

    export_pptx_parser = subparsers.add_parser(
        "export-pptx",
        help="Export the presentation artifact to PPTX.",
    )
    export_pptx_parser.add_argument("--project-root", dest="project_root", default=None)
    export_pptx_parser.add_argument("--source", default=None)
    export_pptx_parser.add_argument("--output", default=None)
    export_pptx_parser.add_argument("--topic", default=None)
    export_pptx_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    export_pptx_parser.add_argument(
        "--deck-type",
        choices=["proposal_review", "lab_update", "conference"],
        default="proposal_review",
    )
    export_pptx_parser.set_defaults(handler=_export_pptx)

    # --- Analyze results ---
    analyze_results_parser = subparsers.add_parser(
        "analyze-results",
        help="Discover and analyze experiment result files in a project directory.",
    )
    analyze_results_parser.add_argument("--project-root", dest="project_root", default=None)
    analyze_results_parser.add_argument("--results-dir", dest="results_dir", default=None,
                                        help="Directory to scan for result files (default: project root)")
    analyze_results_parser.add_argument("--group-by", dest="group_by", default=None,
                                        help="Column name to group results by (e.g. 'method')")
    analyze_results_parser.add_argument("--format", dest="table_format", choices=["latex", "markdown"], default="latex")
    analyze_results_parser.add_argument("--output", default=None)
    analyze_results_parser.set_defaults(handler=_analyze_results)

    # --- Generate figures ---
    gen_figures_parser = subparsers.add_parser(
        "generate-figures",
        help="Auto-generate figures from experiment result files.",
    )
    gen_figures_parser.add_argument("--project-root", dest="project_root", default=None)
    gen_figures_parser.add_argument("--results-dir", dest="results_dir", default=None,
                                    help="Directory containing result files (default: project root)")
    gen_figures_parser.add_argument("--output-dir", dest="output_dir", default=None,
                                    help="Directory for generated figures (default: output/figures)")
    gen_figures_parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    gen_figures_parser.set_defaults(handler=_generate_figures)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            result = args.handler(args)
    except Exception as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": str(error),
                    "logs": {
                        "stdout": stdout_buffer.getvalue(),
                        "stderr": stderr_buffer.getvalue(),
                    },
                },
                ensure_ascii=False,
                default=_json_default,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "success": True,
                "data": result,
                "logs": {
                    "stdout": stdout_buffer.getvalue(),
                    "stderr": stderr_buffer.getvalue(),
                },
            },
            ensure_ascii=False,
            default=_json_default,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
