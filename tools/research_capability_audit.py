from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.paper_dashboard import build_dashboard
from tools.project_state import load_paper_index, sync_project_state


BENCHMARK_CATEGORIES = [
    {
        "name": "文献发现与采集",
        "description": "多源检索、元数据规范化、PDF 下载与正文抓取。",
        "required": ["search", "download", "crawl"],
    },
    {
        "name": "阅读与知识沉淀",
        "description": "本地 paper index、正文存储与研究看板。",
        "required": ["paper_index", "content_store", "dashboard"],
    },
    {
        "name": "综述与证据问答",
        "description": "领域综述、证据矩阵以及段落级回链问答。",
        "required": ["landscape", "literature_review", "research_qa"],
    },
    {
        "name": "研究设计与学术写作",
        "description": "实验设计、开题报告与论文正文生成。",
        "required": ["experiment_plan", "proposal", "paper_draft"],
    },
    {
        "name": "交付与办公导出",
        "description": "汇报材料、项目写论文链路，以及 DOCX/PPTX 导出。",
        "required": ["presentation", "project_paper", "office_export"],
    },
    {
        "name": "项目到论文映射",
        "description": "从本地项目抽取证据并映射到论文结构。",
        "required": ["project_analysis", "project_paper"],
    },
]


def _feature_registry(root: Path) -> dict[str, bool]:
    papers = load_paper_index(root)
    return {
        "search": True,
        "download": (PROJECT_ROOT / "tools" / "arxiv_download.py").exists() or (PROJECT_ROOT / "tools" / "paperscraper_tool.py").exists(),
        "crawl": (PROJECT_ROOT / "tools" / "paper_content_crawler.py").exists(),
        "paper_index": bool(papers) or (PROJECT_ROOT / "tools" / "project_state.py").exists(),
        "content_store": (root / "knowledge-base").exists(),
        "dashboard": (PROJECT_ROOT / "tools" / "paper_dashboard.py").exists(),
        "landscape": (PROJECT_ROOT / "tools" / "landscape_analysis.py").exists(),
        "literature_review": (PROJECT_ROOT / "tools" / "literature_review.py").exists(),
        "research_qa": (PROJECT_ROOT / "tools" / "research_qa.py").exists(),
        "experiment_plan": (PROJECT_ROOT / "tools" / "experiment_design.py").exists(),
        "proposal": (PROJECT_ROOT / "tools" / "research_bridge.py").exists(),
        "paper_draft": (PROJECT_ROOT / "tools" / "paper_writer.py").exists(),
        "project_analysis": (PROJECT_ROOT / "tools" / "project_paper_context.py").exists(),
        "project_paper": (PROJECT_ROOT / "tools" / "paper_writer.py").exists(),
        "presentation": (PROJECT_ROOT / "tools" / "research_bridge.py").exists(),
        "office_export": (PROJECT_ROOT / "tools" / "research_export.py").exists(),
    }


def _coverage_summary(features: dict[str, bool]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for category in BENCHMARK_CATEGORIES:
        required = category["required"]
        implemented = [item for item in required if features.get(item)]
        missing = [item for item in required if not features.get(item)]
        ratio = len(implemented) / max(len(required), 1)
        summary.append(
            {
                "name": category["name"],
                "description": category["description"],
                "implemented": implemented,
                "missing": missing,
                "coverage": round(ratio, 2),
                "status": "complete" if not missing else ("partial" if implemented else "missing"),
            }
        )
    return summary


def _priority_gaps(features: dict[str, bool]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    if not features.get("literature_review"):
        gaps.append(
            {
                "gap": "自动文献综述链路缺失",
                "impact": "无法把已索引论文组织成综述正文和证据矩阵。",
                "next_step": "补齐综述生成、方法族分组和研究空白归纳能力。",
            }
        )
    if not features.get("research_qa"):
        gaps.append(
            {
                "gap": "多论文证据问答缺失",
                "impact": "难以把问题和具体文献段落建立回链。",
                "next_step": "补齐段落级证据索引与问答输出。",
            }
        )
    if not features.get("office_export"):
        gaps.append(
            {
                "gap": "办公文档导出缺失",
                "impact": "难以直接交付给导师、评审或答辩场景。",
                "next_step": "增加 DOCX 与 PPTX 真导出。",
            }
        )
    if not gaps:
        gaps.append(
            {
                "gap": "系统核心能力已基本齐备",
                "impact": "当前短板更多在内容深度和具体课题数据，而不是工作流链路本身。",
                "next_step": "继续增强专题模板、真实数据接入和正式版样式打磨。",
            }
        )
    return gaps


def _render_audit_markdown(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> str:
    lines = [
        "# 科研助手能力审计",
        "",
        f"- 项目根目录：`{payload['project_root']}`",
        f"- 输出文件：`{markdown_path}`",
        f"- 结构化数据：`{json_path}`",
        f"- 已索引论文数：{payload['paper_count']}",
        "",
        "## 定位判断",
        "",
        payload["positioning_summary"],
        "",
        "## 能力覆盖",
        "",
    ]
    for item in payload["coverage"]:
        lines.extend(
            [
                f"### {item['name']}",
                "",
                f"- 状态：{item['status']}",
                f"- 覆盖率：{item['coverage']}",
                f"- 已覆盖：{', '.join(item['implemented']) or '无'}",
                f"- 缺失：{', '.join(item['missing']) or '无'}",
                f"- 说明：{item['description']}",
                "",
            ]
        )
    lines.extend(["## 优先事项", ""])
    for item in payload["priority_gaps"]:
        lines.extend(
            [
                f"### {item['gap']}",
                "",
                f"- 影响：{item['impact']}",
                f"- 建议：{item['next_step']}",
                "",
            ]
        )
    return "\n".join(lines)


def analyze_research_capabilities(project_root: str | Path = ".") -> dict[str, Any]:
    root = Path(project_root).resolve()
    features = _feature_registry(root)
    coverage = _coverage_summary(features)
    complete_count = sum(1 for item in coverage if item["status"] == "complete")
    partial_count = sum(1 for item in coverage if item["status"] == "partial")
    papers = load_paper_index(root)

    positioning_summary = (
        "当前系统已经覆盖科研助手的核心主线：文献检索、下载与正文抓取，实验设计与研究规划，"
        "开题报告、论文正文、汇报材料生成，以及项目到论文映射和办公导出。"
        " 从工作流层面看，它已经具备“研究工作台 / 科研助手”的基本闭环；"
        " 真正决定质量上限的因素，主要转向课题数据、引用深度、实验结果和最终版样式打磨。"
    )

    payload = {
        "kind": "research_capability_audit",
        "project_root": str(root),
        "paper_count": len(papers),
        "features": features,
        "coverage": coverage,
        "complete_category_count": complete_count,
        "partial_category_count": partial_count,
        "positioning_summary": positioning_summary,
        "priority_gaps": _priority_gaps(features),
    }

    markdown_path = root / "drafts" / "research-capability-audit.md"
    json_path = root / "output" / "research-capability-audit.json"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_audit_markdown(payload, markdown_path, json_path), encoding="utf-8")
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
