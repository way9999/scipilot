"""文献全景分析工具
多源搜索论文 → 从摘要提取工具/方法/成果 → 生成汇总表格 + 方法分类图 → Markdown 报告
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.unified_search import auto_search
from tools.project_state import register_search_results, sync_project_state


# ── 关键词词典（用于从摘要中抽取工具/方法/指标/数据集） ───────────────

TOOL_KEYWORDS = {
    # Deep Learning Frameworks
    "pytorch", "tensorflow", "keras", "jax", "mxnet", "caffe", "paddlepaddle",
    "scikit-learn", "sklearn", "xgboost", "lightgbm", "catboost",
    # Simulation / Computation
    "gaussian", "vasp", "lammps", "gromacs", "amber", "comsol", "ansys",
    "matlab", "r language", "stata", "spss", "origin",
    # Bioinformatics
    "blast", "clustal", "autodock", "pymol", "rdkit", "openbabel",
    # NLP / Vision
    "huggingface", "spacy", "nltk", "opencv", "detectron",
    # General
    "python", "julia", "spark", "hadoop", "docker", "kubernetes",
}

METHOD_KEYWORDS = {
    # ML/DL methods
    "transformer", "attention mechanism", "self-attention", "cross-attention",
    "convolutional neural network", "cnn", "recurrent neural network", "rnn",
    "lstm", "gru", "graph neural network", "gnn", "generative adversarial",
    "gan", "variational autoencoder", "vae", "diffusion model",
    "reinforcement learning", "q-learning", "ppo", "dqn",
    "transfer learning", "fine-tuning", "pre-training", "few-shot",
    "zero-shot", "meta-learning", "contrastive learning",
    "federated learning", "knowledge distillation", "pruning", "quantization",
    "bert", "gpt", "llm", "large language model",
    "random forest", "svm", "support vector", "decision tree",
    "gradient boosting", "ensemble", "bagging", "stacking",
    "clustering", "k-means", "dbscan", "pca", "t-sne", "umap",
    "bayesian", "monte carlo", "markov chain",
    # Science methods
    "density functional theory", "dft", "molecular dynamics", "md simulation",
    "finite element", "fem", "computational fluid dynamics", "cfd",
    "first-principles", "ab initio", "machine learning potential",
    "high-throughput screening", "virtual screening",
    "crispr", "pcr", "western blot", "flow cytometry", "mass spectrometry",
    "x-ray diffraction", "xrd", "sem", "tem", "afm", "nmr",
    "regression analysis", "anova", "chi-square", "cox regression",
    "survival analysis", "meta-analysis", "systematic review",
}

METRIC_KEYWORDS = {
    "accuracy", "precision", "recall", "f1-score", "f1 score",
    "auc", "roc", "auroc", "auprc", "map", "ndcg", "bleu", "rouge",
    "perplexity", "fid", "inception score",
    "rmse", "mae", "mse", "mape", "r-squared", "r²",
    "sensitivity", "specificity", "p-value", "confidence interval",
    "ic50", "ec50", "ki", "kd", "binding affinity",
    "yield", "selectivity", "conversion", "efficiency",
    "throughput", "latency", "speedup",
}

DATASET_KEYWORDS = {
    "imagenet", "cifar", "mnist", "coco", "voc", "glue", "squad",
    "wikitext", "common crawl", "openwebtext", "pile",
    "pubmed", "mimic", "chembl", "zinc", "qm9", "pdbbind",
    "tdc", "moleculenet", "ogb", "materials project",
}


# ── 核心分析函数 ─────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Clean text for keyword matching."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _extract_keywords(text: str, keyword_set: set[str]) -> list[str]:
    """Extract matching keywords from text."""
    normalized = _normalize_text(text)
    found = []
    for kw in sorted(keyword_set, key=len, reverse=True):
        if kw in normalized:
            found.append(kw)
            # Remove to avoid substring double-matching
            normalized = normalized.replace(kw, " ")
    return found


def extract_paper_methods(paper: dict) -> dict[str, Any]:
    """Extract tools, methods, metrics, datasets from a paper's metadata.

    Returns a dict with keys: tools, methods, metrics, datasets, contribution.
    """
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")
    full_text = f"{title}. {abstract}"

    tools = _extract_keywords(full_text, TOOL_KEYWORDS)
    methods = _extract_keywords(full_text, METHOD_KEYWORDS)
    metrics = _extract_keywords(full_text, METRIC_KEYWORDS)
    datasets = _extract_keywords(full_text, DATASET_KEYWORDS)

    # Extract contribution: first sentence of abstract (heuristic)
    contribution = ""
    if abstract:
        sentences = re.split(r"(?<=[.!?])\s+", abstract.strip())
        # Try to find a sentence with result-like language
        result_patterns = [
            r"(?:achiev|obtain|reach|attain|outperform|surpass|state.of.the.art|sota|improv|demonstrate|show that|result)",
            r"(?:propos|introduc|present|develop|design|novel|new approach)",
        ]
        for pattern in result_patterns:
            for sent in sentences:
                if re.search(pattern, sent, re.IGNORECASE):
                    contribution = sent.strip()
                    break
            if contribution:
                break
        if not contribution and sentences:
            contribution = sentences[0].strip()

    return {
        "paper_id": paper.get("record_id", ""),
        "title": title,
        "authors": paper.get("authors", []),
        "year": paper.get("year"),
        "venue": paper.get("venue", ""),
        "source": paper.get("source", ""),
        "doi": paper.get("doi", ""),
        "url": paper.get("url", ""),
        "tools": tools,
        "methods": methods,
        "metrics": metrics,
        "datasets": datasets,
        "contribution": contribution[:300] if contribution else "",
        "citation_count": paper.get("citation_count"),
    }


def analyze_landscape(
    topic: str,
    discipline: str = "generic",
    limit: int = 20,
    year: str | None = None,
    save: bool = True,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    """Full landscape analysis pipeline.

    1. Multi-source search
    2. Extract methods from each paper
    3. Build summary statistics
    4. Generate taxonomy + table

    Returns the full analysis payload.
    """
    root = Path(project_root).resolve()

    # 1. Search
    print(f"[landscape] Searching: '{topic}' (discipline={discipline}, limit={limit})")
    papers = auto_search(topic, discipline=discipline, limit=limit, year=year)
    print(f"[landscape] Found {len(papers)} papers")

    if save and papers:
        register_search_results(
            papers, project_root=root, discipline=discipline, query=topic
        )

    # 2. Extract method info from each paper
    analyses = [extract_paper_methods(p) for p in papers]

    # 3. Build statistics
    all_tools = Counter()
    all_methods = Counter()
    all_metrics = Counter()
    all_datasets = Counter()
    year_distribution: Counter[int] = Counter()
    venue_distribution: Counter[str] = Counter()
    methods_by_year: dict[int, Counter[str]] = defaultdict(Counter)

    for a in analyses:
        for t in a["tools"]:
            all_tools[t] += 1
        for m in a["methods"]:
            all_methods[m] += 1
        for mt in a["metrics"]:
            all_metrics[mt] += 1
        for d in a["datasets"]:
            all_datasets[d] += 1
        if a["year"]:
            year_distribution[a["year"]] += 1
            for m in a["methods"]:
                methods_by_year[a["year"]][m] += 1
        if a["venue"]:
            venue_distribution[a["venue"]] += 1

    # 4. Identify method clusters (group related methods)
    method_clusters = _cluster_methods(all_methods)

    # 5. Build result
    result = {
        "topic": topic,
        "discipline": discipline,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "paper_count": len(papers),
        "papers": analyses,
        "statistics": {
            "tools": dict(all_tools.most_common(20)),
            "methods": dict(all_methods.most_common(30)),
            "metrics": dict(all_metrics.most_common(15)),
            "datasets": dict(all_datasets.most_common(15)),
            "years": dict(sorted(year_distribution.items())),
            "venues": dict(venue_distribution.most_common(15)),
        },
        "method_clusters": method_clusters,
        "table_markdown": _build_summary_table(analyses),
        "mermaid_diagram": _build_mermaid_diagram(analyses, method_clusters, topic),
        "trend_summary": _build_trend_summary(methods_by_year),
    }

    return result


def _cluster_methods(method_counts: Counter) -> dict[str, list[str]]:
    """Group methods into high-level categories."""
    clusters: dict[str, list[str]] = {
        "Deep Learning": [],
        "Traditional ML": [],
        "Optimization": [],
        "NLP/Language": [],
        "Computer Vision": [],
        "Scientific Computing": [],
        "Statistical": [],
        "Experimental": [],
        "Other": [],
    }

    dl_terms = {"transformer", "attention mechanism", "self-attention", "cross-attention",
                "cnn", "convolutional neural network", "rnn", "recurrent neural network",
                "lstm", "gru", "gnn", "graph neural network", "gan", "generative adversarial",
                "vae", "variational autoencoder", "diffusion model",
                "transfer learning", "fine-tuning", "pre-training",
                "contrastive learning", "knowledge distillation", "pruning", "quantization",
                "federated learning"}
    trad_ml_terms = {"random forest", "svm", "support vector", "decision tree",
                     "gradient boosting", "ensemble", "bagging", "stacking",
                     "xgboost", "lightgbm", "catboost", "clustering", "k-means",
                     "dbscan", "pca", "t-sne", "umap"}
    opt_terms = {"reinforcement learning", "q-learning", "ppo", "dqn",
                 "bayesian", "monte carlo", "markov chain", "meta-learning"}
    nlp_terms = {"bert", "gpt", "llm", "large language model", "few-shot", "zero-shot",
                 "bleu", "rouge", "perplexity"}
    cv_terms = {"cnn", "convolutional neural network", "detectron", "opencv",
                "imagenet", "cifar", "coco", "voc"}
    sci_terms = {"density functional theory", "dft", "molecular dynamics", "md simulation",
                 "finite element", "fem", "computational fluid dynamics", "cfd",
                 "first-principles", "ab initio", "machine learning potential",
                 "high-throughput screening", "virtual screening"}
    stat_terms = {"regression analysis", "anova", "chi-square", "cox regression",
                  "survival analysis", "meta-analysis", "systematic review"}
    exp_terms = {"crispr", "pcr", "western blot", "flow cytometry", "mass spectrometry",
                 "x-ray diffraction", "xrd", "sem", "tem", "afm", "nmr"}

    for method, count in method_counts.items():
        placed = False
        for label, term_set in [
            ("Deep Learning", dl_terms), ("Traditional ML", trad_ml_terms),
            ("Optimization", opt_terms), ("NLP/Language", nlp_terms),
            ("Computer Vision", cv_terms), ("Scientific Computing", sci_terms),
            ("Statistical", stat_terms), ("Experimental", exp_terms),
        ]:
            if method in term_set:
                clusters[label].append(method)
                placed = True
                break
        if not placed:
            clusters["Other"].append(method)

    # Remove empty clusters
    return {k: v for k, v in clusters.items() if v}


def _build_summary_table(analyses: list[dict]) -> str:
    """Build a Markdown table summarizing all papers."""
    lines = [
        "| # | Paper | Year | Tools | Methods | Key Contribution |",
        "|---|-------|------|-------|---------|------------------|",
    ]

    for i, a in enumerate(analyses, 1):
        title = a["title"][:60] + ("..." if len(a["title"]) > 60 else "")
        authors = ", ".join(a["authors"][:2]) if a["authors"] else "—"
        year = str(a["year"]) if a["year"] else "—"
        tools = ", ".join(a["tools"][:3]) if a["tools"] else "—"
        methods = ", ".join(a["methods"][:3]) if a["methods"] else "—"
        contribution = a["contribution"][:80] + ("..." if len(a["contribution"]) > 80 else "")
        if not contribution:
            contribution = "—"

        lines.append(f"| {i} | **{title}** <br><sub>{authors}</sub> | {year} | {tools} | {methods} | {contribution} |")

    return "\n".join(lines)


def _build_mermaid_diagram(
    analyses: list[dict],
    clusters: dict[str, list[str]],
    topic: str,
) -> str:
    """Build a Mermaid flowchart showing methods taxonomy and relationships."""
    lines = ["graph TD"]
    safe_topic = re.sub(r"[^a-zA-Z0-9 ]", "", topic)[:40]
    lines.append(f'    ROOT["{safe_topic}<br/>Literature Landscape"]')

    node_id = 0

    # Method clusters
    for cluster_name, methods in clusters.items():
        node_id += 1
        cluster_id = f"C{node_id}"
        lines.append(f'    ROOT --> {cluster_id}["{cluster_name}"]')

        for method in methods[:6]:
            node_id += 1
            method_id = f"M{node_id}"
            display = method.title()
            lines.append(f'    {cluster_id} --> {method_id}["{display}"]')

    # Tools section
    all_tools = Counter()
    for a in analyses:
        for t in a["tools"]:
            all_tools[t] += 1

    if all_tools:
        node_id += 1
        tools_id = f"T{node_id}"
        lines.append(f'    ROOT --> {tools_id}["Tools & Frameworks"]')
        for tool, count in all_tools.most_common(8):
            node_id += 1
            tid = f"T{node_id}"
            display = tool.title()
            lines.append(f'    {tools_id} --> {tid}["{display} ({count})"]')

    # Metrics section
    all_metrics = Counter()
    for a in analyses:
        for m in a["metrics"]:
            all_metrics[m] += 1

    if all_metrics:
        node_id += 1
        metrics_id = f"E{node_id}"
        lines.append(f'    ROOT --> {metrics_id}["Evaluation Metrics"]')
        for metric, count in all_metrics.most_common(6):
            node_id += 1
            mid = f"E{node_id}"
            display = metric.upper() if len(metric) <= 5 else metric.title()
            lines.append(f'    {metrics_id} --> {mid}["{display} ({count})"]')

    # Style
    lines.append("")
    lines.append("    style ROOT fill:#6366f1,stroke:#4f46e5,color:#fff,stroke-width:2px")
    for cluster_name in clusters:
        pass  # Keep default styling for readability

    return "\n".join(lines)


def _build_trend_summary(methods_by_year: dict[int, Counter]) -> str:
    """Build a brief trend summary text."""
    if not methods_by_year:
        return "Insufficient temporal data for trend analysis."

    sorted_years = sorted(methods_by_year.keys())
    if len(sorted_years) < 2:
        year = sorted_years[0]
        top = methods_by_year[year].most_common(3)
        methods_str = ", ".join(m for m, _ in top)
        return f"Papers from {year} primarily use: {methods_str}."

    recent_year = sorted_years[-1]
    earlier_year = sorted_years[0]

    recent_top = set(m for m, _ in methods_by_year[recent_year].most_common(5))
    earlier_top = set(m for m, _ in methods_by_year[earlier_year].most_common(5))

    emerging = recent_top - earlier_top
    declining = earlier_top - recent_top

    parts = []
    if emerging:
        parts.append(f"Emerging methods ({recent_year}): {', '.join(sorted(emerging))}")
    if declining:
        parts.append(f"Less prominent since {earlier_year}: {', '.join(sorted(declining))}")

    recent_methods = methods_by_year[recent_year].most_common(3)
    parts.append(f"Most used in {recent_year}: {', '.join(m for m, _ in recent_methods)}")

    return ". ".join(parts) + "."


def generate_landscape_report(
    topic: str,
    discipline: str = "generic",
    limit: int = 20,
    year: str | None = None,
    save: bool = True,
    project_root: str | Path = ".",
    output_dir: str | Path | None = None,
) -> Path:
    """End-to-end: search → analyze → write Markdown report.

    Returns the path to the generated report file.
    """
    root = Path(project_root).resolve()
    result = analyze_landscape(
        topic, discipline=discipline, limit=limit, year=year,
        save=save, project_root=root,
    )

    # Build report markdown
    report_lines = [
        f"# Literature Landscape: {topic}",
        "",
        f"> Discipline: **{discipline}** | Papers analyzed: **{result['paper_count']}** | "
        f"Generated: {result['timestamp'][:10]}",
        "",
        "---",
        "",
        "## Summary Statistics",
        "",
        f"- **Papers found**: {result['paper_count']}",
        f"- **Unique methods identified**: {len(result['statistics']['methods'])}",
        f"- **Tools & frameworks mentioned**: {len(result['statistics']['tools'])}",
        f"- **Evaluation metrics used**: {len(result['statistics']['metrics'])}",
        f"- **Datasets referenced**: {len(result['statistics']['datasets'])}",
        "",
    ]

    # Top methods
    if result["statistics"]["methods"]:
        report_lines.append("### Top Methods")
        report_lines.append("")
        report_lines.append("| Method | Papers |")
        report_lines.append("|--------|--------|")
        for method, count in sorted(result["statistics"]["methods"].items(), key=lambda x: -x[1])[:15]:
            report_lines.append(f"| {method.title()} | {count} |")
        report_lines.append("")

    # Top tools
    if result["statistics"]["tools"]:
        report_lines.append("### Top Tools & Frameworks")
        report_lines.append("")
        report_lines.append("| Tool | Papers |")
        report_lines.append("|------|--------|")
        for tool, count in sorted(result["statistics"]["tools"].items(), key=lambda x: -x[1])[:10]:
            report_lines.append(f"| {tool.title()} | {count} |")
        report_lines.append("")

    # Trend
    report_lines.extend([
        "### Trends",
        "",
        result["trend_summary"],
        "",
    ])

    # Mermaid diagram
    report_lines.extend([
        "---",
        "",
        "## Method Taxonomy",
        "",
        "```mermaid",
        result["mermaid_diagram"],
        "```",
        "",
    ])

    # Paper-level table
    report_lines.extend([
        "---",
        "",
        "## Paper Details",
        "",
        result["table_markdown"],
        "",
    ])

    # Year distribution
    if result["statistics"]["years"]:
        report_lines.extend([
            "",
            "### Year Distribution",
            "",
            "| Year | Papers |",
            "|------|--------|",
        ])
        for yr, count in sorted(result["statistics"]["years"].items()):
            report_lines.append(f"| {yr} | {'█' * count} {count} |")
        report_lines.append("")

    # Venues
    if result["statistics"]["venues"]:
        report_lines.extend([
            "### Top Venues",
            "",
            "| Venue | Papers |",
            "|-------|--------|",
        ])
        for venue, count in sorted(result["statistics"]["venues"].items(), key=lambda x: -x[1])[:10]:
            report_lines.append(f"| {venue} | {count} |")
        report_lines.append("")

    # Footer
    report_lines.extend([
        "---",
        "",
        f"*Generated by SciPilot landscape analysis on {result['timestamp'][:10]}*",
    ])

    report_md = "\n".join(report_lines)

    # Write report
    if output_dir:
        dest = Path(output_dir)
    else:
        dest = root / "output"
    dest.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", "-", topic)[:50].strip("-")
    report_path = dest / f"landscape-{safe_name}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"[landscape] Report written to {report_path}")

    # Also save the raw JSON payload
    json_path = dest / f"landscape-{safe_name}.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return report_path


# ── CLI ──────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python tools/landscape_analysis.py <topic> [options]")
        print()
        print("Options:")
        print("  --discipline <code>  cs, bio, physics, chemistry, materials, energy, economics")
        print("  --limit <n>          Number of papers to analyze (default: 20)")
        print("  --year <range>       Year filter, e.g. 2020-2024")
        print("  --output <dir>       Output directory (default: output/)")
        print("  --no-save            Don't register papers in project index")
        return 1

    topic_parts = []
    discipline = "generic"
    limit = 20
    year = None
    output_dir = None
    save = True

    i = 0
    while i < len(argv):
        if argv[i] == "--discipline" and i + 1 < len(argv):
            discipline = argv[i + 1]
            i += 2
        elif argv[i] == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
            i += 2
        elif argv[i] == "--year" and i + 1 < len(argv):
            year = argv[i + 1]
            i += 2
        elif argv[i] == "--output" and i + 1 < len(argv):
            output_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--no-save":
            save = False
            i += 1
        else:
            topic_parts.append(argv[i])
            i += 1

    topic = " ".join(topic_parts)
    if not topic:
        print("Error: topic is required")
        return 1

    report_path = generate_landscape_report(
        topic, discipline=discipline, limit=limit, year=year,
        save=save, output_dir=output_dir,
    )
    print(f"Done. Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
